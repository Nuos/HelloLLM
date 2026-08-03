"""模块：services/api/client.py —— 流式事件聚合（consume_stream / call_model）。

====================================================================
HelloLLM 项目框架结构（S04-basic-miniloop，论文图1 七组件模型）

S04-basic-miniloop/
└── hello_llm/                            # Python 包
    ├── __init__.py                       包入口：版本号 + 项目说明 + 全局术语表
    ├── __main__.py                       python -m hello_llm 入口
    │
    ├── entrypoints/                      一、交互表面层（图1 "Interfaces"，对照 src/entrypoints/）
    │   ├── cli.py                        CLI 入口（对照 src/entrypoints/cli.tsx）
    │   ├── repl.py                       交互 REPL（多轮对话）
    │   ├── headless.py                   无头单次（对照 claude -p）
    │   └── render.py                     Agent-Loop 事件渲染
    │
    ├── query/                            二、核心层（图1 "Agent Loop"，对照 src/query/）
    │   ├── __init__.py
    │   └── agent_loop.py                 query_loop() 生成器 + Conversation（对照 queryLoop）
    │
    ├── services/                         三、服务层（对照 src/services/）
    │   └── api/                          四、API 客户端（对照 src/services/api/）
    │       ├── __init__.py
    │       ├── config.py                 ModelConfig 模型调用配置（含 API key 校验）
    │       ├── types.py                  数据结构与异常
    │       ├── claude.py                 stream_chat：SSE 流式客户端（对照 claude.ts）
    │       └── client.py                 consume_stream + call_model（对照 client.ts）★★★ 本模块 ★★★
    │
    ├── tools/                            五、工具层（对照 src/tools/：FileReadTool 等）
    │   ├── __init__.py
    │   ├── registry.py                   工具 Schema 池 + execute 分派（对照 tools.ts）
    │   └── file_tools.py                 read_file / write_file / edit_file 实现
    │
    ├── utils/                            六、工具函数层（对照 src/utils/：config.ts 等）
    │   ├── __init__.py
    │   ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）
    │   └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）
    │
    ├── hooks/                            七、hook 机制（★ S03 新增，对照 src/utils/hooks.ts）
        ├── __init__.py                   包入口（聚合导出）
        ├── config.py                     加载 hook 规则（项目内 + 用户级合并）
        ├── runner.py                     subprocess 执行 hook（stdin/stdout JSON）
        └── manager.py                    HookManager：PreToolUse/PostToolUse 调度
    │
    └── permissions/                      八、权限系统（★ S04 新增，图1 "Permission System"）
        ├── __init__.py                   包入口（聚合导出）
        ├── policies.py                   工具→策略等级映射（deny-first）
        ├── modes.py                      权限模式（interactive / auto-accept / read-only）
        └── gate.py                       PermissionGate：execute 前检查（allow/ask/deny）
缩略词说明（本模块涉及的术语）：
    1.  JSON —— JavaScript Object Notation，轻量数据交换格式（工具参数格式）
"""

from __future__ import annotations  # 延迟求值注解

import json  # 工具参数 JSON 串解析成字典
from typing import Any, Iterator, Optional  # 类型标注

from .config import ModelConfig  # 请求配置
from .claude import stream_chat  # SSE 流式事件源
from .types import ModelResponse, ToolCall  # 数据结构


def consume_stream(events: Iterator[dict]) -> Iterator[dict]:
    """函数：消费流式事件并聚合（非流式入口的适配层）。

一、功能作用（算法）
    遍历 stream_chat 产出的事件：把 text_delta/reasoning_delta 转发给
    渲染回调（逐字上屏），同时把内容增量追加进缓冲区；事件流结束后
    用累积的内容与工具调用组装成 ModelResponse 返回。

二、输入（input）
    stream：stream_chat 的事件生成器。
    on_delta：可选渲染回调（接收文本增量）；不传则只聚合不转发。

三、输出（output）
    聚合完成的 ModelResponse：含完整回复文本与工具调用列表。"""
    response = ModelResponse()  # response：聚合结果
    tool_buf: dict[int, dict] = {}  # tool_buf：index → 聚合中的工具调用（id/name/arguments 拼接）
    order: list[int] = []  # order：工具调用出现顺序（保证返回顺序稳定）

    for event in events:  # event：单个流式事件
        if event["type"] == "text_delta":
            response.text += event["text"]  # 累积正文
            yield {"type": "text_delta", "text": event["text"]}  # 转发给 UI
            continue
        if event["type"] == "reasoning_delta":
            yield {"type": "reasoning_delta", "text": event["text"]}  # 只转发不累积
            continue
        # ── 工具调用增量聚合 ──
        idx = event["index"]  # idx：工具调用块的归组 index
        if idx not in tool_buf:
            tool_buf[idx] = {"id": event["id"], "name": event["name"], "arguments": ""}
            order.append(idx)
        buf = tool_buf[idx]  # buf：该 index 的聚合缓冲
        if event["id"]:
            buf["id"] = event["id"]  # 第一块才带 id，后续块只带 arguments
        if event["name"]:
            buf["name"] = event["name"]
        buf["arguments"] += event["arguments"]  # 参数 JSON 字符串逐块拼接

    # 聚合完成：把拼接好的 JSON 参数串解析成字典
    for idx in order:  # idx：按出现顺序遍历
        buf = tool_buf[idx]
        raw_args = buf["arguments"].strip()  # raw_args：拼接后的参数 JSON 串
        try:
            args: dict[str, Any] = json.loads(raw_args) if raw_args else {}  # args：解析后的参数字典
        except json.JSONDecodeError:
            args = {"_raw": raw_args}  # 解析失败兜底：原始串塞进 _raw，不崩循环
        response.tool_calls.append(
            ToolCall(id=buf["id"], name=buf["name"], arguments=args)
        )
    yield {"type": "model_response", "response": response}


def call_model(
    messages: list[dict[str, Any]],  # msgs：对话历史
    tools: Optional[list[dict]] = None,  # 工具 Schema
    cfg: Optional[ModelConfig] = None,  # 请求配置
) -> ModelResponse:
    """函数：聚合模型调用（非流式）。

一、功能作用（算法）
    组装请求并一次性拉取完整响应：成功时从 JSON 解析出回复文本
    与工具调用（tool_use blocks）；HTTP 层错误抛 ModelError(kind="http")，
    网络层错误（URLError/OSError/超时）抛 ModelError(kind="network"/"timeout")。

二、输入（input）
    cfg：模型调用配置。
    messages：OpenAI 格式消息列表。
    tools：工具 Schema 列表；空列表则不声明工具。

三、输出（output）
    ModelResponse：完整回复（文本或工具调用）；失败抛 ModelError，
    由 Agent-Loop 的网络重试机制决定是否重试。"""
    response = ModelResponse()
    for event in consume_stream(stream_chat(messages, tools, cfg)):
        if event["type"] == "model_response":
            response = event["response"]  # 只取最终聚合结果，丢弃增量
    return response

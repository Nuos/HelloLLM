"""模块：providers/client.py —— 流式事件聚合（consume_stream / call_model）。

====================================================================
HelloLLM 项目框架结构（v1，论文图1 七组件模型 → 模块映射）

hello_llm/
├── __init__.py                 包入口：版本号 + 项目说明
├── __main__.py                 python -m hello_llm 入口
│
├── entrypoints/                一、交互表面层（图1 "Interfaces"）
│   ├── __init__.py
│   ├── cli.py                  CLI 入口：argparse + 配置校验 + 分派
│   ├── repl.py                 交互 REPL（多轮对话）
│   ├── headless.py             无头单次（对照 claude -p）
│   └── render.py               Agent-Loop 事件渲染
│
├── query/                      二、核心层（图1 "Agent Loop"）
│   ├── __init__.py
│   └── agent_loop.py           query_loop() 生成器 + Conversation
│
├── config/                     三、配置层（本地配置文件）
│   ├── __init__.py
│   └── loader.py               ~/.hellollm/config.json 定位/解析/合并
│
├── providers/                  四、模型提供商层（Agent-Loop 的 callModel）
│   ├── __init__.py
│   ├── config.py               ModelConfig 模型调用配置（含 API key 校验）
│   ├── types.py                数据结构与异常
│   ├── openai_compatible.py    stream_chat：SSE 流式客户端
│   └── client.py               ★★★ 本模块：流式事件聚合 ★★★
│
├── tools/                      五、工具层（Agent-Loop 的 execute 路径）
    ├── __init__.py
    ├── registry.py             工具 Schema 池 + execute 分派
    └── file_tools.py           文件工具实现
│
└── logging/                    六、日志提示层（诊断提示/事件通知）
    ├── __init__.py
    └── events.py               事件提示函数（裁剪/预算/额度/工具）
====================================================================
缩略词说明（本模块涉及的术语）：
    1.  JSON —— JavaScript Object Notation，轻量数据交换格式（工具参数格式）
"""

from __future__ import annotations  # 延迟求值注解

import json  # 工具参数 JSON 串解析成字典
from typing import Any, Iterator, Optional  # 类型标注

from .config import ModelConfig  # 请求配置
from .openai_compatible import stream_chat  # SSE 流式事件源
from .types import ModelResponse, ToolCall  # 数据结构


def consume_stream(events: Iterator[dict]) -> Iterator[dict]:
    """生成器：消费流式事件，边转发增量边聚合工具调用。

    一、功能作用（对应论文 §4.1）
        "生成器式设计使 UI 层能够获得流式输出，同时在循环内部保持
         单一、同步可读的控制流。" —— 本函数同时满足两者：
            1. text_delta / reasoning_delta 增量原样透传给 UI 层；
            2. tool_call 增量按 index 聚合，最终产出完整 ModelResponse。

    二、参数
        events  （Iterator[dict]）流式事件源（providers/openai_compatible.stream_chat 的产出）

    三、返回
        Iterator[dict]：转发 text_delta / reasoning_delta；
        结束前 yield {"type": "model_response", "response": ModelResponse}。
    """
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
    """函数：非流式聚合入口，返回完整 ModelResponse。

    一、功能作用
        把流式事件聚合成一次完整的模型响应。
        供 query/agent_loop.py 的聚合通道与测试注入使用
        （流式通道直接用 consume_stream 逐事件消费）。

    二、参数
        messages  （list）对话历史
        tools     （list|None）工具 Schema
        cfg       （ModelConfig|None）请求配置

    三、返回
        ModelResponse：正文 + 工具调用列表。
    """
    response = ModelResponse()
    for event in consume_stream(stream_chat(messages, tools, cfg)):
        if event["type"] == "model_response":
            response = event["response"]  # 只取最终聚合结果，丢弃增量
    return response

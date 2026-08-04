"""模块：services/api/client.py —— 流式事件聚合（consume_stream / call_model）。

====================================================================
HelloLLM 项目框架结构（S06-basic-ee，论文图1 七组件模型 → 模块映射）

S06-basic-ee/
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
    └── utils/                            六、工具函数层（对照 src/utils/：config.ts 等）
        ├── __init__.py
        ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）
        └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）缩略词说明（本模块涉及的术语）：
    1.  JSON —— JavaScript Object Notation，轻量数据交换格式（工具参数格式）
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Optional

from .config import ModelConfig
from .claude import stream_chat
from .types import ModelResponse, ToolCall


def consume_stream(events: Iterator[dict]) -> Iterator[dict]:
    response = ModelResponse()
    tool_buf: dict[int, dict] = {}
    order: list[int] = []

    for event in events:
        if event["type"] == "text_delta":
            response.text += event["text"]
            yield {"type": "text_delta", "text": event["text"]}
            continue
        if event["type"] == "reasoning_delta":
            yield {"type": "reasoning_delta", "text": event["text"]}
            continue

        idx = event["index"]
        if idx not in tool_buf:
            tool_buf[idx] = {"id": event["id"], "name": event["name"], "arguments": ""}
            order.append(idx)
        buf = tool_buf[idx]
        if event["id"]:
            buf["id"] = event["id"]
        if event["name"]:
            buf["name"] = event["name"]
        buf["arguments"] += event["arguments"]


    for idx in order:
        buf = tool_buf[idx]
        raw_args = buf["arguments"].strip()
        try:
            args: dict[str, Any] = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            args = {"_raw": raw_args}
        response.tool_calls.append(
            ToolCall(id=buf["id"], name=buf["name"], arguments=args)
        )
    yield {"type": "model_response", "response": response}


def call_model(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict]] = None,
    cfg: Optional[ModelConfig] = None,
) -> ModelResponse:
    # QQQ（已答）：它看起来像"空壳"，实际是"聚合收口"函数，真正的解析发生在更底层：
    # ① stream_chat（claude.py）：发出 HTTP 请求，逐字节读 SSE 网络流；
    # ② consume_stream：把原始流切成事件（文本增量/工具调用），边切边 yield；
    # ③ call_model：只做两件事——遍历 consume_stream 的事件流，把其中
    #    model_response 事件携带的完整 ModelResponse 对象取出来返回。
    # 换句话说：数据解析在 stream_chat/consume_stream 完成，call_model 负责
    # "把生成器变成单个返回值"，供上层（agent_loop）直接使用。
    response = ModelResponse()
    for event in consume_stream(stream_chat(messages, tools, cfg)):
        if event["type"] == "model_response":
            response = event["response"]
    return response

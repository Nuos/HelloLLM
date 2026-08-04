"""模块：services/api/claude.py —— OpenAI 兼容 SSE 流式客户端。

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
    │       ├── claude.py                 stream_chat：SSE 流式客户端（对照 claude.ts）★★★ 本模块 ★★★
    │       └── client.py                 consume_stream + call_model（对照 client.ts）
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
    1.  API —— Application Programming Interface，应用程序编程接口
    2.  SSE —— Server-Sent Events，服务器推送事件（HTTP 流式协议，逐块推送）
    3.  HTTP —— HyperText Transfer Protocol，超文本传输协议
    4.  JSON —— JavaScript Object Notation，轻量数据交换格式（SSE 载荷格式）
    5.  Bearer —— HTTP 鉴权方案（Authorization: Bearer <token>）
    6.  DNS —— Domain Name System，域名系统
    7.  URL —— Uniform Resource Locator，统一资源定位符
    8.  UTF-8 —— 8-bit Unicode Transformation Format，可变长字符编码
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Iterator, Optional

from .config import ModelConfig
from .types import ModelError


def stream_chat(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict]] = None,
    cfg: Optional[ModelConfig] = None,
) -> Iterator[dict]:

    cfg = cfg or ModelConfig()
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
    if cfg.max_tokens:
        payload["max_tokens"] = cfg.max_tokens


    req = urllib.request.Request(
        f"{cfg.api_base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=cfg.timeout)
    except urllib.error.HTTPError as e:

        body = e.read().decode("utf-8", "replace")[:500]
        if e.code == 429:


            from ...utils import rate_limited

            rate_limited(e.code, body[:200])
        raise ModelError(f"HTTP {e.code}: {body}", kind="http") from e
    except (urllib.error.URLError, OSError) as e:

        if isinstance(e, (socket.timeout, TimeoutError)):

            raise ModelError(
                f"请求超时（>{cfg.timeout} 秒）。可稍后重试，或用 --timeout 调大。",
                kind="timeout",
            ) from e
        reason = getattr(e, "reason", e)
        raise ModelError(f"网络错误: {reason}", kind="network") from e


    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = event.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        if delta.get("content"):
            yield {"type": "text_delta", "text": delta["content"]}
        if delta.get("reasoning_content"):
            yield {"type": "reasoning_delta", "text": delta["reasoning_content"]}
        for tc in delta.get("tool_calls") or []:
            fn = tc.get("function") or {}
            yield {
                "type": "tool_call_delta",
                "index": tc.get("index", 0),
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments", ""),
            }

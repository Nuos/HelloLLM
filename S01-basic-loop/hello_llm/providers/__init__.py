"""模块：providers —— 模型提供商层（包）。

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
│   ├── __init__.py             ★★★ 本模块：包入口（聚合导出）★★★
│   ├── config.py               ModelConfig 模型调用配置（含 API key 校验）
│   ├── types.py                数据结构与异常
│   ├── openai_compatible.py    stream_chat：SSE 流式客户端
│   └── client.py               consume_stream + call_model：流式事件聚合
│
├── tools/                      五、工具层（Agent-Loop 的 execute 路径）
│   ├── __init__.py
│   ├── registry.py             工具 Schema 池 + execute 分派
│   └── file_tools.py           文件工具实现
│
└── logging/                    六、日志提示层（诊断提示/事件通知）
    ├── __init__.py
    └── events.py               事件提示函数（裁剪/预算/额度/工具）
====================================================================

对应参考实现（clawcodex 的 src/providers/）：
    本包 = 模型调用配置、数据结构、SSE 流式协议解析与事件聚合。

缩略词说明（本模块涉及的术语）：
    1. API —— Application Programming Interface，应用程序编程接口
    2. SSE —— Server-Sent Events，服务器推送事件（HTTP 流式协议）
"""

from .config import ModelConfig, DEFAULT_API_BASE, DEFAULT_MODEL, DEFAULT_SYSTEM
from .types import ToolCall, ModelResponse, ModelError, ConfigError
from .openai_compatible import stream_chat
from .client import consume_stream, call_model

__all__ = [
    "ModelConfig",
    "ToolCall",
    "ModelResponse",
    "ModelError",
    "ConfigError",
    "stream_chat",
    "consume_stream",
    "call_model",
    "DEFAULT_API_BASE",
    "DEFAULT_MODEL",
    "DEFAULT_SYSTEM",
]

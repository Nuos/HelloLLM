"""模块：utils/config.py —— 本地配置文件加载。

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
    │       └── client.py                 consume_stream + call_model（对照 client.ts）
    │
    ├── tools/                            五、工具层（对照 src/tools/：FileReadTool 等）
    │   ├── __init__.py
    │   ├── registry.py                   工具 Schema 池 + execute 分派（对照 tools.ts）
    │   └── file_tools.py                 read_file / write_file / edit_file 实现
    │
    └── utils/                            六、工具函数层（对照 src/utils/：config.ts 等）
        ├── __init__.py
        ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）★★★ 本模块 ★★★
        └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）
    1.  API —— Application Programming Interface，应用程序编程接口
    2.  JSON —— JavaScript Object Notation，轻量数据交换格式（配置文件格式）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..services.api.config import ModelConfig


DEFAULT_CONFIG_DIR = Path.home() / ".hellollm"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"


def find_config_path(explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    return DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.is_file() else None


def load_config(explicit: Optional[str] = None) -> dict:
    p = find_config_path(explicit)
    if p is None:
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def build_model_config(args) -> ModelConfig:
    file_config = load_config(getattr(args, "config", None))
    return ModelConfig(
        api_base=args.api_base,
        api_key=args.api_key,
        model=args.model,
        timeout=args.timeout,
        file_config=file_config,
    )

"""模块：services/api/config.py —— 模型调用配置 ModelConfig。

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
    │       ├── config.py                 ModelConfig 模型调用配置（含 API key 校验）★★★ 本模块 ★★★
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
        ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）
        └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）缩略词说明（本模块涉及的术语）：
    1.  API —— Application Programming Interface，应用程序编程接口
    2.  JSON —— JavaScript Object Notation，轻量数据交换格式（配置文件格式）
    3.  F5 —— VS Code 调试启动快捷键（指代调试入口；不加载 shell 配置）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .types import ConfigError


DEFAULT_API_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


DEFAULT_SYSTEM = (
    "You are HelloLLM, a minimal coding agent rebuilt from the architecture of "
    "Claude Code (paper: arXiv:2604.14228). You can read, write and edit files "
    "with tools. Be concise. When a task involves files, use the tools."
)


DEFAULT_CONFIG_PATH = "~/.hellollm/config.json"


@dataclass
class ModelConfig:
    api_base: str = ""
    api_key: str = ""
    model: str = ""
    timeout: Optional[float] = None
    max_tokens: Optional[int] = None
    file_config: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        fc = self.file_config or {}
        self.api_base = (
            self.api_base or fc.get("api_base") or DEFAULT_API_BASE
        ).rstrip("/")
        self.api_key = self.api_key or fc.get("api_key") or ""
        self.model = self.model or fc.get("model") or DEFAULT_MODEL
        timeout = self.timeout
        if timeout is None:
            timeout = fc.get("timeout")
        self.timeout = float(timeout) if timeout is not None else 120.0

    def require_api_key(self) -> None:
        if not self.api_key:
            raise ConfigError(
                "未配置 API Key。\n"
                f"  推荐：在本地配置文件 {DEFAULT_CONFIG_PATH} 中填写（JSON 格式）：\n"
                '    {\n'
                '      "api_key": "sk-...",\n'
                '      "api_base": "https://api.deepseek.com",\n'
                '      "model": "deepseek-v4-flash"\n'
                '    }\n'
                f"  创建方式：mkdir -p ~/.hellollm && 写入上述内容 && chmod 600 {DEFAULT_CONFIG_PATH}\n"
                "  备选：命令行 --api-key sk-...（临时用）或 --config <path>（指定其他配置文件）"
            )

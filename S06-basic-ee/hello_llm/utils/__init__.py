"""模块：utils —— 工具层（包），对照 claude-code 源码 src/utils/。

====================================================================
HelloLLM 项目框架结构（S06-basic-ee，论文图1 七组件模型 → 模块映射）

S06-basic-ee/
└── hello_llm/                            # Python 包
    ├── __init__.py                       包入口：版本号 + 项目说明 + 全局术语表
    ├── __main__.py                       python -m hello_llm 入口
    │
    ├── entrypoints/                      一、交互表面层（图1 "Interfaces"，对照 src/entrypoints/）
    │   ├── __init__.py
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
        ├── __init__.py                   ★★★ 本模块：包入口（聚合导出）★★★
        ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）
        └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）
====================================================================

缩略词说明（本模块涉及的术语）：
    1. CLI —— Command-Line Interface，命令行接口
    2. Agent-Loop —— 智能体循环（模型调用/工具分派/结果收集的迭代周期）
"""

from .config import find_config_path, load_config, build_model_config
from .logging import (
    notice,
    warn,
    error,
    tool,
    loop_turn,
    tool_triggered,
    tool_result_summary,
    context_trimmed,
    max_turns_reached,
    rate_limited,
)

__all__ = [
    "find_config_path",
    "load_config",
    "build_model_config",
    "notice",
    "warn",
    "error",
    "tool",
    "loop_turn",
    "tool_triggered",
    "tool_result_summary",
    "context_trimmed",
    "max_turns_reached",
    "rate_limited",
]

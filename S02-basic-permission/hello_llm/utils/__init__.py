"""模块：utils —— 工具层（包），对照 claude-code 源码 src/utils/。

====================================================================
HelloLLM 项目框架结构（S01-basic-loop，论文图1 七组件模型 → 模块映射）

S01-basic-loop/
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
    ├── utils/                            六、工具函数层（对照 src/utils/：config.ts 等）
    │   ├── __init__.py                   ★★★ 本模块：包入口（聚合导出）★★★
    │   ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）
    │   └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）
    │
    └── permissions/                      七、权限系统（★ S02 新增，图1 "Permission System"）
        ├── __init__.py                   包入口（聚合导出）
        ├── policies.py                   工具→策略等级映射（deny-first）
        ├── modes.py                      权限模式（interactive / auto-accept / read-only）
        └── gate.py                       PermissionGate：execute 前检查（allow/ask/deny）
====================================================================

缩略词说明（本模块涉及的术语）：
    1. CLI —— Command-Line Interface，命令行接口
    2. Agent-Loop —— 智能体循环（模型调用/工具分派/结果收集的迭代周期）
"""

from .config import find_config_path, load_config, build_model_config  # 本地配置文件加载
from .logging import (  # 日志提示层：业务事件通知
    notice,  # 信息级提示（青色 ℹ）
    warn,  # 警告级提示（黄色 ⚠）
    error,  # 错误级提示（红色 ✖）
    tool,  # 工具级提示（紫色 ⚙）
    loop_turn,  # Agent-Loop 轮次提示
    tool_triggered,  # 工具触发提示
    tool_result_summary,  # 工具执行结果提示
    context_trimmed,  # 上下文裁剪提示（超预算）
    max_turns_reached,  # 最大轮数提示（循环限制）
    rate_limited,  # 额度/频率限制提示（HTTP 429）
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

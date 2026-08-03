"""模块：permissions —— 权限系统最小子集（包）。

====================================================================
HelloLLM 项目框架结构（S02-basic-permission，论文图1 七组件模型）

S02-basic-permission/
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
    │   └── agent_loop.py                 query_loop() 生成器 + Conversation（对照 queryLoop）
    │
    ├── services/                         三、服务层（对照 src/services/）
    │   └── api/                          四、API 客户端（对照 src/services/api/）
    │       ├── config.py                 ModelConfig 模型调用配置（含 API key 校验）
    │       ├── types.py                  数据结构与异常
    │       ├── claude.py                 stream_chat：SSE 流式客户端（对照 claude.ts）
    │       └── client.py                 consume_stream + call_model（对照 client.ts）
    │
    ├── tools/                            五、工具层（对照 src/tools/：FileReadTool 等）
    │   ├── registry.py                   工具 Schema 池 + execute 分派（对照 tools.ts）
    │   └── file_tools.py                 read_file / write_file / edit_file 实现
    │
    ├── utils/                            六、工具函数层（对照 src/utils/：config.ts 等）
    │   ├── __init__.py
    │   ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）
    │   └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）
    │
    └── permissions/                      七、权限系统（★ S02 新增，图1 "Permission System"）
        ├── __init__.py                   ★★★ 本模块：包入口（聚合导出）★★★
        ├── policies.py                   工具→策略等级映射（deny-first）
        ├── modes.py                      权限模式（interactive / auto-accept / read-only）
        └── gate.py                       PermissionGate：execute 前检查（allow/ask/deny）
====================================================================

缩略词说明（本模块涉及的术语）：
    1.  Permission System —— 权限系统（论文图1 组件，§5 deny-first / 分级信任）
    2.  deny-first —— 拒绝优先：未知/未声明策略的工具一律拒绝
    3.  graduated trust —— 分级信任：按动作危险度分级（读→写→危险）
"""

from .policies import level_for, READ, WRITE, DANGER, TOOL_POLICIES  # 策略等级
from .modes import INTERACTIVE, AUTO_ACCEPT, READ_ONLY, DEFAULT_MODE  # 权限模式
from .gate import PermissionGate  # 权限门（execute 前决策）

__all__ = [
    "level_for",
    "READ",
    "WRITE",
    "DANGER",
    "TOOL_POLICIES",
    "INTERACTIVE",
    "AUTO_ACCEPT",
    "READ_ONLY",
    "DEFAULT_MODE",
    "PermissionGate",
]

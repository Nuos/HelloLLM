"""模块：permissions —— 权限系统最小子集（包）。

====================================================================
HelloLLM 项目框架结构（S04-basic-miniloop，论文图1 七组件模型）

S04-basic-miniloop/
└── hello_llm/
    ├── __init__.py                       包入口：版本号 + 项目说明 + 全局术语表
    ├── __main__.py                       python -m hello_llm 入口
    │
    ├── entrypoints/                      一、交互表面层（图1 "Interfaces"）
    │   ├── cli.py                        CLI 入口（对照 src/entrypoints/cli.tsx）
    │   ├── repl.py                       交互 REPL（多轮对话）
    │   ├── headless.py                   无头单次（对照 claude -p）
    │   └── render.py                     Agent-Loop 事件渲染
    │
    ├── query/                            二、核心层（图1 "Agent Loop"）
    │   └── agent_loop.py                 query_loop() 生成器 + Conversation
    │
    ├── services/                         三、服务层（对照 src/services/）
    │   └── api/                          四、API 客户端（对照 src/services/api/）
    │
    ├── tools/                            五、工具层（对照 src/tools/）
    │
    ├── utils/                            六、工具函数层（对照 src/utils/）
    │
    ├── hooks/                            七、hook 机制（对照 src/utils/hooks.ts，S03）
    │
    └── permissions/                      八、权限系统（★ S04 新增，图1 "Permission System"）
        ├── __init__.py                   ★★★ 本模块：包入口（聚合导出）★★★
        ├── policies.py                   工具→策略等级映射（deny-first）
        ├── modes.py                      权限模式（interactive / auto-accept / read-only）
        └── gate.py                       PermissionGate：execute 前检查（allow/ask/deny）
====================================================================

缩略词说明（本模块涉及的术语）：
    1.  Permission System —— 权限系统（论文图1 组件，§5 deny-first / 分级信任）
    2.  deny-first —— 拒绝优先：未知/未声明策略的工具一律拒绝
    3.  graduated trust —— 分级信任：按动作危险度分级（读→写→危险）

本模块职责（业务含义）：
    在工具真正执行之前，依据"工具属于什么危险等级 + 当前权限模式"做一次
    系统级裁决：读操作直接放行、写操作让用户确认、危险操作一律拒绝。
    与 hook 机制（用户自定义管理）互补——hook 先审，权限系统兜底。
"""

from .policies import level_for, READ, WRITE, DANGER, TOOL_POLICIES  # 策略等级与工具映射
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

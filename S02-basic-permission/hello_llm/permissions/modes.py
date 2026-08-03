"""模块：permissions/modes.py —— 权限模式定义。

====================================================================
HelloLLM 项目框架结构（S02-basic-permission，论文图1 七组件模型）

S02-basic-permission/
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
    └── permissions/                      七、权限系统（★ S02 新增，图1 "Permission System"）
        ├── __init__.py                   包入口（聚合导出）
        ├── policies.py                   工具→策略等级映射（deny-first）
        ├── modes.py                      ★★★ 本模块：权限模式 ★★★
        └── gate.py                       PermissionGate：execute 前检查（allow/ask/deny）
====================================================================

缩略词说明（本模块涉及的术语）：
    1.  Y/N —— Yes/No，交互式批准的二选一确认
"""

INTERACTIVE = "interactive"  # 交互模式（默认）：写级工具弹 Y/N 批准
AUTO_ACCEPT = "auto-accept"  # 自动接受模式（--yes）：全部放行
READ_ONLY = "read-only"  # 只读模式（--read-only）：写/危险工具一律拒绝

DEFAULT_MODE = INTERACTIVE  # 默认权限模式

"""模块：permissions/modes.py —— 权限模式定义。

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
        ├── __init__.py                   包入口（聚合导出）
        ├── policies.py                   工具→策略等级映射（deny-first）
        ├── modes.py                      ★★★ 本模块：权限模式 ★★★
        └── gate.py                       PermissionGate：execute 前检查（allow/ask/deny）
====================================================================

缩略词说明（本模块涉及的术语）：
    1.  Y/N —— Yes/No，交互式批准的二选一确认

本模块职责（业务含义）：
    定义"这次运行按什么松紧程度把关"的三种模式，以及默认用哪一种。
"""

# interactive：默认模式。写级工具执行前弹出 Y/N 让用户拍板
INTERACTIVE = "interactive"
# auto-accept：全自动放行（命令行 --yes 进入），适合信任脚本/批处理场景
AUTO_ACCEPT = "auto-accept"
# read-only：只读模式（命令行 --read-only 进入），写和危险动作一律拒绝，
# 保证模型只能看不能改，适合审查/规划类任务
READ_ONLY = "read-only"

DEFAULT_MODE = INTERACTIVE  # 用户没有显式指定时的默认模式

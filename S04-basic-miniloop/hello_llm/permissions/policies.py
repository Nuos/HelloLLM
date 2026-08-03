"""模块：permissions/policies.py —— 工具到策略等级的映射。

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
        ├── policies.py                   ★★★ 本模块：工具→策略等级映射 ★★★
        ├── modes.py                      权限模式（interactive / auto-accept / read-only）
        └── gate.py                       PermissionGate：execute 前检查（allow/ask/deny）
====================================================================

缩略词说明（本模块涉及的术语）：
    1.  deny-first —— 拒绝优先：未知/未声明策略的工具一律拒绝
    2.  graduated trust —— 分级信任：按动作危险度分级（读→写→危险）

本模块职责（业务含义）：
    给每个内置工具打上危险等级标签，并回答"某个工具属于什么等级"。
    等级标签是权限系统裁决的依据：等级越高，放行门槛越高。
"""

# 一、策略等级（graduated trust 最小版，论文 §5）
# read：只读动作，没有副作用（不改文件不改状态），自动放行
READ = "read"
# write：会改文件或状态的写动作，需要用户点头（Y/N）才放行
WRITE = "write"
# danger：危险动作；未知工具也归入这一级——没声明过策略就当最危险处理（deny-first）
DANGER = "danger"

# 二、工具 → 等级对照表
# 新增工具必须在这里显式声明等级，否则按未知工具默认拒绝处理
TOOL_POLICIES = {
    "read_file": READ,  # 读文件内容：只读，自动放行
    "write_file": WRITE,  # 写入/创建文件：写，需批准
    "edit_file": WRITE,  # 替换文件里的文本：写，需批准
}


def level_for(tool_name: str) -> str:
    """函数：查某个工具的等级。

    一、功能作用（算法）
        先在对照表里精确查找工具名，找到就返回它声明的等级；
        找不到（未知工具）直接返回危险级——宁可多拦，不可漏拦（deny-first）。

    二、输入（input）
        tool_name：要查询的工具名，来自 Agent-Loop 里的工具调用。

    三、输出（output）
        该工具的等级：read（只读）、write（写）、danger（危险/未知）三者之一。
    """
    return TOOL_POLICIES.get(tool_name, DANGER)

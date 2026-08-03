"""模块：permissions/policies.py —— 工具→策略等级映射。

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
        ├── policies.py                   ★★★ 本模块：工具→策略等级映射 ★★★
        ├── modes.py                      权限模式（interactive / auto-accept / read-only）
        └── gate.py                       PermissionGate：execute 前检查（allow/ask/deny）
====================================================================

缩略词说明（本模块涉及的术语）：
    1.  deny-first —— 拒绝优先：未知/未声明策略的工具一律拒绝
    2.  graduated trust —— 分级信任：按动作危险度分级（读→写→危险）
"""

# 一、策略等级（graduated trust 最小版，论文 §5）
READ = "read"  # 只读：无副作用，自动放行
WRITE = "write"  # 写：修改文件/状态，需批准（Y/N）
DANGER = "danger"  # 危险：一律拒绝（含未知工具，deny-first）

# 二、工具 → 策略等级映射（新增工具必须在此显式声明，否则默认拒绝）
TOOL_POLICIES = {
    "read_file": READ,  # 读取文件内容：只读，自动放行
    "write_file": WRITE,  # 写入/创建文件：写，需批准
    "edit_file": WRITE,  # 编辑文件（替换文本）：写，需批准
}


def level_for(tool_name: str) -> str:
    """函数：查询工具的策略等级。

    一、功能作用
        返回工具对应的等级（read / write / danger）；
        未知工具按 danger 处理 —— deny-first（拒绝优先）。

    二、参数
        tool_name （str）工具名（如 "read_file"）

    三、返回
        str：READ / WRITE / DANGER 之一。
    """
    return TOOL_POLICIES.get(tool_name, DANGER)

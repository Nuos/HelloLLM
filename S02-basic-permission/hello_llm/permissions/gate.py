"""模块：permissions/gate.py —— PermissionGate 执行前检查门。

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
        ├── modes.py                      权限模式（interactive / auto-accept / read-only）
        └── gate.py                       ★★★ 本模块：PermissionGate ★★★
====================================================================

缩略词说明（本模块涉及的术语）：
    1.  PermissionGate —— 权限门：工具执行前的统一决策点
    2.  deny-first —— 拒绝优先：无交互通道时 ask 决策默认拒绝
"""

from typing import Callable, Optional  # 类型标注

from .modes import INTERACTIVE, AUTO_ACCEPT, READ_ONLY  # 权限模式常量
from .policies import level_for, READ, WRITE, DANGER  # 策略等级与映射

# 决策结果常量（check 返回值）
ALLOW = "allow"  # 放行：工具可执行
ASK = "ask"  # 询问：交给 asker 回调（REPL Y/N）
DENY = "deny"  # 拒绝：工具不执行

# asker 回调签名：asker(tool_name, arguments) -> bool（True=放行）
Asker = Callable[[str, dict], bool]


class PermissionGate:
    """类：权限门 —— 工具执行前的统一决策（模式 × 等级决策矩阵）。

    一、功能作用（论文 §5 deny-first / graduated trust 最小落地）
        query/agent_loop.py 在 execute 之前调用 decide()：
        决策矩阵（interactive 模式）：read→allow、write→ask、danger/未知→deny。
        集成位置对应论文图1 数据流 "loop → permission system → tools"。

    二、属性
        mode  （str）权限模式（INTERACTIVE / AUTO_ACCEPT / READ_ONLY）
        asker （Asker|None）Y/N 批准回调（REPL 注入；None = 无交互通道）
    """

    def __init__(self, mode: str = INTERACTIVE, asker: Optional[Asker] = None):
        """方法：初始化权限门（mode 权限模式；asker 批准回调）。"""
        self.mode = mode  # mode：权限模式（决定决策矩阵）
        self.asker = asker  # asker：Y/N 回调（交互 REPL 注入）

    def check(self, tool_name: str) -> str:
        """方法：检查工具动作 —— 返回 allow / ask / deny（不交互）。

        一、功能作用
            纯决策（无副作用）：按"模式 × 等级"矩阵返回决策结果。

        二、参数
            tool_name （str）工具名

        三、返回
            str：ALLOW（放行）/ ASK（需询问）/ DENY（拒绝）。

        四、决策矩阵
            | 模式           | read | write | danger/未知 |
            |----------------|------|-------|------------|
            | interactive    | allow| ask   | deny       |
            | auto-accept    | allow| allow | allow      |
            | read-only      | allow| deny  | deny       |
        """
        level = level_for(tool_name)  # level：工具策略等级（未知→danger）
        if self.mode == AUTO_ACCEPT:
            return ALLOW  # 自动接受：全部放行
        if level == READ:
            return ALLOW  # 只读：自动放行
        if self.mode == READ_ONLY:
            return DENY  # 只读模式：写/危险全拒
        if level == DANGER:
            return DENY  # 危险/未知：拒绝（deny-first）
        return ASK  # write + interactive：需批准

    def decide(self, tool_name: str, arguments: dict) -> bool:
        """方法：完整决策 —— 返回 True（放行）/ False（拒绝）。

        一、功能作用
            基于 check() 的结果做最终放行：
            1. allow → True（放行执行）；
            2. deny → False（拒绝，不执行）；
            3. ask → 交给 asker 回调（REPL Y/N）；
               无 asker（无头/非 TTY）→ 默认拒绝（deny-first 安全默认）。

        二、参数
            tool_name  （str）工具名
            arguments  （dict）工具参数（传给 asker 供用户判断）

        三、返回
            bool：True=放行执行；False=拒绝（不执行）。
        """
        result = self.check(tool_name)  # result：决策矩阵结果
        if result == ALLOW:
            return True
        if result == DENY:
            return False
        # ASK：交给批准回调（无回调 → 拒绝，安全默认）
        if self.asker is None:
            return False
        return self.asker(tool_name, arguments)

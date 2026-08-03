"""模块：permissions/gate.py —— PermissionGate 执行前检查门。

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
        ├── modes.py                      权限模式（interactive / auto-accept / read-only）
        └── gate.py                       ★★★ 本模块：PermissionGate ★★★
====================================================================

缩略词说明（本模块涉及的术语）：
    1.  PermissionGate —— 权限门：工具执行前的统一决策点
    2.  deny-first —— 拒绝优先：无交互通道时 ask 决策默认拒绝

本模块职责（业务含义）：
    把"工具等级"和"权限模式"放进一张矩阵，得出三种结果之一：
    放行、需要询问、直接拒绝。interactive 模式下询问会交给用户，
    没有用户可问（无头运行）时，询问一律按拒绝处理——宁可误拦不可漏放。
"""

from typing import Callable, Optional  # 类型标注：Callable 声明批准回调形态，Optional 声明回调可为空

from .modes import INTERACTIVE, AUTO_ACCEPT, READ_ONLY  # 三种权限模式
from .policies import level_for, READ, WRITE, DANGER  # 工具等级与查询

# 决策结果常量（check 返回值）
ALLOW = "allow"  # 放行：工具可以执行
ASK = "ask"  # 询问：交给批准回调（REPL 弹 Y/N）
DENY = "deny"  # 拒绝：工具不执行

# 批准回调的形态：给定工具名和参数，返回是否放行。
# 单独抽出类型是为了让 REPL 注入真实问答、测试注入假回调。
Asker = Callable[[str, dict], bool]


class PermissionGate:
    """类：权限门 —— 工具执行前的系统级裁决。

    一、功能作用（论文 §5 deny-first / 分级信任的最小落地）
        Agent-Loop 在工具执行前调用 decide()，按"模式 × 等级"矩阵裁决：
        interactive 模式下：只读放行、写要批准、危险/未知拒绝。
        集成位置对应论文图1 数据流 "loop → permission system → tools"。
    """

    def __init__(self, mode: str = INTERACTIVE, asker: Optional[Asker] = None):
        """方法：构建权限门。

        一、功能作用
            记下本次运行用的权限模式，以及（交互模式下）向谁征求批准。

        二、输入（input）
            mode：权限模式；不传默认 interactive。
            asker：批准回调；交互 REPL 会传入真实 Y/N 问答，
            无头运行不传（此时 ask 一律按拒绝处理）。

        三、输出（output）
            无返回值。构建结果保存在自身属性上，供 check/decide 使用。
        """
        self.mode = mode
        self.asker = asker

    def check(self, tool_name: str) -> str:
        """方法：查决策矩阵 —— 返回放行/询问/拒绝（不与人交互）。

        一、功能作用（算法）
            按"权限模式 × 工具等级"查表：
            auto-accept 模式：什么等级都放行（信任脚本场景）；
            read-only 模式：只放行只读，写和危险全拒（审查场景）；
            interactive 模式：只读放行、写需询问、危险/未知拒绝。

        二、输入（input）
            tool_name：要执行的工具名，用于查它的等级。

        三、输出（output）
            矩阵裁决结果：放行（allow）、询问（ask）、拒绝（deny）。
        """
        level = level_for(tool_name)
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
        """方法：完整裁决 —— 返回是否放行。

        一、功能作用（算法）
            在矩阵结果之上做最终放行：
            1. 矩阵放行 → 直接放行；
            2. 矩阵拒绝 → 直接拒绝；
            3. 矩阵询问 → 交给批准回调：有回调就听回调的，
               没有回调（无头运行）一律拒绝——没人可问就不放行。

        二、输入（input）
            tool_name：要执行的工具名。
            arguments：本次工具调用的参数，展示给用户判断用。

        三、输出（output）
            放行返回真（工具可以执行），拒绝返回假（工具不执行，
            拒绝原因由 Agent-Loop 回填给模型）。
        """
        result = self.check(tool_name)
        if result == ALLOW:
            return True
        if result == DENY:
            return False
        # 询问：交给批准回调；没有回调 → 拒绝（安全默认）
        if self.asker is None:
            return False
        return self.asker(tool_name, arguments)

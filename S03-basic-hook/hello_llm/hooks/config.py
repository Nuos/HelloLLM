"""模块：hooks/config.py —— hook 规则配置加载。

====================================================================
HelloLLM 项目框架结构（S03-basic-hook，论文图1 七组件模型）

S03-basic-hook/
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
    └── hooks/                            七、hook 机制（★ S03 新增，对照 src/utils/hooks.ts）
        ├── __init__.py                   包入口（聚合导出）
        ├── config.py                     加载 hook 规则（项目内 + 用户级合并） ★★★ 本模块 ★★★
        ├── runner.py                     subprocess 执行 hook（stdin/stdout JSON）
        └── manager.py                    HookManager：PreToolUse/PostToolUse 调度
====================================================================

缩略词说明（本模块涉及的术语）：
    1.  hook —— 钩子：外部命令在工具执行前/后触发的扩展点
    2.  JSON —— JavaScript Object Notation，轻量数据交换格式（hook 通信协议）

本模块职责（业务含义）：
    hook 规则是"哪些工具、在哪个时机、交给哪条命令管理"的对照表。
    规则分两层存放：用户级（跨项目通用）与项目级（本仓库专属），
    加载时把两层规则合并成一张表，供 HookManager 逐条执行。
"""

import json  # 读取并解析 hooks.json 里的规则内容
from pathlib import Path  # 拼接配置文件的绝对路径
from typing import Optional  # 声明 explicit 参数可为空

# 一、配置文件位置
# 项目根向上回溯两级：本文件在 hello_llm/hooks/ 下，父目录是 hello_llm/，
# 再上一级就是 S03-basic-hook/，hooks.json 就放在这个项目根下。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 项目级规则：仅对当前项目生效，内容是用户私人配置，已加入 .gitignore 不提交
PROJECT_HOOKS = PROJECT_ROOT / "hooks.json"
# 用户级规则：放在用户主目录的 .hellollm 下，对机器上所有 HelloLLM 项目生效
USER_HOOKS = Path.home() / ".hellollm" / "hooks.json"
# 示例模板：随仓库提交，展示 hooks.json 的合法写法，供用户复制改造成自己的规则
EXAMPLE_HOOKS = PROJECT_ROOT / "hooks.example.json"


def load_hook_rules(explicit: Optional[str] = None) -> dict:
    """函数：读取并合并 hook 规则表。

    一、功能作用（算法）
        先加载用户级规则，再加载项目级规则，同类规则按"用户级在前、项目级在后"
        追加进同一张表。执行顺序按表内先后：后追加的项目级规则后执行，
        因此项目级规则对工具参数的改写会覆盖用户级规则的同名改写（优先级更高）。
        文件不存在、内容不是合法 JSON、或读取失败时，直接跳过该文件，
        保证配置坏了也不影响主流程（fail-open）。

    二、输入（input）
        explicit：显式指定的规则文件路径。平时不传（走默认两层合并），
        仅调试或单测时传入，用于只读某一个文件。

    三、输出（output）
        合并后的规则表：键是 hook 时机名（如 PreToolUse、PostToolUse），
        值是该时机下的规则列表（每条含 matcher 与 command）。
        一个文件都没读到或全部损坏时，返回空表，等价于"没有配置任何 hook"。
    """
    merged: dict = {}
    paths = [USER_HOOKS, PROJECT_HOOKS]  # 用户级在前（低优先级），项目级在后（高优先级）
    if explicit:
        paths = [Path(explicit)]
    for p in paths:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            rules = data.get("hooks", {})
            for kind, items in rules.items():
                merged.setdefault(kind, []).extend(items)
        except (json.JSONDecodeError, OSError):
            continue  # 文件损坏或不可读：跳过该文件，不阻断主流程
    return merged


def match_hook(rule: dict, tool_name: str) -> bool:
    """函数：判断一条规则是否管得到某个工具。

    一、功能作用（算法）
        规则里的 matcher 是工具名的片段匹配词：工具名里包含 matcher 就算命中。
        matcher 留空表示"这条规则对所有工具生效"（相当于通配符）。
        例如 matcher 为 write 时，write_file 命中、read_file 不命中。

    二、输入（input）
        rule：单条 hook 规则，至少要含 matcher 字段。
        tool_name：即将被管理的工具名，来自 Agent-Loop 里的工具调用。

    三、输出（output）
        命中返回真，不命中返回假。HookManager 只执行命中的规则，
        不命中的规则直接跳过，不浪费一次外部命令调用。
    """
    matcher = rule.get("matcher", "")
    return not matcher or matcher in tool_name

"""模块：hooks/manager.py —— HookManager 调度与决策合并。

====================================================================
HelloLLM 项目框架结构（S05-basic-statepersist，论文图1 七组件模型）

S05-basic-statepersist/
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
    ├── hooks/                            七、hook 机制（★ S03 新增，对照 src/utils/hooks.ts）
        ├── __init__.py                   包入口（聚合导出）
        ├── config.py                     加载 hook 规则（项目内 + 用户级合并）
        ├── runner.py                     subprocess 执行 hook（stdin/stdout JSON）
        └── manager.py                    HookManager：PreToolUse/PostToolUse 调度 ★★★ 本模块 ★★★
====================================================================
    │
    └── state/                            八、状态与持久化（★ S05 新增，图1 "State & Persistence"）
        ├── __init__.py                   包入口（聚合导出）
        ├── store.py                      会话 JSONL 存储（对照 src/utils/sessionStorage.ts）
        └── session.py                    会话名生成与元数据

缩略词说明（本模块涉及的术语）：
    1.  hook —— 钩子：外部命令在工具执行前/后触发的扩展点
    2.  JSON —— JavaScript Object Notation，轻量数据交换格式（hook 通信协议）

本模块职责（业务含义）：
    对 Agent-Loop 里的每一次工具调用，按规则表找出该管它的 hook，
    依次执行并合并决策：执行前（PreToolUse）决定"让不让跑、参数要不要改"，
    执行后（PostToolUse）决定"结果要不要改"。是 hook 机制对工具的管理中枢。
"""

from typing import Callable, Optional  # 类型标注：Callable 声明执行器形态，Optional 声明规则可为空

from .config import load_hook_rules, match_hook  # 规则表加载、规则命中判断
from .runner import run_hook_command, build_payload  # 外部命令执行、上下文拼装

# hook 执行器的形态：给它一条命令和一份上下文，它返回一份决策。
# 单独抽出这个类型，是为了让单测能注入假执行器，避免测试真的拉起外部进程。
HookRunner = Callable[[str, dict], dict]

# 决策常量：hook 返回的 decision 字段只认这三个值。
# deny 只出现在工具执行前（PreToolUse）——工具还没跑，拦下来还来得及；
# error 表示 hook 自身出错，上层按 fail-open 放行，不让 hook 故障卡死流程。
DENY = "deny"


class HookManager:
    """类：工具管理中枢 —— 在 Agent-Loop 执行工具前后跑 hook 并合并结果。"""

    def __init__(self, rules: Optional[dict] = None, runner: HookRunner = run_hook_command):
        """方法：构建管理中枢。

        一、功能作用
            记下规则表和执行器。规则表不传时自动加载配置（用户级 + 项目级合并），
            执行器不传时用真实的外部命令执行器。

        二、输入（input）
            rules：规则表，结构与 load_hook_rules 的返回值一致；
            不传表示"没有配置"，即不管理任何工具。
            runner：执行器函数；不传表示用真实 subprocess 执行 hook 命令。

        三、输出（output）
            无返回值。构建结果保存在自身属性上，供后续两次 run 调用使用。
        """
        self.rules = rules if rules is not None else load_hook_rules()
        self.runner = runner

    def run_pre_tool(self, tool_name: str, arguments: dict) -> dict:
        """方法：工具执行前的管理（PreToolUse）。

        一、功能作用（算法）
            遍历规则表里所有 PreToolUse 规则，只执行命中该工具的规则：
            1. 只要有一条规则决定拒绝，立刻停止后续规则，本次工具不执行，
               拒绝原因带回去回填给模型（模型会看到"为什么被拦"）；
            2. 规则想改写参数时，把改写结果合并进参数，后续规则和真正的
               工具调用都使用改写后的参数；
            3. 全部规则放行、或根本没有命中规则、或 hook 执行出错（fail-open），
               工具照常执行。

        二、输入（input）
            tool_name：本次要执行的工具名，用于筛出管它的规则。
            arguments：模型传给工具的原始参数，hook 可审查也可改写。

        三、输出（output）
            本次工具调用的放行结论：是否放行、放行时使用的最终参数、
            被拒绝时的原因。Agent-Loop 据此决定执行工具还是回填拒绝信息。
        """
        final_args = dict(arguments)
        for rule in self.rules.get("PreToolUse", []):
            if not match_hook(rule, tool_name):
                continue
            result = self.runner(rule["command"], build_payload(tool_name, final_args))
            if result.get("decision") == DENY:
                return {"allow": False, "arguments": final_args,
                        "reason": result.get("reason", "hook 拒绝")}
            if result.get("updatedInput") is not None:
                final_args.update(result["updatedInput"])
        return {"allow": True, "arguments": final_args, "reason": None}

    def run_post_tool(self, tool_name: str, arguments: dict, tool_output: str) -> str:
        """方法：工具执行后的管理（PostToolUse）。

        一、功能作用（算法）
            遍历规则表里所有 PostToolUse 规则，只执行命中该工具的规则：
            每条规则都能改写工具结果，后执行的规则在前一条的结果上继续改，
            最终结果回填给模型。没有命中规则或 hook 出错时，结果原样保留。

        二、输入（input）
            tool_name：刚执行完的工具名，用于筛出管它的规则。
            arguments：本次工具调用实际使用的参数（可能已被 PreToolUse 改写）。
            tool_output：工具执行产生的原始结果文本。

        三、输出（output）
            最终的工具结果文本：可能是原始结果，也可能是被 hook 改写后的结果，
            Agent-Loop 把它作为 tool_result 回填给模型。
        """
        final_output = tool_output
        for rule in self.rules.get("PostToolUse", []):
            if not match_hook(rule, tool_name):
                continue
            result = self.runner(rule["command"], build_payload(tool_name, arguments, final_output))
            if result.get("updatedOutput") is not None:
                final_output = str(result["updatedOutput"])
        return final_output

"""模块：hooks/runner.py —— hook 命令执行器（subprocess + JSON 协议）。

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
    ├── hooks/                            七、hook 机制（★ S03 新增，对照 src/utils/hooks.ts）
        ├── __init__.py                   包入口（聚合导出）
        ├── config.py                     加载 hook 规则（项目内 + 用户级合并）
        ├── runner.py                     subprocess 执行 hook（stdin/stdout JSON） ★★★ 本模块 ★★★
        └── manager.py                    HookManager：PreToolUse/PostToolUse 调度
====================================================================
    │
    └── permissions/                      八、权限系统（★ S04 新增，图1 "Permission System"）
        ├── __init__.py                   包入口（聚合导出）
        ├── policies.py                   工具→策略等级映射（deny-first）
        ├── modes.py                      权限模式（interactive / auto-accept / read-only）
        └── gate.py                       PermissionGate：execute 前检查（allow/ask/deny）

缩略词说明（本模块涉及的术语）：
    1.  hook —— 钩子：外部命令在工具执行前/后触发的扩展点
    2.  JSON —— JavaScript Object Notation，轻量数据交换格式（hook 通信协议）

本模块职责（业务含义）：
    hook 是外部命令，主进程与它之间只有 stdin/stdout 两条管道可以说话。
    本模块负责把"工具名、参数、工具输出"打包成 JSON 写进 stdin，
    再读回 stdout 里的 JSON 决策，并处理外部命令可能出现的各种失败。
"""

import json  # 把输入打包成 JSON 写进 stdin，把 stdout 的 JSON 解析成决策
import subprocess  # 拉起外部 hook 命令并等待它结束
from typing import Optional  # 声明 tool_output 参数可以为空（PreToolUse 不传）

# hook 命令最多允许运行 10 秒：超过就杀掉，按失败处理。
# 定这个上限是因为 hook 只是工具管理的一环，不能让它拖死整个 Agent-Loop。
HOOK_TIMEOUT = 10
# stdout 最多接收 10 万字节：防止 hook 输出海量垃圾撑爆内存。
MAX_OUTPUT = 100_000


def run_hook_command(command: str, payload: dict) -> dict:
    """函数：执行一条 hook 命令并解析它的决策。

    一、功能作用（算法）
        1. 把 payload 序列化成 JSON 文本，通过 stdin 交给外部命令；
        2. 外部命令结束运行后，读取 stdout，把内容解析成决策字典；
        3. 只要外部命令没正常退出、或超时、或输出不是合法决策 JSON、
           或输出超过上限，一律返回"执行出错"的标记，由上层按 fail-open
           放行 —— 宁可让工具照常执行，也不让 hook 故障卡死业务流程。

    二、输入（input）
        command：要执行的 shell 命令文本，支持管道、重定向等 bash 写法，
        通常形如 "python3 某个脚本.py"，脚本内部自行读取 stdin 的 JSON。
        payload：写给 hook 看的上下文，含工具名、工具参数，
        工具执行后才有的输出也放在这里。

    三、输出（output）
        hook 的决策结果：放行（allow）、拒绝（deny）、出错（error）三选一。
        放行时可能附带改写后的工具参数或输出；拒绝时附带拒绝原因；
        出错时附带出错原因。上层依据这份结果决定工具是执行、拦截还是原样放行。
    """
    try:
        proc = subprocess.run(
            command,
            shell=True,  # 用 bash 解释命令，支持管道/重定向等写法
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT,
        )
        if proc.returncode != 0:
            return {"decision": "error", "reason": f"hook 退出码 {proc.returncode}"}
        if len(proc.stdout) > MAX_OUTPUT:
            return {"decision": "error", "reason": "hook 输出超限"}
        out = json.loads(proc.stdout)
        if not isinstance(out, dict) or "decision" not in out:
            return {"decision": "error", "reason": "hook 输出缺少 decision"}
        return out
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        return {"decision": "error", "reason": str(e)}


def build_payload(tool_name: str, arguments: dict, tool_output: Optional[str] = None) -> dict:
    """函数：拼装写给 hook 的上下文 JSON。

    一、功能作用（算法）
        把 hook 判断所需的素材集中成一个字典：工具叫什么、这次带什么参数、
        工具执行完的结果是什么。工具输出只在工具执行后（PostToolUse）
        才存在，所以这一项留空表示当前时机拿不到输出。

    二、输入（input）
        tool_name：工具名，hook 据此决定要不要管这个工具。
        arguments：本次工具调用的参数，hook 可审查，也可在决策里改写。
        tool_output：工具执行后的结果文本；工具执行前（PreToolUse）不传。

    三、输出（output）
        结构统一的上下文字典，直接序列化后喂给外部 hook 命令的 stdin。
    """
    payload: dict = {"tool_name": tool_name, "arguments": arguments}
    if tool_output is not None:
        payload["tool_output"] = tool_output
    return payload

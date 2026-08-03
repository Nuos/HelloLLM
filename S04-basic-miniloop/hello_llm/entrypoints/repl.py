"""模块：entrypoints/repl.py —— 交互式 REPL（多轮对话）。

====================================================================
HelloLLM 项目框架结构（S04-basic-miniloop，论文图1 七组件模型）

S04-basic-miniloop/
└── hello_llm/                            # Python 包
    ├── __init__.py                       包入口：版本号 + 项目说明 + 全局术语表
    ├── __main__.py                       python -m hello_llm 入口
    │
    ├── entrypoints/                      一、交互表面层（图1 "Interfaces"，对照 src/entrypoints/）
    │   ├── cli.py                        CLI 入口（对照 src/entrypoints/cli.tsx）
    │   ├── repl.py                       交互 REPL（多轮对话）★★★ 本模块 ★★★
    │   ├── headless.py                   无头单次（对照 claude -p）
    │   └── render.py                     Agent-Loop 事件渲染
    │
    ├── query/                            二、核心层（图1 "Agent Loop"，对照 src/query/）
    │   ├── __init__.py
    │   └── agent_loop.py                 query_loop() 生成器 + Conversation（对照 queryLoop）
    │
    ├── services/                         三、服务层（对照 src/services/）
    │   └── api/                          四、API 客户端（对照 src/services/api/）
    │       ├── __init__.py
    │       ├── config.py                 ModelConfig 模型调用配置（含 API key 校验）
    │       ├── types.py                  数据结构与异常
    │       ├── claude.py                 stream_chat：SSE 流式客户端（对照 claude.ts）
    │       └── client.py                 consume_stream + call_model（对照 client.ts）
    │
    ├── tools/                            五、工具层（对照 src/tools/：FileReadTool 等）
    │   ├── __init__.py
    │   ├── registry.py                   工具 Schema 池 + execute 分派（对照 tools.ts）
    │   └── file_tools.py                 read_file / write_file / edit_file 实现
    │
    ├── utils/                            六、工具函数层（对照 src/utils/：config.ts 等）
    │   ├── __init__.py
    │   ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）
    │   └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）
    │
    ├── hooks/                            七、hook 机制（★ S03 新增，对照 src/utils/hooks.ts）
        ├── __init__.py                   包入口（聚合导出）
        ├── config.py                     加载 hook 规则（项目内 + 用户级合并）
        ├── runner.py                     subprocess 执行 hook（stdin/stdout JSON）
        └── manager.py                    HookManager：PreToolUse/PostToolUse 调度
    │
    └── permissions/                      八、权限系统（★ S04 新增，图1 "Permission System"）
        ├── __init__.py                   包入口（聚合导出）
        ├── policies.py                   工具→策略等级映射（deny-first）
        ├── modes.py                      权限模式（interactive / auto-accept / read-only）
        └── gate.py                       PermissionGate：execute 前检查（allow/ask/deny）
缩略词说明（本模块涉及的术语）：
    1.  REPL —— Read-Eval-Print Loop，读取-求值-打印循环（本模块即交互式对话界面）
    2.  TTY —— Teletype，终端设备；isatty() = 判断 stdin 是否为交互终端
    3.  EOF —— End of File，文件结束（终端 Ctrl-D 触发 EOFError）
"""

from __future__ import annotations  # 延迟求值注解

import sys  # stdin.isatty 检测 / 提示输出
from typing import TYPE_CHECKING, Optional  # 类型标注

try:
    # readline：让 input() 获得 GNU readline 行编辑能力 ——
    # 退格/左右移动/历史导航（↑↓）。仅 TTY 下生效；非 TTY（管道/输出面板）
    # 自动退化为普通读取，无害。Windows 无此模块，忽略即可。
    import readline  # noqa: F401
except ImportError:  # pragma: no cover
    pass

from ..hooks import HookManager  # S03：工具管理中枢（类型标注）
from ..permissions import PermissionGate  # S04：权限门（write 级工具 Y/N 批准）
from ..services.api import ModelConfig  # 模型配置（已由 cli 校验过 API key）
from ..query.agent_loop import Conversation  # 会话状态
from .render import render_events  # 事件渲染

if TYPE_CHECKING:
    from argparse import Namespace  # 命令行参数（仅类型标注）


def _ask_permission(tool_name: str, arguments: dict) -> bool:
    """函数：Y/N 批准回调。

    一、功能作用（算法）
        权限门决定"需要询问"时调用：把工具名和参数展示给用户，
        读用户输入，y/yes 批准执行，其余（回车/空/n/no）拒绝。
        输入流意外结束（EOF）或中断时也按拒绝处理——没人回答就不放行。

    二、输入（input）
        tool_name：请求批准的工具名。
        arguments：本次工具调用的参数，展示给用户判断。

    三、输出（output）
        放行返回真，拒绝返回假。权限门据此决定工具是否执行。
    """
    import json  # 把工具参数序列化成可读文本展示

    print(f"\n⚠ 权限请求：{tool_name}({json.dumps(arguments, ensure_ascii=False)})", file=sys.stderr)
    while True:
        try:
            ans = input("允许执行？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False  # 无输入/中断 → 拒绝（安全默认）
        if ans in ("y", "yes"):
            return True
        if ans in ("", "n", "no"):
            return False

# 一、模块常量
_EXIT_WORDS = ("exit", "quit", "/exit")  # REPL 退出指令（输入这些词即退出交互模式）


def run_repl(args: "Namespace", cfg: ModelConfig,
             hook_manager: Optional["HookManager"] = None,
             gate: Optional["PermissionGate"] = None) -> int:
    """函数：交互式 REPL 主循环。

一、功能作用（论文 §3.3 "交互式 CLI"）
    循环读用户输入：打印提示符 → 读一行 → 判定退出指令（exit/quit）
    或空行跳过 → 追加进会话 → 调 render_events 跑一轮 Agent-Loop。
    多轮共用同一会话（记忆贯穿整场对话）。

二、输入（input）
    args：命令行参数（含 --max-turns 等）。
    cfg：模型调用配置。
    hook_manager：工具管理中枢（S03）。
    gate：权限门（S04），其 asker 为 Y/N 批准回调。

三、输出（output）
    进程退出码：0 正常退出。    """
    conv = Conversation(args.system, cfg, max_context_chars=args.max_context)  # conv：会话状态
    if not sys.stdin.isatty():
        # 非 TTY（VS Code 输出面板 / 管道）：input() 无行编辑，
        # 退格/左右键/中文输入法都可能异常 —— 提前告知并引导到终端
        print(
            "⚠ 检测到非终端环境（如 VS Code 输出面板）：退格/行编辑不可用，"
            "中文输入可能异常。\n"
            "  建议在 VS Code 集成终端或系统终端运行：python -m hello_llm",
            file=sys.stderr,
        )
    print(f"HelloLLM — 交互模式（Ctrl-C 中止当前回复；exit() 或 Ctrl-D 退出）")
    while True:
        try:
            prompt = input("\n> ")  # prompt：用户输入的一行
        except (EOFError, KeyboardInterrupt):  # Ctrl-D / 输入处 Ctrl-C 直接退出
            print()
            return 0
        prompt = prompt.strip()
        if not prompt:
            continue  # 空输入忽略
        if prompt in _EXIT_WORDS:
            return 0
        conv.add_user(prompt)  # 追加用户消息 → 进入 Agent-Loop
        render_events(conv, args.max_turns, stream=True, hook_manager=hook_manager, permission_gate=gate)
    return 0

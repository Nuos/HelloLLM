"""模块：entrypoints/repl.py —— 交互式 REPL（多轮对话）。

====================================================================
HelloLLM 项目框架结构（S01-basic-loop，论文图1 七组件模型 → 模块映射）

S01-basic-loop/
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
    └── permissions/                      七、权限系统（★ S02 新增，图1 "Permission System"）
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

from ..permissions import PermissionGate  # 权限门（S02）：write 级工具 Y/N 批准
from ..services.api import ModelConfig  # 模型配置（已由 cli 校验过 API key）
from ..query.agent_loop import Conversation  # 会话状态
from .render import render_events  # 事件渲染

if TYPE_CHECKING:
    from argparse import Namespace  # 命令行参数（仅类型标注）


def _ask_permission(tool_name: str, arguments: dict) -> bool:
    """函数：Y/N 批准回调（论文 §5 approval dialog）。

    一、功能作用
        PermissionGate 的 asker：write 级工具执行前向用户确认。
        非 TTY（无交互输入）时 input() 抛 EOFError → 返回 False（拒绝，安全默认）。

    二、参数
        tool_name  （str）工具名
        arguments  （dict）工具参数（展示给用户判断）

    三、返回
        bool：True=批准执行；False=拒绝。
    """
    import json  # 工具参数序列化展示

    print(f"\n⚠ 权限请求：{tool_name}({json.dumps(arguments, ensure_ascii=False)})", file=sys.stderr)
    while True:
        try:
            ans = input("允许执行？[y/N] ").strip().lower()  # ans：用户输入（y/yes 批准）
        except (EOFError, KeyboardInterrupt):
            return False  # 无输入/中断 → 拒绝（deny-first 安全默认）
        if ans in ("y", "yes"):
            return True
        if ans in ("", "n", "no"):
            return False

# 一、模块常量
_EXIT_WORDS = ("exit", "quit", "/exit")  # REPL 退出指令（输入这些词即退出交互模式）


def run_repl(args: "Namespace", cfg: ModelConfig, gate: Optional[PermissionGate] = None) -> int:
    """函数：交互式 REPL 主循环。返回进程退出码。

    一、功能作用（论文 §3.3 "交互式 CLI"）
        多轮、多回合对话界面：读输入 → Conversation.add_user（触发滑动窗口
        裁剪）→ render_events 消费一轮 Agent-Loop → 回到读输入。
        历史全部保留在 Conversation.messages 里，跨轮次生效。

    二、参数
        args  （Namespace）命令行参数（system / max_turns / max_context）
        cfg   （ModelConfig）模型配置（已由 cli 完成 API key 校验）

    三、返回
        int：进程退出码（0 正常退出）。

    运行环境提示：
        非 TTY（VS Code 输出面板 / 管道）下 input() 无行编辑能力 ——
        退格无法清除字符、中文输入法可能异常，启动时检测并引导到终端。
"""
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
            prompt = input("\n❯ ")  # prompt：用户输入的一行
        except (EOFError, KeyboardInterrupt):  # Ctrl-D / 输入处 Ctrl-C 直接退出
            print()
            return 0
        prompt = prompt.strip()
        if not prompt:
            continue  # 空输入忽略
        if prompt in _EXIT_WORDS:
            return 0
        conv.add_user(prompt)  # 追加用户消息 → 进入 Agent-Loop
        render_events(conv, args.max_turns, stream=True, permission_gate=gate)
    return 0

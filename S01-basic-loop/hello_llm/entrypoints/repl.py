"""模块：entrypoints/repl.py —— 交互式 REPL（多轮对话）。

====================================================================
HelloLLM 项目框架结构（v1，论文图1 七组件模型 → 模块映射）

hello_llm/
├── __init__.py                 包入口：版本号 + 项目说明
├── __main__.py                 python -m hello_llm 入口
│
├── entrypoints/                一、交互表面层（图1 "Interfaces"）
│   ├── __init__.py
│   ├── cli.py                  CLI 入口：argparse + 配置校验 + 分派
│   ├── repl.py                 ★★★ 本模块：交互 REPL（多轮对话）★★★
│   ├── headless.py             无头单次（对照 claude -p）
│   └── render.py               Agent-Loop 事件渲染
│
├── query/                      二、核心层（图1 "Agent Loop"）
│   ├── __init__.py
│   └── agent_loop.py           query_loop() 生成器 + Conversation
│
├── config/                     三、配置层（本地配置文件）
│   ├── __init__.py
│   └── loader.py               ~/.hellollm/config.json 定位/解析/合并
│
├── providers/                  四、模型提供商层（Agent-Loop 的 callModel）
│   ├── __init__.py
│   ├── config.py               ModelConfig 模型调用配置（含 API key 校验）
│   ├── types.py                数据结构与异常
│   ├── openai_compatible.py    stream_chat：SSE 流式客户端
│   └── client.py               consume_stream + call_model：流式事件聚合
│
├── tools/                      五、工具层（Agent-Loop 的 execute 路径）
    ├── __init__.py
    ├── registry.py             工具 Schema 池 + execute 分派
    └── file_tools.py           文件工具实现
│
└── logging/                    六、日志提示层（诊断提示/事件通知）
    ├── __init__.py
    └── events.py               事件提示函数（裁剪/预算/额度/工具）
====================================================================
缩略词说明（本模块涉及的术语）：
    1.  REPL —— Read-Eval-Print Loop，读取-求值-打印循环（本模块即交互式对话界面）
    2.  TTY —— Teletype，终端设备；isatty() = 判断 stdin 是否为交互终端
    3.  EOF —— End of File，文件结束（终端 Ctrl-D 触发 EOFError）
"""

from __future__ import annotations  # 延迟求值注解

import sys  # stdin.isatty 检测 / 提示输出
from typing import TYPE_CHECKING  # 类型标注：仅类型检查用

try:
    # readline：让 input() 获得 GNU readline 行编辑能力 ——
    # 退格/左右移动/历史导航（↑↓）。仅 TTY 下生效；非 TTY（管道/输出面板）
    # 自动退化为普通读取，无害。Windows 无此模块，忽略即可。
    import readline  # noqa: F401
except ImportError:  # pragma: no cover
    pass

from ..providers import ModelConfig  # 模型配置（已由 cli 校验过 API key）
from ..query.agent_loop import Conversation  # 会话状态
from .render import render_events  # 事件渲染

if TYPE_CHECKING:
    from argparse import Namespace  # 命令行参数（仅类型标注）

# 一、模块常量
_EXIT_WORDS = ("exit", "quit", "/exit")  # REPL 退出指令（输入这些词即退出交互模式）


def run_repl(args: "Namespace", cfg: ModelConfig) -> int:
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
        render_events(conv, args.max_turns, stream=True)
    return 0

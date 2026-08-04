"""模块：entrypoints/repl.py —— 交互式 REPL（多轮对话）。

====================================================================
HelloLLM 项目框架结构（S05-basic-statepersist，论文图1 七组件模型）

S05-basic-statepersist/
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
        ├── manager.py                    HookManager：PreToolUse/PostToolUse 调度
    │
    └── state/                            八、状态与持久化（★ S05 新增，图1 "State & Persistence"）
        ├── __init__.py                   包入口（聚合导出）
        ├── store.py                      会话 JSONL 存储（对照 src/utils/sessionStorage.ts）
        └── session.py                    会话名生成与元数据
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

from ..services.api import ModelConfig  # 模型配置（已由 cli 校验过 API key）
from ..query.agent_loop import Conversation  # 会话状态
from ..state import load_session, create_session, append_message  # S05：会话恢复/新建/追加
from .render import render_events  # 事件渲染

if TYPE_CHECKING:
    from argparse import Namespace  # 命令行参数（仅类型标注）

# 一、模块常量
_EXIT_WORDS = ("exit", "quit", "/exit")  # REPL 退出指令（输入这些词即退出交互模式）


def run_repl(args: "Namespace", cfg: ModelConfig,
             hook_manager: Optional["HookManager"] = None,
             session_name: Optional[str] = None) -> int:
    """函数：交互式 REPL 主循环。返回进程退出码。

    一、功能作用（论文 §3.3 "交互式 CLI"）
        多轮、多回合对话界面：读输入 → Conversation.add_user（触发滑动窗口
        裁剪）→ render_events 消费一轮 Agent-Loop → 回到读输入。
        历史全部保留在 Conversation.messages 里，跨轮次生效。
        S05 新增：给定 session_name 时启用状态持久化——启动时从转录
        文件恢复历史（--resume），每轮对话后把新消息追加落盘。

    二、输入（input）
        args：命令行参数（system / max_turns / max_context）。
        cfg：模型配置（已由 cli 完成 API key 校验）。
        hook_manager：工具管理中枢（S03）。
        session_name：会话名；给定则持久化（恢复 + 逐轮落盘），
        不传则与 S03 行为一致（不落盘）。

    三、输出（output）
        进程退出码：0 正常退出。

    运行环境提示：
        非 TTY（VS Code 输出面板 / 管道）下 input() 无行编辑能力 ——
        退格无法清除字符、中文输入法可能异常，启动时检测并引导到终端。
    """
    # ── 会话持久化（S05）：启动时恢复历史（--resume 场景）──
    last_saved = 1  # 已落盘消息数（初始 1：跳过 messages[0] 的 system 消息）
    if session_name:
        state = load_session(session_name)
        if state["messages"]:
            # 转录文件里有历史：用 meta 里的系统提示重建会话，再回放消息
            conv = Conversation(state["system_prompt"] or args.system, cfg,
                                max_context_chars=args.max_context)
            conv.messages.extend(state["messages"])
            last_saved = len(conv.messages)
            print(f"ℹ 已恢复会话「{session_name}」：{len(state['messages'])} 条历史消息",
                  file=sys.stderr)
        else:
            # 会话不存在或为空：新建转录文件，从空会话开始
            conv = Conversation(args.system, cfg, max_context_chars=args.max_context)
            create_session(session_name, conv.system_prompt, cfg.model)
            print(f"ℹ 新建会话「{session_name}」（退出后可用 --resume 恢复）",
                  file=sys.stderr)
    else:
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
        render_events(conv, args.max_turns, stream=True, hook_manager=hook_manager)
        # ── 会话持久化（S05）：本轮新产生的消息追加落盘 ──
        #    conv.messages 是持续增长的列表，只把 last_saved 之后的新消息
        #    写入转录文件（避免整会话重写），退出后历史即完整。
        if session_name:
            for msg in conv.messages[last_saved:]:
                append_message(session_name, msg)
            last_saved = len(conv.messages)
    return 0

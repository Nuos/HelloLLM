"""模块：entrypoints/repl.py —— 交互式 REPL（多轮对话）。

====================================================================
HelloLLM 项目框架结构（S06-basic-ee，论文图1 七组件模型 → 模块映射）

S06-basic-ee/
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
    └── utils/                            六、工具函数层（对照 src/utils/：config.ts 等）
        ├── __init__.py
        ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）
        └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）缩略词说明（本模块涉及的术语）：
    1.  REPL —— Read-Eval-Print Loop，读取-求值-打印循环（本模块即交互式对话界面）
    2.  TTY —— Teletype，终端设备；isatty() = 判断 stdin 是否为交互终端
    3.  EOF —— End of File，文件结束（终端 Ctrl-D 触发 EOFError）
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

try:



    import readline
except ImportError:
    pass

from ..services.api import ModelConfig
from ..query.agent_loop import Conversation
from .render import render_events

if TYPE_CHECKING:
    from argparse import Namespace


_EXIT_WORDS = ("exit", "quit", "/exit")


def run_repl(args: "Namespace", cfg: ModelConfig) -> int:
    
    # QQQ69（已答）：run_repl 由 cli.main 在"未给 -p 参数"的分支里调用，且只调用一次——
    # 它是交互模式的唯一入口，内部用 while True 循环持续接收用户输入。
    # QQQ70（已答）：进入 Agent-Loop 的入口链是：
    #   cli.main → run_repl → conv.add_user(prompt)（每轮用户输入）
    #     → render_events() → for event in query_loop(conv, ...)  ← 这里进入 Agent-Loop
    # 设计策略：界面层（Interface）是 Agent-Loop 的外层包装——用户每输入一句话，
    # 就触发一轮 Agent-Loop（模型推理 + 可能的工具调用），结果渲染回界面。
    # 这与论文图1 的 Interface + Agent Loop 分层一致：界面管"与人对话"，
    # 循环管"与模型/工具协作"。

    conv = Conversation(args.system, cfg, max_context_chars=args.max_context)
    if not sys.stdin.isatty():


        print(
            "⚠ 检测到非终端环境（如 VS Code 输出面板）：退格/行编辑不可用，"
            "中文输入可能异常。\n"
            "  建议在 VS Code 集成终端或系统终端运行：python -m hello_llm",
            file=sys.stderr,
        )
    print(f"HelloLLM — 交互模式（Ctrl-C 中止当前回复；exit() 或 Ctrl-D 退出）")
    while True:
        try:
            prompt = input("\n❯ ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        prompt = prompt.strip()
        if not prompt:
            continue
        if prompt in _EXIT_WORDS:
            return 0
        conv.add_user(prompt)
        render_events(conv, args.max_turns, stream=True)
    return 0

"""模块：entrypoints/headless.py —— 无头单次模式（对照 claude -p）。

====================================================================
HelloLLM 项目框架结构（S06-basic-ee，论文图1 七组件模型 → 模块映射）

S06-basic-ee/
└── hello_llm/                            # Python 包
    ├── __init__.py                       包入口：版本号 + 项目说明 + 全局术语表
    ├── __main__.py                       python -m hello_llm 入口
    │
    ├── entrypoints/                      一、交互表面层（图1 "Interfaces"，对照 src/entrypoints/）
    │   ├── cli.py                        CLI 入口（对照 src/entrypoints/cli.tsx）
    │   ├── repl.py                       交互 REPL（多轮对话）
    │   ├── headless.py                   无头单次（对照 claude -p）★★★ 本模块 ★★★
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
    1.  CLI —— Command-Line Interface，命令行接口（无头 CLI 是其中一种交互表面）
    2.  API —— Application Programming Interface，应用程序编程接口
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from ..services.api import ModelConfig
from ..query.agent_loop import Conversation
from .render import render_events

if TYPE_CHECKING:
    from argparse import Namespace


def run_headless(args: "Namespace", cfg: ModelConfig) -> int:
    conv = Conversation(args.system, cfg, max_context_chars=args.max_context)
    prompt = args.prompt
    if prompt == "-":
        prompt = sys.stdin.read().strip()
    conv.add_user(prompt)

    # QQQ62（已答）：是的，你的理解正确。无头模式（headless）= "一次提问、一次回答"：
    # conv.add_user(prompt) 把用户输入送进会话 → render_events() 驱动 Agent-Loop
    # 跑完整轮（可能含模型回复/工具调用），并把最终文本结果返回给 text。
    # 有流式时逐字输出，无流式（--no-stream）时整段打印。对应 claude-code 的
    # claude -p "问题" 用法——不进入多轮交互，答完即退。
    text = render_events(conv, args.max_turns, stream=not args.no_stream)
    if text is None:
        return 1
    if args.no_stream:
        print(text)
    return 0

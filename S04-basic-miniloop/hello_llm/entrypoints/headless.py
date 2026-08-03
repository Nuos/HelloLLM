"""模块：entrypoints/headless.py —— 无头单次模式（对照 claude -p）。

====================================================================
HelloLLM 项目框架结构（S04-basic-miniloop，论文图1 七组件模型）

S04-basic-miniloop/
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
    1.  CLI —— Command-Line Interface，命令行接口（无头 CLI 是其中一种交互表面）
    2.  API —— Application Programming Interface，应用程序编程接口
"""

from __future__ import annotations  # 延迟求值注解

import sys  # stdin 读取 / 退出码
from typing import TYPE_CHECKING, Optional  # 类型标注

from ..hooks import HookManager  # S03：hook_manager 参数的类型标注
from ..permissions import PermissionGate  # S04：gate 参数的类型标注
from ..services.api import ModelConfig  # 模型配置（已由 cli 校验过 API key）
from ..query.agent_loop import Conversation  # 会话状态
from .render import render_events  # 事件渲染

if TYPE_CHECKING:
    from argparse import Namespace  # 命令行参数（仅类型标注）


def run_headless(args: "Namespace", cfg: ModelConfig,
                 hook_manager: Optional["HookManager"] = None,
                 gate: Optional["PermissionGate"] = None) -> int:
    """函数：无头单次执行。

一、功能作用（论文 §3.3 "无头 CLI（claude -p）"）
    单轮提问直接跑完一轮 Agent-Loop：读取提问（-p 或 stdin），
    追加进会话，调用 render_events 消费事件；流式（默认）逐字输出，
    --no-stream 整段输出。适合脚本/管道场景。

二、输入（input）
    args：命令行参数（含 prompt/--no-stream 等）。
    cfg：模型调用配置。
    hook_manager：工具管理中枢（S03）。
    gate：权限门（S04）。

三、输出（output）
    进程退出码：0 成功，1 模型调用失败/被中止。    """
    conv = Conversation(args.system, cfg, max_context_chars=args.max_context)  # conv：会话状态
    prompt = args.prompt  # prompt：提问内容
    if prompt == "-":  # stdin 模式：读全部标准输入作为提问
        prompt = sys.stdin.read().strip()
    conv.add_user(prompt)
    text = render_events(conv, args.max_turns, stream=not args.no_stream,
                         hook_manager=hook_manager, permission_gate=gate)  # text：模型回复全文
    if text is None:
        return 1  # 模型调用失败 / 被中止
    if args.no_stream:
        print(text)  # 整段输出（--no-stream 模式）
    return 0

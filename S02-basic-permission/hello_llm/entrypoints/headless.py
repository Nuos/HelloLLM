"""模块：entrypoints/headless.py —— 无头单次模式（对照 claude -p）。

====================================================================
HelloLLM 项目框架结构（S01-basic-loop，论文图1 七组件模型 → 模块映射）

S01-basic-loop/
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
    └── permissions/                      七、权限系统（★ S02 新增，图1 "Permission System"）
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

from ..permissions import PermissionGate  # 权限门（S02）：类型标注
from ..services.api import ModelConfig  # 模型配置（已由 cli 校验过 API key）
from ..query.agent_loop import Conversation  # 会话状态
from .render import render_events  # 事件渲染

if TYPE_CHECKING:
    from argparse import Namespace  # 命令行参数（仅类型标注）


def run_headless(args: "Namespace", cfg: ModelConfig, gate: Optional["PermissionGate"] = None) -> int:
    """函数：无头单次执行。返回进程退出码（0 成功 / 1 失败）。

    一、功能作用（论文 §3.3 "无头 CLI（claude -p）"）
        单次提问跑完即退，不进入交互循环。只是围绕共享 Agent-Loop 的薄壳：
        建 Conversation → 塞入用户消息 → render_events 消费一轮 → 输出。
        对照 claude-code 源码 src/entrypoints/ 的设计思路。

    二、参数
        args  （Namespace）命令行参数（prompt / system / max_turns 等）
        cfg   （ModelConfig）模型配置（已由 cli 完成 API key 校验）

    三、返回
        int：0 成功；1 模型调用失败/被中止。

    四、stdin 模式
        prompt 为 "-" 时从 stdin 读取（支持管道：echo "hi" | hello-llm -p -）。
"""
    conv = Conversation(args.system, cfg, max_context_chars=args.max_context)  # conv：会话状态
    prompt = args.prompt  # prompt：提问内容
    if prompt == "-":  # stdin 模式：读全部标准输入作为提问
        prompt = sys.stdin.read().strip()
    conv.add_user(prompt)
    text = render_events(conv, args.max_turns, stream=not args.no_stream, permission_gate=gate)  # text：模型回复全文
    if text is None:
        return 1  # 模型调用失败 / 被中止
    if args.no_stream:
        print(text)  # 整段输出（--no-stream 模式）
    return 0

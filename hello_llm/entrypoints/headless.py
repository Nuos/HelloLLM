"""模块：entrypoints/headless.py —— 无头单次模式（对照 claude -p）。

====================================================================
HelloLLM 项目框架结构（v1，论文图1 七组件模型 → 模块映射）

hello_llm/
├── __init__.py                 包入口：版本号 + 项目说明
├── __main__.py                 python -m hello_llm 入口
│
├── entrypoints/                一、交互表面层（图1 "Interfaces"）
│   ├── __init__.py
│   ├── cli.py                  CLI 入口：argparse + 配置校验 + 分派
│   ├── repl.py                 交互 REPL（多轮对话）
│   ├── headless.py             ★★★ 本模块：无头单次（对照 claude -p）★★★
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
    1.  CLI —— Command-Line Interface，命令行接口（无头 CLI 是其中一种交互表面）
    2.  API —— Application Programming Interface，应用程序编程接口
"""

from __future__ import annotations  # 延迟求值注解

import sys  # stdin 读取 / 退出码
from typing import TYPE_CHECKING  # 类型标注：仅类型检查用

from ..providers import ModelConfig  # 模型配置（已由 cli 校验过 API key）
from ..query.agent_loop import Conversation  # 会话状态
from .render import render_events  # 事件渲染

if TYPE_CHECKING:
    from argparse import Namespace  # 命令行参数（仅类型标注）


def run_headless(args: "Namespace", cfg: ModelConfig) -> int:
    """函数：无头单次执行。返回进程退出码（0 成功 / 1 失败）。

    一、功能作用（论文 §3.3 "无头 CLI（claude -p）"）
        单次提问跑完即退，不进入交互循环。只是围绕共享 Agent-Loop 的薄壳：
        建 Conversation → 塞入用户消息 → render_events 消费一轮 → 输出。
        对照 clawcodex 的 src/entrypoints/headless.py 设计思路。

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
    text = render_events(conv, args.max_turns, stream=not args.no_stream)  # text：模型回复全文
    if text is None:
        return 1  # 模型调用失败 / 被中止
    if args.no_stream:
        print(text)  # 整段输出（--no-stream 模式）
    return 0

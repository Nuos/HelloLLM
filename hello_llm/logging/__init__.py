"""模块：logging —— 日志提示层（包）。

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
│   ├── __init__.py
│   ├── registry.py             工具 Schema 池 + execute 分派
│   └── file_tools.py           文件工具实现
│
└── logging/                    六、日志提示层（诊断提示/事件通知）
    ├── __init__.py             ★★★ 本模块：包入口（聚合导出）★★★
    └── events.py               事件提示函数（裁剪/预算/额度/工具/轮次）
====================================================================

缩略词说明（本模块涉及的术语）：
    1. CLI —— Command-Line Interface，命令行接口
    2. Agent-Loop —— 智能体循环（模型调用/工具分派/结果收集的迭代周期）
"""

from .events import (
    notice,  # 信息级提示（青色 ℹ）
    warn,  # 警告级提示（黄色 ⚠）
    error,  # 错误级提示（红色 ✖）
    tool,  # 工具级提示（紫色 ⚙）
    loop_turn,  # Agent-Loop 轮次提示
    tool_triggered,  # 工具触发提示
    tool_result_summary,  # 工具执行结果提示
    context_trimmed,  # 上下文裁剪提示（超预算）
    max_turns_reached,  # 最大轮数提示（循环限制）
    rate_limited,  # 额度/频率限制提示（HTTP 429）
)

__all__ = [
    "notice",
    "warn",
    "error",
    "tool",
    "loop_turn",
    "tool_triggered",
    "tool_result_summary",
    "context_trimmed",
    "max_turns_reached",
    "rate_limited",
]

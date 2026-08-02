"""模块：entrypoints/render.py —— Agent-Loop 事件渲染。

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
│   └── render.py               ★★★ 本模块：Agent-Loop 事件渲染 ★★★
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
    1.  Agent-Loop —— 智能体循环（模型调用/工具分派/结果收集的迭代周期）
    2.  API —— Application Programming Interface，应用程序编程接口
    3.  ANSI —— 转义序列：终端颜色/样式控制码（如 \\033[2m 暗淡字体）
    4.  HTTP —— HyperText Transfer Protocol，超文本传输协议
"""

from __future__ import annotations  # 延迟求值注解

import sys  # stderr 输出（思维链/错误提示）
from typing import Optional  # 类型标注

from ..logging import warn as log_warn  # 日志提示层：警告级提示（空回复兜底）
from ..providers import stream_chat, ModelError  # 模型层：流式通道 / 模型调用异常
from ..query.agent_loop import Conversation, query_loop  # 会话状态 + Agent-Loop


def render_events(
    conv: Conversation,  # conv：会话状态（含 messages，被 query_loop 读写）
    max_turns: int,  # 循环轮数上限
    stream: bool,  # True=逐字输出；False=只收集文本（无头 --no-stream 用）
) -> Optional[str]:
    """函数：消费 query_loop 事件并按类型渲染到终端；返回收集到的文本。

    一、功能作用
        repl.py / headless.py 共用的输出通道：把 Agent-Loop 的事件流
        渲染成用户可见的终端输出（对应论文 §4.1 的事件流设计）。

    二、参数
        conv       （Conversation）会话状态
        max_turns  （int）Agent-Loop 循环轮数上限
        stream     （bool）True 逐字输出；False 只收集文本

    三、返回
        str|None：收集到的文本；失败（模型错误/中止）返回 None。

    四、事件渲染规则
        1. text_delta        → 逐字打印到 stdout（流式核心体验）
        2. reasoning_delta   → 灰色 ANSI 打到 stderr（思维链不污染答案）
        3. tool_use          → ⚙ 工具名(参数) 打印到 stdout
        4. tool_result       → 静默（避免刷屏，结果已进历史供模型看）
        5. turn_end          → 换行收尾（空回复时给出显式提示）

    五、异常处理（本层是用户可见的最后防线）
        1. KeyboardInterrupt  流式中 Ctrl-C = 中止本轮，不退出程序
        2. ModelError         模型调用失败（HTTP/网络/超时），中文提示
        3. Exception          兜底：任何异常显式报告，杜绝"静默没回复"
"""
    chunks: list[str] = []  # chunks：文本累积（stream=False 时最终一次性打印）
    try:
        if stream:
            # 请求中的可见反馈：推理模型先输出长思维链（stderr 灰色），
            # 正文迟迟未到会显得"没回复"，这里先给一个进行中标记
            print("⟳ ", end="", flush=True, file=sys.stderr)
        for event in query_loop(  # event：Agent-Loop 产出的事件
            conv,
            max_turns=max_turns,
            stream_model=stream_chat,  # 真·逐字流式（含思维链）
        ):
            t = event["type"]  # t：事件类型（分派依据）
            if t == "text_delta":
                chunks.append(event["text"])
                if stream:
                    print(event["text"], end="", flush=True)  # flush：立即上屏
            elif t == "reasoning_delta":
                if stream:
                    # \033[2m = 暗淡字体，\033[0m = 重置；走 stderr 不污染 stdout
                    print(f"\033[2m{event['text']}\033[0m", end="", flush=True, file=sys.stderr)
            elif t == "tool_use":
                # 工具触发提示由 logging 层输出（紫色 ⚙，stderr，任何模式可见）
                pass
            elif t == "turn_end":
                if stream:
                    if not event["text"]:
                        # 日志提示：模型未返回内容（推理模型偶发空回复）——
                        # 显式通知，避免"输入后没反应"的错觉
                        log_warn("模型未返回内容（空回复）")
                    else:
                        print()  # 换行收尾
    except KeyboardInterrupt:
        # 流式中 Ctrl-C：中止本轮（论文 §4.5 显式中止），回到输入提示而非退出
        print("\n[已中止本轮]", file=sys.stderr)
        return None
    except ModelError as e:  # e：模型调用异常
        print(f"\n[模型错误] {e}", file=sys.stderr)
        return None  # 返回 None 表示失败，调用方据此返回退出码 1
    except Exception as e:  # e：未知异常
        print(f"\n[内部错误] {type(e).__name__}: {e}", file=sys.stderr)
        return None
    return "".join(chunks)

"""模块：utils/logging.py —— 业务事件提示函数（诊断日志层）。

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
        └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）★★★ 本模块 ★★★
    1. stderr —— 标准错误输出（诊断信息通道，不污染 stdout 答案）
    2. ANSI —— 转义序列：终端颜色控制码（如 \\033[31m 红色）
    3. JSON —— JavaScript Object Notation，轻量数据交换格式
"""

from __future__ import annotations  # 延迟求值注解

import json  # 工具参数序列化（提示里展示调用参数）
import sys  # stderr 输出通道

# 一、输出前缀（stderr + ANSI 颜色，明显区分级别）
_PFX_INFO = "\033[36mℹ\033[0m "  # 前缀：青色 ℹ —— 信息（模块/功能触发）
_PFX_WARN = "\033[33m⚠\033[0m "  # 前缀：黄色 ⚠ —— 警告（限制/裁剪/超预算）
_PFX_ERR = "\033[31m✖\033[0m "  # 前缀：红色 ✖ —— 错误（异常/失败）
_PFX_TOOL = "\033[35m⚙\033[0m "  # 前缀：紫色 ⚙ —— 工具触发/结果

# 二、基本输出函数（三个级别 + 工具级别）


def notice(msg: str) -> None:
    """函数：信息级提示（模块/功能被触发的日志）。

    一、功能作用
        输出青色 ℹ 前缀的信息到 stderr，flush 保证立即上屏。

    二、参数
        msg  （str）提示内容
    """
    print(f"{_PFX_INFO}{msg}", file=sys.stderr, flush=True)


def warn(msg: str) -> None:
    """函数：警告级提示（遇到限制/超出预算等）。

    一、功能作用
        输出黄色 ⚠ 前缀的警告到 stderr —— 用户必须知晓的限制事件。
    """
    print(f"{_PFX_WARN}{msg}", file=sys.stderr, flush=True)


def error(msg: str) -> None:
    """函数：错误级提示（异常/失败）。

    一、功能作用
        输出红色 ✖ 前缀的错误到 stderr —— 异常或失败事件。
    """
    print(f"{_PFX_ERR}{msg}", file=sys.stderr, flush=True)


def tool(msg: str) -> None:
    """函数：工具级提示（工具触发/执行结果）。

    一、功能作用
        输出紫色 ⚙ 前缀的工具事件到 stderr —— 与 render.py 的对话流
        分离，任何运行模式（含 --no-stream）都能看到工具调用。
    """
    print(f"{_PFX_TOOL}{msg}", file=sys.stderr, flush=True)


# 三、业务事件提示函数（按场景分类，带统计数据）


def loop_turn(turn: int, total: int, model: str) -> None:
    """函数：Agent-Loop 轮次提示（模块触发日志）。

    一、功能作用
        每轮模型调用前输出"第 N/M 轮，调用模型 X"——
        让用户看到循环在推进（尤其推理模型思考久时）。

    二、参数
        turn   （int）当前轮次（从 1 起）
        total  （int）最大轮数
        model  （str）模型名
    """
    notice(f"Agent-Loop 第 {turn}/{total} 轮：调用模型 {model}")


def tool_triggered(name: str, arguments: dict) -> None:
    """函数：工具触发提示。

    一、功能作用
        模型请求调用工具时输出工具名与参数 —— 对应"调用了读文件工具"
        这类可观测事件。
    """
    tool(f"工具触发：{name}({json.dumps(arguments, ensure_ascii=False)})")


def tool_result_summary(name: str, result: str) -> None:
    """函数：工具执行结果提示（成功摘要 / 失败明细）。

    一、功能作用
        工具执行后输出结果状态：
            成功 → 返回字符数摘要；
            失败（结果以"错误"开头）→ 红色提示 + 错误详情。
    """
    if result.startswith("错误"):
        error(f"工具失败：{name} → {result[:200]}")
    else:
        tool(f"工具完成：{name} 返回 {len(result)} 字符")


def context_trimmed(removed: int, current: int, budget: int) -> None:
    """函数：上下文裁剪提示（超预算事件）。

    一、功能作用
        滑动窗口触发裁剪时输出警告：当前字符数、预算、裁剪条数 ——
        用户应知晓"历史被截断"（影响模型记忆）。

    二、参数
        removed  （int）裁剪的消息条数
        current  （int）裁剪后的当前字符数
        budget   （int）预算上限（字符）
    """
    warn(f"上下文超预算：{current} 字符 > 预算 {budget} 字符，已裁剪 {removed} 条最老消息")


def max_turns_reached(turn: int) -> None:
    """函数：最大轮数提示（循环限制事件）。

    一、功能作用
        Agent-Loop 达到 max_turns 上限停止时输出警告 —— 通知用户
        "模型持续调用工具，循环被限制终止"。
    """
    warn(f"达到最大轮数限制（{turn} 轮），循环停止")


def rate_limited(status: int, detail: str) -> None:
    """函数：额度/频率限制提示（HTTP 429 事件）。

    一、功能作用
        服务端返回 429（请求过多或额度用尽）时输出醒目错误提示，
        并给出处理建议（稍后重试 / 检查账户额度）。

    二、参数
        status  （int）HTTP 状态码（429）
        detail  （str）服务端错误详情
    """
    error(f"额度/频率限制：HTTP {status}（{detail}）—— 请稍后重试，或检查 API 账户额度")

"""模块：utils/logging.py —— 业务事件提示函数（诊断日志层）。

====================================================================
HelloLLM 项目框架结构（S04-basic-miniloop，论文图1 七组件模型）

S04-basic-miniloop/
└── hello_llm/                            # Python 包
    ├── __init__.py                       包入口：版本号 + 项目说明 + 全局术语表
    ├── __main__.py                       python -m hello_llm 入口
    │
    ├── entrypoints/                      一、交互表面层（图1 "Interfaces"）
    │   ├── cli.py                        CLI 入口（对照 src/entrypoints/cli.tsx）
    │   ├── repl.py                       交互 REPL（多轮对话）
    │   ├── headless.py                   无头单次（对照 claude -p）
    │   └── render.py                     Agent-Loop 事件渲染
    │
    ├── query/                            二、核心层（图1 "Agent Loop"）
    │   ├── __init__.py
    │   └── agent_loop.py                 query_loop() 生成器 + Conversation
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
    ├── utils/                            六、工具函数层（对照 src/utils/）
    │   ├── __init__.py
    │   ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）
    │   └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）★★★ 本模块 ★★★
    │
    ├── hooks/                            七、hook 机制（对照 src/utils/hooks.ts，S03）
    │   ├── __init__.py                   包入口（聚合导出）
    │   ├── config.py                     加载 hook 规则（项目内 + 用户级合并）
    │   ├── runner.py                     subprocess 执行 hook（stdin/stdout JSON）
    │   └── manager.py                    HookManager：PreToolUse/PostToolUse 调度
    │
    └── permissions/                      八、权限系统（★ S04 新增，图1 "Permission System"）
        ├── __init__.py                   包入口（聚合导出）
        ├── policies.py                   工具→策略等级映射（deny-first）
        ├── modes.py                      权限模式（interactive / auto-accept / read-only）
        └── gate.py                       PermissionGate：execute 前检查（allow/ask/deny）
====================================================================

缩略词说明（本模块涉及的术语）：
    1.  stderr —— 标准错误输出：诊断信息通道，不污染 stdout 的答案
    2.  ANSI —— 转义序列：终端颜色控制码（如 \033[31m 红色）
    3.  JSON —— JavaScript Object Notation，轻量数据交换格式
    4.  HTTP —— HyperText Transfer Protocol，超文本传输协议
    5.  SSE —— Server-Sent Events，服务端推送事件流（模型流式输出）

本模块职责（业务含义）：
    全项目唯一的日志出口：所有"业务事件提示"都通过这里的函数输出到
    stderr，用颜色前缀区分级别，保证任何运行模式（含 --no-stream）
    都能看到工具调用与限制事件，且不污染 stdout 的模型答案。
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
    """函数：输出普通通知事件。

    一、功能作用
        日志层的统一出口：把模型回复、问候语等常规信息打印到 stderr，
        flush 保证立即上屏。调用方只传消息文本，颜色前缀由本函数统一处理。

    二、输入（input）
        msg：要展示给用户的消息文本。

    三、输出（output）
        无返回值。消息直接打印到标准错误流。
    """
    print(f"{_PFX_INFO}{msg}", file=sys.stderr, flush=True)


def warn(msg: str) -> None:
    """函数：输出警告事件。

    一、功能作用
        日志层的统一出口：把权限拒绝、网络重试、上下文裁剪等提醒类
        信息打印到 stderr，用黄色前缀区分级别——用户必须知晓的限制事件。

    二、输入（input）
        msg：要展示的警告文本。

    三、输出（output）
        无返回值。警告直接打印到标准错误流。
    """
    print(f"{_PFX_WARN}{msg}", file=sys.stderr, flush=True)


def error(msg: str) -> None:
    """函数：输出错误事件。

    一、功能作用
        日志层的统一出口：把模型调用失败、工具失败等错误信息打印到
        stderr，用红色前缀区分级别——异常或失败事件。

    二、输入（input）
        msg：要展示的错误文本。

    三、输出（output）
        无返回值。错误直接打印到标准错误流。
    """
    print(f"{_PFX_ERR}{msg}", file=sys.stderr, flush=True)


def tool(msg: str) -> None:
    """函数：输出工具调试事件。

    一、功能作用
        日志层的统一出口：把工具触发、工具结果等事件打印到 stderr，
        用紫色前缀区分级别；与 render.py 的对话流分离，任何运行模式
        （含 --no-stream）都能看到工具调用。

    二、输入（input）
        msg：工具相关的事件文本。

    三、输出（output）
        无返回值。事件直接打印到标准错误流。
    """
    print(f"{_PFX_TOOL}{msg}", file=sys.stderr, flush=True)


# 三、业务事件提示函数（按场景分类）


def loop_turn(turn: int, total: int, model: str) -> None:
    """函数：Agent-Loop 轮次提示。

    一、功能作用
        每轮模型调用前输出"第 N/M 轮，调用模型 X"，让用户看到循环
        在推进——尤其推理模型思考很久时，这个提示能消除"卡住"的错觉。

    二、输入（input）
        turn：当前轮次序号（从 1 起）。
        total：本轮对话的总轮数上限。
        model：本次调用使用的模型名。

    三、输出（output）
        无返回值。轮次提示经 notice 打印到标准错误流。
    """
    notice(f"Agent-Loop 第 {turn}/{total} 轮：调用模型 {model}")


def tool_triggered(name: str, arguments: dict) -> None:
    """函数：工具触发提示。

    一、功能作用
        模型请求调用工具时输出工具名与参数——对应"调用了读文件工具"
        这类可观测事件，让用户在看日志时知道模型在调什么、带了什么参数。

    二、输入（input）
        name：工具名（如 read_file）。
        arguments：本次工具调用的参数，序列化后展示。

    三、输出（output）
        无返回值。触发提示经 tool 打印到标准错误流。
    """
    tool(f"工具触发：{name}({json.dumps(arguments, ensure_ascii=False)})")


def tool_result_summary(name: str, result: str) -> None:
    """函数：工具结果摘要提示。

    一、功能作用
        工具执行后输出结果状态：成功 → 返回字符数摘要；失败（结果以
        "错误"开头）→ 红色提示 + 错误详情（截断前 200 字符防刷屏）。

    二、输入（input）
        name：工具名。
        result：工具执行返回的结果文本。

    三、输出（output）
        无返回值。按成功/失败分别走 tool 或 error 打印到标准错误流。
    """
    if result.startswith("错误"):
        error(f"工具失败：{name} → {result[:200]}")
    else:
        tool(f"工具完成：{name} 返回 {len(result)} 字符")


def context_trimmed(removed: int, current: int, budget: int) -> None:
    """函数：上下文裁剪提示。

    一、功能作用
        滑动窗口触发裁剪时输出警告：当前字符数、预算、裁剪条数——
        用户应知晓"历史被截断"（影响模型记忆）。

    二、输入（input）
        removed：本次被裁剪掉的消息条数。
        current：裁剪后当前的上下文字符数。
        budget：上下文长度预算上限（--max-context）。

    三、输出（output）
        无返回值。裁剪提示经 warn 打印到标准错误流。
    """
    warn(f"上下文超预算：{current} 字符 > 预算 {budget} 字符，已裁剪 {removed} 条最老消息")


def max_turns_reached(turn: int) -> None:
    """函数：最大轮数提示。

    一、功能作用
        Agent-Loop 达到 max_turns 上限停止时输出警告——通知用户
        "模型持续调用工具，循环被限制终止"，可用 --max-turns 放宽。

    二、输入（input）
        turn：达到上限时的轮次值。

    三、输出（output）
        无返回值。停止提示经 warn 打印到标准错误流。
    """
    warn(f"达到最大轮数限制（{turn} 轮），循环停止")


def rate_limited(status: int, detail: str) -> None:
    """函数：额度/频率限制提示。

    一、功能作用
        服务端返回 429（请求过多或额度用尽）时输出醒目错误提示，
        并给出处理建议（稍后重试 / 检查 API 账户额度）。

    二、输入（input）
        status：限流响应状态码（HTTP 429）。
        detail：服务端返回的错误详情。

    三、输出（output）
        无返回值。限流提示经 error 打印到标准错误流。
    """
    error(f"额度/频率限制：HTTP {status}（{detail}）—— 请稍后重试，或检查 API 账户额度")

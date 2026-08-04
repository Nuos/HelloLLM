"""模块：utils/logging.py —— 业务事件提示函数（诊断日志层）。

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

from __future__ import annotations

import json
import sys


_PFX_INFO = "\033[36mℹ\033[0m "
_PFX_WARN = "\033[33m⚠\033[0m "
_PFX_ERR = "\033[31m✖\033[0m "
_PFX_TOOL = "\033[35m⚙\033[0m "




def notice(msg: str) -> None:
    print(f"{_PFX_INFO}{msg}", file=sys.stderr, flush=True)


def warn(msg: str) -> None:
    print(f"{_PFX_WARN}{msg}", file=sys.stderr, flush=True)


def error(msg: str) -> None:
    print(f"{_PFX_ERR}{msg}", file=sys.stderr, flush=True)


def tool(msg: str) -> None:
    print(f"{_PFX_TOOL}{msg}", file=sys.stderr, flush=True)





def loop_turn(turn: int, total: int, model: str) -> None:
    notice(f"Agent-Loop 第 {turn}/{total} 轮：调用模型 {model}")


def tool_triggered(name: str, arguments: dict) -> None:
    tool(f"工具触发：{name}({json.dumps(arguments, ensure_ascii=False)})")


def tool_result_summary(name: str, result: str) -> None:
    if result.startswith("错误"):
        error(f"工具失败：{name} → {result[:200]}")
    else:
        tool(f"工具完成：{name} 返回 {len(result)} 字符")


def context_trimmed(removed: int, current: int, budget: int) -> None:
    warn(f"上下文超预算：{current} 字符 > 预算 {budget} 字符，已裁剪 {removed} 条最老消息")


def max_turns_reached(turn: int) -> None:
    warn(f"达到最大轮数限制（{turn} 轮），循环停止")


def rate_limited(status: int, detail: str) -> None:
    error(f"额度/频率限制：HTTP {status}（{detail}）—— 请稍后重试，或检查 API 账户额度")

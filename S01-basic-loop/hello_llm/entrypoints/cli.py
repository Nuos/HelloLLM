"""模块：entrypoints/cli.py —— CLI 入口层（薄壳）。

====================================================================
HelloLLM 项目框架结构（v1，论文图1 七组件模型 → 模块映射）

hello_llm/
├── __init__.py                 包入口：版本号 + 项目说明
├── __main__.py                 python -m hello_llm 入口
│
├── entrypoints/                一、交互表面层（图1 "Interfaces"）
│   ├── __init__.py
│   ├── cli.py                  ★★★ 本模块：CLI 入口（argparse+校验+分派）★★★
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
    ├── __init__.py
    ├── registry.py             工具 Schema 池 + execute 分派
    └── file_tools.py           文件工具实现
│
└── logging/                    六、日志提示层（诊断提示/事件通知）
    ├── __init__.py
    └── events.py               事件提示函数（裁剪/预算/额度/工具）
====================================================================
缩略词说明（本模块涉及的术语）：
    1.  CLI —— Command-Line Interface，命令行接口（本模块即 CLI 入口）
    2.  API —— Application Programming Interface，应用程序编程接口
    3.  REPL —— Read-Eval-Print Loop，读取-求值-打印循环（由 repl.py 提供）
    4.  F5 —— VS Code 调试启动快捷键（指代调试入口；不加载 shell 配置）
"""

from __future__ import annotations  # 延迟求值注解

import os  # 直接以脚本运行时定位项目根目录（见下方双模式兼容）
import argparse  # 命令行参数解析（-p / --model / --max-turns / ...）
import sys  # 退出码、stderr 输出
from typing import Optional  # 类型标注

# ── 双运行模式兼容（VS Code "运行当前文件" / python entrypoints/cli.py）──
# 包模式（python -m hello_llm）下 __package__ 非空，走相对导入；
# 直接以脚本运行（VS Code Run 按钮）时 __package__ 为空，Python 把 cli.py
# 所在目录（entrypoints/）当作 sys.path[0]，没有包上下文，相对导入必炸。
# 修复：此时把项目根目录（上三级）插入 sys.path 最前，改用绝对导入。
if __package__ in (None, ""):
    _PROJECT_ROOT = os.path.dirname(  # 项目根：HelloLLM/
        os.path.dirname(  # hello_llm/
            os.path.dirname(os.path.abspath(__file__))  # entrypoints/
        )
    )
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)  # 让 `import hello_llm` 可解析
    from hello_llm import __version__  # 版本号（--version 用）
    from hello_llm.config.loader import build_model_config, find_config_path  # 配置文件加载
    from hello_llm.entrypoints.headless import run_headless  # 无头模式
    from hello_llm.entrypoints.repl import run_repl  # 交互 REPL
    from hello_llm.providers import ConfigError, ModelConfig, DEFAULT_SYSTEM  # 配置/异常
else:
    from .. import __version__  # 版本号（--version 用）
    from ..config.loader import build_model_config, find_config_path  # 配置文件加载
    from ..providers import ConfigError, ModelConfig, DEFAULT_SYSTEM  # 配置与配置错误
    from .headless import run_headless  # 无头模式（单次提问，对照 claude -p）
    from .repl import run_repl  # 交互 REPL（多轮对话）


def build_parser() -> argparse.ArgumentParser:
    """函数：构建命令行参数解析器（CLI 入口层的参数面）。

    一、功能作用
        定义全部命令行参数：交互模式/无头模式共用一套参数。
    """
    p = argparse.ArgumentParser(  # p：参数解析器对象
        prog="hello-llm",
        description="HelloLLM — 最简可交互 AI 编码 Agent（CLI 入口层 + Agent-Loop）",
    )
    p.add_argument(
        "-p", "--print", dest="prompt",
        help="无头模式：单次提问后输出并退出（对照 claude -p）；传 - 则从 stdin 读取提问",
    )
    p.add_argument(
        "--config", default="",
        help="本地配置文件路径（默认 ~/.hellollm/config.json；JSON 格式，含 api_key 等）",
    )
    p.add_argument("--model", default="", help="模型名（默认 deepseek-v4-flash，可被配置文件覆盖）")
    p.add_argument("--api-base", default="", help="OpenAI 兼容 API 地址（默认 https://api.deepseek.com）")
    p.add_argument("--api-key", default="", help="API Key（推荐写入配置文件；此参数仅临时覆盖）")
    p.add_argument(
        "--timeout", type=float, default=None,
        help="请求超时秒数（默认 120；推理模型思考久，超时可调大）",
    )
    p.add_argument("--system", default=DEFAULT_SYSTEM, help="系统提示词")
    p.add_argument("--max-turns", type=int, default=10, help="Agent 循环最大轮数（默认 10）")
    p.add_argument(
        "--max-context", type=int, default=30_000,
        help="多轮对话滑动窗口上限（字符，默认 30000）：超出裁剪最老消息，防上下文超限",
    )
    p.add_argument("--no-stream", action="store_true", help="无头模式整段输出，不逐字流式")
    p.add_argument("--version", action="version", version=f"HelloLLM {__version__}")
    return p


def _print_config_source(args) -> None:
    """函数：启动时输出 API key 配置来源（不泄露 key 本身）。

    一、功能作用（配置提示要求）
        提前告知用户 key 从哪来：本地配置文件（推荐）或命令行参数。
        缺 key 的情况由 require_api_key 的 fail-fast 指引覆盖。
    """
    if args.api_key:
        print("✓ API Key 来源：命令行参数（--api-key）", file=sys.stderr)
        return
    path = find_config_path(args.config)  # path：定位到的配置文件
    if path is not None:
        print(f"✓ API Key 来源：本地配置文件 {path}", file=sys.stderr)


def _make_config(args) -> ModelConfig:
    """函数：合并命令行参数与本地配置文件，构建 ModelConfig。

    一、功能作用
        优先级：命令行显式参数 > 配置文件（~/.hellollm/config.json 或
        --config）> 内置默认值。API key 的唯一本地来源是配置文件。
    """
    return build_model_config(args)


def main(argv: Optional[list[str]] = None) -> int:
    """函数：程序入口 —— 配置校验（fail-fast）→ 分派到无头/REPL。

    一、功能作用（论文 §3.4）
        "共享代码路径是循环函数，而不是 Engine 类" —— 本入口只做三件事：
            1. 解析命令行参数（argparse）
            2. fail-fast 配置校验（API key 缺失 → 明确指引，不发请求）
            3. 分派到交互 REPL（repl.py）或无头模式（headless.py）

    二、参数
        argv  （list|None）命令行参数；None 表示取 sys.argv

    三、返回
        int：进程退出码（0 成功 / 1 配置错误或模型调用失败）。
    """
    args = build_parser().parse_args(argv)

    # ── fail-fast 配置校验：API key 缺失时给明确指引，不发网络请求 ──
    try:
        cfg = _make_config(args)  # cfg：合并后的最终配置
        cfg.require_api_key()
    except ConfigError as e:  # e：配置错误（含完整配置指引）
        print(f"[配置错误] {e}", file=sys.stderr)
        return 1

    # 启动提示：告知 API key 配置来源（不泄露 key 本身）
    _print_config_source(args)

    if args.prompt is not None:
        return run_headless(args, cfg)  # 无头单次
    return run_repl(args, cfg)  # 交互 REPL


if __name__ == "__main__":
    sys.exit(main())

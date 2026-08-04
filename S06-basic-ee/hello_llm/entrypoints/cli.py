"""模块：entrypoints/cli.py —— CLI 入口层（薄壳）。

====================================================================
HelloLLM 项目框架结构（S06-basic-ee，论文图1 七组件模型 → 模块映射）

S06-basic-ee/
└── hello_llm/                            # Python 包
    ├── __init__.py                       包入口：版本号 + 项目说明 + 全局术语表
    ├── __main__.py                       python -m hello_llm 入口
    │
    ├── entrypoints/                      一、交互表面层（图1 "Interfaces"，对照 src/entrypoints/）
    │   ├── cli.py                        CLI 入口（对照 src/entrypoints/cli.tsx）★★★ 本模块 ★★★
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
        └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）缩略词说明（本模块涉及的术语）：
    1.  CLI —— Command-Line Interface，命令行接口（本模块即 CLI 入口）
    2.  API —— Application Programming Interface，应用程序编程接口
    3.  REPL —— Read-Eval-Print Loop，读取-求值-打印循环（由 repl.py 提供）
    4.  F5 —— VS Code 调试启动快捷键（指代调试入口；不加载 shell 配置）
"""

from __future__ import annotations

import os
import argparse
import sys
from typing import Optional






if __package__ in (None, ""):
    _PROJECT_ROOT = os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
    )
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)
    from hello_llm import __version__
    from hello_llm.utils.config import build_model_config, find_config_path
    from hello_llm.entrypoints.headless import run_headless
    from hello_llm.entrypoints.repl import run_repl
    from hello_llm.services.api import ConfigError, ModelConfig, DEFAULT_SYSTEM
else:
    from .. import __version__
    from ..utils.config import build_model_config, find_config_path
    from ..services.api import ConfigError, ModelConfig, DEFAULT_SYSTEM
    from .headless import run_headless
    from .repl import run_repl


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
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
    if args.api_key:
        print("✓ API Key 来源：命令行参数（--api-key）", file=sys.stderr)
        return
    path = find_config_path(args.config)
    if path is not None:
        print(f"✓ API Key 来源：本地配置文件 {path}", file=sys.stderr)


def _make_config(args) -> ModelConfig:
    return build_model_config(args)


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)


    try:
        cfg = _make_config(args)
        cfg.require_api_key()
    except ConfigError as e:
        print(f"[配置错误] {e}", file=sys.stderr)
        return 1


    _print_config_source(args)

    # QQQ135（已答）：是的，理解正确。这里就是"模式分派"：
    # - args.prompt 非空（用户给了 -p "问题"）→ run_headless：无头单次问答，
    #   答完即退，没有多轮交互；
    # - args.prompt 为空（用户没给 -p）→ run_repl：进入交互式 REPL，多轮对话。
    # 这是 claude-code 的 -p/--print 无头模式设计：给参数 = 脚本式单次，
    # 不给 = 进入交互会话。
    if args.prompt is not None:
        return run_headless(args, cfg)
    return run_repl(args, cfg)


if __name__ == "__main__":
    sys.exit(main())

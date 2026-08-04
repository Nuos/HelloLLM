"""模块：entrypoints/cli.py —— CLI 入口层（薄壳）。

====================================================================
HelloLLM 项目框架结构（S05-basic-statepersist，论文图1 七组件模型）

S05-basic-statepersist/
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
    ├── utils/                            六、工具函数层（对照 src/utils/：config.ts 等）
    │   ├── __init__.py
    │   ├── config.py                     本地配置文件加载（对照 src/utils/config.ts）
    │   └── logging.py                    日志提示层（对照 src/utils/ 的日志模块）
    │
    ├── hooks/                            七、hook 机制（★ S03 新增，对照 src/utils/hooks.ts）
        ├── __init__.py                   包入口（聚合导出）
        ├── config.py                     加载 hook 规则（项目内 + 用户级合并）
        ├── runner.py                     subprocess 执行 hook（stdin/stdout JSON）
        ├── manager.py                    HookManager：PreToolUse/PostToolUse 调度
    │
    └── state/                            八、状态与持久化（★ S05 新增，图1 "State & Persistence"）
        ├── __init__.py                   包入口（聚合导出）
        ├── store.py                      会话 JSONL 存储（对照 src/utils/sessionStorage.ts）
        └── session.py                    会话名生成与元数据
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
    from hello_llm.utils.config import build_model_config, find_config_path  # 配置文件加载
    from hello_llm.entrypoints.headless import run_headless  # 无头模式
    from hello_llm.entrypoints.repl import run_repl  # 交互 REPL
    from hello_llm.hooks import HookManager  # S03：构建工具管理中枢（自动加载 hook 规则）
    from hello_llm.state import list_sessions, validate_name, generate_name, get_transcript_path  # S05：会话列表/名校验/自动命名/路径
    from hello_llm.services.api import ConfigError, ModelConfig, DEFAULT_SYSTEM  # 配置/异常
else:
    from .. import __version__  # 版本号（--version 用）
    from ..utils.config import build_model_config, find_config_path  # 配置文件加载
    from ..services.api import ConfigError, ModelConfig, DEFAULT_SYSTEM  # 配置与配置错误
    from ..hooks import HookManager  # S03：构建工具管理中枢（自动加载 hook 规则）
    from ..state import list_sessions, validate_name, generate_name, get_transcript_path  # S05：会话列表/名校验/自动命名/路径
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
    # ── S05 状态持久化参数（State & Persistence）──
    p.add_argument(
        "--resume", metavar="NAME",
        help="恢复指定会话继续对话（转录文件在 ~/.hellollm/sessions/ 下）",
    )
    p.add_argument(
        "--save-as", nargs="?", const="__auto__", metavar="NAME",
        help="保存本轮对话到指定会话；不带名字则自动生成时间戳会话名",
    )
    p.add_argument(
        "--list-sessions", action="store_true",
        help="列出所有历史会话（名字/消息数/大小/时间）后退出",
    )
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


def _session_exists(name: str) -> bool:
    """函数：判断会话是否已存在。

    一、功能作用
        --save-as 前先查重：同名转录文件已存在时返回真，
        调用方据此拒绝新建（提示改用 --resume 恢复），防止覆盖旧会话。

    二、输入（input）
        name：会话名。

    三、输出（output）
        转录文件已存在返回真，否则返回假。
    """
    return get_transcript_path(name).exists()


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

    # ── hook 机制（S03）：构建工具管理中枢 ──
    #    自动读取项目内 hooks.json 与用户级 ~/.hellollm/hooks.json 的规则；
    #    一个规则都没有时它等于不存在，工具照常执行，行为与 S01 完全一致。
    hook_manager = HookManager()

    # ── 状态持久化（S05）：会话名解析与列表 ──
    if args.list_sessions:
        # --list-sessions：列出历史会话后直接退出，不进入对话
        sessions = list_sessions()
        if not sessions:
            print("（暂无历史会话）")
            return 0
        print(f"{'会话名':<24} {'消息数':>6} {'大小':>8}  最后修改")
        for s in sessions:
            print(f"{s['name']:<24} {s['messages']:>6} {s['size']:>7}B  {s['updated']}")
        return 0
    if args.resume and args.save_as is not None:
        print("[参数错误] --resume 与 --save-as 不能同时使用", file=sys.stderr)
        return 1
    session_name = None  # session_name：本次运行的会话名（None = 不持久化）
    if args.resume:
        session_name = args.resume  # 恢复已有会话
    elif args.save_as is not None:
        session_name = args.save_as if args.save_as != "__auto__" else generate_name()
        try:
            validate_name(session_name)
        except ValueError as e:
            print(f"[参数错误] {e}", file=sys.stderr)
            return 1
        if args.prompt is None and _session_exists(session_name):
            print(f"[参数错误] 会话已存在：{session_name}（恢复请用 --resume）", file=sys.stderr)
            return 1

    if args.prompt is not None:
        return run_headless(args, cfg, hook_manager, session_name)  # 无头单次
    return run_repl(args, cfg, hook_manager, session_name)  # 交互 REPL


if __name__ == "__main__":
    sys.exit(main())

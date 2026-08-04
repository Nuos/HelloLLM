"""模块：ee/runner.py —— 命令执行器（Execution Environment 核心）。

====================================================================
HelloLLM 项目框架结构（S06-basic-ee，论文图1 七组件模型）

S06-basic-ee/
└── hello_llm/
    ├── entrypoints/
    │   ├── __init__.py      包入口（聚合导出）
    │   ├── cli.py           命令行入口（双模式导入，VS Code 兼容）
    │   ├── repl.py          交互式 REPL 主循环
    │   ├── headless.py      无头单次执行
    │   └── render.py        事件渲染（流式/整段/工具/状态）
    ├── query/
    │   ├── __init__.py      包入口
    │   └── agent_loop.py    Agent-Loop 主循环（论文图5）
    ├── services/
    │   ├── __init__.py      包入口
    │   └── api/
    │       ├── __init__.py  包入口（ModelConfig/ModelResponse/ToolCall）
    │       ├── claude.py    流式对话（SSE 协议）
    │       ├── client.py    网络客户端（重试/超时）
    │       ├── config.py    ModelConfig 与 API key 校验
    │       └── types.py     数据结构（ModelError/ToolCall）
    ├── tools/
    │   ├── __init__.py      包入口
    │   ├── file_tools.py    文件工具（read/write/edit）
    │   ├── bash_tool.py     bash 工具（★ S06 新增，执行走 ee.runner）
    │   └── registry.py      工具注册表与执行分发
    ├── ee/                             十、执行环境（★ S06 新增，图1 "Execution Environment"）
    │   ├── __init__.py                  包入口（聚合导出）
    │   ├── runner.py                    命令执行器（subprocess，cwd/env/超时）★★★ 本模块 ★★★
    │   ├── policy.py                    命令语义分类（对照 src/tools/BashTool 命令集）
    │   └── config.py                    执行环境配置（工作目录/超时上限）
    └── utils/
        ├── __init__.py      聚合导出
        ├── config.py        配置文件加载（~/.hellollm/config.json）
        └── logging.py       业务事件提示（诊断日志层）

模块分层（依赖单向）：entrypoints → query → services/api；tools → ee；utils 被
全部模块依赖。对照 claude-code 源码 restored-src/src 分层。
====================================================================

"""
import subprocess
import time
from dataclasses import dataclass


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration: float = 0.0


def run_command(command, cwd=None, env=None, timeout=None):
    # QQQ77（已答）：return 后面的 CommandResult(...) 不是"一个特殊语句"，
    # 而是先"构造一个临时对象"再作为返回值返回。执行顺序：
    # ① 调用 CommandResult 的构造函数（dataclass 自动生成 __init__，按参数填字段）
    # ② 新对象成为函数返回值，交回调用方（如 bash 工具拿到 result 再拼字符串）。
    start = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.stdout:
            stdout = proc.stdout
        else:
            stdout = ""
        if proc.stderr:
            stderr = proc.stderr
        else:
            stderr = ""
        result = CommandResult(
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            duration=time.monotonic() - start,
        )
        return result
    except subprocess.TimeoutExpired as exc:
        if isinstance(exc.stdout, bytes):
            stdout = exc.stdout.decode()
        else:
            stdout = exc.stdout if exc.stdout else ""
        if isinstance(exc.stderr, bytes):
            stderr = exc.stderr.decode()
        else:
            stderr = exc.stderr if exc.stderr else ""
        result = CommandResult(
            exit_code=-1,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
            duration=time.monotonic() - start,
        )
        return result
    except OSError as exc:
        result = CommandResult(
            exit_code=-2,
            stdout="",
            stderr=str(exc),
            duration=time.monotonic() - start,
        )
        return result


# QQQ94/QQQ95（已答）：已把该模块的"简写/高级语法"全部改写为通俗写法：
# ① proc.stdout or ""（or 短路简写）→ 改成 if 判断后赋值，语义一目了然；
# ② isinstance(...) 条件表达式（三元）→ 拆成 if/else 块；
# ③ return CommandResult(...) 直接构造（语句作为返回值）→ 先构造 result 变量，再 return result。

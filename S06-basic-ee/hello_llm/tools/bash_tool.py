"""模块：tools/bash_tool.py —— bash 工具（S06 新增，执行走 ee.runner）。

====================================================================
HelloLLM 项目框架结构（S06-basic-ee，论文图1 七组件模型）

S06-basic-ee/
└── hello_llm/
    ├── entrypoints/        一、交互表面层（图1 "Interfaces"）
    ├── query/              二、核心层（图1 "Agent Loop"）
    ├── services/api/       四、API 客户端
    ├── tools/              五、工具层（★ S06 新增 bash 工具 ★★★ 本模块 ★★★）
    │   ├── __init__.py
    │   ├── file_tools.py   read_file / write_file / edit_file 实现
    │   ├── bash_tool.py    bash 命令执行（对照 src/tools/BashTool/BashTool.tsx）
    │   └── registry.py     工具 Schema 池 + execute 分派
    ├── ee/                 十、执行环境（图1 "Execution Environment"）
    └── utils/              六、工具函数层

对照 claude-code 源码 src/tools/BashTool/BashTool.tsx：
工具名 Bash、输入 command 字符串、timeout 可选参数、子进程执行。
====================================================================

"""
from ..ee import run_command, clamp_timeout


def bash(command, cwd=None, timeout=None):
    result = run_command(command, cwd=cwd, timeout=clamp_timeout(timeout))
    parts = [f"退出码 {result.exit_code}"]
    if result.stdout:
        parts.append(result.stdout.rstrip())
    if result.stderr:
        parts.append(f"[stderr] {result.stderr.rstrip()}")
    if result.timed_out:
        parts.append(f"[超时] 命令超过 {clamp_timeout(timeout)} 秒被终止")
    return "\n".join(parts)

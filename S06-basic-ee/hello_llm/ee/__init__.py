"""模块：ee —— 执行环境（包），S06 新增。

====================================================================
HelloLLM 项目框架结构（S06-basic-ee，论文图1 七组件模型）

S06-basic-ee/
└── hello_llm/
    ├── entrypoints/        一、交互表面层（图1 "Interfaces"）
    ├── query/              二、核心层（图1 "Agent Loop"）
    ├── services/api/       四、API 客户端
    ├── tools/              五、工具层（含 S06 新增 bash 工具）
    ├── ee/                 十、执行环境（★ S06 新增，图1 "Execution Environment"）
    │   ├── __init__.py     包入口（聚合导出）★★★ 本模块 ★★★
    │   ├── runner.py       命令执行器（subprocess，cwd/env/超时）
    │   ├── policy.py       命令语义分类（对照 src/tools/BashTool 命令集）
    │   └── config.py       执行环境配置（工作目录/超时上限）
    └── utils/              六、工具函数层
====================================================================

"""
from .runner import CommandResult, run_command
from .policy import command_semantics, is_read_only
from .config import DEFAULT_TIMEOUT, MAX_TIMEOUT, resolve_cwd, merge_env, clamp_timeout

__all__ = [
    "CommandResult", "run_command",
    "command_semantics", "is_read_only",
    "DEFAULT_TIMEOUT", "MAX_TIMEOUT", "resolve_cwd", "merge_env", "clamp_timeout",
]

"""模块：ee/config.py —— 执行环境配置（工作目录 / 超时上限 / 环境变量）。

====================================================================
HelloLLM 项目框架结构（S06-basic-ee，论文图1 七组件模型）

S06-basic-ee/
└── hello_llm/
    ├── entrypoints/        一、交互表面层（图1 "Interfaces"）
    ├── query/              二、核心层（图1 "Agent Loop"）
    ├── services/api/       四、API 客户端
    ├── tools/              五、工具层（含 S06 新增 bash 工具）
    ├── ee/                 十、执行环境（★ S06 新增，图1 "Execution Environment"）
    │   ├── __init__.py     包入口（聚合导出）
    │   ├── runner.py       命令执行器（subprocess，cwd/env/超时）
    │   ├── policy.py       命令语义分类（对照 src/tools/BashTool 命令集）
    │   └── config.py       执行环境配置（工作目录/超时上限）★★★ 本模块 ★★★
    └── utils/              六、工具函数层

对照 claude-code 源码 src/tools/BashTool/BashTool.tsx 的 timeout 参数与
getMaxTimeoutMs() 超时上限设计。
====================================================================

"""
import os
from pathlib import Path

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600


# QQQ30（已答）：resolve_cwd 的作用 = 确定"命令在哪个目录里执行"。
# bash 命令都离不开工作目录（pwd/相对路径都以它为准）。
# - 调用方给了 cwd：把 ~ 展开成绝对路径、再解析成规范路径（去掉 . 和 ..），防止歧义；
# - 没给 cwd：返回当前进程所在目录（项目根）。
# 为什么"细碎"：它只回答一个问题（工作目录在哪），独立成函数方便单独测试与复用。
def resolve_cwd(cwd=None):
    if cwd is None:
        return str(Path.cwd())
    path = Path(cwd).expanduser()
    resolved = path.resolve()
    return str(resolved)


# QQQ36（已答）：merge_env 的作用 = 准备"命令执行时的环境变量"。
# 子进程默认只继承当前进程的环境变量；有时候调用方想额外传一些变量（如测试用的
# 临时变量 EE_TEST_VAR），merge_env 把"当前环境"和"额外变量"合并成一份完整字典。
# 拆开的原因：合并逻辑（当前环境 + 额外覆盖）与执行逻辑（subprocess.run）解耦，
# 调用方可以先单独测试"合并结果对不对"，再决定要不要真的执行命令。
def merge_env(extra=None):
    env = dict(os.environ)
    if extra:
        for key, value in extra.items():
            env[key] = str(value)
    return env


# QQQ43（已答）：clamp_timeout 的作用 = 把"超时秒数"钳制在合法范围内（1~600 秒）。
# 调用方可能传 0（非法）、负数、或远超上限的值（如 9999），
# 该函数保证返回的值永远落在 [1, MAX_TIMEOUT] 之间，防止命令瞬间超时或无限运行。
# 拆开的原因：超时规则是执行环境的独立策略，单独成函数便于测试边界值
# （0 → 1、9999 → 600、30 → 30）。
def clamp_timeout(timeout=None):
    if timeout is None:
        return DEFAULT_TIMEOUT
    value = int(timeout)
    if value < 1:
        return 1
    if value > MAX_TIMEOUT:
        return MAX_TIMEOUT
    return value

"""模块：ee/policy.py —— 命令语义分类（Execution Environment 策略层）。

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
    │   ├── policy.py       命令语义分类（对照 src/tools/BashTool 命令集）★★★ 本模块 ★★★
    │   └── config.py       执行环境配置（工作目录/超时上限）
    └── utils/              六、工具函数层

对照 claude-code 源码 restored-src/src/tools/BashTool/：
bashCommandHelpers.ts 的 BASH_READ_COMMANDS / BASH_SEARCH_COMMANDS /
BASH_SILENT_COMMANDS / BASH_SEMANTIC_NEUTRAL_COMMANDS 命令集。
====================================================================

"""

# QQQ25（已答）：read 类型 = 只读命令。它们只"看"文件内容，不修改任何东西，
# 因此执行它们绝对安全。例如 cat（显示文件）、head/tail（看开头/结尾）、
# wc（统计行数）、diff（比较文件差异）。
READ_COMMANDS = frozenset({
    "cat", "head", "tail", "less", "more",
    "wc", "diff", "cmp", "sort", "uniq", "cut", "paste", "nl",
})

# QQQ31（已答）：search 类型 = 检索命令。它们在整个文件系统/代码库里"找东西"，
# 同样只读不改。例如 grep（按内容搜索）、find（按名字/条件找文件）、
# which（查命令安装位置）。
SEARCH_COMMANDS = frozenset({
    "find", "grep", "rg", "ag", "ack", "locate", "which", "whereis",
})

# QQQ36（已答）：silent 类型 = "会改动文件系统、但通常不打印输出"的命令，
# 命名为 silent（静默）正因为它们干完活不出声。它们有副作用（改变磁盘状态），
# 属于 write（写操作）类别。
# QQQ37（已答）：逐条解释：
#   mv     移动/重命名文件
#   cp     复制文件
#   rm     删除文件（危险，不可恢复）
#   mkdir  创建目录
#   rmdir  删除空目录
#   chmod  修改文件权限
#   chown  修改文件属主
#   chgrp  修改文件所属组
#   touch  创建空文件/更新时间戳
#   ln     创建硬链接/软链接
#   cd     切换当前目录（无输出但改变执行位置）
#   export 设置环境变量（影响后续命令）
#   unset  删除环境变量
#   wait   等待后台任务结束
SILENT_COMMANDS = frozenset({
    "mv", "cp", "rm", "mkdir", "rmdir", "chmod", "chown", "chgrp",
    "touch", "ln", "cd", "export", "unset", "wait",
})

# QQQ43（已答）：neutral 类型 = 中性/无副作用命令。它们只打印文本或返回状态，
# 不读文件也不改文件，执行它们绝对安全。例如 echo（打印文本）、printf（格式化打印）、
# true/false（直接返回成功/失败状态）、:（空操作）。
NEUTRAL_COMMANDS = frozenset({
    "echo", "printf", "true", "false", ":",
})


def command_semantics(command):
    if command is None:
        command = ""
    tokens = command.strip().split()
    if not tokens:
        return "empty"
    first_word = tokens[0]
    parts = first_word.split("/")
    base = parts[-1]
    if base in READ_COMMANDS:
        return "read"
    if base in SEARCH_COMMANDS:
        return "search"
    if base in SILENT_COMMANDS:
        return "write"
    if base in NEUTRAL_COMMANDS:
        return "neutral"
    return "other"


def is_read_only(command):
    # QQQ62/QQQ63（已答）：你的理解完全正确。这里分两步执行：
    # 第一步：先调用 command_semantics(command)，得到命令的语义类型（返回值是一个字符串，
    #   例如 "read"、"write"、"other"）。
    # 第二步：用 in 判断这个字符串是否属于 ("read", "search", "neutral") 三个只读类型之一。
    #   "函数调用后紧跟 in" 不是特殊语法——只是"把函数返回值直接拿来比较"。
    # 原写法是 return command_semantics(command) in ("read", "search", "neutral")，
    # 已按通俗风格改写为下面的分步写法（结果完全一样）：
    semantics = command_semantics(command)
    if semantics in ("read", "search", "neutral"):
        return True
    return False


# QQQ70/QQQ71（已答）：模块"拆得零碎"是刻意设计，三部分各司其职：
# ① 四个命令集常量 = "声明哪些命令属于哪类"（数据表）；
# ② command_semantics = "分类器"（输入一条命令，输出它的类型）；
# ③ is_read_only = "决策器"（基于分类结果，输出是否只读）。
# 拆开的好处：每一部分都能单独测试、单独复用（比如后续权限系统只想要分类结果，
# 不必依赖 is_read_only 的只读判断）。若强行合并成一个函数，会同时耦合
# "分类数据 + 分类逻辑 + 决策逻辑"三件事，反而更难懂、难测。

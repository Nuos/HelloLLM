# Python 项目在 VS Code 完整运行配置指南（HelloLLM 实证）

> 适用: HelloLLM 教程项目（S01-S05）与任意 Python 项目
> 版本: v1.0
> 日期: 2026-08-03
> 依据: VS Code Python 扩展官方机制 + PEP 668 + HelloLLM 实际踩坑修复

---

## 一、机制依据：VS Code 怎么"跑"Python

VS Code 本身不含 Python 运行时，它靠 Python 扩展把三件事串起来，而这三件事全部依赖同一个"选中的解释器"。

1. IntelliSense 与 Pylance（代码补全、报错）——依赖解释器。选错解释器时补全出不来、明明装了库还报"找不到"。
2. 运行与调试（F5、右上角运行按钮）——依赖解释器与 launch.json。用错解释器时 import 全炸。
3. 终端激活（打开终端自动 activate venv）——依赖解释器。选错时终端里 python 是别的版本。

打个比方：解释器是项目的"工作台"。VS Code 的所有工具（语法助手、调试器、终端）都到这个工作台上拿工具。工作台选错了，一切都乱。

**配置的本质**：项目里的一切 Python 相关能力，最终都要回答同一个问题——"用哪个 Python 执行"。settings.json、launch.json、终端激活，都是把这个答案钉死，不让 VS Code 猜。

## 二、配置清单（7 项，按必要性排序）

### 1. 创建虚拟环境 .venv（最基础）

命令：`python3 -m venv .venv`

依据：PEP 668 规定现代 macOS/Linux 系统 Python 受保护，直接 pip install 会拒绝（externally-managed-environment），必须用 venv；同时 venv 提供依赖隔离，每个项目独立装包互不污染。

### 2. 安装 Python 扩展与 Pylance

安装 ms-python.python（自带 Pylance）。没有扩展 VS Code 不认 Python 文件；Pylance 提供补全、类型检查与诊断。

### 3. 告诉 VS Code 用哪个解释器（.vscode/settings.json）

配置示例：python.defaultInterpreterPath 指向 .venv/bin/python，python.terminal.activateEnvironment 设为 true。

依据：Python 扩展默认选系统 Python 或"最近打开过的解释器记忆"，必须显式指定。这就是 S04/S05 曾出问题的根因——根 settings.json 只指向 S01 的 venv，S04/S05 没配时 VS Code 就会用错工作台。

注意：单根工作区（打开一个文件夹）只能有一个默认解释器。HelloLLM 这种"一个仓库含多个独立项目"的结构，解决方式是每个阶段目录内放自己的 .vscode/settings.json（单独打开该目录时生效），再加上 launch.json 每条配置显式指定 python。

### 4. 调试配置（.vscode/launch.json）

debugpy 调试器需要四样东西：python（用哪个解释器启动）、program（启动哪个入口文件）、cwd（在哪个目录跑，相对路径与配置文件查找依赖它）、env（运行时环境变量，如 API key、NO_PROXY 代理直连）。

### 5. 测试配置

settings.json 里开启 python.testing.pytestEnabled 并指定 pytestArgs。不配也能命令行跑 pytest，但 VS Code 侧边栏"测试"视图和 Run Test 按钮不可用。

### 6. 环境变量（API key / 代理）

运行时真实依赖。HelloLLM 的 API key 走 ~/.hellollm/config.json（项目外、0o600，不进仓库）；代理问题用 launch.json 的 NO_PROXY=api.deepseek.com 直连解决——SSE 长连接走系统代理会间歇断连。

### 7. .gitignore 排除 .venv 与敏感文件

venv 是机器相关的（路径/包版本），提交了别人也跑不了，且可能含敏感信息。.venv、__pycache__、hooks.json（私人配置）都在排除清单。

## 三、HelloLLM 实际配置（实证对照）

1. 五阶段各自 venv（S0X/.venv）——解决 PEP 668 与依赖隔离。
2. 五阶段各自 settings.json（S0X/.vscode/settings.json）——单独打开目录时解释器正确。
3. 五条调试配置（根 .vscode/launch.json）——F5 用对 venv，NO_PROXY 直连。
4. 顶部双模式导入块（cli.py 顶部）——解决 VS Code 运行按钮直接跑脚本时无包上下文、相对导入必炸的问题（踩过两次）。
5. 非 TTY 引导（repl.py）——检测 stdin 非终端时引导用户到集成终端（输出面板无行编辑、中文输入异常）。
6. pytest 配置（各阶段 tests 目录）——测试发现与运行。

## 四、常见坑与解决（踩坑记录）

1. 默认解释器只指向 S01——打开 S04/S05 的 .py 时 IntelliSense、运行按钮、终端激活都用 S01 的 venv。解决：每个阶段目录放自己的 .vscode/settings.json。
2. 相对导入在脚本模式必炸——VS Code 运行按钮（或 python cli.py）直接执行时 __package__ 为空，from ..xxx import 报 ImportError。解决：cli.py 顶部双模式块（脚本模式用绝对导入，包模式用相对导入），且函数体内禁止相对导入。
3. 非 TTY 终端无行编辑——VS Code 输出面板里 input() 退格无效、中文输入异常。解决：启动时检测 isatty() 并引导到集成终端。
4. 系统代理破坏 SSE 长连接——Python urllib 自动读 macOS 系统代理（VPN 127.0.0.1:4780），转发 SSE 长连接不稳定导致 Connection reset。解决：launch.json 里 NO_PROXY=api.deepseek.com 直连。
5. PEP 668 拒绝 pip install——系统 Python 受保护。解决：一律用 venv。

## 五、完整 workflow（从零到跑通）

1. 克隆或创建项目目录，进入项目根。
2. 创建虚拟环境：python3 -m venv .venv。
3. 激活并安装依赖：.venv/bin/pip install -r requirements.txt（或 uv sync）。
4. 安装 VS Code 扩展：Python（含 Pylance）、debugpy（随扩展自带）。
5. 创建 .vscode/settings.json：指定 defaultInterpreterPath 与终端激活。
6. 创建 .vscode/launch.json：为每个入口（REPL/无头）写调试配置（python、program、cwd、env）。
7. 配置测试：settings.json 开启 pytest。
8. 配置环境变量：API key 入本地配置文件（项目外），代理直连入 launch.json env。
9. 验证：打开 .py 文件看右下角解释器；F5 调试跑通；pytest 全绿；终端 which python 指向 venv。
10. 收尾：.gitignore 排除 .venv 与敏感文件，提交前敏感扫描。

## 六、todo list（配置核对清单）

- ☐ 已创建 .venv 且依赖安装完成
- ☐ 已安装 Python 扩展与 Pylance
- ☐ .vscode/settings.json 已指定 defaultInterpreterPath 指向本阶段 venv
- ☐ 终端自动激活已开启（activateEnvironment）
- ☐ .vscode/launch.json 每条调试配置的 python/program/cwd/env 完整
- ☐ 调试配置的 env 含 NO_PROXY 直连（有代理环境时）
- ☐ pytest 测试配置已开启且测试发现正常
- ☐ API key 已在本地配置文件（项目外），未出现在仓库
- ☐ .gitignore 已排除 .venv、__pycache__、私人配置
- ☐ 双模式导入块已就位（脚本模式可跑）
- ☐ 打开 .py 文件右下角显示正确解释器
- ☐ F5 调试断点命中
- ☐ pytest 全绿
- ☐ 终端 which python 指向 .venv/bin/python
- ☐ 真实运行（带 API key）一轮对话正常

## 七、验证与验收标准

1. 打开任意 .py，右下角状态栏显示正确解释器（本阶段 venv）。
2. F5 调试：断点命中、变量可查（debugpy 正常）。
3. 运行按钮直接跑（脚本模式）：无 ImportError（双模式块生效）。
4. 终端：which python 指向 .venv/bin/python（自动激活）。
5. 侧边栏测试视图：发现全部用例，跑全绿。
6. 真实运行：带 API key 跑一轮对话正常（网络直连无断连）。

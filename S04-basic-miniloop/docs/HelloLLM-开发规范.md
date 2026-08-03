# HelloLLM 开发规范

> 版本: v4.0
> 日期: 2026-08
> 依据: 论文《Dive into Claude Code》(arXiv:2604.14228v2) 图1 七组件模型
> 用途: 后续模块开发说明文档
> 结构: 教程式打包，每阶段 = 完整项目（Nuos/HelloLLM/S04-basic-miniloop/hello_llm）

## 一、项目总览

**目标：** 依据论文分析，按步骤重建 Claude Code 架构，功能、模块逐步添加。
当前为 S04 版本：**S03（S01 + hook 机制）+ Permission System 最小子集（独立模块，§5 deny-first）**，其余组件留待后续版本。

**后续模块路线（论文章节映射）：**
会话持久化（§9）→ MCP/工具扩展（§6）→ 上下文压缩流水线（§7）→ 子 Agent 委派（§8）→ 记忆（§7.2）。
（Hook §5/§6 已于 S03 实现；权限系统 §5 最小子集已于 S04 实现。）

**仓库结构（教程式打包）：** 每个课程阶段是一个完整项目包（功能 + 配置 + 测试 + 文档），
并列放在仓库根下；当前阶段为 `S04-basic-miniloop/`：

```text
HelloLLM/                            # GitHub: Nuos/HelloLLM
├── README.md                        # 仓库说明（结构树 + 快速开始）
└── S04-basic-miniloop/                  # ★ 当前阶段：完整项目包（S01 + hook 机制）
    ├── hello_llm/                   #   Python 包（见 3.1 分层）
    ├── tests/                       #   53 项单元测试
    ├── docs/                        #   开发规范文档（本文件）
    ├── pyproject.toml               #   项目配置
    ├── README.md                    #   项目说明
    └── requirements*.txt            #   依赖声明（运行时零第三方依赖）
```

后续阶段（权限系统 / 会话持久化 / MCP / 压缩流水线 / Hook / 子 Agent）将作为
`S02-*`、`S03-*` 完整项目包并列放入仓库根。

**阶段结构约定（重要）：**
1. **每个阶段目录 = 截至该阶段的完整最新版项目包**（自包含、可直接运行）——
   包含当前全部功能、配置、测试、文档，不是增量补丁；
2. 新阶段（如 `S02-*`）在上一阶段基础上**按开发需求新增**功能，
   同时**继承并升级**所有既有内容（代码 / 测试 / 文档随阶段同步演进）；
3. 每阶段的 `docs/` 目录均保留一份**截至该阶段的开发规范**（含最新目录结构树与约定），
   保证任意阶段独立可读、可运行、可继续开发。

## 二、开发流程要求

1. **先计划后执行：** 任务开始前先展示完整 workflow 计划（目录结构、场景清单、依赖、预估时长），确认后再动手。
2. **迭代工作流：** inspect（读代码）→ adapt（适配）→ execute（执行）→ verify（验证）→ table（表格汇报）。
3. **修改后自测再报告：** 每轮改动必须跑 pytest 全绿 + 真实运行验证，再汇报结果。
4. **发布前验证：** 逐功能系统化测试（模块导入 / 对象运行 / 生命周期 / 渲染流水线 / 系统依赖 五维度）。
5. **修根因不修症状：** 发现 bug 先定位根因，检查兄弟调用路径同类缺陷，修复后补**回归测试**。
6. **不擅自缩小/扩大范围：** 任务范围模糊时先问用户；删除文件前必须问；不主动下载/安装未经同意的依赖。
7. **如实报告：** 失败/受阻直接说，绝不伪造运行结果。

## 三、模块化要求

### 3.1 目录结构（参考 claude-code 源码分层）

```text
HelloLLM/S04-basic-miniloop/               # 当前阶段根（完整项目包）
├── hello_llm/                         # Python 包
│   ├── __init__.py                    包入口：版本号 + 项目说明 + 全局术语表
│   ├── __main__.py                    python -m hello_llm 入口
│   │
│   ├── entrypoints/                   一、交互表面层（论文图1 "Interfaces"）
│   │   ├── cli.py                     CLI 入口：argparse + 配置校验 + 分派（薄壳）
│   │   ├── repl.py                    交互 REPL（多轮对话）
│   │   ├── headless.py                无头单次（对照 claude -p）
│   │   └── render.py                  Agent-Loop 事件渲染
│   │
│   ├── query/                         二、核心层（论文图1 "Agent Loop"，§4.1）
│   │   └── agent_loop.py              query_loop() 生成器 + Conversation 会话状态
│   │
│   ├── services/                     三、服务层（对照 claude-code src/services/）
│   │   └── api/                      四、API 客户端（对照 src/services/api/）
│   │       ├── config.py             ModelConfig 模型调用配置（含 API key 校验）
│   │       ├── types.py              数据结构与异常
│   │       ├── claude.py             stream_chat：SSE 流式客户端（对照 claude.ts）
│   │       └── client.py             consume_stream + call_model（对照 client.ts）
│   │
│   ├── tools/                         五、工具层（对照 claude-code src/tools/）
│   │   ├── registry.py                工具 Schema 池 + execute 分派
│   │   └── file_tools.py              read_file / write_file / edit_file
│   │
│   ├── utils/                         六、工具函数层（对照 claude-code src/utils/）
│   │   ├── __init__.py                包入口（聚合导出）
│   │   ├── config.py                  本地配置文件加载（对照 src/utils/config.ts）
│   │   └── logging.py                 日志提示层（对照 errorLogSink.ts 类比）
│   │
│   ├── hooks/                         七、hook 机制（对照 claude-code src/utils/hooks.ts，S03）
│   │   ├── __init__.py                包入口（聚合导出）
│   │   ├── config.py                  加载 hook 规则（项目内 + 用户级合并）
│   │   ├── runner.py                  subprocess 执行 hook（stdin/stdout JSON）
│   │   └── manager.py                 HookManager：PreToolUse/PostToolUse 调度
│   │
│   └── permissions/                   八、权限系统（★ S04 新增，图1 "Permission System"）
│       ├── __init__.py                包入口（聚合导出）
│       ├── policies.py                工具→策略等级映射（deny-first）
│       ├── modes.py                   权限模式（interactive / auto-accept / read-only）
│       └── gate.py                    PermissionGate：execute 前检查（allow/ask/deny）
│
├── tests/                             53 项单元测试（test_agent_loop / cli / config / tools / logging / hooks）
├── docs/                              开发规范文档（本文件 + HTML 版）
├── pyproject.toml                     项目配置（[project.scripts] hello-llm）
├── README.md                          项目说明
└── requirements*.txt                  依赖声明（运行时零第三方依赖）
```

### 3.2 硬性规则

1. **所有 Python 文件必须放在对应模块文件夹中**，一个文件一个职责，禁止散落的顶层 .py。
2. 新增/移动模块后，**必须同步更新所有文件头 docstring 里的框架树**（统一脚本或手工）。
3. 包内 `__init__.py` 聚合导出，外部统一从包导入（如 `from hello_llm.services.api import ModelConfig`）。
4. 依赖方向单向：entrypoints → query / services/api → tools → utils / hooks / permissions；utils、hooks、permissions 无依赖（可被任何层引用）。
5. 模型/工具等实现类模块内部可拆子包（config / types / client 等），命名对齐 claude-code 源码（如 services/api/claude.py 对照 claude.ts）。
6. 关键模块可被测试注入（call_model / execute_tool / stream_model 均为注入点），便于 mock 测试。

## 四、注释要求

1. **文件头模块描述（每个 .py 必写）：** docstring 首行 `模块：xxx —— 一句话职责`；完整项目框架结构树（与上文 3.1 一致）；树中用 `★★★ 本模块：xxx ★★★` 标注本模块位置；末尾"缩略词说明"小节（列出本模块出现的术语）。
2. **函数描述（每个函数必写）：** docstring 说明"当前函数在本模块中的功能作用"，编号结构：

```text
一、功能作用     —— 结合本模块真实业务逻辑，说清算法/流程（做了什么、为什么这么做）
二、输入（input）—— 每个入参的业务含义（中文叙述，不写类型符号、不重复变量名）
三、输出（output）—— 返回值在业务中的含义（中文叙述；无返回值也要写明）
四、（可选）异常/安全策略/设计原则/注意事项
```

3. **注释语言规范（硬性）：** 行内注释与 docstring 一律用**简体中文叙述句**，
   描述**真实业务逻辑**（这段代码在这个模块里做什么、为什么这样处理）；
   **禁止**用变量名解释变量名（如 `# result_text：工具返回的 tool_result`）、
   **禁止**堆砌类型符号解释含义（如 `dict：{"allow": bool, ...}`）、
   禁止"统一处理/核心机制"之类脱离业务的大而空表述。

3. **简写/缩写变量注释：** 所有简写变量/函数在**声明位置**注释全称与含义，如 `cfg`=ModelConfig、`conv`=Conversation、`msgs`=messages、`tc`=ToolCall、`buf`=buffer、`fn`=function、`p`=Path 等。
4. **缩略词术语注释：** CLI / REPL / SSE / API / JSON / HTTP / TTY / ANSI / DNS / URL / Bearer / ReAct / EOF / MCP / F5 等，在首次出现处或模块"缩略词说明"小节给出全称 + 中文含义（全局术语表见 `hello_llm/__init__.py`）。
5. **注释有条理：** 使用编号层级 `一、 → 1. → 1.1`；行内注释解释"为什么"而非复述代码。
6. **导入包也要加注释：** 每个 import 标注用途（如 `import json  # 工具参数序列化`）。

## 五、异常处理要求

1. **未配置 API Key** — CLI 启动 fail-fast：`[配置错误]` + 完整配置指引，退出码 1，不发网络请求
2. **HTTP 错误（401/400/429…）** — `[模型错误] HTTP xxx: <服务端错误体>`；429 额外输出额度提示
3. **请求超时** — 分类提示 + `--timeout` 调大建议（注意 socket.timeout 需单独捕获）；network/timeout 类错误由 agent_loop **自动重试最多 3 次**（递增退避 1s/2s），http 类不重试
4. **网络错误（DNS/连接拒绝）** — `[模型错误] 网络错误: <原因>`
5. **REPL 流式中 Ctrl-C** — 中止本轮（`[已中止本轮]`），回到输入提示，不退出程序
6. **模型空回复** — 显式提示，杜绝"输入后没反应"的错觉
7. **其他未知异常** — `[内部错误] <类型>: <信息>` 兜底，绝不静默失败
8. **协议一致性（tool 消息）** — 历史裁剪后必须清理孤立 tool 消息（否则 HTTP 400）——已实现的 `_prune_incomplete_tool_sequences` 守卫

## 六、日志提示要求

> **原则：** 触发了某个模块、模块的功能（信息裁剪、调用读文件工具、超出预算、超出额度等），必须出现明显日志提醒；出现异常或遇到限制，至少发出提示信息通知用户。

1. `ℹ` 青色 · 信息 · 模块/轮次触发 — `Agent-Loop 第 1/10 轮：调用模型 deepseek-v4-flash`
2. `⚙` 紫色 · 工具 · 工具触发/完成 — `工具触发：read_file({...})` / `工具完成：read_file 返回 4154 字符`
3. `⚠` 黄色 · 警告 · 限制事件 — `上下文超预算：…已裁剪 N 条最老消息` / `达到最大轮数限制（N 轮）` / `模型未返回内容`
4. `✖` 红色 · 错误 · 失败/异常 — `工具失败：read_file → 错误：…` / `额度/频率限制：HTTP 429…`

- 输出到 **stderr**（不污染 stdout 答案），flush 即时上屏，任何运行模式（含 --no-stream）可见。
- 事件函数集中在 `utils/logging.py`（notice / warn / error / tool + 业务事件函数），由各层接入。
- 新增限制/异常事件必须走日志层，禁止裸 print 或无提示。

## 七、配置规范

1. **API key 不写死在代码中，不读环境变量**（VS Code F5 调试不加载 shell 配置，环境变量方案不可靠）。
2. 唯一本地来源：配置文件 `~/.hellollm/config.json`（JSON），独立加载模块 `utils/config.py`（对照 claude-code src/utils/config.ts）。

```json
{
  "api_key": "sk-...",
  "api_base": "https://api.deepseek.com",
  "model": "deepseek-v4-flash",
  "timeout": 120
}
```

3. 优先级：**命令行参数 > 配置文件 > 内置默认值**；`--config <path>` 指定后只认该路径（不回退默认）。
4. API key 可填写位置：① `~/.hellollm/config.json` 的 `api_key` 字段（推荐，chmod 600）；② `--api-key sk-...`（临时覆盖）；③ `--config <path>`（其他配置文件）。
5. 启动时输出配置来源（`✓ API Key 来源：本地配置文件 …`），缺 key 时 fail-fast 给出完整指引。
6. 密钥文件位于用户主目录、项目仓库之外；创建后 `chmod 600`。

## 八、测试与验证要求

1. 测试用 **pytest**；模型调用一律 mock（注入假 call_model），**不发真实 API**。
2. 新增功能必须有测试；bug 修复必须有回归测试（如协议 400 的 `_assert_protocol_valid` 回归保护）。
3. 现有测试文件：`test_agent_loop`（循环+协议守卫）/ `test_cli`（fail-fast）/ `test_config`（配置合并）/ `test_tools`（文件工具）/ `test_logging`（日志提示）。
4. 验证流程（在阶段目录 `S04-basic-miniloop/` 下执行）：
   1. `cd S04-basic-miniloop && .venv/bin/python -m pytest -q` 全绿；
   2. 真实 API 冒烟：无头 `-p` / REPL 多轮 / 工具路径各跑一遍；
   3. 清空环境变量验证配置来源（`env -u HELLOLLM_API_KEY ...`）；
   4. VS Code 调试验证：debugpy 运行 `hello_llm/entrypoints/cli.py`（脚本模式双兼容）。
5. 运行环境：`/opt/homebrew/bin/python3.11 -m venv .venv`；运行时零第三方依赖（纯标准库）。

## 十、hook 机制规范（S03 新增）

### 10.1 设计原则（对照 claude-code 源码 utils/hooks.ts 最小子集）

1. **外部命令扩展点**：hook 是 shell 命令（可引用脚本），在工具执行前/后触发，
   通过 **stdin/stdout JSON** 与主进程通信（同源码协议）；
2. **fail-open（失败放行）**：无配置 / 不匹配 / hook 失败 / 超时（10s）→ 放行，
   不影响 S01 原有行为（向后兼容）；
3. **决策与执行分离**：PreToolUse 在 `execute` 之前裁决（deny → 不执行 + 回填），
   PostToolUse 在 `execute` 之后改写输出。

### 10.2 hook 类型与协议

| hook 类型 | 触发时机 | 输入（stdin） | 输出（stdout） |
|-----------|---------|--------------|---------------|
| `PreToolUse` | 工具执行前 | `{"tool_name", "arguments"}` | `{"decision": "allow"/"deny", "reason"?, "updatedInput"?}` |
| `PostToolUse` | 工具执行后 | `{"tool_name", "arguments", "tool_output"}` | `{"decision": "allow", "updatedOutput"?}` |

### 10.3 配置（config.py）

1. 配置来源合并：用户级 `~/.hellollm/hooks.json`（低优先级）+ 项目内 `<项目根>/hooks.json`（高优先级）；
   示例模板 `hooks.example.json`（可提交，含示例规则）；
2. **项目内 `hooks.json` 属私人配置，已加入 `.gitignore`，禁止提交**；
3. 规则格式：`{"hooks": {"PreToolUse": [{"matcher": "...", "command": "..."}]}}`；
   `matcher` 空 = 匹配全部；`command` 为 shell 命令；
4. 配置损坏 / 不存在 → 空规则（fail-open）。

### 10.4 集成与事件

1. **集成点**：`query/agent_loop.py` 在 `execute_tool()` 之前调用 `HookManager.run_pre_tool()`
   （deny → 不执行，拒绝原因作为 `tool_result` 回填模型），之后调用 `run_post_tool()`（改写输出）；
2. **入口透传**：`cli.py` 构建 `HookManager()`（自动加载配置），经 repl/headless → render → query_loop 传递；
3. **日志事件**：hook 拒绝输出 `⚠ hook 拒绝：<工具>（<原因>）`；hook 失败放行时静默（fail-open）；
4. **新增 hook 必做**：在 `hooks.json` 配置规则并验证协议（stdin/stdout JSON 合法）。

## 九、新增模块开发 Checklist

1. 确定模块归属层（entrypoints / query / services/api / tools / utils / hooks / permissions），放入 `S04-basic-miniloop/hello_llm/` 对应文件夹；
2. 文件头 docstring：模块职责 + 完整框架树 + `★★★ 本模块位置 ★★★` + 缩略词说明；
3. 每个函数：编号 docstring（一、功能作用（含算法）二、输入（input）三、输出（output））；行内注释用简体中文叙述业务逻辑（禁变量名重复、禁类型符号堆砌）；缩写变量声明处注释；import 注释；
4. 包内 `__init__.py` 聚合导出，更新各文件框架树；
5. 异常路径全部有提示（fail-fast / 分类提示 / 日志层事件）；
6. 补测试（正常路径 + 边界 + 回归），pytest 全绿；
7. 真实运行冒烟（无头 + REPL + 工具路径），表格汇报改动文件与验证结果；
8. 更新 README（结构表 / 异常表 / 日志表 / 配置说明）。


## 十一、权限系统规范（S04 新增）

### 11.1 设计原则（论文 §5 最小落地，与 hook 双层把关）

1. **deny-first（拒绝优先）**：未知/未声明策略的工具一律按危险级拒绝，须显式声明才可放行；
2. **分级信任（graduated trust）**：只读（自动放行）→ 写（需批准）→ 危险（拒绝）；
3. **执行顺序**：hook PreToolUse（用户自定义管理，可改写参数）→ 权限门（系统级裁决）→ execute → hook PostToolUse。

### 11.2 策略分级表（policies.py）

| 等级 | 含义 | 覆盖工具 | 默认处理 |
|------|------|---------|---------|
| `read` | 只读，无副作用 | `read_file` | 自动放行 |
| `write` | 修改文件/状态 | `write_file`、`edit_file` | interactive 下 Y/N 批准 |
| `danger` | 危险动作 | 未知工具（deny-first） | 一律拒绝 |

### 11.3 权限模式矩阵（modes.py × gate.py）

| 模式 | CLI 入口 | read | write | danger |
|------|---------|------|-------|--------|
| `interactive`（默认） | 无参数 | allow | ask（Y/N） | deny |
| `auto-accept` | `--yes` | allow | allow | allow |
| `read-only` | `--read-only` | allow | deny | deny |

### 11.4 集成与事件

1. **集成点**：`query/agent_loop.py` 在 hook 放行后、`execute_tool()` 之前调用
   `permission_gate.decide()`；拒绝则不执行，拒绝原因作为 `tool_result` 回填模型；
2. **批准回调**：`repl.py` 的 `_ask_permission()` 注入 `PermissionGate.asker`；
   无头模式（`-p`）无交互 → ask 自动拒绝（安全默认）；
3. **日志事件**：权限拒绝输出 `⚠ 权限拒绝：<工具> 未获批准（模式 <mode>）`；
4. **新增工具必做**：在 `policies.py` 的 `TOOL_POLICIES` 中显式声明等级，否则默认拒绝。

## 十二、核心函数与结构体索引（S04）

以下按模块列出**重要核心**函数与结构体：用途（在项目中干什么）、使用时机（谁在什么时候调用它）、功能作用（结合真实业务逻辑）。

### 12.1 entrypoints/ —— 交互表面层

| 函数/结构体 | 用途 | 使用时机 | 功能作用 |
|------------|------|---------|---------|
| `cli.main` | 进程入口 | 任何运行方式（`python -m hello_llm` 或 VS Code 直接跑 `cli.py`）都汇聚到这里 | 解析命令行参数 → fail-fast 校验 API key（缺失给指引不发请求）→ 打印配置来源 → 构建 hook 管理中枢与权限门 → 按是否带 `-p` 分派到无头或 REPL |
| `cli.build_parser` | 命令行参数解析器 | `main` 开头调用 | 注册全部 CLI 参数（模型/超时/轮数/上下文窗口/`--no-stream`/`--yes`/`--read-only`/`--version`）并返回解析器 |
| `cli._make_gate` | 构建权限门 | `main` 校验通过后调用 | 按 `--read-only` > `--yes` > 默认 interactive 解析权限模式；仅交互 REPL 注入 Y/N 批准回调，无头模式回调留空（权限询问一律拒绝） |
| `repl.run_repl` | 交互主循环 | 用户不带 `-p` 启动时由 `main` 调用 | 循环：读用户输入 → 追加进会话 → `render_events` 跑一轮 Agent-Loop → 回到读输入；`exit`/Ctrl-D 退出 |
| `repl._ask_permission` | Y/N 批准回调 | 权限门在 interactive 模式下裁决"需要询问"时调用 | 把工具名和参数展示给用户，读输入：y/yes 放行，其余拒绝；输入流中断也拒绝 |
| `headless.run_headless` | 无头单次执行 | 用户带 `-p`（含 `-p -` 读 stdin）时由 `main` 调用 | 单轮提问 → `render_events` 消费一轮 Agent-Loop → 输出；`--no-stream` 时整段输出 |
| `render.render_events` | 事件消费者/渲染器 | REPL 与无头每轮对话都调用它 | 消费 `query_loop` 生成器产出的事件流：`text_delta` 逐字输出、`tool_use`/`tool_result` 显示工具、`model_error` 报错；同时把 hook 中枢与权限门透传给循环；返回聚合的回复全文 |

### 12.2 query/agent_loop.py —— 核心层（Agent-Loop）

| 函数/结构体 | 用途 | 使用时机 | 功能作用 |
|------------|------|---------|---------|
| `Conversation`（类） | 会话状态容器 | `main` 创建后贯穿整个运行期，REPL 多轮共用同一个实例 | 持有 system 提示词、消息历史、模型配置；`add_user` 追加用户消息；上下文超预算时按滑动窗口裁剪最老消息 |
| `query_loop`（生成器） | Agent-Loop 心脏 | `render_events` 里被 `for` 循环消费 | 实现论文图5 伪代码：每轮 组装上下文 → 模型调用（流式/聚合）→ 逐个执行工具调用（hook 前审 → 权限门 → execute → hook 后审）→ 结果回填历史；无 tool_use 时本轮完成、循环结束 |
| `call_model_fallback` | 默认聚合模型调用 | `query_loop` 的默认 `call_model` 参数 | 延迟导入 `call_model` 再调用（避开模块级循环依赖），非流式路径的模型入口 |
| `_retry_network` | 网络错误重试包装 | `query_loop` 每次模型调用都经过它 | 对 network/timeout 类错误自动重试最多 3 次（递增退避 1s/2s）；http 类错误不重试直接抛 |

### 12.3 hooks/ —— 工具管理扩展点（S03）

| 函数/结构体 | 用途 | 使用时机 | 功能作用 |
|------------|------|---------|---------|
| `HookManager`（类） | 工具管理中枢 | `cli.main` 构建后随参数链传入 `query_loop` | `run_pre_tool`：工具执行前逐条跑命中 hook，deny 则拦截（原因回填模型）、updatedInput 改写参数；`run_post_tool`：执行后跑 hook，updatedOutput 改写结果；hook 失败一律 fail-open 放行 |
| `load_hook_rules` | hook 规则表加载 | `HookManager` 构造时自动调用 | 先读用户级 `~/.hellollm/hooks.json` 再读项目内 `hooks.json` 合并成一张表；文件缺失/损坏跳过（fail-open） |
| `match_hook` | 规则命中判断 | `HookManager` 遍历规则时调用 | matcher 为空匹配所有工具；否则工具名包含 matcher 子串即命中 |
| `run_hook_command` | 执行外部 hook 命令 | `HookManager` 对每条命中的规则调用 | 把上下文 JSON 写进 stdin，拉起 shell 命令，读 stdout 解析决策；非 0 退出/超时/输出非法一律返回 error 由上层放行 |

### 12.4 permissions/ —— 权限系统（S04）

| 函数/结构体 | 用途 | 使用时机 | 功能作用 |
|------------|------|---------|---------|
| `PermissionGate`（类） | 权限门（系统级裁决） | `cli.main` 构建后随参数链传入 `query_loop`，在 hook 放行后、execute 前裁决 | `check`：按"模式 × 工具等级"矩阵返回 allow/ask/deny；`decide`：最终裁决——allow 放行、deny 拒绝、ask 交给批准回调（无回调一律拒绝） |
| `level_for` | 工具等级查询 | `PermissionGate.check` 内部调用 | 在 `TOOL_POLICIES` 对照表查工具等级；未知工具返回 danger（deny-first） |
| `TOOL_POLICIES`（常量表） | 工具 → 等级映射 | `level_for` 查询 | 声明 `read_file`=read、`write_file`/`edit_file`=write；新增工具必须在此声明，否则默认拒绝 |

### 12.5 services/api/ —— 模型客户端

| 函数/结构体 | 用途 | 使用时机 | 功能作用 |
|------------|------|---------|---------|
| `stream_chat`（生成器） | SSE 流式客户端 | `render_events` 作为 `stream_model` 传给循环 | 组装 OpenAI 协议请求（含工具 schema），发 HTTP 长连接，逐块解析 SSE 事件：内容增量、思维链、工具调用块；错误分类抛 `ModelError`（http/network/timeout） |
| `consume_stream` | 流式事件聚合 | `query_loop` 流式通道调用 | 边把 `text_delta`/思维链转发给渲染层，边聚合完整内容与工具调用，最终产出 `ModelResponse` |
| `call_model` | 聚合模型调用 | 非流式路径（`--no-stream`）与重试路径 | 一次性请求拿到完整 `ModelResponse`（文本或工具调用）；不做流式增量 |
| `ModelConfig`（类） | 模型调用配置 | `cli._make_config` 构建后贯穿全程 | 持有模型名、API base、API key、超时、最大 token；`require_api_key` 校验 key 缺失并给出配置指引 |
| `ModelResponse`/`ToolCall`（类） | 数据结构 | 模型调用产出的标准结果 | `ModelResponse` 承载回复文本与工具调用列表；`ToolCall` 承载单次工具调用的 id/名称/参数，是 Agent-Loop 与工具层之间的契约 |

### 12.6 tools/ —— 工具层

| 函数/结构体 | 用途 | 使用时机 | 功能作用 |
|------------|------|---------|---------|
| `execute` | 工具执行分派 | `query_loop` 在 hook 与权限都放行后调用 | 按工具名查注册表分派到实现函数；未知工具返回错误文本；异常捕获为错误文本而非抛出 |
| `read_file` | 读文件工具 | 模型请求读文件时 | 读文本文件内容，自动拒绝二进制、截断超大文件（防撑爆上下文） |
| `write_file` | 写文件工具 | 模型请求创建/覆写文件时 | 写入指定路径，返回写入摘要 |
| `edit_file` | 编辑文件工具 | 模型请求替换文件内文本时 | 在文件里替换首个匹配的旧文本为新文本；旧文本不存在时返回明确错误 |

### 12.7 utils/ —— 工具函数层

| 函数/结构体 | 用途 | 使用时机 | 功能作用 |
|------------|------|---------|---------|
| `build_model_config` | 配置合并 | `cli._make_config` 调用 | 按"命令行显式参数 > 配置文件 > 内置默认值"合并出 `ModelConfig`；API key 唯一本地来源是 `~/.hellollm/config.json` |
| `find_config_path` | 配置文件定位 | `build_model_config` 内部 | 确认配置文件路径；不存在返回 None 由 `require_api_key` 兜底提示 |
| `notice`/`warn`/`error` 等日志函数 | 统一日志出口 | 各层上报业务事件 | 按六层分类输出诊断提示：事件通知、警告、错误、工具事件、轮次、裁剪/限流（见第七章） |

### 12.8 数据流总览（核心函数如何串起来）

```
用户输入 → repl.run_repl / headless.run_headless
        → render.render_events（消费事件流）
        → query.query_loop（循环心脏）
              ├─ call_model_fallback → client.call_model / stream_chat（模型调用，网络错误 _retry_network 重试）
              ├─ hooks.HookManager.run_pre_tool（用户管理：拦截/改写参数）
              ├─ permissions.PermissionGate.decide（系统裁决：allow/ask/deny）
              ├─ tools.execute → read_file/write_file/edit_file（真正执行）
              └─ hooks.HookManager.run_post_tool（改写结果）
        → tool_result 回填 → 下一轮
```

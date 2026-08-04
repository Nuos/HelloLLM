# HelloLLM 开发规范

> 版本: v5.0
> 日期: 2026-08
> 依据: 论文《Dive into Claude Code》(arXiv:2604.14228v2) 图1 七组件模型
> 用途: 后续模块开发说明文档
> 结构: 教程式打包，每阶段 = 完整项目（Nuos/HelloLLM/S05-basic-statepersist/hello_llm）

## 一、项目总览

**目标：** 依据论文分析，按步骤重建 Claude Code 架构，功能、模块逐步添加。
当前为 S05 版本：**S03（S01 + hook 机制）+ State & Persistence 最小实现（会话持久化，§9）**，其余组件留待后续版本。

**后续模块路线（论文章节映射）：**
MCP/工具扩展（§6）→ 上下文压缩流水线（§7）→ 子 Agent 委派（§8）→ 记忆（§7.2）。
（权限系统 §5 已于 S02 实现；Hook §5/§6 已于 S03 实现。）

**仓库结构（教程式打包）：** 每个课程阶段是一个完整项目包（功能 + 配置 + 测试 + 文档），
并列放在仓库根下；当前阶段为 `S05-basic-statepersist/`：

```text
HelloLLM/                            # GitHub: Nuos/HelloLLM
├── README.md                        # 仓库说明（结构树 + 快速开始）
└── S05-basic-statepersist/                  # ★ 当前阶段：完整项目包（S01 + hook 机制）
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
HelloLLM/S05-basic-statepersist/               # 当前阶段根（完整项目包）
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
│   └── state/                         八、状态与持久化（★ S05 新增，图1 "State & Persistence"）
│       ├── __init__.py                包入口（聚合导出）
│       ├── store.py                   会话 JSONL 存储（对照 src/utils/sessionStorage.ts）
│       └── session.py                 会话名生成与元数据
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
4. 依赖方向单向：entrypoints → query / services/api → tools → utils / hooks；utils、hooks 无依赖（可被任何层引用）。
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
4. 验证流程（在阶段目录 `S05-basic-statepersist/` 下执行）：
   1. `cd S05-basic-statepersist && .venv/bin/python -m pytest -q` 全绿；
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

1. 确定模块归属层（entrypoints / query / services/api / tools / utils / hooks），放入 `S05-basic-statepersist/hello_llm/` 对应文件夹；
2. 文件头 docstring：模块职责 + 完整框架树 + `★★★ 本模块位置 ★★★` + 缩略词说明；
3. 每个函数：编号 docstring（一、功能作用（含算法）二、输入（input）三、输出（output））；行内注释用简体中文叙述业务逻辑（禁变量名重复、禁类型符号堆砌）；缩写变量声明处注释；import 注释；
4. 包内 `__init__.py` 聚合导出，更新各文件框架树；
5. 异常路径全部有提示（fail-fast / 分类提示 / 日志层事件）；
6. 补测试（正常路径 + 边界 + 回归），pytest 全绿；
7. 真实运行冒烟（无头 + REPL + 工具路径），表格汇报改动文件与验证结果；
8. 更新 README（结构表 / 异常表 / 日志表 / 配置说明）。


## 十一、状态与持久化规范（S05 新增）

### 11.1 功能模块

1. `hello_llm/state/store.py`——会话存储核心：路径计算、名校验、新建（meta 首行）、追加、恢复、列表。
2. `hello_llm/state/session.py`——会话名生成（时间戳 YYYYMMDD_HHMMSS）与首条消息标题提炼。
3. `hello_llm/state/__init__.py`——聚合导出，入口层统一从这里 import。

### 11.2 架构

1. 独立模块 `state/`，与 hooks/（工具管理）、permissions/（权限）平级，互不依赖——各模块各司其职。
2. 存储位置在用户主目录 `~/.hellollm/sessions/`（项目外），与 API 配置同目录，跨项目可用。
3. 入口层（cli/repl/headless）只依赖 state 的公开函数，不接触存储细节——依赖方向单向：entrypoints → state。

### 11.3 运行机制

1. REPL 每轮对话结束后，把 `conv.messages` 中新增部分逐条 `append_message()` 追加进转录文件（增量写，不整会话重写）。
2. `--resume` 启动时 `load_session()` 逐行回放转录：meta 行重建 Conversation（system 提示/模型），消息行回放进 messages。
3. 恢复后继续对话，新消息继续追加——文件始终完整，退出即历史完整。

### 11.4 代码功能运行管线

```
cli.py 解析会话名（--resume/--save-as/--list-sessions）
  → repl.run_repl / headless.run_headless（session_name 透传）
  → repl 启动：load_session 恢复 或 create_session 新建
  → Agent-Loop 每轮结束：append_message 增量落盘
  → 退出后：~/.hellollm/sessions/<name>.jsonl 完整转录
  → 下次 --resume：逐行回放 → 继续对话
```

### 11.5 设计原则（对齐 claude-code 源码 utils/sessionStorage.ts）

1. **转录文件（transcript）模式**：每个会话一个 JSONL 文件，消息逐条追加
   （append），恢复时逐行回放（restore）——与源码的转录设计一致；
2. **存储位置**：`~/.hellollm/sessions/<会话名>.jsonl`（与 API 配置同目录，
   项目无关、跨项目可用）；
3. **meta 首行**：首行记录 system 提示 / 模型 / 创建时间，恢复会话时
   用它重建 Conversation 初始状态；
4. **容错**：坏行（非法 JSON）跳过不中断恢复；单行超 16MB 跳过；
   文件超 50MB（MAX_TRANSCRIPT_READ_BYTES，对齐源码）标记截断不读；
5. **路径安全**：会话名只允许 ASCII 字母/数字/下划线/连字符（1-64 位），
   生成路径前先校验，防路径注入。

### 11.6 CLI 参数

| 参数 | 行为 |
|------|------|
| `--save-as [NAME]` | 保存本轮对话到会话；不带名自动生成时间戳名；已存在则拒绝（用 --resume） |
| `--resume NAME` | 恢复指定会话继续对话（REPL）；不存在则警告并新建 |
| `--list-sessions` | 列出所有会话（名字/消息数/大小/时间）后退出 |

### 11.7 保存时机

1. **REPL**：每轮 `render_events` 后，把 `conv.messages` 中新增部分追加落盘
   （增量写，不整会话重写）；
2. **无头**（`-p ... --save-as`）：单轮对话整段保存；
3. 会话持久化只在给定会话名时启用（`session_name=None` 时行为与 S03 一致）。


## 十二、文档章节分析要求（五维度）

**每个章节的主题，都必须结合实际代码与真实业务，从以下五个维度突出说明**（缺一不可）：

### 12.1 功能模块

1. 该章节主题对应哪些模块/文件（给出路径）。
2. 每个模块/文件的职责是什么（一句话说清"干什么"）。

### 12.2 架构

1. 模块间的层次与依赖关系（谁依赖谁，依赖方向单向）。
2. 与论文图1 七组件模型的关系（对应哪个组件）。
3. 与 claude-code 源码的对齐点（哪个文件/设计）。

### 12.3 运行机制

1. 运行时怎么工作：关键函数调用链、事件流、数据流。
2. 触发时机（谁在什么时候调用）。
3. 边界与容错（异常怎么处理）。

### 12.4 设计原则

1. 本模块遵循的设计原则（最小实现 / 对齐源码 / deny-first / fail-open 等）。
2. 为什么这样设计（权衡与取舍）。

### 12.5 代码功能运行管线

1. 从入口到出口的完整管线（文字版流程：输入 → 处理 → 输出 → 落点）。
2. 用箭头图/编号步骤展示调用链，让读者能"照着走一遍"。

**写作检查项**：每章写完对照五维度自查——缺一个维度即视为不合格，须补齐后再定稿。

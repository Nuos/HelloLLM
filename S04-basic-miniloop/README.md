# HelloLLM

最简可交互 AI 编码 Agent —— 依据论文《Dive into Claude Code: The Design Space of
Today's and Future AI Agent Systems》(arXiv:2604.14228v2) 图1 高层系统结构重建。

## S04 范围（S04-basic-miniloop）

论文图1 将系统分解为七个组件：用户、接口、Agent 循环、权限系统、工具、状态与持久化、执行环境。
S04 = S03（S01 + hook 机制）+ **Permission System 最小子集（独立模块）**：

| 模块 | 对应论文 | 实现 |
|------|----------|------|
| CLI 入口层 | 图1 "Interfaces"（交互式 CLI / 无头 CLI） | `hello_llm/entrypoints/`（cli / repl / headless / render） |
| Agent-Loop | 图1 "Agent Loop"（§4.1 查询流水线、图5 伪代码） | `hello_llm/query/agent_loop.py`（hook + 权限门双层把关） |
| **hook 机制** | **图1 工具扩展点（对照 claude-code 源码 utils/hooks.ts）** | **`hello_llm/hooks/`（config / runner / manager）** |
| **权限系统** | **图1 "Permission System"（§5 deny-first / 分级信任）** | **`hello_llm/permissions/`（policies / modes / gate）** |
| （模型客户端） | Agent-Loop 的 deps.callModel() 内部依赖 | `hello_llm/services/api/`（config / types / claude / client） |
| （本地配置） | 配置来源（API key 等） | `hello_llm/utils/config.py` |
| （文件工具） | Agent-Loop 的 execute 路径 | `hello_llm/tools/`（registry / file_tools） |

目录结构参考 claude-code 源码（restored-src/src）分层：`entrypoints / query / services/api / tools / utils / hooks / permissions`。

## hook 机制（S03 新增，工具管理）

对照 claude-code 源码 `utils/hooks.ts` 的最小实现：外部命令（hook）在**工具执行前/后**触发，
通过 **stdin/stdout JSON** 与主进程通信，管理（拦截/改写）工具调用：

| 组件 | 职责 |
|------|------|
| `hooks/config.py` | 加载 hook 规则：项目内 `hooks.json` + 用户级 `~/.hellollm/hooks.json` 合并；`hooks.example.json` 为可提交示例模板 |
| `hooks/runner.py` | subprocess 执行 hook：stdin 收 JSON（tool_name/arguments/output），stdout 收 JSON（decision/reason/updatedInput/updatedOutput），超时 10s |
| `hooks/manager.py` | `HookManager`：PreToolUse/PostToolUse 调度 + 决策合并 |

**hook 类型**：
- **`PreToolUse`**（工具执行前）：`{"decision": "deny"}` → 工具**不执行**，拒绝原因回填模型；
  `{"updatedInput": {...}}` → 改写本次工具参数；`allow` → 放行；
- **`PostToolUse`**（工具执行后）：`{"updatedOutput": "..."}` → 改写工具结果回填模型。

**决策语义**：任一 PreToolUse hook deny → 拒绝；无 hook / 不匹配 / hook 失败或超时 → **放行（fail-open）**，
不影响 S01 原有行为（向后兼容）。

**hooks.json 配置示例**（项目内 `hooks.json` 被 `.gitignore` 排除，私人规则不提交）：
```json
{
  "hooks": {
    "PreToolUse": [
      {"matcher": "write_file", "command": "python3 scripts/check_path.py"}
    ],
    "PostToolUse": [
      {"matcher": "read_file", "command": "python3 scripts/log_read.py"}
    ]
  }
}
```
`matcher` 为空 = 匹配全部工具；`command` 为任意 shell 命令（可引用脚本）。

**集成点**：`query/agent_loop.py` 在 `execute` 之前（PreToolUse）与之后（PostToolUse）调用
`HookManager` —— 对应论文图1 工具扩展点 + 源码 utils/hooks.ts 的 Pre/PostToolUse hooks。

## 权限系统（S04 新增，最小子集）

论文 §5 的 deny-first（拒绝优先）与分级信任（graduated trust）的最小落地，
实现为独立模块 `hello_llm/permissions/`，与 hook 机制**双层把关**：

| 层 | 时机 | 职责 |
|----|------|------|
| hook（S03） | 工具执行前/后 | 用户自定义管理：拦截、改写参数、改写输出（fail-open） |
| **权限门（S04）** | hook 之后、execute 之前 | 内置策略：按工具等级 + 权限模式裁决（allow/ask/deny） |

| 文件 | 职责 |
|------|------|
| `policies.py` | 工具→策略等级映射：`read_file`=read（自动放行）、`write_file`/`edit_file`=write（需批准）、**未知工具=danger（默认拒绝）** |
| `modes.py` | 三种权限模式：`interactive`（默认，Y/N 批准）/ `auto-accept`（--yes 全放行）/ `read-only`（--read-only 写全拒） |
| `gate.py` | `PermissionGate`：`check()` 决策矩阵（allow/ask/deny）+ `decide()` 完整决策（ask 交给 Y/N 回调，无回调→拒绝） |

**决策矩阵**（interactive 模式）：只读→放行；写→弹 `⚠ 权限请求：write_file({...})` + `允许执行？[y/N]`；
危险/未知→拒绝。拒绝时工具**不执行**，`tool_result` 回填"权限拒绝"给模型，模型据此调整行为。

**执行顺序**：hook PreToolUse（用户管理）→ 权限门（系统裁决）→ execute → hook PostToolUse。

## 日志提示层（logging/）

所有**限制事件与异常**都会在 stderr 输出醒目提示（彩色前缀 + flush 即时上屏），
任何运行模式（含 `--no-stream`）都可见：

| 前缀 | 级别 | 触发事件示例 |
|------|------|-------------|
| `ℹ` 青色 | 信息 | `Agent-Loop 第 1/10 轮：调用模型 deepseek-v4-flash`（模块触发） |
| `⚙` 紫色 | 工具 | `工具触发：read_file({...})` / `工具完成：read_file 返回 4154 字符` |
| `⚠` 黄色 | 警告 | `上下文超预算：…已裁剪 N 条最老消息` / `达到最大轮数限制（N 轮）` / `模型未返回内容` |
| `✖` 红色 | 错误 | `工具失败：read_file → 错误：…` / `额度/频率限制：HTTP 429…` / `[模型错误] …` |

实现位置：`hello_llm/utils/logging.py`（事件提示函数），由 `query/agent_loop.py`
（轮次/工具/裁剪/轮数）、`services/api/claude.py`（429 额度）、
`entrypoints/render.py`（空回复）统一接入。

**未包含**（后续版本）：权限系统、MCP、记忆、上下文压缩、会话持久化、Hook、子 Agent。

## 异常处理（基本异常面）

| 场景 | 处理 |
|------|------|
| 未配置 API Key | CLI 启动 fail-fast：`[配置错误]` + 配置指引，退出码 1，不发网络请求 |
| HTTP 错误（401/400/429…） | `[模型错误] HTTP xxx: <服务端错误体>` |
| 请求超时 | `[模型错误] 请求超时（>N 秒）…`，可用 `--timeout` 调大 |
| 网络错误（DNS/连接拒绝） | `[模型错误] 网络错误: <原因>` |
| REPL 流式中 Ctrl-C | 中止本轮（`[已中止本轮]`），回到输入提示，不退出程序 |
| 模型返回空内容 | 显式提示 `[模型未返回内容]`，不静默 |
| 网络错误 / 超时 | **自动重试最多 3 次**（递增退避 1s/2s），`⚠ 网络错误（…），自动重试 n/3…`；耗尽后 `[模型错误]` |
| 其他未知异常 | `[内部错误] <类型>: <信息>` 兜底，杜绝静默失败 |

> ⚠ **网络自动重试**：`network`/`timeout` 类错误自动重试（`MAX_NETWORK_RETRIES=3`），
> `http` 类（400/401/429）不重试（重试无益）。流式中途重试会**重新生成**（可能重复输出已显示部分，属设计取舍）。

> ⚠ 启动时会打印 API key 配置来源（`✓ API Key 来源：本地配置文件 …`），
> 便于确认 key 从哪读入；缺失时 fail-fast 给出完整配置指引。

## API Key 填写位置一览

| 位置 | 方式 | 说明 |
|------|------|------|
| ① 本地配置文件（推荐） | `~/.hellollm/config.json` 的 `"api_key"` 字段 | 主配置来源；启动自动加载，缺 key 时提示创建 |
| ② 命令行参数（临时） | `--api-key sk-...` | 仅覆盖当次运行，不落盘 |
| ③ 指定配置文件 | `--config <path>` | 改用其他 JSON 配置文件（指定后只认该路径） |

配置文件格式：`{"api_key": "...", "api_base": "...", "model": "...", "timeout": 120}`
（`~/.hellollm/` 位于用户主目录、项目仓库之外；创建后 `chmod 600` 仅本人可读。）

## Agent-Loop（论文图5 伪代码）

```
while not stopped:
    context = assemble(system_prompt, tool_schemas, history)   # a. 组装
    action = model(context, tools)                             # b. 模型调用（流式）
    if action.is_text_only(): stopped = ...; continue          #    停止条件（§4.5）
    result = execute(action)                                   # c. 执行工具
    history.append(action, result)
```

停止条件（§4.5）：模型只产生文本（主要条件）/ 达到 `--max-turns` / Ctrl-C 中止。

## 使用

### 1. 配置（本地配置文件，不写死在代码、不读环境变量）

API key 等配置存放在本地 JSON 配置文件 `~/.hellollm/config.json`
（独立模块 `hello_llm/utils/config.py` 负责定位、解析与合并）：

```bash
mkdir -p ~/.hellollm
cat > ~/.hellollm/config.json <<'EOF'
{
  "api_key": "sk-...",
  "api_base": "https://api.deepseek.com",
  "model": "deepseek-v4-flash",
  "timeout": 120
}
EOF
chmod 600 ~/.hellollm/config.json
```

- 优先级：**命令行参数 > 配置文件 > 内置默认值**
- `--config <path>` 可指定其他配置文件（指定后只认该路径）
- `--api-key sk-...` 仅作临时覆盖
- 未配置 key 时启动 fail-fast，打印完整指引，退出码 1
- VS Code F5 调试**无需改 launch.json**：配置文件独立于 shell 环境
- 仓库根 `.vscode/launch.json` 已含 S01/S02 调试配置（`NO_PROXY=api.deepseek.com` 直连，
  避免 macOS 系统代理转发 SSE 长连接不稳定导致 `Connection reset`）

```bash
# 交互式 REPL（多轮、多回合对话，历史保留；滑动窗口防上下文超限）
python3 -m hello_llm

# 也可直接运行脚本（VS Code "运行当前文件" 按钮走的就是这条路）
python3 hello_llm/entrypoints/cli.py  # 交互 REPL（自动兼容两种运行方式）

# 无头单次（对照 claude -p）
python3 -m hello_llm -p "读取当前目录下的 README.md 并总结"

# 无头 + stdin（管道/脚本友好）
echo "用一句话总结 README" | python3 -m hello_llm -p -

# 其他选项
python3 -m hello_llm --model deepseek-v4-pro -p "hi"   # 指定模型
python3 -m hello_llm --max-turns 3 -p "hi"             # 限制循环轮数
python3 -m hello_llm --max-context 20000               # 滑动窗口上限（字符）
python3 -m hello_llm -p "hi" --no-stream               # 无头模式整段输出
```

> ⚠ 交互 REPL 请在**终端**里运行（VS Code 集成终端 / 系统终端）：
> 非终端环境（如 VS Code 输出面板）下 `input()` 没有行编辑能力，
> 退格无法清除字符、中文输入法可能异常（程序会检测并提示）。
> 无头模式 `-p` 不受影响，任何环境都可用。

## 多轮、多回合对话

- **交互 REPL**：一次会话内连续多轮提问，历史保留在 `Conversation.messages`，
  模型能看到完整对话脉络（如"记住我叫小明"→ 之后能答出名字）。
- **滑动窗口**：多轮对话会让上下文无限增长，超过模型窗口会报
  `prompt_too_long`（论文 §4.5 停止条件）。v1 用 `--max-context`（默认 30000
  字符）做滑动窗口：超限时从最老的消息整条裁剪，system 提示词永远保留。
  论文的完整方案（五层压缩流水线）留待后续版本。
- **无头模式**：单次提问（`-p "..."`），或 `-p -` 从 stdin 读提问。

## 工具（内置文件工具，S01 提供）

| 工具 | 说明 |
|------|------|
| `read_file` | 读取文本文件（自动拒绝二进制、截断超大文件） |
| `write_file` | 写入/覆盖文件，自动创建父目录 |
| `edit_file` | 用 new_string 替换第一处 old_string |

## 测试

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.dev.txt
.venv/bin/python -m pytest -q      # mock 模型，不发真实 API
```

依赖见 `requirements.txt`（运行时零依赖，纯标准库）。

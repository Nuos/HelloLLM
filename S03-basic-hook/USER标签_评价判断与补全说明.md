# S03 Hook：USER 标签评价判断与补全说明

## 结论先行

本次对上传源码中新增加的 USER 标签逐项做了“判断 + 修正 + 补全”，并保持 Agent/Hook 核心业务逻辑不变。

最关键的四个纠正是：

1. **`match_hook()` 只负责 matcher 与 tool_name 的匹配。**
   生命周期 event 过滤由 `HookManager.run_pre_tool()/run_post_tool()` 负责；
   具体安全审核由 Handler 负责。

2. **`command` 不是审核条件。**
   `command` 是启动 Hook Handler 的 argv 描述；
   Handler 才是具体 policy logic。

3. **`run_hook_command()` 与 `run_hook_worker()` 不同。**
   前者在父进程中通过 `subprocess.run()` 创建/管理 Hook 子进程；
   后者是子进程内部入口与 Handler 分发器。

4. **PreToolUse 与 PostToolUse 的时间语义不同。**
   - PreToolUse：真实副作用尚未发生，可以 deny / updatedInput。
   - PostToolUse：Tool 已经执行，主要治理进入下一轮 LLM 前的 Observation。

---

## 1. `match_hook()`：“tool_name 是否被 rules 勾住”

**评价：宏观比喻基本正确，但函数职责描述过宽。**

整个 Runtime 层可以说 Tool 被某条 HookRule“勾住”：

```text
Hook Point
  -> event 命中
  -> matcher 命中
  -> Rule 被选中
  -> Handler 运行
```

但 `match_hook()` 自身只做：

```text
rule.matcher <-> tool_name
```

它不判断生命周期、不执行审核、不返回 allow/deny，也不直接决定 Tool 是否执行。

---

## 2. `p.relative_to(root)`：“规定执行路径”

**评价：部分正确。**

它不是设置 cwd，也不修改执行目录。

它做的是：

```text
验证 p 是否属于 workspace_root
+
若属于，则得到 workspace-relative path
```

越界时抛 `ValueError`，被当前 Hook 转成 `decision="deny"`。

---

## 3. 敏感文件规则

原注释把完整文件名与扩展名混在一起。

实际：

```text
SENSITIVE_NAMES
  -> .env / id_rsa / id_ed25519 ...

SENSITIVE_SUFFIXES
  -> .pem / .key / .p12 / .pfx
```

代码使用 OR 合并两套规则。

---

## 4. `protect-write` 返回 `allow`

**评价：基本正确，但只代表通过当前 Handler 的有限策略。**

它只确认：

- 没越出 workspace；
- 不在 `.git`；
- 不命中敏感完整文件名；
- 不命中敏感扩展名。

这不等于“写入内容本身绝对安全”。

而且 `allow` 只是返回父 Runtime，真正写文件仍发生在后面的 `execute_tool()`。

---

## 5. `run_hook_worker()`：“启动 Hook 子进程”

**评价：不准确。**

真正创建子进程：

```python
run_hook_command()
    -> subprocess.run(...)
```

子进程创建以后：

```text
main()
  -> args.hook_worker
  -> run_hook_worker(name)
```

所以：

```text
run_hook_command = Parent-side Process Launcher
run_hook_worker  = Child-side Worker Entry / Dispatcher
```

---

## 6. `protect-write` 分支

**评价：基本正确。**

但 Tool matcher 已经在父进程 HookManager 中处理完成。

此处只是根据 worker name：

```text
protect-write
```

分发到具体 Handler，再由 Handler 检查 payload 中的 path/workspace_root。

---

## 7. `redact-read` 分支

**评价：部分正确。**

它不是读取前授权。

这是 PostToolUse：

```text
read_file 已执行
  -> raw output
  -> redact-read
  -> updatedOutput
  -> LLM
```

所以本质是 **Observation Sanitization / 输出脱敏**。

---

## 8. unknown worker 分支

**评价：正确。**

未知 worker 名称返回 `decision="error"`。

这是 Hook Runtime 自身错误，不是业务 deny；
当前教学版父 Runtime 按 fail-open 处理。

---

## 9. `default_hook_manager()` 初始化 `HookManager.rules`

**评价：正确。**

调用链：

```text
default_hook_manager()
  -> 构造 HookRule 列表
  -> HookManager(rules=...)
  -> __init__
  -> self.rules = rules
```

本示例没有单独 `register()` API，因此“注册”以构造注入方式完成。

---

## 10. `PreToolUse + write_file`

**评价：总体基本正确，但 `command` 角色需要纠正。**

```text
event
= 什么时候考虑 Rule

matcher
= 这条 Rule 对哪些 Tool 生效

command
= 如何启动 Handler

Handler
= 实际检查什么并产生 allow/deny/updatedInput
```

`command` 不是 policy predicate。

---

## 11. `PreToolUse + edit_file`

**评价：正确。**

`write_file` 与 `edit_file` 是两条 Rule，但共用同一个 `protect-write` Handler：

```text
write_file --\
              -> protect-write
edit_file  --/
```

说明 Rule 与 Handler 可以是多对一复用关系。

---

## 12. `PostToolUse + read_file`

**评价：部分正确。**

这里更准确叫：

```text
Post-result policy / output transformation
```

因为读取已经发生，当前示例做的是结果脱敏，而不是决定“能不能读”。

---

## 13. `command=(python, me, "--hook-worker", "redact-read")`

| 参数 | 含义 |
|---|---|
| `python` | `sys.executable`，当前 Python 解释器 |
| `me` | 当前 `.py` 文件绝对路径 |
| `--hook-worker` | 内部 CLI 开关，令 `main()` 进入 Hook Worker 模式 |
| `redact-read` | Worker 名称，分发到 `hook_worker_redact_read()` |

等价命令：

```bash
<python> <this_file.py> --hook-worker redact-read
```

Hook payload 不放在 argv 中，而通过 `subprocess.run(input=...)` 的 stdin 传输。

---

## 14. Agent Loop 调用 `run_pre_tool()`

**评价：整体正确，但要严格分层。**

```text
event + matcher
= Hook Selection

payload
= 本次 Hook Event 上下文

Handler
= Hook Policy Logic

HookManager
= 调度 + 结果合并

Agent Loop
= 根据最终 Hook 结果决定是否进入 Tool Execution
```

---

## 15. Agent Loop 调用 `run_post_tool()`

**评价：正确。**

关键时序：

```text
execute_tool()
  -> raw_result 已产生
  -> PostToolUse
  -> updatedOutput / audit / normalization
  -> role="tool" 回填 messages
  -> 下一轮 LLM
```

这正是 PostToolUse 的核心语义：

> Tool output 已经产生，但尚未成为下一轮模型 Observation。

---

## 最终结构记忆

```text
HookRule
= 声明：何时(event)、匹配谁(matcher)、调用谁(command)

HookManager
= 选择 Rule + 调度 Handler + 合并结果

Hook Handler
= 具体 policy / transform 逻辑

Agent Loop
= 根据 Hook 结果推进或阻断真实 Tool Execution
```

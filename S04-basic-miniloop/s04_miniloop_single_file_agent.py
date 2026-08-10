#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S04 Basic MiniLoop — 单文件、最小、可运行 Agent（Stream 模式）
=============================================================

目标
----
把 HelloLLM/S04-basic-miniloop 中最关键的 Agent Harness 压缩到一个 Python 文件：

    User / REPL
        |
        v
    Agent Loop
        |
        +--> Model Streaming (SSE, stream=true)
        |       |
        |       +--> text delta ----------------------> 立即打印
        |       +--> tool_call delta --聚合----------> ToolCall
        |
        +--> 若无工具调用：结束本轮
        |
        +--> 若有工具调用：
                |
                v
          PreToolUse Hook
                |
                v
          Permission Gate
                |
                v
          Tool Executor
                |
                v
          PostToolUse Hook
                |
                v
          tool_result 回填 messages
                |
                +-------------------------------> 下一轮 Model

核心边界
--------
1) Tool Set / Tool Registry
   - 决定“模型能够提出哪些动作”。
   - Schema 是给模型看的能力描述；Python handler 是给 Runtime 调用的真实实现。
   - 工具本身不负责整个 Agent Loop，也不负责模型调用。

2) Permission Set / Permission Gate
   - 决定“模型提出的动作是否真的允许执行”。
   - 它位于 LLM 与副作用之间，是系统控制边界。
   - deny-first：未知工具默认按危险动作处理并拒绝。
   - 本教学版额外加入 workspace 参数级检查：文件访问不得逃出工作区。

3) Hook Mechanism
   - PreToolUse：工具执行前，可观察、拒绝、改写参数。
   - PostToolUse：工具执行后，可观察、改写回填给模型的结果。
   - Hook 是“扩展/治理点”，不是最终安全边界；示例实现对 Hook 异常 fail-open。
   - HelloLLM S04 原项目通过外部命令 + stdin/stdout JSON 执行 Hook；为了单文件可运行，
     本文件把它压缩成进程内 Python 回调，但保留相同生命周期与决策语义。

4) Agent Loop
   - 是“耦合点”，但不吞并上述模块职责。
   - 它只编排：Model -> ToolCall -> Hook -> Permission -> Tool -> ToolResult -> Model。
   - 每一个被拒绝/失败的工具调用仍必须形成 tool_result 回填，保证协议闭合，让模型
     能看到失败原因并在下一轮调整计划。

与 Claude Code 源码的对应关系（概念映射）
----------------------------------------
- src/QueryEngine.ts
    会话生命周期、消息状态、预算/权限拒绝记录、调用 query()。
- src/query.ts
    queryLoop 状态机；流式消费模型响应；收集 tool_use；工具结果回填后继续下一轮。
- src/services/tools/toolOrchestration.ts
    工具批次编排；完整版会把并发安全的读取并行、修改类工具串行。
- src/services/tools/toolExecution.ts
    单次工具执行管线：输入校验 -> PreToolUse -> Permission -> tool.call -> PostToolUse。

本文件为了“最小、最简单”故意省略
--------------------------------
- MCP / 子 Agent / Memory / Skill / Context Compression
- Session persistence / resume
- 并发工具执行
- 复杂 Bash sandbox / classifier
- 完整 Hook 外部进程协议
- Provider SDK（只用 Python 标准库调用 OpenAI-compatible SSE）

论文依据
--------
《Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems》
(arXiv:2604.14228v2) 所强调的核心思想：
- 核心循环本身很简单：模型 -> 工具 -> 结果 -> 再模型；
- 真正决定生产可用性的，是 Permission、Context、Hooks、State 等运行时控制层；
- deny-first、graduated trust、defense in depth、人类最终决策权是权限设计重点。

运行要求
--------
Python >= 3.10（仅标准库）
模型端点需兼容：POST /chat/completions + stream=true + OpenAI function-calling 格式。

环境变量：
    LLM_API_KEY   必填（也可 --api-key）
    LLM_API_BASE  默认 https://api.openai.com/v1
    LLM_MODEL     必填（也可 --model）

例子：
    export LLM_API_KEY='sk-...'
    export LLM_API_BASE='https://api.openai.com/v1'
    export LLM_MODEL='your-model'

    # 交互模式（写文件时询问）
    python3 s04_miniloop_single_file_agent.py

    # 单次任务
    python3 s04_miniloop_single_file_agent.py -p '读取 README.md 并总结'

    # 自动允许已知工具（仍受 workspace 边界限制）
    python3 s04_miniloop_single_file_agent.py --permission-mode auto-accept \
        -p '创建 hello.txt，写入 hello agent'

    # 只读模式
    python3 s04_miniloop_single_file_agent.py --permission-mode read-only \
        -p '读取 README.md'
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional


# =============================================================================
# 0. 配置层：只保存运行参数，不承担网络调用 / 工具执行
# =============================================================================


@dataclass
class Config:
    """Agent 运行配置。

    边界：Config 只是数据，不应该在这里执行 HTTP、读写文件或做权限判断。
    """

    api_key: str
    api_base: str
    model: str
    workspace: Path
    permission_mode: str = "interactive"
    timeout: int = 120
    max_turns: int = 10
    max_tool_output_chars: int = 30_000


# =============================================================================
# 1. 模型层数据结构：Agent Loop 与 Provider 之间的“内部协议”
# =============================================================================


@dataclass
class ToolCall:
    """聚合后的一个模型工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""
    parse_error: Optional[str] = None


@dataclass
class ModelTurn:
    """一次模型流式响应聚合后的结果。"""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


# =============================================================================
# 2. Tool Set：Schema（给模型）与 Handler（给 Runtime）分离
# =============================================================================

# ---- 2.1 模型可见的工具 Schema ------------------------------------------------
# 这部分决定模型“知道自己可以调用什么”。
# 注意：把某工具 Schema 发给模型 ≠ 允许模型真的执行它。
# 真正是否执行还要经过 PermissionGate。

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取工作区内 UTF-8 文本文件。需要查看文件内容时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "工作区内的相对路径，例如 README.md 或 src/main.py",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或完整覆盖工作区内文本文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "工作区内文件路径"},
                    "content": {"type": "string", "description": "要写入的完整文本"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "把工作区内文本文件中的第一处 old_string 替换为 new_string。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
                "additionalProperties": False,
            },
        },
    },
]


def _resolve_in_workspace(workspace: Path, raw_path: str) -> Path:
    """把模型传入路径解析为工作区内绝对路径。

    这是 execution-side defense-in-depth：即使上层 PermissionGate 有 bug，
    工具实现本身仍不接受工作区逃逸（例如 ../../etc/passwd）。
    """

    workspace = workspace.resolve()
    p = Path(raw_path).expanduser()
    if not p.is_absolute():
        p = workspace / p
    p = p.resolve()

    try:
        p.relative_to(workspace)
    except ValueError as exc:
        raise PermissionError(f"路径越过工作区边界：{p}") from exc
    return p


# ---- 2.2 真正的业务实现 --------------------------------------------------------


def tool_read_file(*, path: str, workspace: Path) -> str:
    target = _resolve_in_workspace(workspace, path)
    if not target.exists():
        return f"错误：文件不存在：{path}"
    if not target.is_file():
        return f"错误：不是普通文件：{path}"
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"错误：{path} 不是可按 UTF-8 读取的文本文件"


def tool_write_file(*, path: str, content: str, workspace: Path) -> str:
    target = _resolve_in_workspace(workspace, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"成功：已写入 {path}（{len(content)} 字符）"


def tool_edit_file(
    *, path: str, old_string: str, new_string: str, workspace: Path
) -> str:
    target = _resolve_in_workspace(workspace, path)
    if not target.exists() or not target.is_file():
        return f"错误：文件不存在：{path}"
    text = target.read_text(encoding="utf-8")
    if old_string not in text:
        return "错误：未找到 old_string，未修改文件"
    updated = text.replace(old_string, new_string, 1)
    target.write_text(updated, encoding="utf-8")
    return (
        f"成功：已编辑 {path}（第一处匹配已替换；"
        f"{len(old_string)} -> {len(new_string)} 字符）"
    )


# Handler 映射只给 Runtime 使用；模型看不到 Python 函数对象。
TOOL_HANDLERS: dict[str, Callable[..., str]] = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
}


def execute_tool(name: str, arguments: dict[str, Any], workspace: Path) -> str:
    """统一 Tool Executor / Dispatcher。

    作用范围：
    - 只负责“找到对应 handler 并调用”；
    - 不负责模型选择工具；
    - 不负责权限策略；
    - 不负责 Hook 生命周期；
    - 异常转成字符串 tool_result，而不是让 Agent Loop 崩掉。
    """

    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return f"错误：未知工具 {name}"
    try:
        return handler(**arguments, workspace=workspace)
    except TypeError as exc:
        return f"错误：工具参数不合法：{exc}"
    except Exception as exc:  # 教学版：把执行错误回填模型，让模型可恢复
        return f"错误：工具 {name} 执行失败：{type(exc).__name__}: {exc}"


# =============================================================================
# 3. Permission Set：工具风险等级 + PermissionGate
# =============================================================================

READ = "read"
WRITE = "write"
DANGER = "danger"

# “权限集”本质上是工具 -> 风险/策略映射。
# 新工具如果忘了加入表，默认 DANGER；这是 deny-first 的最小实现。
TOOL_POLICIES: dict[str, str] = {
    "read_file": READ,
    "write_file": WRITE,
    "edit_file": WRITE,
}


@dataclass
class PermissionDecision:
    allowed: bool
    reason: str


class PermissionGate:
    """工具执行前的系统级裁决点。

    模式：
    - interactive : read 自动放行；write 询问；danger/unknown 拒绝
    - auto-accept : 已知工具自动放行（仍受 workspace 参数边界约束）
    - read-only   : 只允许 read

    为什么 Permission 不写进 Tool handler？
    --------------------------------------
    因为 Permission 是“跨工具统一策略”。若把权限散落到每个工具内部：
    - 容易有工具漏加检查；
    - 模式切换困难；
    - 审计与统一审批困难；
    - Agent Loop 看不到清晰的 allow/deny 控制点。

    为什么 Permission 要在 PreToolUse Hook 之后？
    --------------------------------------------
    因为 Pre Hook 可以改写参数。安全检查必须针对“最终将被执行的参数”，
    而不是模型最初提出但之后已被 Hook 改写的参数。
    """

    VALID_MODES = {"interactive", "auto-accept", "read-only"}

    def __init__(self, mode: str, workspace: Path):
        if mode not in self.VALID_MODES:
            raise ValueError(f"未知 permission mode: {mode}")
        self.mode = mode
        self.workspace = workspace.resolve()

    def _check_workspace_argument(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> Optional[str]:
        """教学增强：参数级的工作区边界检查。

        HelloLLM S04 的最小 PermissionGate 主要按工具等级裁决；这里增加参数级路径检查，
        体现真实业务中 Permission System 不应只看“工具叫什么”，也应检查“要操作哪里”。
        """

        if tool_name not in {"read_file", "write_file", "edit_file"}:
            return None
        raw = arguments.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return "缺少合法 path 参数"
        try:
            _resolve_in_workspace(self.workspace, raw)
        except Exception as exc:
            return str(exc)
        return None

    def decide(self, tool_name: str, arguments: dict[str, Any]) -> PermissionDecision:
        # 1) 未知工具 -> danger -> deny-first
        level = TOOL_POLICIES.get(tool_name, DANGER)
        if level == DANGER:
            return PermissionDecision(False, "未知/危险工具：deny-first 拒绝")

        # 2) 参数级边界：最终参数必须仍在 workspace 内
        workspace_error = self._check_workspace_argument(tool_name, arguments)
        if workspace_error:
            return PermissionDecision(False, workspace_error)

        # 3) 权限模式 × 风险等级
        if self.mode == "read-only":
            if level == READ:
                return PermissionDecision(True, "read-only 模式允许只读工具")
            return PermissionDecision(False, "read-only 模式禁止写操作")

        if self.mode == "auto-accept":
            return PermissionDecision(True, "auto-accept 模式允许已知工具")

        # interactive
        if level == READ:
            return PermissionDecision(True, "只读工具自动允许")

        # WRITE：人类在环审批。
        summary = json.dumps(arguments, ensure_ascii=False)
        if len(summary) > 400:
            summary = summary[:400] + "..."
        print(
            f"\n[permission] 工具 {tool_name} 请求写权限\n"
            f"             参数: {summary}\n"
            "             允许执行？ [y/N] ",
            end="",
            flush=True,
        )
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return PermissionDecision(False, "无法获得用户批准")
        if answer in {"y", "yes"}:
            return PermissionDecision(True, "用户批准")
        return PermissionDecision(False, "用户拒绝")


# =============================================================================
# 4. Hook Mechanism：PreToolUse / PostToolUse 扩展点
# =============================================================================


@dataclass
class PreHookResult:
    allow: bool = True
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: Optional[str] = None


PreHook = Callable[[str, dict[str, Any]], PreHookResult]
PostHook = Callable[[str, dict[str, Any], str], str]
Matcher = Callable[[str], bool]


class HookManager:
    """单文件版 Hook 调度器。

    与 HelloLLM S04 的语义保持一致：
    - PreToolUse：按顺序运行；任一 deny 立即停止；可改写参数；
    - PostToolUse：按顺序运行；可链式改写结果；
    - hook 自身异常：fail-open，只告警，不让治理插件故障卡死主 Agent。

    注意：fail-open 的 Hook 不能承担不可绕过的安全边界。
    强制安全策略应由 PermissionGate / sandbox / OS 隔离承担。
    """

    def __init__(self) -> None:
        self._pre: list[tuple[str, Matcher, PreHook]] = []
        self._post: list[tuple[str, Matcher, PostHook]] = []

    def register_pre(self, name: str, matcher: Matcher, hook: PreHook) -> None:
        self._pre.append((name, matcher, hook))

    def register_post(self, name: str, matcher: Matcher, hook: PostHook) -> None:
        self._post.append((name, matcher, hook))

    def run_pre_tool(self, tool_name: str, arguments: dict[str, Any]) -> PreHookResult:
        current = dict(arguments)
        for hook_name, matcher, hook in self._pre:
            if not matcher(tool_name):
                continue
            try:
                result = hook(tool_name, dict(current))
            except Exception as exc:
                # 对齐“治理插件故障不应导致主循环崩溃”的 fail-open 思路。
                print(
                    f"[hook warning] PreToolUse {hook_name} 失败，fail-open: {exc}",
                    file=sys.stderr,
                )
                continue

            if not result.allow:
                return PreHookResult(
                    allow=False,
                    arguments=current,
                    reason=result.reason or f"{hook_name} 拒绝",
                )
            current = dict(result.arguments)

        return PreHookResult(True, current, None)

    def run_post_tool(
        self, tool_name: str, arguments: dict[str, Any], tool_output: str
    ) -> str:
        current = tool_output
        for hook_name, matcher, hook in self._post:
            if not matcher(tool_name):
                continue
            try:
                current = str(hook(tool_name, dict(arguments), current))
            except Exception as exc:
                print(
                    f"[hook warning] PostToolUse {hook_name} 失败，fail-open: {exc}",
                    file=sys.stderr,
                )
        return current


# ---- 4.1 两个“真实业务逻辑”示例 Hook ------------------------------------------


def governance_pre_hook(tool_name: str, arguments: dict[str, Any]) -> PreHookResult:
    """业务治理示例：保护敏感配置，并限制超大写入。

    这类规则适合 Hook：
    - 项目定制；
    - 可以随团队政策变化；
    - 可被替换/扩展；
    - 并非操作系统级安全边界。
    """

    args = dict(arguments)

    if tool_name in {"write_file", "edit_file"}:
        path = str(args.get("path", ""))
        lowered_parts = {part.lower() for part in Path(path).parts}
        sensitive_names = {".env", ".git", "credentials", "secrets"}
        if lowered_parts & sensitive_names:
            return PreHookResult(
                allow=False,
                arguments=args,
                reason="项目治理 Hook 禁止修改 .env/.git/credentials/secrets",
            )

    if tool_name == "write_file":
        content = args.get("content", "")
        if isinstance(content, str) and len(content) > 1_000_000:
            return PreHookResult(
                allow=False,
                arguments=args,
                reason="项目治理 Hook 拒绝单次写入超过 1,000,000 字符",
            )

    return PreHookResult(True, args, None)


def context_budget_post_hook(
    tool_name: str, arguments: dict[str, Any], output: str
) -> str:
    """上下文治理示例：超长 read_file 结果截断。

    真实 Agent 中，大工具结果直接回填会快速膨胀上下文。
    PostToolUse 是一个自然的结果清洗/截断/脱敏位置。
    """

    if tool_name == "read_file" and len(output) > 30_000:
        omitted = len(output) - 30_000
        return output[:30_000] + f"\n\n[PostToolUse: 已截断 {omitted} 字符]"
    return output


def build_default_hooks() -> HookManager:
    manager = HookManager()
    manager.register_pre(
        "project-governance",
        matcher=lambda name: name in {"write_file", "edit_file"},
        hook=governance_pre_hook,
    )
    manager.register_post(
        "context-budget",
        matcher=lambda name: name == "read_file",
        hook=context_budget_post_hook,
    )
    return manager


# =============================================================================
# 5. Model Provider：OpenAI-compatible SSE Stream（只用标准库）
# =============================================================================


class ModelError(RuntimeError):
    pass


def stream_chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    cfg: Config,
) -> Iterator[dict[str, Any]]:
    """向 OpenAI-compatible /chat/completions 发起真正的 stream=true 请求。

    输出内部事件：
    - text_delta
    - reasoning_delta（若兼容端点提供 reasoning_content）
    - tool_call_delta（工具名/arguments 往往被分成多个 SSE chunk）

    这里的职责只到“把 Provider SSE 翻译成内部事件”，不执行任何工具。
    """

    endpoint = cfg.api_base.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "stream": True,  # 用户要求：必须 stream 模式
    }

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        },
        method="POST",
    )

    try:
        response = urllib.request.urlopen(req, timeout=cfg.timeout)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:2000]
        raise ModelError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ModelError(f"网络错误：{getattr(exc, 'reason', exc)}") from exc
    except TimeoutError as exc:
        raise ModelError(f"请求超时（>{cfg.timeout}s）") from exc

    # SSE: 每行形如  data: {...}\n ，最终 data: [DONE]
    for raw_line in response:
        line = raw_line.decode("utf-8", "replace").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue

        data = line[5:].strip()
        if data == "[DONE]":
            break

        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue

        choices = event.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}

        content = delta.get("content")
        if content:
            yield {"type": "text_delta", "text": content}

        reasoning = delta.get("reasoning_content")
        if reasoning:
            # 不默认打印模型私有/推理内容，仅保留事件类型，示范 Provider 可分流不同增量。
            yield {"type": "reasoning_delta", "text": reasoning}

        for tc in delta.get("tool_calls") or []:
            fn = tc.get("function") or {}
            yield {
                "type": "tool_call_delta",
                "index": int(tc.get("index", 0)),
                "id": tc.get("id") or "",
                "name": fn.get("name") or "",
                "arguments": fn.get("arguments") or "",
            }


# =============================================================================
# 6. Streaming 聚合：边显示文本，边把碎片 ToolCall 还原成完整调用
# =============================================================================


def stream_model_turn(
    messages: list[dict[str, Any]], cfg: Config
) -> ModelTurn:
    """消费一个模型流并聚合成 ModelTurn。

    Tool call 在 SSE 中经常是：
      chunk1: name='write_file', arguments='{"pa'
      chunk2: name='',           arguments='th":"a'
      chunk3: name='',           arguments='.txt",...}'

    所以 Agent Runtime 必须按 index 聚合，而不能每个 chunk 都执行一次工具。
    """

    text_parts: list[str] = []
    partial_calls: dict[int, dict[str, str]] = {}

    for event in stream_chat(messages, TOOLS, cfg):
        et = event["type"]
        if et == "text_delta":
            text = str(event["text"])
            text_parts.append(text)
            print(text, end="", flush=True)  # 真正逐块展示

        elif et == "reasoning_delta":
            # 有些兼容端点会返回 reasoning_content。
            # 本教学版不输出，只演示它与正文事件可以解耦。
            pass

        elif et == "tool_call_delta":
            idx = int(event["index"])
            slot = partial_calls.setdefault(
                idx, {"id": "", "name": "", "arguments": ""}
            )
            # id / name 一般只在第一块出现；arguments 通常分多块。
            slot["id"] += str(event.get("id") or "")
            slot["name"] += str(event.get("name") or "")
            slot["arguments"] += str(event.get("arguments") or "")

    text = "".join(text_parts)
    tool_calls: list[ToolCall] = []

    for idx in sorted(partial_calls):
        item = partial_calls[idx]
        raw_args = item["arguments"] or "{}"
        try:
            parsed = json.loads(raw_args)
            if not isinstance(parsed, dict):
                raise ValueError("arguments 不是 JSON object")
            parse_error = None
        except Exception as exc:
            parsed = {}
            parse_error = f"工具参数 JSON 解析失败：{exc}; raw={raw_args[:500]!r}"

        tool_calls.append(
            ToolCall(
                id=item["id"] or f"call_{idx}",
                name=item["name"],
                arguments=parsed,
                raw_arguments=raw_args,
                parse_error=parse_error,
            )
        )

    return ModelTurn(text=text, tool_calls=tool_calls)


# =============================================================================
# 7. Agent Loop：唯一的“耦合/编排中心”
# =============================================================================


class Agent:
    """最小 Agent Runtime。

    关键点：Agent Loop 不是 Tool/Permission/Hook 本身；它只是把这些模块串起来。
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.permission_gate = PermissionGate(cfg.permission_mode, cfg.workspace)
        self.hooks = build_default_hooks()
        self.messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self._system_prompt(),
            }
        ]

    def _system_prompt(self) -> str:
        return f"""你是一个最小编码 Agent。
当前工作区：{self.cfg.workspace.resolve()}

规则：
1. 需要读取、写入或修改文件时必须调用相应工具，不要虚构文件内容或执行结果。
2. write_file 会完整覆盖文件；只修改局部优先使用 edit_file。
3. 工具可能被 Hook 或 Permission 系统拒绝。若收到拒绝/错误 tool_result，分析原因并调整方案，不要声称已经成功。
4. 所有文件操作只能位于工作区内。
5. 完成任务后直接给出简洁结果。
"""

    @staticmethod
    def _assistant_tool_message(turn: ModelTurn) -> dict[str, Any]:
        """把内部 ToolCall 转回 OpenAI messages 协议。

        必须把 assistant 的 tool_calls 放进历史，随后每个 tool_result 用相同 tool_call_id
        配对；否则下一轮 API 可能直接拒绝消息序列。
        """

        return {
            "role": "assistant",
            "content": turn.text or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.raw_arguments,
                    },
                }
                for tc in turn.tool_calls
            ],
        }

    def _execute_one_tool(self, tc: ToolCall) -> str:
        """单次工具执行流水线。

        这是本文件最重要的业务耦合点：

            model ToolCall
               -> PreToolUse Hook       （项目可编程治理）
               -> PermissionGate        （系统权限裁决）
               -> execute_tool          （真实副作用）
               -> PostToolUse Hook      （结果治理）
               -> tool_result           （回填给模型）

        任何环节拒绝/失败，都转成 tool_result；Agent Loop 不会假装成功。
        """

        print(
            f"\n[tool_use] {tc.name}({tc.raw_arguments[:500]})",
            file=sys.stderr,
        )

        # 0) Provider 层收到了无法解析的 JSON：不能执行，但必须回填结果。
        if tc.parse_error:
            return f"错误：{tc.parse_error}"

        # 1) PreToolUse Hook：可以拒绝或改写参数。
        pre = self.hooks.run_pre_tool(tc.name, tc.arguments)
        if not pre.allow:
            return f"错误：PreToolUse Hook 拒绝：{pre.reason}"

        final_args = pre.arguments

        # 2) Permission Gate：必须检查 Hook 改写后的最终参数。
        decision = self.permission_gate.decide(tc.name, final_args)
        if not decision.allowed:
            return f"错误：Permission 拒绝：{decision.reason}"

        # 3) 真正执行工具。
        result = execute_tool(tc.name, final_args, self.cfg.workspace)

        # 4) PostToolUse Hook：清洗/截断/重写“回填给模型的结果”。
        #    注意：它不能撤销已经发生的文件写入；所以高风险动作必须在执行前拦截。
        result = self.hooks.run_post_tool(tc.name, final_args, result)
        return result

    def run(self, user_text: str) -> str:
        """执行一个用户 turn；内部最多经历 max_turns 次 Model <-> Tool 循环。"""

        self.messages.append({"role": "user", "content": user_text})

        for turn_no in range(1, self.cfg.max_turns + 1):
            print(
                f"\n[agent-loop] iteration {turn_no}/{self.cfg.max_turns}",
                file=sys.stderr,
            )
            print("assistant> ", end="", flush=True)

            # A. MODEL：真·SSE stream。正文 delta 立即打印；工具调用碎片在 Runtime 聚合。
            try:
                model_turn = stream_model_turn(self.messages, self.cfg)
            except ModelError as exc:
                print()
                raise RuntimeError(f"模型调用失败：{exc}") from exc

            if model_turn.text:
                print()  # 收束逐块输出行
            elif model_turn.has_tool_calls:
                print("[请求工具调用]")
            else:
                print("[模型未返回内容]")

            # B. STOP：没有 tool call，说明模型给出了最终文本答案。
            if not model_turn.has_tool_calls:
                self.messages.append(
                    {"role": "assistant", "content": model_turn.text}
                )
                return model_turn.text

            # C. 把模型提出的 action 先记入 history。
            self.messages.append(self._assistant_tool_message(model_turn))

            # D. TOOL PIPELINE：逐个执行。
            #    最小版刻意串行；Claude Code 完整版会把并发安全的读取批量并行，
            #    会产生副作用的写/执行动作串行。
            for tc in model_turn.tool_calls:
                result = self._execute_one_tool(tc)

                preview = result.replace("\n", " ")
                if len(preview) > 220:
                    preview = preview[:220] + "..."
                print(f"[tool_result] {tc.name}: {preview}", file=sys.stderr)

                # E. 回填：无论成功、Hook 拒绝、Permission 拒绝、参数错误，都要闭合 tool call。
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

            # F. continue：messages 现在包含 assistant(tool_calls) + tool results，
            #    下一次模型调用会看到真实执行结果，继续推理。

        msg = f"达到最大 Agent Loop 轮数：{self.cfg.max_turns}，任务被有界终止。"
        print(f"[agent-loop] {msg}", file=sys.stderr)
        return msg


# =============================================================================
# 8. CLI / REPL：接口层，不把 Agent Loop 逻辑写进 UI
# =============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="S04 单文件最小 Agent：stream + tools + permission + hooks + agent-loop"
    )
    parser.add_argument("-p", "--prompt", help="单次无头任务；不传则进入 REPL")
    parser.add_argument("--api-key", default=os.getenv("LLM_API_KEY"))
    parser.add_argument(
        "--api-base",
        default=os.getenv("LLM_API_BASE", "https://api.openai.com/v1"),
        help="OpenAI-compatible API base，例如 https://api.openai.com/v1",
    )
    parser.add_argument("--model", default=os.getenv("LLM_MODEL"))
    parser.add_argument(
        "--workspace",
        default=os.getcwd(),
        help="Agent 可访问的工作区，默认当前目录",
    )
    parser.add_argument(
        "--permission-mode",
        choices=["interactive", "auto-accept", "read-only"],
        default="interactive",
    )
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-turns", type=int, default=10)
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> Config:
    if not args.api_key:
        raise SystemExit("缺少 API Key：请设置 LLM_API_KEY 或传 --api-key")
    if not args.model:
        raise SystemExit("缺少模型名：请设置 LLM_MODEL 或传 --model")

    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    return Config(
        api_key=args.api_key,
        api_base=args.api_base,
        model=args.model,
        workspace=workspace,
        permission_mode=args.permission_mode,
        timeout=args.timeout,
        max_turns=args.max_turns,
    )


def repl(agent: Agent) -> None:
    print(
        "S04 MiniLoop Agent (stream mode)\n"
        "输入 exit / quit 退出。\n"
        f"workspace = {agent.cfg.workspace}\n"
        f"permission = {agent.cfg.permission_mode}\n"
    )

    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            print("bye")
            return
        try:
            agent.run(text)
        except Exception as exc:
            print(f"[error] {exc}", file=sys.stderr)


def main() -> int:
    args = parse_args()
    cfg = build_config(args)
    agent = Agent(cfg)

    if args.prompt is not None:
        try:
            agent.run(args.prompt)
            return 0
        except Exception as exc:
            print(f"[error] {exc}", file=sys.stderr)
            return 1

    repl(agent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

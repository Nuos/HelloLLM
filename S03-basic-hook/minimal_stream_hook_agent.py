#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minimal_stream_hook_agent.py
============================

一个“单文件、零第三方运行时依赖、真正 stream 模式”的最小 Coding Agent。

设计依据
--------
1. HelloLLM / S03-basic-hook
   - hello_llm/query/agent_loop.py
   - hello_llm/hooks/config.py
   - hello_llm/hooks/runner.py
   - hello_llm/hooks/manager.py
   - hello_llm/services/api/claude.py
   - hello_llm/tools/registry.py

2. Claude Code 源码（Nuos/claude-code-claude-code-source-code-full）
   - src/query.ts
   - src/utils/hooks.ts
   - src/services/tools/StreamingToolExecutor.ts
   - src/services/tools/toolOrchestration.ts

3. 论文
   - Dive into Claude Code: The Design Space of Today's and Future AI Agent Systems
     arXiv:2604.14228v2
   - ReAct: Synergizing Reasoning and Acting in Language Models
     arXiv:2210.03629

核心循环
--------
用户 -> LLM(SSE stream) -> tool_call? -> PreToolUse Hook
                                      -> Tool
                                      -> PostToolUse Hook
                                      -> tool_result 回填 LLM
                                      -> 下一轮 LLM
                             无 tool_call -> 输出完成

为什么 Hook 放在 Tool 前后？
----------------------------
Hook 不是“工具本身”，而是 Agent Runtime 的控制/扩展切面：
- PreToolUse：工具真正产生副作用之前，做审核、拒绝、参数改写；
- PostToolUse：工具已经执行之后，但结果还没回填模型之前，做脱敏、审计、
  质量检查、结果增强。

这个文件为了保持“一个 Python 文件”：
- 主进程运行 Agent；
- 同一文件还可被主进程作为 subprocess 再次启动，充当 hook worker；
- 主进程与 hook worker 通过 stdin/stdout JSON 通信。
这与 HelloLLM S03 的 hook runner 思路一致，但这里不用额外 hooks.json / hook 脚本。

运行要求
--------
Python 3.10+，仅标准库。

配置文件（沿用 HelloLLM S03 的形式）：
~/.hellollm/config.json

示例：
{
  "api_key": "sk-...",
  "api_base": "https://api.deepseek.com",
  "model": "deepseek-v4-flash",
  "timeout": 120
}

也可以临时通过命令行覆盖：
python3 minimal_stream_hook_agent.py \
  --api-key "sk-..." \
  --api-base "https://api.deepseek.com" \
  --model "deepseek-v4-flash" \
  -p "读取 README.md 并总结"

交互模式：
python3 minimal_stream_hook_agent.py

退出：
/exit

重要边界
--------
1. Hook 不是 OS Sandbox。
2. 本示例的写入范围由 Tool Executor 自身再次限制在 --root 工作区内；
   即使 Hook 故障并按 S03 的 fail-open 继续，工具执行层仍有路径边界。
3. 生产环境不应只依赖 Prompt 或 Hook 来做安全隔离。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional


# ============================================================================
# 0. 基本配置
# ============================================================================

DEFAULT_CONFIG = Path.home() / ".hellollm" / "config.json"
MAX_READ_BYTES = 200_000
HOOK_TIMEOUT_SECONDS = 5
MAX_HOOK_OUTPUT_CHARS = 100_000

SYSTEM_PROMPT = """你是一个最小 Coding Agent。
你可以读取、写入和定点编辑当前工作区中的文本文件。
规则：
1. 需要知道文件真实内容时先调用 read_file，不要猜。
2. 修改局部内容优先 edit_file；完整创建/覆盖用 write_file。
3. 工具失败时阅读 tool_result，调整参数后重试。
4. 不要虚构已经执行过的工具结果。
5. 最终用简洁中文汇报你实际完成了什么。
"""

'''
  user:
    @dataclass, 究竟是什么含义？为了解决什么问题？
     数据类，类似纯数据结构类 C/C++ 中的struct 关键字吗？
     不准其有自己的 成员方法接口？    
'''
@dataclass
class ModelConfig:
    """OpenAI-compatible Chat Completions 配置。"""

    api_key: str
    api_base: str
    model: str
    timeout: int = 120


def load_model_config(args: argparse.Namespace) -> ModelConfig:
    """读取 ~/.hellollm/config.json，再由命令行参数覆盖。"""
    data: dict[str, Any] = {}
    config_path = Path(args.config).expanduser()

    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise SystemExit(f"[配置错误] 无法读取 {config_path}: {e}")

    api_key = args.api_key or data.get("api_key")
    api_base = args.api_base or data.get("api_base") or "https://api.deepseek.com"
    model = args.model or data.get("model") or "deepseek-v4-flash"
    timeout = args.timeout or int(data.get("timeout", 120))

    if not api_key:
        raise SystemExit(
            "[配置错误] 缺少 api_key。\n"
            f"请创建 {config_path}，例如：\n"
            '{"api_key":"sk-...","api_base":"https://api.deepseek.com",'
            '"model":"deepseek-v4-flash","timeout":120}'
        )

    return ModelConfig(
        api_key=str(api_key),
        api_base=str(api_base).rstrip("/"),
        model=str(model),
        timeout=int(timeout),
    )


# ============================================================================
# 1. Tool Schema：给模型看的“可行动能力说明”
# ============================================================================

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取当前工作区内的文本文件。需要知道文件真实内容时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对工作区根目录的文件路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "在当前工作区内创建或完整覆盖文本文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "把文件中第一处完全匹配的 old_string 替换为 new_string。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
]


# ============================================================================
# 2. Tool Executor：真正产生本地副作用的执行层
# ============================================================================

def resolve_in_workspace(path: str, workspace_root: Path) -> Path:
    """
    把模型给出的路径限制在 workspace_root 内。

    注意：
    这是“执行层硬约束”，不是 Hook。
    安全边界应放在真正执行副作用的位置；Hook 只是额外控制切面。

    user：
      该函数用于限制/规定/约束，工具/命令执行的操作目录/空间吗？
    """
    root = workspace_root.resolve()
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = root / p
    p = p.resolve()

    try:
        p.relative_to(root)
    except ValueError:
        raise PermissionError(f"路径越界：{path} 不在工作区 {root} 内")

    return p


def read_file(path: str, workspace_root: Path) -> str:
    p = resolve_in_workspace(path, workspace_root)
    if not p.exists():
        return f"错误：文件不存在：{path}"
    if p.is_dir():
        return f"错误：{path} 是目录"

    data = p.read_bytes()
    if b"\x00" in data[:8192]:
        return f"错误：{path} 可能是二进制文件"

    text = data.decode("utf-8", "replace")
    if len(data) > MAX_READ_BYTES:
        text = text[:MAX_READ_BYTES] + "\n…（文件过大，已截断）"
    return text


def write_file(path: str, content: str, workspace_root: Path) -> str:
    p = resolve_in_workspace(path, workspace_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {len(content.encode('utf-8'))} 字节：{p.relative_to(workspace_root.resolve())}"


def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    workspace_root: Path,
) -> str:
    p = resolve_in_workspace(path, workspace_root)
    if not p.exists():
        return f"错误：文件不存在：{path}"

    text = p.read_text(encoding="utf-8")
    if old_string not in text:
        return "错误：未找到完全匹配的 old_string；请先重新读取文件核对。"

    count = text.count(old_string)
    p.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
    return f"已替换第 1/{count} 处匹配：{p.relative_to(workspace_root.resolve())}"


def execute_tool(name: str, arguments: dict[str, Any], workspace_root: Path) -> str:
    """
    Tool Registry + Executor 的最小合并版。

    工具异常不让 Agent Loop 崩溃，而是转成 tool_result 再回填模型。
    这就是 ReAct 中“Action -> Observation -> 下一步推理”的工程实现。
    """
    try:
        if name == "read_file":
            return read_file(
                path=str(arguments["path"]),
                workspace_root=workspace_root,
            )
        if name == "write_file":
            return write_file(
                path=str(arguments["path"]),
                content=str(arguments["content"]),
                workspace_root=workspace_root,
            )
        if name == "edit_file":
            return edit_file(
                path=str(arguments["path"]),
                old_string=str(arguments["old_string"]),
                new_string=str(arguments["new_string"]),
                workspace_root=workspace_root,
            )
        return f"错误：未知工具 {name}"
    except Exception as e:
        return f"错误：工具 {name} 执行失败：{type(e).__name__}: {e}"


# ============================================================================
# 3. Hook：Agent Runtime 的“工具前/后控制切面”
# ============================================================================

@dataclass(frozen=True)
class HookRule:
    """
    最小 Hook 规则。

    event:
        PreToolUse  = 工具执行前
        PostToolUse = 工具执行后
    matcher:
        空字符串 = 匹配所有工具；
        否则采用 S03 教学版同样的“工具名包含 matcher”逻辑。
    command:
        外部 hook 命令 argv。

    user：
      这似乎也是一个纯粹的 数据结构类，用于将多个不同数据类型组合/捆绑在一起，用于传递/返回数据的一种自定义结构协议？？？
      但是为什么 HookRule 是一种数据结构呢？？
    """

    event: str
    matcher: str
    command: tuple[str, ...]


def match_hook(rule: HookRule, tool_name: str) -> bool:
    """
      user: 解释注释？？
    """
    return not rule.matcher or rule.matcher in tool_name


def build_hook_payload(
    tool_name: str,
    arguments: dict[str, Any],
    workspace_root: Path,
    tool_output: Optional[str] = None,
) -> dict[str, Any]:
    """
    Hook stdin JSON。

    S03 最小字段：tool_name / arguments / tool_output。
    这里额外加入 workspace_root，让“保护路径”这个真实业务 hook
    可以独立判断目标是否越过工作区。

    user：
      payload 是什么含义，为什么http请求的时候，也会有这个 单词？详细解释一下？ 为LLM选择的工具，提供运行之前的信息准备（类似在http请求之前的操作）？
      payload在此处，是一个字典列表，tag-字符串、value-函数名/参数列表/路径名 ？？？是这样的对应关系吗？？
    """
    payload: dict[str, Any] = {
        "tool_name": tool_name,
        "arguments": arguments,
        "workspace_root": str(workspace_root.resolve()),
    }
    if tool_output is not None:
        payload["tool_output"] = tool_output
    return payload


def run_hook_command(command: tuple[str, ...], payload: dict[str, Any]) -> dict[str, Any]:
    """
    subprocess + stdin/stdout JSON：S03 Hook Runner 的单文件版本。

    与 S03 的差别：
    - S03 为了允许任意 shell 语法使用 shell=True；
    - 本例直接传 argv，不经过 shell，减少命令字符串注入面。

    故障策略：
    - 保留 S03 教学版的 fail-open：hook 自己失败时返回 decision=error；
    - HookManager 对 error 不做 deny。
    - 但真正的 Tool Executor 仍有 workspace 路径硬限制，所以安全边界不依赖 hook。

    user：
      subprocess，似乎是一种新模块、新方法；在子进程中运行命令/工具？？并返回执行结果？
      似乎需要如下参数执行 command命令 +输入数据（工具名称、参数列表、执行目录空间）+ 是否捕获输出、返回文本、超时限制？？
    """
    try:
        proc = subprocess.run(
            list(command),
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"decision": "error", "reason": f"hook 启动/超时失败：{e}"}

    if proc.returncode != 0:
        return {
            "decision": "error",
            "reason": f"hook 退出码 {proc.returncode}: {proc.stderr[:300]}",
        }

    if len(proc.stdout) > MAX_HOOK_OUTPUT_CHARS:
        return {"decision": "error", "reason": "hook stdout 超限"}

    try:
        out = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        return {"decision": "error", "reason": f"hook stdout 不是 JSON：{e}"}

    if not isinstance(out, dict):
        return {"decision": "error", "reason": "hook 输出必须是 JSON object"}

    return out


class HookManager:
    """
    对应 HelloLLM hooks/manager.py 的最小调度器。

    PRE：
        model tool_call
             ↓
        匹配 PreToolUse rules
             ↓
        deny ? ── yes ──> 不执行工具，把拒绝原因作为 tool_result 回给模型
             │
             no
             ↓
        updatedInput ? ──> 合并参数
             ↓
        真正执行 Tool

    POST：
        Tool 原始结果
             ↓
        匹配 PostToolUse rules
             ↓
        updatedOutput ? ──> 改写
             ↓
        最终 tool_result 回填模型

    为什么“回填模型”非常关键？
    Hook 的决定不是只给 UI 看。拒绝、改写、脱敏后的结果进入下一轮上下文，
    LLM 才能依据真实 Observation 改变下一步 Action。

    user：
      这是一个纯粹的空壳管理器类，没有自己的成员变量吗？？
    """

    def __init__(self, rules: list[HookRule]):
        self.rules = rules

    def run_pre_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        workspace_root: Path,
    ) -> dict[str, Any]:
        """
          user:
            结合真实业务逻辑代码，补充函数功能、使用、参数注释。
            分别针对本函数中各个   ***代码块***   进行，注释解释！！
        """
        final_args = dict(arguments)

        for rule in self.rules:
            if rule.event != "PreToolUse" or not match_hook(rule, tool_name):
                """
                  user:
                   rule.event!="PreToolUse", 判断当前 hook节点位置，是否对应 PreToolUse?
                     如果 工具注册Hook的标签tag/event != PreToolUse 则继续执行
                       或者 没有匹配到 ？
                       详细注释说明，结合真实业务逻辑。。。。
                       
                """
                continue

            # user: 执行 工具。
            result = run_hook_command(
                rule.command,
                build_hook_payload(tool_name, final_args, workspace_root),
            )

            print(
                f"\n[hook:PreToolUse] {tool_name} -> {result.get('decision', 'allow')}",
                file=sys.stderr,
                flush=True,
            )

            if result.get("decision") == "deny":
                return {
                    "allow": False,
                    "arguments": final_args,
                    "reason": result.get("reason", "被 PreToolUse hook 拒绝"),
                }

            # 多个 hook 串联时，后面的 hook 看到前面已经改写过的参数。
            updated = result.get("updatedInput")
            if isinstance(updated, dict):
                final_args.update(updated)

            # decision=error：按 S03 的 fail-open 继续。
            if result.get("decision") == "error":
                print(
                    f"[hook warning] fail-open: {result.get('reason')}",
                    file=sys.stderr,
                    flush=True,
                )

        return {"allow": True, "arguments": final_args, "reason": None}

    def run_post_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_output: str,
        workspace_root: Path,
    ) -> str:
        """
          user: 
            分别针对本函数中各个代码块进行，注释解释！！
        """
        final_output = tool_output

        for rule in self.rules:
            if rule.event != "PostToolUse" or not match_hook(rule, tool_name):
                continue

            result = run_hook_command(
                rule.command,
                build_hook_payload(
                    tool_name,
                    arguments,
                    workspace_root,
                    tool_output=final_output,
                ),
            )

            print(
                f"\n[hook:PostToolUse] {tool_name} -> {result.get('decision', 'allow')}",
                file=sys.stderr,
                flush=True,
            )

            updated = result.get("updatedOutput")
            if updated is not None:
                final_output = str(updated)

            if result.get("decision") == "error":
                print(
                    f"[hook warning] fail-open: {result.get('reason')}",
                    file=sys.stderr,
                    flush=True,
                )

        return final_output


# ============================================================================
# 4. 同一 Python 文件兼任 Hook Worker
# ============================================================================

SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
}

SECRET_LINE_RE = re.compile(
    r"""(?ix)
    \b(
        api[_-]?key |
        access[_-]?token |
        auth[_-]?token |
        password |
        passwd |
        secret
    )\b
    \s*[:=]\s*
    ([^\s,;]+)
    """
)
TOKEN_RE = re.compile(r"\b(?:sk|ghp|glpat|xox[baprs])[-_A-Za-z0-9]{12,}\b")


def hook_worker_protect_write(payload: dict[str, Any]) -> dict[str, Any]:
    """
    真实业务示例 A：PreToolUse 写入保护。

    用途：
    - 禁止模型写 .git；
    - 禁止直接覆盖常见凭据/私钥文件；
    - 禁止工作区外路径。

    这类 Hook 适合做：
    permission policy / compliance / protected-path guard / approval gate。
    """
    args = dict(payload.get("arguments") or {})
    path_text = str(args.get("path", ""))
    root = Path(str(payload["workspace_root"])).resolve()

    p = Path(path_text).expanduser()
    if not p.is_absolute():
        p = root / p
    p = p.resolve()

    try:
        rel = p.relative_to(root)
    except ValueError:
        return {
            "decision": "deny",
            "reason": f"禁止写出工作区：{path_text}",
        }

    if ".git" in rel.parts:
        return {
            "decision": "deny",
            "reason": "禁止通过普通文件工具直接修改 .git 内部数据",
        }

    if p.name in SENSITIVE_NAMES or p.suffix.lower() in SENSITIVE_SUFFIXES:
        return {
            "decision": "deny",
            "reason": f"受保护的敏感文件：{rel}",
        }

    # 这里演示 updatedInput：把 "./a/../b.txt" 之类路径正规化成工作区相对路径。
    normalized = str(rel)
    return {
        "decision": "allow",
        "updatedInput": {"path": normalized},
    }


def hook_worker_redact_read(payload: dict[str, Any]) -> dict[str, Any]:
    """
    真实业务示例 B：PostToolUse 输出脱敏。

    工具已经读取了文件，但在内容进入 LLM Context 之前遮盖常见 secret。
    这正是 PostToolUse 的典型位置：
        Tool 原始输出 != 必须直接进入模型的最终 Observation。
    """
    output = str(payload.get("tool_output", ""))

    def repl(m: re.Match[str]) -> str:
        return f"{m.group(1)}=<REDACTED_BY_POST_TOOL_HOOK>"

    output = SECRET_LINE_RE.sub(repl, output)
    output = TOKEN_RE.sub("<REDACTED_TOKEN>", output)

    return {
        "decision": "allow",
        "updatedOutput": output,
    }


def run_hook_worker(name: str) -> int:
    """
    Hook 子进程入口。
    stdin 读 JSON，stdout 只打印一个 JSON object，便于父进程稳定解析。
    """
    try:
        payload = json.loads(sys.stdin.read() or "{}")

        if name == "protect-write":
            result = hook_worker_protect_write(payload)
        elif name == "redact-read":
            result = hook_worker_redact_read(payload)
        else:
            result = {"decision": "error", "reason": f"未知 hook worker: {name}"}

        sys.stdout.write(json.dumps(result, ensure_ascii=False))
        sys.stdout.flush()
        return 0
    except Exception as e:
        # hook runner 将非 0 退出码视作 hook 自身错误；主 Agent 按 fail-open 处理。
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 2


def default_hook_manager() -> HookManager:
    """
    仍然只有一个 .py 文件：
    主进程启动“自己”作为独立 Hook subprocess。
    """
    me = str(Path(__file__).resolve())
    python = sys.executable

    return HookManager(
        rules=[
            HookRule(
                event="PreToolUse",
                matcher="write_file",
                command=(python, me, "--hook-worker", "protect-write"),
            ),
            HookRule(
                event="PreToolUse",
                matcher="edit_file",
                command=(python, me, "--hook-worker", "protect-write"),
            ),
            HookRule(
                event="PostToolUse",
                matcher="read_file",
                command=(python, me, "--hook-worker", "redact-read"),
            ),
        ]
    )


# ============================================================================
# 5. Model Stream：OpenAI-compatible SSE，零第三方依赖
# ============================================================================

class ModelError(RuntimeError):
    pass


def stream_chat(
    messages: list[dict[str, Any]],
    cfg: ModelConfig,
) -> Iterator[dict[str, Any]]:
    """
    真正流式请求：
        POST {api_base}/chat/completions
        "stream": true

    每收到一个 SSE data: 块就立刻 yield，不先聚合完整答案。

    只暴露两类我们需要的增量：
    - text_delta
    - tool_call_delta

    某些推理模型还会返回 reasoning_content。
    本最小示例不把隐藏推理过程打印到终端，也不需要它来执行 Tool Loop。
    """
    payload = {
        "model": cfg.model,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "stream": True,
    }

    request = urllib.request.Request(
        f"{cfg.api_base}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    try:
        response = urllib.request.urlopen(request, timeout=cfg.timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:1000]
        raise ModelError(f"HTTP {e.code}: {body}") from e
    except (urllib.error.URLError, OSError) as e:
        if isinstance(e, (socket.timeout, TimeoutError)):
            raise ModelError(f"请求超时（>{cfg.timeout}s）") from e
        raise ModelError(f"网络错误：{getattr(e, 'reason', e)}") from e

    with response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
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

            if delta.get("content"):
                yield {
                    "type": "text_delta",
                    "text": str(delta["content"]),
                }

            for tc in delta.get("tool_calls") or []:
                fn = tc.get("function") or {}
                yield {
                    "type": "tool_call_delta",
                    "index": int(tc.get("index", 0)),
                    "id": str(tc.get("id") or ""),
                    "name": str(fn.get("name") or ""),
                    "arguments": str(fn.get("arguments") or ""),
                }


def consume_model_stream(
    messages: list[dict[str, Any]],
    cfg: ModelConfig,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    一边把 text_delta 立即打印给用户，一边只为协议需要聚合 tool_call delta。

    OpenAI-compatible stream 中，一个 function call 的 arguments 通常会被拆成很多块：
        {"path":
        "README"
        ".md"}
    所以必须按 index 累积后才能 json.loads。
    """
    text_parts: list[str] = []
    pending: dict[int, dict[str, str]] = {}

    for event in stream_chat(messages, cfg):
        if event["type"] == "text_delta":
            text = event["text"]
            text_parts.append(text)
            print(text, end="", flush=True)

        elif event["type"] == "tool_call_delta":
            index = event["index"]
            slot = pending.setdefault(
                index,
                {"id": "", "name": "", "arguments": ""},
            )
            if event["id"]:
                slot["id"] += event["id"]
            if event["name"]:
                slot["name"] += event["name"]
            slot["arguments"] += event["arguments"]

    tool_calls: list[dict[str, Any]] = []
    for index in sorted(pending):
        tc = pending[index]
        tool_calls.append(
            {
                "id": tc["id"] or f"call_{index}",
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": tc["arguments"] or "{}",
                },
            }
        )

    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts) or None,
    }
    if tool_calls:
        assistant_message["tool_calls"] = tool_calls

    return assistant_message, tool_calls


# ============================================================================
# 6. Agent Loop：Model -> Hook -> Tool -> Hook -> Observation -> Model
# ============================================================================

def run_agent_turn(
    messages: list[dict[str, Any]],
    cfg: ModelConfig,
    workspace_root: Path,
    hooks: HookManager,
    max_turns: int,
) -> None:
    """
    单个用户回合内部可能有多次 LLM 调用。

    这就是论文/Claude Code 里最值得先理解的核心：
        while not stopped:
            action = model(...)
            if no tool:
                stop
            observation = execute_tool(...)
            history += action + observation

    S03 只在 execute_tool 前后增加了两个 Hook 扩展点。
    """
    for turn in range(1, max_turns + 1):
        print(
            f"\n[agent] turn {turn}/{max_turns} | model={cfg.model}",
            file=sys.stderr,
            flush=True,
        )

        assistant_message, tool_calls = consume_model_stream(messages, cfg)
        messages.append(assistant_message)

        # 主要停止条件：模型本轮没有请求工具。
        if not tool_calls:
            print()
            return

        # 如果模型一边输出文本一边调用工具，工具状态另走 stderr，避免破坏正文流。
        print(file=sys.stderr)

        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            raw_args = tc["function"]["arguments"]

            try:
                arguments = json.loads(raw_args)
                if not isinstance(arguments, dict):
                    """
                     user:
                       含义是：LLM输出的函数调用格式 不符合协议/即不是JSON-object ？

                       此处这个 try...exception...模块为何不在 consume_model_stream 函数接口中处理完成呢？？
                    """
                    raise ValueError("工具 arguments 必须是 JSON object")
            except (json.JSONDecodeError, ValueError) as e:
                result = f"错误：工具参数 JSON 无效：{e}; raw={raw_args[:300]}"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    }
                )
                continue

            print(
                f"[tool proposed] {tool_name}({json.dumps(arguments, ensure_ascii=False)[:240]})",
                file=sys.stderr,
                flush=True,
            )

            # ----------------------------------------------------------------
            # Hook Point 1: PreToolUse
            # ----------------------------------------------------------------
            pre = hooks.run_pre_tool(
                tool_name=tool_name,          # user: 工具 name
                arguments=arguments,          # user: 参数 执行工具所需要传递的参数/input
                workspace_root=workspace_root,# user: 执行目录/环境 Execution-Environment 
            )

            if not pre["allow"]:
                # 关键：拒绝不是让整个 Agent 崩掉。
                # 它成为 Observation 回给模型，模型下一轮可选择别的路径。
                result = f"HOOK_DENIED: {pre['reason']}"
                final_arguments = pre["arguments"]
                print(
                    f"[tool blocked] {tool_name}: {pre['reason']}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                final_arguments = pre["arguments"]

                # 真正的副作用发生在这里。
                raw_result = execute_tool(
                    tool_name,
                    final_arguments,
                    workspace_root,
                )

                # ------------------------------------------------------------
                # Hook Point 2: PostToolUse
                # ------------------------------------------------------------
                result = hooks.run_post_tool(
                    tool_name=tool_name,
                    arguments=final_arguments,
                    tool_output=raw_result,
                    workspace_root=workspace_root,
                )

                print(
                    f"[tool done] {tool_name} -> {len(result)} chars",
                    file=sys.stderr,
                    flush=True,
                )

            # 最终 Observation 回填模型。
            # 注意是“经过 PostToolUse 后的 result”，不是原始工具输出。
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )

    print(
        f"\n[agent] 达到最大工具循环轮数 {max_turns}，本回合停止。",
        file=sys.stderr,
    )


# ============================================================================
# 7. CLI / REPL
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="单文件 Stream + Hook 最小 Coding Agent"
    )
    parser.add_argument("-p", "--prompt", help="无头单次提示词；不传则进入 REPL")
    parser.add_argument(
        "--root",
        default=".",
        help="Agent 可访问的工作区根目录，默认当前目录",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"配置文件，默认 {DEFAULT_CONFIG}",
    )
    parser.add_argument("--api-key")
    parser.add_argument("--api-base")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--max-turns", type=int, default=8)

    # 内部模式：同一文件被父 Agent 作为 hook subprocess 启动。
    parser.add_argument("--hook-worker", help=argparse.SUPPRESS)

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Hook 子进程必须最先分流；它不需要 API key，也不能再进入 Agent。
    if args.hook_worker:
        return run_hook_worker(args.hook_worker)

    cfg = load_model_config(args)
    workspace_root = Path(args.root).expanduser().resolve()
    if not workspace_root.exists() or not workspace_root.is_dir():
        raise SystemExit(f"[参数错误] --root 不是目录：{workspace_root}")

    hooks = default_hook_manager()

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
            + f"\n当前工作区根目录：{workspace_root}\n"
            + "文件工具的 path 请使用相对该根目录的路径。",
        }
    ]

    print(f"[workspace] {workspace_root}", file=sys.stderr)
    print("[mode] model streaming = ON; hooks = ON", file=sys.stderr)

    if args.prompt:
        messages.append({"role": "user", "content": args.prompt})
        run_agent_turn(
            messages,
            cfg,
            workspace_root,
            hooks,
            max_turns=args.max_turns,
        )
        return 0

    # 最小 REPL：同一 messages 列表持续保存上下文。
    while True:
        try:
            user_text = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not user_text:
            continue
        if user_text in {"/exit", "/quit", "exit", "quit"}:
            return 0

        messages.append({"role": "user", "content": user_text})

        try:
            run_agent_turn(
                messages,
                cfg,
                workspace_root,
                hooks,
                max_turns=args.max_turns,
            )
        except ModelError as e:
            print(f"\n[模型错误] {e}", file=sys.stderr)
        except KeyboardInterrupt:
            print("\n[已中止本轮]", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S06-basic-ee 单文件最小 Agent
================================

目标
----
把 HelloLLM / S06-basic-ee 里分散在多个模块的关键职责，压缩成一个
“单文件可运行”的最小 Agent：

- stream 模式：边生成边输出文本
- Agent-loop：模型 → 工具 → 结果回填 → 下一轮
- Execution Environment：把 shell 命令真正放到受控执行环境里跑
- 依赖尽量少：仅使用 Python 标准库

对应 HelloLLM / S06-basic-ee 的模块思路
----------------------------------------
- `services/api/claude.py`：SSE 流式客户端
- `services/api/client.py`：流事件聚合
- `query/agent_loop.py`：Agent-loop 生成器
- `tools/registry.py`：工具注册与分发
- `tools/bash_tool.py`：bash 工具
- `ee/config.py` / `ee/policy.py` / `ee/runner.py`：Execution Environment

Execution Environment 的作用
-----------------------------
它不负责“思考”，只负责“执行”：

1. 统一命令执行参数
   - cwd：命令在哪个目录运行
   - env：带哪些环境变量
   - timeout：最多跑多久

2. 统一命令语义
   - read / search / neutral：只读、可安全放行
   - write：会修改文件系统
   - other：保守分类

3. 统一执行后果
   - 收集 stdout / stderr / exit code
   - 超时与 OSError 变成结构化结果
   - 结果回填给 Agent-loop，供下一轮模型继续推理

在 Agent-loop 中如何介入
------------------------
模型一旦发出 `bash` 工具调用：

    模型输出 tool_call
        ↓
    tool registry 分发到 bash()
        ↓
    bash() 调用 ExecutionEnvironment.run_command()
        ↓
    subprocess 真正执行 shell
        ↓
    CommandResult 回填为 tool message
        ↓
    下一轮模型读取 tool result 再继续推理

这样 Execution Environment 就是“模型意图”与“机器执行”之间的边界层。

运行方式
--------
1) 准备配置文件（推荐）：

    mkdir -p ~/.hellollm
    cat > ~/.hellollm/config.json <<'EOF'
    {
      "api_key": "sk-...",
      "api_base": "https://api.deepseek.com",
      "model": "deepseek-v4-flash",
      "timeout": 120
    }
    EOF

2) 运行：

    python3 s06_basic_ee_min_agent.py -p "请列出当前目录下的文件，并解释这个项目做什么"

也可以不带 `-p`，程序会从 stdin 或交互输入读取一次。

说明
----
- 本脚本默认使用 OpenAI 兼容的 `/chat/completions` + `stream=True`
- 如果你的模型端不支持 tool_calls，这个最小版仍然可以做纯文本流式对话
- bash 工具会直接走 Execution Environment；文件工具直接读写本地文件
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional


# =============================================================================
# 基础数据结构
# =============================================================================

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class ModelError(RuntimeError):
    def __init__(self, message: str, kind: str = "unknown"):
        super().__init__(message)
        self.kind = kind


class ConfigError(RuntimeError):
    pass


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration: float = 0.0


# =============================================================================
# 模型配置
# =============================================================================

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.hellollm/config.json")
DEFAULT_API_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_SYSTEM_PROMPT = (
    "You are HelloLLM, a minimal coding agent rebuilt from the architecture "
    "of Claude Code. Be concise. When a task needs local file access or shell "
    "execution, use tools. Prefer read/search before write."
)
DEFAULT_MAX_CONTEXT_CHARS = 30_000
DEFAULT_MAX_TURNS = 10
DEFAULT_TIMEOUT = 120.0
MAX_TIMEOUT = 600
MAX_NETWORK_RETRIES = 3


def load_json_file(path: str) -> dict[str, Any]:
    p = Path(path).expanduser()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"无法读取配置文件 {p}: {exc}") from exc


@dataclass
class ModelConfig:
    api_base: str = ""
    api_key: str = ""
    model: str = ""
    timeout: Optional[float] = None
    max_tokens: Optional[int] = None
    file_config: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        fc = self.file_config or {}
        self.api_base = (self.api_base or fc.get("api_base") or DEFAULT_API_BASE).rstrip("/")
        self.api_key = self.api_key or fc.get("api_key") or ""
        self.model = self.model or fc.get("model") or DEFAULT_MODEL
        if self.timeout is None:
            self.timeout = float(fc.get("timeout", DEFAULT_TIMEOUT))
        if self.max_tokens is None and fc.get("max_tokens") is not None:
            self.max_tokens = int(fc["max_tokens"])

    def require_api_key(self) -> None:
        if not self.api_key:
            raise ConfigError(
                "未配置 API Key。\n"
                f"推荐在 {DEFAULT_CONFIG_PATH} 中写入 JSON 配置，或使用 --api-key 临时覆盖。"
            )


def build_config(args: argparse.Namespace) -> ModelConfig:
    file_cfg = load_json_file(args.config or DEFAULT_CONFIG_PATH)
    cfg = ModelConfig(
        api_base=args.api_base or os.getenv("LLM_BASE_URL", ""),
        api_key=args.api_key or os.getenv("LLM_API_KEY", "") or os.getenv("OPENAI_API_KEY", ""),
        model=args.model or os.getenv("LLM_MODEL", ""),
        timeout=args.timeout,
        file_config=file_cfg,
    )
    return cfg


# =============================================================================
# Execution Environment
# =============================================================================

class ExecutionEnvironment:
    """
    受控执行环境：
    - 负责命令执行，不负责命令决策
    - 统一 cwd / env / timeout / 结果结构
    - 保留“命令语义分类”，供后续权限层复用
    """

    READ_COMMANDS = frozenset({
        "cat", "head", "tail", "less", "more",
        "wc", "diff", "cmp", "sort", "uniq", "cut", "paste", "nl",
    })
    SEARCH_COMMANDS = frozenset({
        "find", "grep", "rg", "ag", "ack", "locate", "which", "whereis",
    })
    SILENT_COMMANDS = frozenset({
        "mv", "cp", "rm", "mkdir", "rmdir", "chmod", "chown", "chgrp",
        "touch", "ln", "cd", "export", "unset", "wait",
    })
    NEUTRAL_COMMANDS = frozenset({
        "echo", "printf", "true", "false", ":",
    })

    def resolve_cwd(self, cwd: Optional[str] = None) -> str:
        # user： 这函数什么含义？ 完整的执行路径嘛？
        if cwd is None:
            return str(Path.cwd())
        return str(Path(cwd).expanduser().resolve())

    def merge_env(self, extra: Optional[dict[str, Any]] = None) -> dict[str, str]:
        # user：将args 格式转为 字典/dict 格式吗？
        env = dict(os.environ)
        if extra:
            for key, value in extra.items():
                env[key] = str(value)
        return env

    def clamp_timeout(self, timeout: Optional[Any] = None) -> int:
        # user： 超时约束吗？
        if timeout is None:
            return int(DEFAULT_TIMEOUT)
        value = int(timeout)
        if value < 1:
            return 1
        if value > MAX_TIMEOUT:
            return MAX_TIMEOUT
        return value

    def command_semantics(self, command: Optional[str]) -> str:
        # user：语义分析，是固定格式解析功能语义吗？
        if not command:
            return "empty"
        tokens = command.strip().split()
        if not tokens:
            return "empty"
        base = tokens[0].split("/")[-1]
        if base in self.READ_COMMANDS:
            return "read"
        if base in self.SEARCH_COMMANDS:
            return "search"
        if base in self.SILENT_COMMANDS:
            # 会修改文件系统，归为 write
            return "write"
        if base in self.NEUTRAL_COMMANDS:
            return "neutral"
        return "other"

    def is_read_only(self, command: Optional[str]) -> bool:
        semantics = self.command_semantics(command)
        return semantics in ("read", "search", "neutral")

    def run_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        env: Optional[dict[str, Any]] = None,
        timeout: Optional[Any] = None,
    ) -> CommandResult:
        """
        真正的执行边界：
        - tool 只把 command 传进来
        - ExecutionEnvironment 负责选择 cwd/env/timeout
        - subprocess 负责真正跑 shell
        """
        start = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.resolve_cwd(cwd),
                env=self.merge_env(env),
                capture_output=True,
                text=True,
                timeout=self.clamp_timeout(timeout),
            )
            return CommandResult(
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration=time.monotonic() - start,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = ""
            stderr = ""
            if isinstance(exc.stdout, bytes):
                stdout = exc.stdout.decode("utf-8", "replace")
            elif exc.stdout:
                stdout = str(exc.stdout)
            if isinstance(exc.stderr, bytes):
                stderr = exc.stderr.decode("utf-8", "replace")
            elif exc.stderr:
                stderr = str(exc.stderr)
            return CommandResult(
                exit_code=-1,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                duration=time.monotonic() - start,
            )
        except OSError as exc:
            return CommandResult(
                exit_code=-2,
                stdout="",
                stderr=str(exc),
                duration=time.monotonic() - start,
            )


# user：这是声明定义了一个全局、单例 ExecutionEnvironment()对象吗？
EE = ExecutionEnvironment()


# =============================================================================
# 文件工具
# =============================================================================

def read_file(path: str) -> str:
    p = Path(path).expanduser()
    try:
        data = p.read_bytes()
    except Exception as exc:
        return f"错误：无法读取文件 {p}: {exc}"
    if b"\x00" in data[:4096]:
        return f"错误：{p} 看起来是二进制文件，拒绝直接读取。"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"错误：{p} 不是 UTF-8 文本文件。"
    # 最小版：避免一次性把超大文件完全灌进上下文
    max_chars = 20_000
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n[截断：原始长度 {len(text)} 字符]"
    return text


def write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入：{p}"
    except Exception as exc:
        return f"错误：写入文件失败 {p}: {exc}"


def edit_file(path: str, old_string: str, new_string: str) -> str:
    p = Path(path).expanduser()
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as exc:
        return f"错误：无法读取待编辑文件 {p}: {exc}"
    if old_string not in text:
        return f"错误：文件中找不到要替换的旧字符串。{p}"
    new_text = text.replace(old_string, new_string, 1)
    try:
        p.write_text(new_text, encoding="utf-8")
        return f"已编辑：{p}"
    except Exception as exc:
        return f"错误：写回文件失败 {p}: {exc}"


# =============================================================================
# bash 工具：Execution Environment 的入口
# =============================================================================

def bash(command: str, cwd: Optional[str] = None, timeout: Optional[Any] = None) -> str:
    semantic = EE.command_semantics(command)
    result = EE.run_command(command=command, cwd=cwd, timeout=timeout)

    parts = [
        f"[bash] 语义={semantic}",
        f"[bash] 退出码={result.exit_code}",
    ]
    if result.timed_out:
        parts.append(f"[bash] 超时：命令超过 {EE.clamp_timeout(timeout)} 秒")
    if result.stdout:
        parts.append(result.stdout.rstrip())
    if result.stderr:
        parts.append(f"[stderr]\n{result.stderr.rstrip()}")
    return "\n".join(parts)


# =============================================================================
# 工具注册表
# =============================================================================

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文本文件内容。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入（覆盖）文件，父目录不存在则自动创建。",
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
            "description": "在文件中用 new_string 替换 old_string 的第一处出现。",
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
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "在受控执行环境中运行 shell 命令。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": ["command"],
            },
        },
    },
]

_TOOL_IMPL: dict[str, Callable[..., str]] = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "bash": bash,
}


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    fn = _TOOL_IMPL.get(name)
    if fn is None:
        return f"错误：未知工具 {name}"
    try:
        return fn(**arguments)
    except Exception as exc:
        return f"错误：工具 {name} 执行失败：{exc}"


# =============================================================================
# OpenAI-compatible SSE 流式客户端
# =============================================================================

def stream_chat(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
    cfg: Optional[ModelConfig] = None,
) -> Iterator[dict[str, Any]]:
    """
    读取 OpenAI-compatible /chat/completions 的 SSE 流。
    这里是“流式模式”的最底层。
    """
    cfg = cfg or ModelConfig()
    cfg.require_api_key()

    payload: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
    if cfg.max_tokens:
        payload["max_tokens"] = cfg.max_tokens

    req = urllib.request.Request(
        f"{cfg.api_base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            for raw in resp:
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
                    yield {"type": "text_delta", "text": delta["content"]}
                if delta.get("reasoning_content"):
                    yield {"type": "reasoning_delta", "text": delta["reasoning_content"]}
                for tc in delta.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    yield {
                        "type": "tool_call_delta",
                        "index": tc.get("index", 0),
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "arguments": fn.get("arguments", ""),
                    }
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise ModelError(f"HTTP {e.code}: {body}", kind="http") from e
    except (urllib.error.URLError, OSError) as e:
        if isinstance(e, (socket.timeout, TimeoutError)):
            raise ModelError(
                f"请求超时（>{cfg.timeout} 秒）。可用 --timeout 调大。",
                kind="timeout",
            ) from e
        reason = getattr(e, "reason", e)
        raise ModelError(f"网络错误: {reason}", kind="network") from e


def consume_stream(events: Iterator[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """
    把原始 SSE 事件聚合成：
    - text_delta
    - reasoning_delta
    - model_response（最终模型对象）
    """
    response = ModelResponse()
    tool_buf: dict[int, dict[str, Any]] = {}
    order: list[int] = []

    for event in events:
        if event["type"] == "text_delta":
            response.text += event["text"]
            yield event
            continue
        if event["type"] == "reasoning_delta":
            yield event
            continue

        idx = event["index"]
        if idx not in tool_buf:
            tool_buf[idx] = {"id": event["id"], "name": event["name"], "arguments": ""}
            order.append(idx)
        buf = tool_buf[idx]
        if event["id"]:
            buf["id"] = event["id"]
        if event["name"]:
            buf["name"] = event["name"]
        buf["arguments"] += event["arguments"]

    for idx in order:
        buf = tool_buf[idx]
        raw_args = buf["arguments"].strip()
        try:
            args: dict[str, Any] = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            args = {"_raw": raw_args}
        response.tool_calls.append(
            ToolCall(id=buf["id"], name=buf["name"], arguments=args)
        )

    yield {"type": "model_response", "response": response}


def call_model(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
    cfg: Optional[ModelConfig] = None,
) -> ModelResponse:
    """
    非流式收口：把流式事件聚合成完整 ModelResponse。
    最小 Agent 默认还是走 stream，但这里保留一个收口函数，便于测试。
    """
    response = ModelResponse()
    for event in consume_stream(stream_chat(messages, tools, cfg)):
        if event["type"] == "model_response":
            response = event["response"]
    return response


# =============================================================================
# Conversation / Agent Loop
# =============================================================================

class Conversation:
    def __init__(
        self,
        system_prompt: str,
        cfg: Optional[ModelConfig] = None,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    ):
        self.system_prompt = system_prompt
        self.cfg = cfg or ModelConfig()
        self.max_context_chars = max_context_chars
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
        self._trim_to_budget()

    def _trim_to_budget(self) -> None:
        total = sum(len(m.get("content") or "") for m in self.messages)
        removed = 0
        while total > self.max_context_chars and len(self.messages) > 2:
            removed_msg = self.messages.pop(1)
            total -= len(removed_msg.get("content") or "")
            removed += 1
        if removed:
            print(f"[context] 已裁剪最老消息 {removed} 条，当前字符预算 {total}/{self.max_context_chars}", file=sys.stderr)
        self._prune_incomplete_tool_sequences()

    def _prune_incomplete_tool_sequences(self) -> None:
        msgs = self.messages
        out: list[dict[str, Any]] = [msgs[0]]
        i = 1
        while i < len(msgs):
            m = msgs[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                j = i + 1
                tools: list[dict[str, Any]] = []
                while j < len(msgs) and msgs[j].get("role") == "tool":
                    tools.append(msgs[j])
                    j += 1
                if tools:
                    out.append(m)
                    out.extend(tools)
                i = j
                continue
            if m.get("role") == "tool":
                i += 1
                continue
            out.append(m)
            i += 1
        self.messages = out

'''
 user:
   query_loop ,
    是agent-runtime 的大主线 循环系统。
    负责处理与各个模块，包括LLM 介入、整个轮次/turn 是否结束？？
    也负责，将 agent-runtime 产生的新形态 dispatch 给周围子系统、子模块进行专门化处理？？


   render_loop, 
    是向 入口层/用户层/surface/interface，渲染呈现过程、结果的 循环系统？

   run_agent，

'''
def query_loop(
    conversation: Conversation,
    tools: Optional[list[dict[str, Any]]] = None,
    max_turns: int = DEFAULT_MAX_TURNS,
    stream_model: Optional[Callable[..., Iterator[dict[str, Any]]]] = stream_chat,
    execute_tool_fn: Callable[[str, dict[str, Any]], str] = execute_tool,
) -> Iterator[dict[str, Any]]:
    """
    Agent-loop 生成器：
    - 每一轮：模型流式输出
    - 如果出现 tool call：执行工具，把结果 append 回历史
    - 如果没有 tool call：结束
    """
    if max_turns < 1:
        raise ValueError("max_turns 必须 >= 1")

    msgs = conversation.messages
    tool_schemas = tools if tools is not None else TOOLS

    for turn in range(1, max_turns + 1):
        print(f"[loop] 第 {turn}/{max_turns} 轮", file=sys.stderr)

        response: Optional[ModelResponse] = None

        if stream_model is not None:
            # 这是“流式模式”的关键：边收到 token 边输出，最后再聚合 tool_calls。
            for event in consume_stream(stream_model(msgs, tool_schemas, conversation.cfg)):
                if event["type"] == "model_response":
                    response = event["response"]
                    continue
                yield event
        else:
            response = call_model(msgs, tool_schemas, conversation.cfg)

        if response is None:
            raise ModelError("模型调用没有产生有效响应。", kind="unknown")

        if not response.has_tool_calls:
            msgs.append({"role": "assistant", "content": response.text})
            yield {"type": "turn_end", "text": response.text}
            yield {"type": "done", "reason": "no_tool_use"}
            return

        # 把模型发出的 tool_calls 写回上下文，下一轮模型才能“看到自己刚才调用了什么”。
        tool_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in response.tool_calls
        ]
        msgs.append({"role": "assistant", "content": response.text or None, "tool_calls": tool_calls})

        for tc in response.tool_calls:
            yield {
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments,
            }

            result_text = execute_tool_fn(tc.name, tc.arguments)
            yield {"type": "tool_result", "name": tc.name, "content": result_text}
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})

        yield {"type": "turn_end", "text": response.text or ""}

    yield {"type": "done", "reason": "max_turns"}


# =============================================================================
# 渲染层
# =============================================================================

def render_event(event: dict[str, Any]) -> None:
    et = event["type"]
    if et == "text_delta":
        print(event["text"], end="", flush=True)
    elif et == "reasoning_delta":
        # 默认不把思考链打印到终端；如需调试可以改成 stderr 输出。
        pass
    elif et == "tool_use":
        print(f"\n\n[tool_use] {event['name']} {json.dumps(event['arguments'], ensure_ascii=False)}", flush=True)
    elif et == "tool_result":
        print(f"\n[tool_result] {event['name']}\n{event['content']}\n", flush=True)
    elif et == "turn_end":
        if event.get("text"):
            print("\n", end="", flush=True)
    elif et == "done":
        print(f"\n[done] reason={event['reason']}", flush=True)


# =============================================================================
# 入口
# =============================================================================

def read_prompt_from_stdin_or_tty() -> str:
    if not sys.stdin.isatty():
        data = sys.stdin.read().strip()
        if data:
            return data
    try:
        return input("Prompt> ").strip()
    except EOFError:
        return ""


def run_agent(prompt: str, cfg: ModelConfig, max_turns: int, max_context_chars: int) -> int:
    conversation = Conversation(
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        cfg=cfg,
        max_context_chars=max_context_chars,
    )
    conversation.add_user(prompt)

    try:
        for event in query_loop(conversation, max_turns=max_turns):
            render_event(event)
        return 0
    except KeyboardInterrupt:
        print("\n[已中止]", file=sys.stderr)
        return 130
    except ConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 1
    except ModelError as exc:
        print(f"[模型错误] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[内部错误] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="HelloLLM S06-basic-ee 单文件最小 Agent（stream 模式）"
    )
    parser.add_argument("-p", "--prompt", default="", help="单次提示词；也可从 stdin 读取")
    parser.add_argument("--config", default="", help="JSON 配置文件路径（默认 ~/.hellollm/config.json）")
    parser.add_argument("--api-key", default="", help="临时 API Key 覆盖")
    parser.add_argument("--api-base", default="", help="OpenAI-compatible Base URL")
    parser.add_argument("--model", default="", help="模型名")
    parser.add_argument("--timeout", type=float, default=None, help="请求超时秒数")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS, help="Agent-loop 最大轮数")
    parser.add_argument("--max-context", type=int, default=DEFAULT_MAX_CONTEXT_CHARS, help="上下文字符上限")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    cfg = build_config(args)

    if args.api_base:
        cfg.api_base = args.api_base.rstrip("/")
    if args.model:
        cfg.model = args.model
    if args.api_key:
        cfg.api_key = args.api_key
    if args.timeout is not None:
        cfg.timeout = float(args.timeout)

    prompt = args.prompt.strip()
    if not prompt:
        prompt = read_prompt_from_stdin_or_tty()

    if not prompt:
        print("未提供 prompt。可以用 -p 或 stdin 输入。", file=sys.stderr)
        return 1

    return run_agent(
        prompt=prompt,
        cfg=cfg,
        max_turns=args.max_turns,
        max_context_chars=args.max_context,
    )


if __name__ == "__main__":
    raise SystemExit(main())

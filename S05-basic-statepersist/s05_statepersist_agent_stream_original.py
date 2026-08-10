#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HelloLLM S05 —— 最小 State & Persistence Agent（单文件 + Stream）

核心目标：
    把 HelloLLM/S05-basic-statepersist 的 State & Persistence
    压缩成一个最小、可运行、可阅读的 Python 文件。

核心运动：

    User
      ↓
    Runtime State(messages)
      ↓
    JSONL Transcript  ←—— State Persistence
      ↓
    Agent Loop
      ↓
    LLM（OpenAI-compatible SSE，stream=True）
      ↓
    tool_call ?
      ├─ no  → assistant → JSONL → STOP
      └─ yes → execute tool → tool_result → JSONL
                               ↓
                           下一轮 Loop

本文件重点：
    1. State 是什么
    2. Persistence 是什么
    3. State 与 Persistence 的边界
    4. Persistence 如何介入 Agent Loop
    5. 为什么使用 JSONL / append
    6. Stream 模式下为什么“不把每个 token 都落盘”

对应来源：
    - HelloLLM/S05-basic-statepersist
      state/store.py
      state/session.py
      query/agent_loop.py
    - Claude Code
      src/utils/sessionStorage.ts
    - arXiv:2604.14228v2
      §4 Agentic Query Loop
      §9 Session Persistence and Recovery

为了保持“最小”，本文件刻意不加入：
    - MCP
    - Memory
    - Subagent
    - Context Compression
    - Sandbox
    - 完整 Permission System
    - 完整 Hook System

这些属于其他 Stage / 横切机制；这里把 State Persistence 单独剥离出来学习。

运行：
    mkdir -p ~/.hellollm
    cat > ~/.hellollm/config.json <<'EOF'
    {
      "api_key": "sk-...",
      "api_base": "https://api.deepseek.com",
      "model": "YOUR_MODEL",
      "timeout": 120
    }
    EOF
    chmod 600 ~/.hellollm/config.json

    # 新建持久化会话
    python3 s05_statepersist_agent_stream.py --session demo

    # 恢复已有会话
    python3 s05_statepersist_agent_stream.py --resume demo

    # 查看历史会话
    python3 s05_statepersist_agent_stream.py --list-sessions

    # 无头单轮，但仍然持久化
    python3 s05_statepersist_agent_stream.py \
        --session demo2 \
        -p "读取 README.md 并总结"

依赖：
    Python 3.10+，运行时仅使用 Python 标准库。

重要：
    stream=True 只决定“模型响应如何传输”；
    persistence 决定“已经确认的 Agent State 如何落盘”。

    本实现：
        text_delta × N
            ↓
        终端实时显示
            ↓
        完整 assistant message
            ↓
        一次 append 到 JSONL

    因此既保留真正流式输出，又避免每个 token 都产生磁盘写入。
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


# =============================================================================
# 0. 基本配置
# =============================================================================

CONFIG_PATH = Path.home() / ".hellollm" / "config.json"
SESSIONS_DIR = Path.home() / ".hellollm" / "sessions"

DEFAULT_API_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "YOUR_MODEL"
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_TURNS = 10
DEFAULT_MAX_CONTEXT_CHARS = 30_000

# 与 HelloLLM S05 的 store.py 保持同一量级。
MAX_TRANSCRIPT_READ_BYTES = 50 * 1024 * 1024
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_READ_BYTES = 200_000

# Session 名最终会进入文件路径，所以必须做路径注入防护。
SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

SYSTEM_PROMPT = """You are a minimal coding agent.
Use the provided file tools whenever you need real filesystem information.
Never pretend that a tool succeeded.
After a tool result, continue from the actual result.
When the task is complete, answer concisely in Chinese.
"""


# =============================================================================
# 1. ModelConfig —— 模型调用配置
# =============================================================================

class ModelConfig:
    """最小模型配置。

    对应 HelloLLM 的 services/api/config.py。
    """

    def __init__(
        self,
        api_key: str,
        api_base: str = DEFAULT_API_BASE,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.timeout = float(timeout)


def load_config(config_path: Path = CONFIG_PATH) -> ModelConfig:
    """读取 ~/.hellollm/config.json。

    配置文件负责“模型是谁、API 在哪里、鉴权是什么”；
    它不是 Agent State。
    """
    if not config_path.exists():
        raise SystemExit(
            f"未找到配置文件：{config_path}\n"
            "请创建，例如：\n"
            '{"api_key":"sk-...","api_base":"https://api.deepseek.com",'
            '"model":"YOUR_MODEL","timeout":120}'
        )

    data = json.loads(config_path.read_text(encoding="utf-8"))
    api_key = str(data.get("api_key", "")).strip()

    if not api_key:
        raise SystemExit(f"配置文件缺少 api_key：{config_path}")

    return ModelConfig(
        api_key=api_key,
        api_base=str(data.get("api_base", DEFAULT_API_BASE)),
        model=str(data.get("model", DEFAULT_MODEL)),
        timeout=float(data.get("timeout", DEFAULT_TIMEOUT)),
    )


# =============================================================================
# 2. Tools —— 最小真实业务能力
# =============================================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取当前工作目录下的 UTF-8 文本文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于当前工作目录的文件路径。",
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
            "description": "在当前工作目录下创建或覆盖 UTF-8 文本文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于当前工作目录的文件路径。",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的完整文本。",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
]


def resolve_workspace_path(raw_path: str) -> Path:
    """将工具路径限制在当前工作目录内。

    这是最小执行边界，不等同于完整 sandbox。
    """
    root = Path.cwd().resolve()
    candidate = (root / raw_path).resolve()

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"路径越界：{raw_path}") from exc

    return candidate


def read_file(path: str) -> str:
    p = resolve_workspace_path(path)

    if not p.exists():
        return f"错误：文件不存在：{path}"
    if not p.is_file():
        return f"错误：不是普通文件：{path}"

    data = p.read_bytes()

    if len(data) > MAX_READ_BYTES:
        data = data[:MAX_READ_BYTES]
        suffix = f"\n\n[已截断：最多读取 {MAX_READ_BYTES} bytes]"
    else:
        suffix = ""

    try:
        return data.decode("utf-8") + suffix
    except UnicodeDecodeError:
        return f"错误：不是 UTF-8 文本文件：{path}"


def write_file(path: str, content: str) -> str:
    p = resolve_workspace_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {p.relative_to(Path.cwd().resolve())}"


TOOL_IMPL = {
    "read_file": read_file,
    "write_file": write_file,
}


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Tool Runtime 的最小执行入口。

    重要边界：
        Tool 只负责“执行”；
        State Persistence 不藏在 Tool 内部；
        Agent Loop 负责把 tool_result 写入 State + Persistence。
    """
    fn = TOOL_IMPL.get(name)

    if fn is None:
        return f"错误：未知工具：{name}"

    try:
        return str(fn(**arguments))
    except Exception as exc:
        return f"错误：工具 {name} 执行失败：{type(exc).__name__}: {exc}"


# =============================================================================
# 3. State & Persistence —— S05 核心
# =============================================================================

def validate_session_name(name: str) -> None:
    """校验 session 名。

    因为 session 名最终进入：
        ~/.hellollm/sessions/<name>.jsonl

    所以它属于一个真实的文件系统输入边界。
    """
    if not SESSION_NAME_RE.fullmatch(name):
        raise ValueError(
            f"非法 session 名：{name!r}；"
            "只允许 1-64 位 ASCII 字母、数字、_、-"
        )


def generate_session_name(prefix: str = "") -> str:
    """生成时间戳 session 名。

    对应 S05 state/session.py 的核心思想。
    """
    base = datetime.now().strftime("%Y%m%d_%H%M%S")

    if not prefix:
        return base

    '''
    
      user: 
        详细解释该代码块,按照功能模块、按照代码执行语句 不同层级说明。
        c for c in prefix (
        if c.isascii() and (c.isalnum() or c in "_-")
        )[:40]

        说明注释中，改写为非简略形式，线性展开写一遍。

    '''
    cleaned = "".join(
        c for c in prefix
        if c.isascii() and (c.isalnum() or c in "_-")
    )[:40]

    return f"{base}_{cleaned}" if cleaned else base


def make_session_path(name: str) -> Path:
    """session name -> transcript path。"""
    validate_session_name(name)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR / f"{name}.jsonl"


def create_session(
    name: str,
    system_prompt: str,
    model: str,
) -> Path:
    """创建 session，并写入 meta 首行。

    meta 是“恢复运行环境所需的元数据”，不是给 LLM 的 message。

    JSONL：
        line 1 -> {"type":"meta", ...}
        line 2 -> user message
        line 3 -> assistant message
        line 4 -> tool result
        ...
    """
    path = make_session_path(name)

    if path.exists():
        raise ValueError(f"session 已存在：{name}")
    else:
        "user: check first！"


    '''
      user: 
        各部分参数，有何意义，type, system_prompt, model, created_at, 
        以及 具体内容信息是什么？
    '''
    meta = {
        "type": "meta",
        "system_prompt": system_prompt,
        "model": model,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    path.write_text(
        json.dumps(meta, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return path


def append_message(
    name: str,
    message: dict[str, Any],
) -> None:
    """将一条 message append 到 transcript。

    这是 State → Persistence 的核心边界：

        messages.append(message)
                  │
                  ▼
        json.dumps(message)
                  │
                  ▼
        open(..., "a")
                  │
                  ▼
        transcript.jsonl

    只追加，不重写整个历史。
    """
    path = make_session_path(name)

    if not path.exists():
        create_session(name, "（自动创建）", "unknown")
    else:
        "user: check first.!"

    line = json.dumps(message, ensure_ascii=False)

    if len(line.encode("utf-8")) > MAX_LINE_BYTES:
        raise ValueError(
            f"消息过大：单行超过 {MAX_LINE_BYTES // 1024 // 1024} MB"
        )
    else:
        "user: data size restriction.."

    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()


def load_session(name: str) -> dict[str, Any]:
    """从 JSONL transcript 逐行 replay，恢复 State。

    注意：
        磁盘不是直接作为 LLM Context；
        磁盘只是 durable source。

        JSONL
          ↓ replay
        messages[]
          ↓
        Agent Loop
          ↓
        LLM Context
    """
    path = make_session_path(name)

    state: dict[str, Any] = {
        "system_prompt": SYSTEM_PROMPT,
        "model": "",
        "created_at": "",
        "messages": [],
    }

    if not path.exists():
        return state

    if path.stat().st_size > MAX_TRANSCRIPT_READ_BYTES:
        raise RuntimeError(
            f"session 文件超过读取上限："
            f"{MAX_TRANSCRIPT_READ_BYTES // 1024 // 1024} MB"
        )

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            if len(raw.encode("utf-8")) > MAX_LINE_BYTES:
                continue

            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                # 对齐 S05：单个坏行不应该阻断整个 session 恢复。
                continue

            if entry.get("type") == "meta":
                state["system_prompt"] = entry.get(
                    "system_prompt",
                    SYSTEM_PROMPT,
                )
                state["model"] = entry.get("model", "")
                state["created_at"] = entry.get("created_at", "")
                continue

            if entry.get("role") in {
                "system",
                "user",
                "assistant",
                "tool",
            }:
                state["messages"].append(entry)

    return state


def list_sessions() -> list[dict[str, Any]]:
    """列出 JSONL session，按最近修改时间排序。"""
    if not SESSIONS_DIR.exists():
        return []

    rows: list[dict[str, Any]] = []

    for path in SESSIONS_DIR.glob("*.jsonl"):
        try:
            count = sum(1 for _ in path.open(encoding="utf-8"))
            stat = path.stat()
        except OSError:
            continue

        rows.append(
            {
                "name": path.stem,
                "messages": max(count - 1, 0),
                "size": stat.st_size,
                "updated": datetime.fromtimestamp(
                    stat.st_mtime
                ).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    rows.sort(key=lambda x: x["updated"], reverse=True)
    return rows


# =============================================================================
# 4. Context —— State 恢复后，形成本轮 LLM 输入
# =============================================================================

def trim_context(
    messages: list[dict[str, Any]],
    MAX_CHARS_param: int,
) -> None:
    """最小滑动窗口。

    必须区分：

        Persistence
        = 磁盘上保存什么

        Context
        = 本轮 LLM 看什么

    因此这里裁剪 messages，
    不等于删除 JSONL transcript。

      user:
        裁剪对象是：作为context ，将被送入到 LLM 中的部分。
        本地state 存储文件、已经加载到缓存中的数据，不会裁剪？？

        messages，所对应的是在运行时/agent-runtime-loop 中的数据？？

    """
    if len(messages) <= 2:
        return

    '''
      user:
        函数内部，为何 又声明了临时函数（def size_of(...) ？？？
        
        这是什么语法现象？？

        将下列浓缩、简写代码，在注释中，详细开展写一个版本？？
    '''
    def size_of(message: dict[str, Any]) -> int:
        return len(json.dumps(message, ensure_ascii=False))

    total = sum(size_of(m) for m in messages)

    while total > MAX_CHARS_param and len(messages) > 2:
        removed = messages.pop(1)
        total -= size_of(removed)


def rebuild_messages_from_state(
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """把持久化 State 恢复成 Agent Loop 可直接使用的 messages。

    S05 的 meta 行保存 system_prompt，
    但 system prompt 不需要重复作为普通 transcript message 保存。

    所以 resume 时：
        meta.system_prompt
             +
        replayed messages
             ↓
        messages[0] = system
        messages[1:] = user/assistant/tool
    """
    system_prompt = state.get("system_prompt") or SYSTEM_PROMPT

    messages = [
        {"role": "system", "content": system_prompt}
    ]

    for message in state.get("messages", []):
        if message.get("role") == "system":
            continue
        messages.append(message)

    return messages


# =============================================================================
# 5. Streaming Model —— 真正 SSE stream=True
# =============================================================================

class ModelError(RuntimeError):
    pass


def stream_chat(
    messages: list[dict[str, Any]],
    cfg: ModelConfig,
) -> Iterator[dict[str, Any]]:
    """调用 OpenAI-compatible /chat/completions，并启用 stream=True。

    每个 SSE data 块立即 yield。
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
        data=json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {cfg.api_key}",
        },
        method="POST",
    )

    try:
        response = urllib.request.urlopen(
            request,
            timeout=cfg.timeout,
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:2000]
        raise ModelError(f"HTTP {exc.code}: {body}") from exc
    except (
        urllib.error.URLError,
        socket.timeout,
        TimeoutError,
        OSError,
    ) as exc:
        raise ModelError(f"网络/超时错误：{exc}") from exc

    with response:
        for raw in response:
            line = raw.decode(
                "utf-8",
                "replace",
            ).strip()

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

            for tool_call in delta.get("tool_calls") or []:
                fn = tool_call.get("function") or {}

                yield {
                    "type": "tool_call_delta",
                    "index": int(
                        tool_call.get("index", 0)
                    ),
                    "id": str(
                        tool_call.get("id") or ""
                    ),
                    "name": str(
                        fn.get("name") or ""
                    ),
                    "arguments": str(
                        fn.get("arguments") or ""
                    ),
                }


def consume_stream(
    messages: list[dict[str, Any]],
    cfg: ModelConfig,
) -> dict[str, Any]:
    """消费 Stream，并最终形成一个完整 assistant message。

    text_delta：
        立即显示给用户。

    tool_call_delta：
        不能立即 json.loads；
        必须等所有 arguments fragment 拼完。

    最终只有完整 message 才进入 State。
    """
    text_parts: list[str] = []

    # index -> {"id", "name", "arguments"}
    tool_buffers: dict[int, dict[str, str]] = {}

    print("assistant> ", end="", flush=True)

    for event in stream_chat(messages, cfg):
        if event["type"] == "text_delta":
            text = event["text"]
            text_parts.append(text)

            # 真正的 stream：收到一块，立即输出一块。
            print(
                text,
                end="",
                flush=True,
            )

        elif event["type"] == "tool_call_delta":
            index = event["index"]

            buf = tool_buffers.setdefault(
                index,
                {
                    "id": "",
                    "name": "",
                    "arguments": "",
                },
            )

            if event["id"]:
                buf["id"] = event["id"]

            if event["name"]:
                buf["name"] = event["name"]

            # function.arguments 是 JSON 字符串碎片。
            buf["arguments"] += event["arguments"]

    print()

    tool_calls: list[dict[str, Any]] = []

    for index in sorted(tool_buffers):
        buf = tool_buffers[index]
        raw_arguments = buf["arguments"]

        try:
            parsed_arguments = json.loads(
                raw_arguments or "{}"
            )
        except json.JSONDecodeError:
            parsed_arguments = {
                "_raw": raw_arguments
            }

        tool_calls.append(
            {
                "id": buf["id"] or f"call_{index}",
                "type": "function",
                "function": {
                    "name": buf["name"],
                    "arguments": json.dumps(
                        parsed_arguments,
                        ensure_ascii=False,
                    ),
                },
            }
        )

    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": (
            "".join(text_parts)
            or None
        ),
    }

    if tool_calls:
        assistant["tool_calls"] = tool_calls

    return assistant


# =============================================================================
# 6. Agent Loop —— Persistence 真正介入的位置
# =============================================================================

def run_turn(
    session_name: str,
    messages: list[dict[str, Any]],
    cfg: ModelConfig,
    user_text: str,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> str:
    """运行一次用户 Turn。

      user:
        详细解说一下，State-Persistence 概念，以及该模块在运行时的循环结构、机制？
        详细解说一下，State-Persistence 是如何记入 运行时的，以及 什么事 状态转换边界？

      user：
        State-Persistence 模块，是附着在 agent-runtime-loop/运行时循环 上的模块？
        他通过3类节点介入运行时循环：1. 用户/input 2. 模型/LLM 3. 工具/tools ？


        
    State Persistence 不是另一个 Loop。
    它介入的是 Loop 内的“状态转换边界”。

    A. User Event
       messages.append(user)
             ↓
       append_message(user)

    B. Model Response
       stream delta
             ↓
       aggregate assistant
             ↓
       messages.append(assistant)
             ↓
       append_message(assistant)

    C. Tool Execution
       execute(tool)
             ↓
       messages.append(tool_result)
             ↓
       append_message(tool_result)

    D. 下一轮
       messages
          ↓
       LLM

    所以它可以理解成：

        Agent Loop
            │
            ├── State mutation
            │
            └── Persistence side-effect

    而不是：

        Agent Loop
            ↓
        Persistence Loop
            ↓
        Agent Loop
    """
    user_message = {
        "role": "user",
        "content": user_text,
    }

    # ① Runtime State 更新
    messages.append(user_message)

    # ② Durable State 更新
    append_message(
        session_name,
        user_message,
    )

    final_text = ""

    for turn in range(
        1,
        max_turns + 1,
    ):
        print(
            f"\n[Agent Loop {turn}/{max_turns}]"
        )

        # Context 管理：
        # 只影响“这一轮送给模型什么”，
        # 不删除磁盘 transcript。
        '''
          user:
            进行http/LLM 请求时，trim_context 上下文数据？

        '''
        trim_context(
            messages,
            max_context_chars,
        )

        try:
            assistant = consume_stream(
                messages,
                cfg,
            )
        except KeyboardInterrupt:
            print("\n[已中止本轮]")
            return final_text
        except ModelError as exc:
            print(
                f"\n[模型错误] {exc}",
                file=sys.stderr,
            )
            return final_text

        # ③ Model State 更新
        messages.append(assistant)

        # ④ Model State 持久化
        #
        # 注意不是每个 text_delta 都写，
        # 而是完整 assistant turn 完成后写一次。
        append_message(
            session_name,
            assistant,
        )

        final_text = assistant.get(
            "content"
        ) or ""

        tool_calls = (
            assistant.get("tool_calls")
            or []
        )

        # Stop condition：
        # 模型只产生文本。
        if not tool_calls:
            return final_text

        # ⑤ Tool-use：
        # assistant(tool_calls) 已经持久化。
        # 下面把真实执行结果追加为 tool message。
        for tool_call in tool_calls:
            call_id = tool_call.get(
                "id",
                "",
            )

            function = (
                tool_call.get("function")
                or {}
            )

            tool_name = function.get(
                "name",
                "",
            )

            raw_arguments = function.get(
                "arguments",
                "{}",
            )

            try:
                arguments = json.loads(
                    raw_arguments or "{}"
                )
            except json.JSONDecodeError:
                arguments = {
                    "_raw": raw_arguments
                }

            print(
                "[tool] "
                f"{tool_name}"
                "("
                f"{json.dumps(arguments, ensure_ascii=False)}"
                ")"
            )

            # Tool Runtime：
            # 真正改变外部环境的动作发生在这里。
            result = execute_tool(
                tool_name,
                arguments,
            )

            tool_message = {
                "role": "tool",
                "tool_call_id": call_id,
                "content": result,
            }

            # ⑥ Tool Result -> Runtime State
            messages.append(tool_message)

            # ⑦ Tool Result -> Durable State
            append_message(
                session_name,
                tool_message,
            )

        print(
            "[state] tool_result 已写入 "
            "transcript，继续 Agent Loop"
        )

    print(
        f"[警告] 达到最大 Agent Loop 轮数："
        f"{max_turns}"
    )
    return final_text


# =============================================================================
# 7. Session 生命周期
# =============================================================================

def open_or_create_session(
    *,
    session_name: str | None,
    resume: bool,
    cfg: ModelConfig,
) -> tuple[str, list[dict[str, Any]]]:
    """创建或恢复 session。

    create：
        meta -> 空 messages

    resume：
        JSONL -> replay -> rebuild messages
    """
    if session_name:
        validate_session_name(
            session_name
        )
    else:
        session_name = generate_session_name()

    path = make_session_path(
        session_name
    )

    if resume:
        state = load_session(
            session_name
        )

        # session transcript 中记录的 model
        # 是这个历史 session 的运行元数据。
        if state.get("model"):
            cfg.model = state["model"]

        messages = (
            rebuild_messages_from_state(
                state
            )
        )

        return session_name, messages

    if path.exists():
        raise SystemExit(
            f"session 已存在：{session_name}\n"
            f"请使用 --resume {session_name} "
            "恢复，或换一个 session 名。"
        )

    create_session(
        session_name,
        SYSTEM_PROMPT,
        cfg.model,
    )

    return (
        session_name,
        [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            }
        ],
    )


# =============================================================================
# 8. CLI / REPL
# =============================================================================

def print_sessions() -> None:
    rows = list_sessions()

    if not rows:
        print("没有历史 session。")
        return

    print(
        "NAME                         "
        "MESSAGES   SIZE       UPDATED"
    )
    print("-" * 72)

    for row in rows:
        print(
            f"{row['name']:<28} "
            f"{row['messages']:>8}   "
            f"{row['size']:>8}   "
            f"{row['updated']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "HelloLLM S05 最小 State "
            "Persistence Agent（SSE Stream）"
        )
    )

    parser.add_argument(
        "--session",
        help="新建 session 名，例如 demo",
    )

    parser.add_argument(
        "--resume",
        metavar="SESSION",
        help="恢复已有 session",
    )

    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="列出历史 session 后退出",
    )

    parser.add_argument(
        "-p",
        "--prompt",
        help="无头模式：执行一次请求后退出",
    )

    parser.add_argument(
        "--api-key",
        help="临时覆盖 API key",
    )

    parser.add_argument(
        "--api-base",
        help="临时覆盖 API base",
    )

    parser.add_argument(
        "--model",
        help="临时覆盖模型名",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        help="HTTP timeout 秒数",
    )

    parser.add_argument(
        "--max-turns",
        type=int,
        default=DEFAULT_MAX_TURNS,
        help=(
            "Agent Loop 最大轮数，"
            f"默认 {DEFAULT_MAX_TURNS}"
        ),
    )

    parser.add_argument(
        "--max-context",
        type=int,
        default=DEFAULT_MAX_CONTEXT_CHARS,
        help=(
            "Context 最大字符数，"
            f"默认 {DEFAULT_MAX_CONTEXT_CHARS}"
        ),
    )

    args = parser.parse_args()

    if args.list_sessions:
        print_sessions()
        return

    cfg = load_config()

    # CLI > config > default
    if args.api_key:
        cfg.api_key = args.api_key

    if args.api_base:
        cfg.api_base = (
            args.api_base.rstrip("/")
        )

    if args.model:
        cfg.model = args.model

    if args.timeout:
        cfg.timeout = args.timeout

    if args.resume:
        session_name, messages = (
            open_or_create_session(
                session_name=args.resume,
                resume=True,
                cfg=cfg,
            )
        )
    else:
        session_name, messages = (
            open_or_create_session(
                session_name=args.session,
                resume=False,
                cfg=cfg,
            )
        )

    print("=" * 72)
    print(
        "HelloLLM S05 "
        "State Persistence Agent"
    )
    print(
        f"session : {session_name}"
    )
    print(
        f"model   : {cfg.model}"
    )
    print(
        f"store   : "
        f"{make_session_path(session_name)}"
    )
    print("stream  : ON")
    print("=" * 72)

    if args.prompt:
        run_turn(
            session_name,
            messages,
            cfg,
            args.prompt,
            max_turns=args.max_turns,
            max_context_chars=args.max_context,
        )
        return

    print(
        "输入 /exit 或 /quit 退出。"
    )
    print(
        "输入 /sessions 查看历史 session。"
    )
    print(
        "输入 /show-state 查看当前内存 State。"
    )

    while True:
        try:
            user_text = input(
                "\nuser> "
            ).strip()
        except (
            EOFError,
            KeyboardInterrupt,
        ):
            print("\n退出。")
            break

        if not user_text:
            continue

        if user_text in {
            "/exit",
            "/quit",
        }:
            break

        if user_text == "/sessions":
            print_sessions()
            continue

        if user_text == "/show-state":
            print(
                json.dumps(
                    messages,
                    ensure_ascii=False,
                    indent=2,
                )
            )
            continue

        run_turn(
            session_name,
            messages,
            cfg,
            user_text,
            max_turns=args.max_turns,
            max_context_chars=args.max_context,
        )


if __name__ == "__main__":
    main()

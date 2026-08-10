#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HelloLLM S05 —— 最小 State & Persistence Agent（单文件 + SSE Stream）

本版是在原学习版基础上的“结构化精简版”：
1. 保留单文件、OpenAI-compatible SSE stream=True、read_file/write_file、JSONL 持久化。
2. 将松散函数收束为 3 个核心对象：
      SessionStore     —— Durable State / JSONL
      StreamClient     —— LLM Transport / SSE
      AgentRuntime     —— Runtime State + Agent Loop
3. 将原来会修改 messages 的 trim_context() 改为 build_context()：
      完整 Runtime State 不被裁剪；
      磁盘 Transcript 不被裁剪；
      只构造“本轮送给 LLM 的 Context 投影”。
4. 删除无意义的 else: "user: ..." 字符串表达式。
5. 减少人为拆行，让代码尽量按“一个逻辑动作一行”阅读。
6. 所有原 user: 标记问题均在对应位置保留，并增加“判断 / 补全”。

核心关系：

    User / LLM / Tool
          │
          ▼
    AgentRuntime.record(message)
          │
          ├── 1) Runtime State: state.messages.append(message)
          └── 2) Durable State: store.append_message(message)
                              │
                              ▼
                         transcript.jsonl

每次模型调用：

    完整 Runtime State
          │
          ▼
      build_context()
          │ 只做副本/投影，不删除 State
          ▼
      LLM SSE Stream
          │
          ├── text_delta 立即打印
          └── 完整 assistant message 后再 record()
          ▼
      tool_calls ?
        ├─ no  -> STOP
        └─ yes -> execute_tool -> record(tool_result) -> 下一轮

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

    python3 s05_statepersist_agent_stream_optimized.py --session demo
    python3 s05_statepersist_agent_stream_optimized.py --resume demo
    python3 s05_statepersist_agent_stream_optimized.py --list-sessions
    python3 s05_statepersist_agent_stream_optimized.py --session demo2 -p "读取 README.md 并总结"

依赖：
    Python 3.10+；运行时仅使用 Python 标准库。
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


# =============================================================================
# 0. Constants / System Prompt
# =============================================================================

CONFIG_PATH = Path.home() / ".hellollm" / "config.json"
SESSIONS_DIR = Path.home() / ".hellollm" / "sessions"

DEFAULT_API_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "YOUR_MODEL"
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_TURNS = 10
DEFAULT_MAX_CONTEXT_CHARS = 30_000

MAX_TRANSCRIPT_READ_BYTES = 50 * 1024 * 1024
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_READ_BYTES = 200_000

SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

SYSTEM_PROMPT = """You are a minimal coding agent.
Use the provided file tools whenever you need real filesystem information.
Never pretend that a tool succeeded.
After a tool result, continue from the actual result.
When the task is complete, answer concisely in Chinese.
"""


# =============================================================================
# 1. Data Models / Config
# =============================================================================

@dataclass
class ModelConfig:
    """模型 API 配置；它属于运行配置，不属于会话 Conversation State。"""

    api_key: str
    api_base: str = DEFAULT_API_BASE
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT

    def __post_init__(self) -> None:
        self.api_base = self.api_base.rstrip("/")
        self.timeout = float(self.timeout)


@dataclass
class SessionState:
    """内存中的 Runtime State。

    messages 只保存对话事件：user / assistant / tool。
    system_prompt、model、created_at 属于 Session Metadata。
    """

    name: str
    system_prompt: str
    model: str
    created_at: str
    messages: list[dict[str, Any]] = field(default_factory=list)


def load_config(path: Path = CONFIG_PATH) -> ModelConfig:
    """读取 ~/.hellollm/config.json。"""
    if not path.exists():
        raise SystemExit(
            f"未找到配置文件：{path}\n"
            '请创建，例如：{"api_key":"sk-...","api_base":"https://api.deepseek.com",'
            '"model":"YOUR_MODEL","timeout":120}'
        )

    data = json.loads(path.read_text(encoding="utf-8"))
    api_key = str(data.get("api_key", "")).strip()
    if not api_key:
        raise SystemExit(f"配置文件缺少 api_key：{path}")

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
                    "path": {"type": "string", "description": "相对于当前工作目录的文件路径。"}
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
                    "path": {"type": "string", "description": "相对于当前工作目录的文件路径。"},
                    "content": {"type": "string", "description": "要写入的完整文本。"},
                },
                "required": ["path", "content"],
            },
        },
    },
]


def resolve_workspace_path(raw_path: str) -> Path:
    """把工具路径限制在当前工作目录内；这是最小边界，不等同于完整 Sandbox。"""
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
    suffix = ""
    if len(data) > MAX_READ_BYTES:
        data = data[:MAX_READ_BYTES]
        suffix = f"\n\n[已截断：最多读取 {MAX_READ_BYTES} bytes]"

    try:
        return data.decode("utf-8") + suffix
    except UnicodeDecodeError:
        return f"错误：不是 UTF-8 文本文件：{path}"


def write_file(path: str, content: str) -> str:
    p = resolve_workspace_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {p.relative_to(Path.cwd().resolve())}"


TOOL_IMPL = {"read_file": read_file, "write_file": write_file}


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Tool Runtime 的最小执行入口；持久化不放在 Tool 内，而由 AgentRuntime 统一记录。"""
    fn = TOOL_IMPL.get(name)
    if fn is None:
        return f"错误：未知工具：{name}"

    try:
        return str(fn(**arguments))
    except Exception as exc:
        return f"错误：工具 {name} 执行失败：{type(exc).__name__}: {exc}"


# =============================================================================
# 3. Session Store —— State Persistence 核心
# =============================================================================

def validate_session_name(name: str) -> None:
    """Session 名最终进入文件路径，因此这里是文件系统输入边界。"""
    if not SESSION_NAME_RE.fullmatch(name):
        raise ValueError(f"非法 session 名：{name!r}；只允许 1-64 位 ASCII 字母、数字、_、-")


def generate_session_name(prefix: str = "") -> str:
    """生成时间戳 Session 名：YYYYMMDD_HHMMSS[_prefix]。"""
    base = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not prefix:
        return base

    # USER:
    # 原问题：c for c in prefix if c.isascii() and (c.isalnum() or c in "_-") 是什么？
    #
    # 判断：
    # 这是 Python comprehension（推导式）里的“遍历 + 条件过滤”。
    # 原版把“遍历字符、判断是否合法、收集字符、join、截断”压缩到一个表达式，合法但学习成本偏高。
    #
    # 原简写：
    # cleaned = "".join(c for c in prefix if c.isascii() and (c.isalnum() or c in "_-"))[:40]
    #
    # 线性展开如下；本优化版直接采用展开形式作为实际代码：
    allowed_chars: list[str] = []
    for char in prefix:                                  # ① 逐字符遍历 prefix
        is_ascii = char.isascii()                        # ② 是否为 ASCII
        is_allowed_symbol = char.isalnum() or char in "_-"  # ③ 字母数字，或 _ -
        if is_ascii and is_allowed_symbol:              # ④ 两个条件同时满足才保留
            allowed_chars.append(char)                  # ⑤ 收集合法字符

    cleaned = "".join(allowed_chars)[:40]               # ⑥ 合并字符串；最多保留 40 个字符
    return f"{base}_{cleaned}" if cleaned else base


class SessionStore:
    """JSONL Durable Store。

    边界：
        Runtime State 由 AgentRuntime/SessionState 持有；
        SessionStore 只负责持久化 create / append / load / list。
    """

    def __init__(self, sessions_dir: Path = SESSIONS_DIR) -> None:
        self.sessions_dir = sessions_dir

    def path_for(self, name: str) -> Path:
        validate_session_name(name)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        return self.sessions_dir / f"{name}.jsonl"

    def create(self, name: str, system_prompt: str, model: str) -> SessionState:
        """创建 Session，同时写入 JSONL 首行 meta。"""
        path = self.path_for(name)

        # USER: "check first!"
        # 判断：原代码 if path.exists(): raise ... 已经完成“先检查”。
        # 原来的 else: "user: check first!" 是无效果字符串表达式，不执行任何逻辑，应删除。
        # Guard clause 更清楚：异常条件直接退出，后续代码自然就是“文件不存在”的正常路径。
        if path.exists():
            raise ValueError(f"session 已存在：{name}")

        created_at = datetime.now().isoformat(timespec="seconds")

        # USER:
        # 原问题：type / system_prompt / model / created_at 各字段是什么意思？
        #
        # 判断与补全：
        # - type="meta"：
        #   这是存储层的记录类型标签，说明这一行不是 user/assistant/tool 对话消息。
        # - system_prompt：
        #   创建 Session 时使用的系统指令。Resume 时用它重建本次会话的系统上下文。
        # - model：
        #   创建 Session 时使用的模型名，例如 deepseek-chat。用于恢复历史运行配置。
        # - created_at：
        #   Session 创建时间；isoformat(timespec="seconds") 产生如 2026-08-10T09:32:15。
        #
        # 具体 JSONL 首行类似：
        # {"type":"meta","system_prompt":"...","model":"deepseek-chat",
        #  "created_at":"2026-08-10T09:32:15"}
        meta = {
            "type": "meta",
            "system_prompt": system_prompt,
            "model": model,
            "created_at": created_at,
        }
        path.write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")

        return SessionState(
            name=name,
            system_prompt=system_prompt,
            model=model,
            created_at=created_at,
            messages=[],
        )

    def append_message(self, name: str, message: dict[str, Any]) -> None:
        """把一条已确认的 Agent Event 追加到 JSONL，不重写整个历史。"""
        path = self.path_for(name)

        # USER: "check first!"
        # 判断：这里确实要先确认 transcript 存在。
        # 原版“若不存在就自动 create(..., unknown)”虽然能继续运行，但会制造不完整 metadata。
        # 优化版选择严格生命周期：必须先 create/resume，再 append；否则立即报错。
        if not path.exists():
            raise FileNotFoundError(f"session 不存在，不能 append：{name}")

        line = json.dumps(message, ensure_ascii=False)
        line_bytes = len(line.encode("utf-8"))

        # USER: "data size restriction"
        # 判断：是的，这里就是“单条 JSONL 记录大小限制”。
        # 目的：避免一次异常 Tool Result/Message 把 transcript 写入超大单行。
        # 原来的 else: "user: data size restriction.." 仍是无效果字符串，应删除。
        if line_bytes > MAX_LINE_BYTES:
            raise ValueError(f"消息过大：单行超过 {MAX_LINE_BYTES // 1024 // 1024} MB")

        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()

    def load(self, name: str) -> SessionState:
        """逐行 replay JSONL，重建 SessionState。"""
        path = self.path_for(name)
        if not path.exists():
            raise FileNotFoundError(f"session 不存在：{name}")
        if path.stat().st_size > MAX_TRANSCRIPT_READ_BYTES:
            raise RuntimeError(
                f"session 文件超过读取上限：{MAX_TRANSCRIPT_READ_BYTES // 1024 // 1024} MB"
            )

        system_prompt = SYSTEM_PROMPT
        model = ""
        created_at = ""
        messages: list[dict[str, Any]] = []

        with path.open("r", encoding="utf-8") as f:
            for raw in f:
                if len(raw.encode("utf-8")) > MAX_LINE_BYTES:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    # 单个坏行不阻断整个 Session replay。
                    continue

                if entry.get("type") == "meta":
                    system_prompt = entry.get("system_prompt", SYSTEM_PROMPT)
                    model = entry.get("model", "")
                    created_at = entry.get("created_at", "")
                    continue

                if entry.get("role") in {"user", "assistant", "tool"}:
                    messages.append(entry)

        return SessionState(
            name=name,
            system_prompt=system_prompt,
            model=model,
            created_at=created_at,
            messages=messages,
        )

    def list_sessions(self) -> list[dict[str, Any]]:
        """列出 JSONL Session，按最近修改时间降序。"""
        if not self.sessions_dir.exists():
            return []

        rows: list[dict[str, Any]] = []
        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                line_count = sum(1 for _ in path.open(encoding="utf-8"))
                stat = path.stat()
            except OSError:
                continue

            rows.append(
                {
                    "name": path.stem,
                    "messages": max(line_count - 1, 0),  # 第 1 行通常是 meta
                    "size": stat.st_size,
                    "updated": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

        rows.sort(key=lambda row: row["updated"], reverse=True)
        return rows


# =============================================================================
# 4. Context Projection —— 从完整 State 构造本轮 LLM Context
# =============================================================================

def message_size_chars(message: dict[str, Any]) -> int:
    """以 JSON 字符数近似衡量 Context 大小；这是教学版预算，不是 tokenizer token 计数。"""
    return len(json.dumps(message, ensure_ascii=False))


def split_user_turns(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """按 user message 划分 Turn，避免裁剪时拆散 assistant-tool 配对。"""
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    for message in messages:
        if message.get("role") == "user" and current:
            turns.append(current)
            current = []
        current.append(message)

    if current:
        turns.append(current)
    return turns


def build_context(state: SessionState, max_chars: int) -> list[dict[str, Any]]:
    """从完整 Runtime State 构造“本轮送给 LLM 的 Context 副本”。

    USER:
    原问题：
      1. 裁剪对象是否只是作为 Context 送入 LLM 的部分？
      2. 本地 State 文件、已经加载到缓存中的数据是否不会裁剪？
      3. messages 是否就是 agent-runtime-loop 中的数据？

    判断：
      - “磁盘 JSONL 不裁剪”是对的。
      - 原版 trim_context(messages) 会对传入 list 执行 pop(1)，所以“内存 messages 不裁剪”并不成立。
      - messages 的确是 Agent Runtime 当前正在使用的 Conversation State。
      - 因此原版把“Runtime State”和“Model Context”混用了同一个可变 list。

    本版修正：
      Durable State(JSONL)       —— 完整保留
      Runtime State(messages)    —— 完整保留
      Model Context              —— 每轮由 build_context() 新建副本，可裁剪

    即：
      Full Runtime State
             │
             ├───────────────> JSONL（完整）
             │
             └─ build_context()
                    ↓
               LLM Context（可缩短）

    USER:
    原问题：为什么函数内部又 def size_of(...)？这是什么语法？

    判断：
      Python 允许 nested/local function（嵌套函数/局部函数）。
      它只在外层函数执行期间定义，并只在外层局部作用域中使用。
      原版这样写语法正确，但对教学代码增加了一层阅读跳转。

    原版浓缩代码：
      def size_of(message):
          return len(json.dumps(message, ensure_ascii=False))
      total = sum(size_of(m) for m in messages)

    完全线性展开等价思路：
      total = 0
      for message in messages:
          serialized = json.dumps(message, ensure_ascii=False)
          current_size = len(serialized)
          total = total + current_size

    本版把 message_size_chars() 提升为模块级小函数，避免在函数内部再声明函数。
    """
    system_message = {"role": "system", "content": state.system_prompt}
    turns = split_user_turns(state.messages)

    # 从最新 Turn 倒序选择；至少保留最新 Turn。
    selected_reversed: list[list[dict[str, Any]]] = []
    total = message_size_chars(system_message)

    for turn in reversed(turns):
        turn_size = sum(message_size_chars(message) for message in turn)
        if selected_reversed and total + turn_size > max_chars:
            break
        selected_reversed.append(turn)
        total += turn_size

    selected_turns = reversed(selected_reversed)
    context = [system_message]
    for turn in selected_turns:
        context.extend(turn)
    return context


# =============================================================================
# 5. Streaming Model —— OpenAI-compatible SSE
# =============================================================================

class ModelError(RuntimeError):
    pass


class StreamClient:
    """只负责模型传输/流解析，不负责 Session State。"""

    def __init__(self, cfg: ModelConfig) -> None:
        self.cfg = cfg

    def stream_chat(self, messages: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        """POST /chat/completions，stream=True；把 SSE 解析成内部 delta event。"""
        payload = {
            "model": self.cfg.model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "stream": True,
        }
        request = urllib.request.Request(
            f"{self.cfg.api_base}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {self.cfg.api_key}",
            },
            method="POST",
        )

        try:
            response = urllib.request.urlopen(request, timeout=self.cfg.timeout)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:2000]
            raise ModelError(f"HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise ModelError(f"网络/超时错误：{exc}") from exc

        with response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
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
                    yield {"type": "text_delta", "text": str(delta["content"])}

                for tool_call in delta.get("tool_calls") or []:
                    fn = tool_call.get("function") or {}
                    yield {
                        "type": "tool_call_delta",
                        "index": int(tool_call.get("index", 0)),
                        "id": str(tool_call.get("id") or ""),
                        "name": str(fn.get("name") or ""),
                        "arguments": str(fn.get("arguments") or ""),
                    }

    def consume(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """实时显示文本 delta；流结束后聚合成一个完整 assistant message。"""
        text_parts: list[str] = []
        tool_buffers: dict[int, dict[str, str]] = {}

        print("assistant> ", end="", flush=True)
        for event in self.stream_chat(messages):
            if event["type"] == "text_delta":
                text = event["text"]
                text_parts.append(text)
                print(text, end="", flush=True)
                continue

            index = event["index"]
            buf = tool_buffers.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if event["id"]:
                buf["id"] = event["id"]
            if event["name"]:
                buf["name"] = event["name"]
            buf["arguments"] += event["arguments"]  # arguments 是流式 JSON 字符串碎片

        print()

        tool_calls: list[dict[str, Any]] = []
        for index in sorted(tool_buffers):
            buf = tool_buffers[index]
            raw_arguments = buf["arguments"]
            try:
                parsed_arguments = json.loads(raw_arguments or "{}")
            except json.JSONDecodeError:
                parsed_arguments = {"_raw": raw_arguments}

            tool_calls.append(
                {
                    "id": buf["id"] or f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": buf["name"],
                        "arguments": json.dumps(parsed_arguments, ensure_ascii=False),
                    },
                }
            )

        assistant: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        return assistant


# =============================================================================
# 6. Agent Runtime / Agent Loop —— Persistence 介入点
# =============================================================================

class AgentRuntime:
    """把 Runtime State、Persistence、Model Stream 和 Tool Loop 收束到一个运行时对象。"""

    def __init__(
        self,
        state: SessionState,
        store: SessionStore,
        client: StreamClient,
        max_turns: int = DEFAULT_MAX_TURNS,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    ) -> None:
        self.state = state
        self.store = store
        self.client = client
        self.max_turns = max_turns
        self.max_context_chars = max_context_chars

    def record(self, message: dict[str, Any]) -> None:
        """一个明确的“状态转换边界”：内存 State 成功更新后，同步 append Durable State。

        这一个函数把最核心关系写死在同一处：

            Event
              ↓
            Runtime State mutation
              ↓
            Durable persistence side-effect
        """
        self.state.messages.append(message)
        self.store.append_message(self.state.name, message)

    def run_turn(self, user_text: str) -> str:
        """运行一个用户 Turn。

        USER:
        原问题：
          State-Persistence 是什么？
          它是否附着在 agent-runtime-loop 上？
          是否主要通过 3 类节点介入：User / LLM / Tool？
          什么叫“状态转换边界”？

        判断：
          基本判断是对的，但要更精确地说：

          1. State
             是 Agent 在某一时刻“当前已经发生了什么”的内存表示。
             本例核心就是 state.messages。

          2. Persistence
             是把已经确认的 State Event 写到进程外的 Durable Store（JSONL）。
             进程退出后，可以 replay JSONL 重建 Runtime State。

          3. State-Persistence 不是第二条 Agent Loop
             它是挂接在 Agent Loop 状态变化点上的横切能力。

          4. 本例最主要确实有三类介入点：
             A) User/Input 被接受；
             B) LLM 完整 Assistant Turn 被确认；
             C) Tool 执行结果形成 Observation。

          5. “状态转换边界”不是抽象口号：
             指系统从 S_n 变成 S_{n+1} 的那个明确时刻。
             例如：
                 S0
                 + user_message
                 -> S1
             当 user_message 已经被接受为会话事实后，就调用 record(user_message)。
             record() 同时更新 Runtime State 与 JSONL，这就是本例的持久化边界。

        Agent Loop：

            user
              │
              ▼
            record(user)  ------------------┐
              │                             │
              ▼                             │
            build_context()                 │
              │                             │
              ▼                             │
            LLM stream                      │
              │                             │
              ▼                             │
            record(assistant)  -------------┤
              │                             │
              ├─ no tool -> stop            │
              │                             │
              └─ tool -> execute_tool       │
                           │                 │
                           ▼                 │
                       record(tool) ---------┘
                           │
                           └------> 下一轮 LLM
        """
        self.record({"role": "user", "content": user_text})
        final_text = ""

        for turn in range(1, self.max_turns + 1):
            print(f"\n[Agent Loop {turn}/{self.max_turns}]")

            # USER:
            # 原问题：“进行 HTTP/LLM 请求时，trim_context 上下文数据？”
            #
            # 判断：是。在每一次模型 API 请求“之前”进行 Context 构造/预算控制。
            # 但本版不再 trim/mutate Runtime State，而是生成 context_messages 副本。
            # 因此下面的 HTTP 请求只接收 context_messages。
            context_messages = build_context(self.state, self.max_context_chars)

            try:
                assistant = self.client.consume(context_messages)
            except KeyboardInterrupt:
                print("\n[已中止本轮]")
                return final_text
            except ModelError as exc:
                print(f"\n[模型错误] {exc}", file=sys.stderr)
                return final_text

            # LLM 完整 Turn 已确认 -> 状态转换边界。
            self.record(assistant)
            final_text = assistant.get("content") or ""

            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                return final_text

            for tool_call in tool_calls:
                call_id = tool_call.get("id", "")
                function = tool_call.get("function") or {}
                tool_name = function.get("name", "")
                raw_arguments = function.get("arguments", "{}")

                try:
                    arguments = json.loads(raw_arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {"_raw": raw_arguments}

                print(f"[tool] {tool_name}({json.dumps(arguments, ensure_ascii=False)})")
                result = execute_tool(tool_name, arguments)

                # Tool Observation 已确认 -> 状态转换边界。
                self.record({"role": "tool", "tool_call_id": call_id, "content": result})

            print("[state] tool_result 已进入 Runtime State + JSONL，继续 Agent Loop")

        print(f"[警告] 达到最大 Agent Loop 轮数：{self.max_turns}")
        return final_text


# =============================================================================
# 7. Session Lifecycle
# =============================================================================

def open_or_create_session(
    store: SessionStore,
    cfg: ModelConfig,
    session_name: str | None,
    resume: bool,
    explicit_model: str | None,
) -> SessionState:
    """创建或恢复 Session；Resume = JSONL replay -> SessionState。"""
    name = session_name or generate_session_name()
    validate_session_name(name)

    if resume:
        state = store.load(name)
        # 默认沿用历史模型；显式 --model 优先级更高。
        cfg.model = explicit_model or state.model or cfg.model
        state.model = cfg.model
        return state

    if explicit_model:
        cfg.model = explicit_model
    return store.create(name, SYSTEM_PROMPT, cfg.model)


# =============================================================================
# 8. CLI / REPL
# =============================================================================

def print_sessions(store: SessionStore) -> None:
    rows = store.list_sessions()
    if not rows:
        print("没有历史 session。")
        return

    print("NAME                         MESSAGES   SIZE       UPDATED")
    print("-" * 72)
    for row in rows:
        print(f"{row['name']:<28} {row['messages']:>8}   {row['size']:>8}   {row['updated']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HelloLLM S05 最小 State Persistence Agent（SSE Stream）"
    )
    parser.add_argument("--session", help="新建 session 名，例如 demo")
    parser.add_argument("--resume", metavar="SESSION", help="恢复已有 session")
    parser.add_argument("--list-sessions", action="store_true", help="列出历史 session 后退出")
    parser.add_argument("-p", "--prompt", help="无头模式：执行一次请求后退出")
    parser.add_argument("--api-key", help="临时覆盖 API key")
    parser.add_argument("--api-base", help="临时覆盖 API base")
    parser.add_argument("--model", help="临时覆盖模型名；Resume 时也优先于历史模型")
    parser.add_argument("--timeout", type=float, help="HTTP timeout 秒数")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--max-context", type=int, default=DEFAULT_MAX_CONTEXT_CHARS)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    store = SessionStore()

    if args.list_sessions:
        print_sessions(store)
        return

    cfg = load_config()
    if args.api_key:
        cfg.api_key = args.api_key
    if args.api_base:
        cfg.api_base = args.api_base.rstrip("/")
    if args.timeout:
        cfg.timeout = args.timeout

    if args.resume:
        state = open_or_create_session(store, cfg, args.resume, True, args.model)
    else:
        state = open_or_create_session(store, cfg, args.session, False, args.model)

    runtime = AgentRuntime(
        state=state,
        store=store,
        client=StreamClient(cfg),
        max_turns=args.max_turns,
        max_context_chars=args.max_context,
    )

    print("=" * 72)
    print("HelloLLM S05 State Persistence Agent")
    print(f"session : {state.name}")
    print(f"model   : {cfg.model}")
    print(f"store   : {store.path_for(state.name)}")
    print("stream  : ON")
    print("=" * 72)

    if args.prompt:
        runtime.run_turn(args.prompt)
        return

    print("命令：/exit 或 /quit 退出；/sessions 查看历史；/show-state 查看完整内存 State。")
    while True:
        try:
            user_text = input("\nuser> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            break

        if not user_text:
            continue
        if user_text in {"/exit", "/quit"}:
            break
        if user_text == "/sessions":
            print_sessions(store)
            continue
        if user_text == "/show-state":
            print(json.dumps(state.messages, ensure_ascii=False, indent=2))
            continue

        runtime.run_turn(user_text)


if __name__ == "__main__":
    main()

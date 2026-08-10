#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
HelloLLM S01 最小单文件 Agent

来源：
https://github.com/Nuos/HelloLLM/tree/main/S01-basic-loop

保留 S01 的真实业务闭环：
用户输入 -> messages -> LLM -> tool_calls -> 本地工具执行
-> tool_result 写回 messages -> 再调用 LLM -> 最终文本

配置文件：
~/.hellollm/config.json

示例：
{
  "api_key": "sk-...",
  "api_base": "https://api.deepseek.com",
  "model": "deepseek-v4-flash",
  "timeout": 120
}

运行：
python3 mini_s01_agent.py
"""

import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path


CONFIG_PATH = Path("~/.hellollm/config.json").expanduser()
DEFAULT_API_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT = 120
MAX_TURNS = 10
MAX_READ_BYTES = 200_000

# 下面的 SYSTEM_PROMPT 是 S01 的最小版本，保留了核心功能：
SYSTEM_PROMPT = (
    "You are HelloLLM, a minimal coding agent. "
    "You can read, write and edit files with tools. "
    "When a task involves files, use the tools."
)

# 这份列表是真正发送给 LLM 的工具 Schema。
# 模型根据 name / description / parameters 决定是否产生 tool_calls。
# 该部分 TOOLS 作为 prompt 进入 LLM调用的 context/上下文。。
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文本文件内容。任何需要读取文件的任务都用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入或覆盖文件，父目录不存在时自动创建。",
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
            "description": "用 new_string 替换文件中第一处 old_string。",
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


def read_file(path):
    p = Path(path).expanduser()

    if not p.exists():
        return f"错误：文件不存在：{path}"
    if p.is_dir():
        return f"错误：{path} 是目录"

    data = p.read_bytes()

    # S01 用前 8KB 中是否存在 NUL 字节做简单二进制检测。
    if b"\x00" in data[:8192]:
        return f"错误：{path} 是二进制文件"

    text = data.decode("utf-8", "replace")

    # 工具结果会进入下一轮上下文，所以限制单次读取体积。
    if len(data) > MAX_READ_BYTES:
        text = text[:MAX_READ_BYTES] + "\n…（文件过大，已截断）"

    return text


def write_file(path, content):
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {len(content.encode('utf-8'))} 字节到 {path}"


def edit_file(path, old_string, new_string):
    p = Path(path).expanduser()

    if not p.exists():
        return f"错误：文件不存在：{path}"

    text = p.read_text(encoding="utf-8")

    if old_string not in text:
        return f"错误：未找到完全一致的 old_string：{path}"

    n = text.count(old_string)

    # 与 S01 一样只替换第一处，避免一次调用误改所有同名文本。
    p.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")

    return f"已在 {path} 中替换第 1/{n} 处匹配"


# 这是本地执行侧的注册表。
# TOOLS 给模型看；TOOL_IMPL 给 Python Runtime 看；二者靠相同 name 对接。
TOOL_IMPL = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
}


# user:执行结果，通过权限检查的 LLM 工具得以被执行。
def execute_tool(name, arguments):
    fn = TOOL_IMPL.get(name)

    if fn is None:
        return f"错误：未知工具 {name}"

    try:
        return fn(**arguments)
    except Exception as e:
        # 工具失败不终止 Agent；错误作为 tool_result 回给模型继续处理。
        return f"错误：工具 {name} 执行失败：{e}"


# user:加载 配置信息，即： 模型配置文件位置、API key、Model、Timeout 、API接口地址。
def load_config():
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"未找到配置文件：{CONFIG_PATH}\n"
            '请创建：{"api_key":"sk-...","api_base":"https://api.deepseek.com",'
            '"model":"deepseek-v4-flash","timeout":120}'
        )

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    api_key = cfg.get("api_key", "")                              # API key
    api_base = cfg.get("api_base", DEFAULT_API_BASE).rstrip("/")  # API接口地址,run: https://api.deepseek.com/chat/completions, https://api.openai.com/v1/chat/completions, https://api.openai.com/v1/completions
    model = cfg.get("model", DEFAULT_MODEL)                       # 模型, 如 deepseek-v4-flash， gpt-4o-mini，gpt-4o，gpt-4，gpt-3.5-turbo 等
    timeout = float(cfg.get("timeout", DEFAULT_TIMEOUT))          # （与LLM服务器通讯）通过 http 请求的超时时间，单位秒，默认 120 秒。

    if not api_key:
        raise SystemExit(f"{CONFIG_PATH} 中缺少 api_key")

    return api_key, api_base, model, timeout


# user: 详细解释一下 流式协议及概念？以及类似还有其他什么协议？？
def call_model(messages, api_key, api_base, model, timeout):
    ###################
    ###
    # user: 真实发送给 LLM 的请求体，包含（1）模型/model、（2）消息列表/messages、（3）工具列表/TOOLS、（4）流式标记/stream。
    # user: 其中 messages 是一个列表，包含了（1）系统消息、（2）用户消息、（3）助手消息等，按照顺序排列。
    # S01 使用 OpenAI-compatible /chat/completions + SSE 流式协议。
    payload = {
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "stream": True,
    }

    # user：结合真实代码逻辑业务，解释含义？？？
    req = urllib.request.Request(
        f"{api_base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    ###################
    ###
    text = ""

    # OpenAI 流式 tool_calls 会分成多个 delta；
    # 按 index 把 id、name、arguments 重新拼成完整工具调用。
    tool_buf = {}

    # user：结合真实代码逻辑业务，解释含义？？？
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)   --- 通过 http 请求发送给 LLM，返回一个响应对象 resp。

        ###################
        ###
        # user：结合真实代码逻辑业务，解释含义？？？
        # user：resp 是一个迭代器，逐行读取 LLM 的流式响应数据，每行都是一个 SSE 事件。
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()

            if not line.startswith("data:"):
                continue

            data = line[5:].strip()

            if data == "[DONE]":
                break

            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue

            choices = event.get("choices") or []           --- user：第01环节处理，接收数据处理。拆解 choices 列表标签。 
            if not choices:
                continue

            delta = choices[0].get("delta") or {}

            if delta.get("content"):                       --- user：第02环节处理，接收数据处理。文本信息类型。 
                piece = delta["content"]
                text += piece                              --- user：将SSE式返回的数据，文本信息类型/content 拼接成完整的文本响应 text。
                print(piece, end="", flush=True)

            # user：结合真实代码逻辑业务，解释含义？？？ 这里似乎是对 tool_calls 的增量处理，主要是为了将 LLM 返回的工具调用请求进行拼接，最终形成完整的工具调用请求。
            # reasoning_content 属于模型推理增量。
            # S01 原版单独交给 UI；最小版只消费最终 content，不显示内部推理。
            for tool_call_tag in delta.get("tool_calls") or []:        --- user: 第03环节处理，接收数据处理。工具调用类型。拆解 tool_calls 列表标签。
                index = tool_call_tag.get("index", 0)
                fn = tool_call_tag.get("function") or {}

                buf = tool_buf.setdefault(                      --- user：将 tool_calls 按 index 进行缓存，最终形成完整的工具调用请求。
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )

                if tool_call_tag.get("id"):
                    buf["id"] = tool_call_tag["id"]
                if fn.get("name"):
                    buf["name"] = fn["name"]

                buf["arguments"] += fn.get("arguments", "")

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {e.code}: {body}") from e

    except (urllib.error.URLError, OSError) as e:
        if isinstance(e, (socket.timeout, TimeoutError)):
            raise RuntimeError(f"请求超时（>{timeout} 秒）") from e
        raise RuntimeError(f"网络错误：{getattr(e, 'reason', e)}") from e

    if text:
        print()

    ###################
    ###
    tool_calls = []

    # user：结合真实代码逻辑业务，解释含义？？？
    for index in sorted(tool_buf):
        buf = tool_buf[index]
        raw_args = buf["arguments"].strip()

        try:
            arguments = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            arguments = {"_raw": raw_args}

        tool_calls.append(
            {
                "id": buf["id"],
                "name": buf["name"],
                "arguments": arguments,
            }
        )

    ###################
    ###
    return text, tool_calls

"""
user:
    1. messages 追加顺序：
      1.1 无工具调用时，messages 追加 assistant 的文本响应 text；
      1.2 有工具调用时，messages 追加 ：
        （1）首先，protocol_calls（即 assistant 的 tool_calls）列表 ； 
        （2）然后，每个 tool_call 处理返回的文本结果。

    2. 最小agent-loop 内的运行机制：
      2.1 http 网络超时异常处理：如果 LLM 返回网络错误，那么自动重试 range(1,4)；
      2.2 判断是否有工具调用：如果没有工具调用，那么就把 LLM 的文本响应 text 追加到 messages 中，并返回结束当前轮次；
      2.3 如果有工具调用，那么就把 LLM 的工具调用请求 tool_calls 追加到 messages 中，并逐个真实执行工具调用，回填结果到 messages 中；

"""
def run_agent(messages, api_key, api_base, model, timeout):
    # Agent 核心循环：
    # model -> tool_calls? -> execute -> tool_result -> model -> ...
    for turn in range(1, MAX_TURNS + 1):
        print(f"[Agent {turn}/{MAX_TURNS}] {model}")

        # user：该模块是一个 http请求超时 异常处理。如果LLM返回网络错误，那么自动重试 range(1,4) 
        for attempt in range(1, 4):
            try:
                text, tool_calls = call_model(
                    messages, api_key, api_base, model, timeout
                )
                break
            except RuntimeError as e:
                if str(e).startswith("HTTP ") or attempt == 3:
                    raise
                print(f"[网络错误] {e}，重试 {attempt}/3")
                time.sleep(attempt)

        # user：无工具调用结束轮次/turn分支。（1）如果 LLM 没有请求工具调用，那么就把 LLM 的文本响应 text 追加到 messages 中，并返回结束当前轮次。
        # user：显然这里可以做优化处理。
        # 没有工具调用，就是 S01 Agent Loop 的主要停止条件。
        if not tool_calls:
            messages.append({"role": "assistant", "content": text})
            return

        # 先保存 LLM/assistant 的 tool_calls。
        # user: 此处为何要进行此操作？为何将LLM/assistant的tools_calls 进行转换为 protocol_calls？？
        # role="tool" 必须对应前面 assistant.tool_calls 中的 tool_call_id。
        protocol_calls = []

        for tc in tool_calls:
            protocol_calls.append(
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(
                            tc["arguments"], ensure_ascii=False
                        ),
                    },
                }
            )

        # user:将LLM请求调用的 protocol_calls 记录到 message 记录中???
        messages.append(
            {
                "role": "assistant",
                "content": text or None,
                "tool_calls": protocol_calls,
            }
        )

        # 一个响应可能请求多个工具，逐个真实执行并回填结果。
        # user: 这里为什么要进行 tool_calls 的循环执行？显然是因为 LLM 可能一次性请求多个工具调用，而每个工具调用都需要被执行并返回结果给 LLM。
        for tc in tool_calls:
            print(f"[Tool] {tc['name']}({tc['arguments']})")

            result = execute_tool(tc["name"], tc["arguments"])

            print(f"[Result] {result[:300]}")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )

    print(f"[停止] 达到最大循环轮数 {MAX_TURNS}")



"""
user:
  此处 messages 最小构成及其含义：
  1. agent 会话状态
  2. 系统提示词/system_prompt：
    此处为固定（但生产环境中可能需要动态调整）；
    system_prompt 主要用于约束模型行为，告诉模型它是一个最小的编码 Agent，可以读写文件，并且在涉及文件的任务中使用工具。
  3. 用户输入/user_input：
    用户输入的文本内容，作为模型的输入：此处即为由 CLI输入部分内容，即一次完整轮次/Turn 起点开始。
"""
def main():
    api_key, api_base, model, timeout = load_config()

    # messages 就是最小 Agent 的会话状态：
    # system / user / assistant(tool_calls) / tool 按真实调用顺序不断追加。
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]             # messages, 系统提示词。

    print("HelloLLM S01 Minimal Agent")
    print("输入 quit / exit 结束。\n")

    while True:
        try:
            user_text = input("You> ").strip()                           # user_text, 用户输入。即 surface/Interface/入口层，其他如 chat-box， SDK- agent， 桌面app
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_text:
            continue

        if user_text.lower() in {"quit", "exit"}:
            break

        messages.append({"role": "user", "content": user_text})          # messages, 用户输入。

        try:
            print("Agent> ", end="", flush=True)
            run_agent(messages, api_key, api_base, model, timeout)       # messages, 真实执行 Agent 核心循环。
        except Exception as e:
            # API/网络错误只结束当前轮，不退出 REPL。
            print(f"\n[错误] {type(e).__name__}: {e}")

        print()


if __name__ == "__main__":
    main()

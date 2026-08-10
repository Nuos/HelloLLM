#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
HelloLLM S01 最小单文件 Agent —— USER 注释审阅修订版

来源：
https://github.com/Nuos/HelloLLM/tree/main/S01-basic-loop

本版本处理内容：
1. 对原文件中所有带 user / USER 标签的理解逐项评估；
2. 正确的理解保留并补充边界；
3. 部分正确的理解说明“正确到哪里、缺什么”；
4. 错误理解直接纠正；
5. 将原文件中使用 `---` 写行内说明造成的 Python 语法错误改为合法注释；
6. 不改变 S01 的核心业务逻辑，不擅自加入 Permission / MCP / Hook / Memory 等后续能力。

S01 的真实最小闭环：

    用户输入
       ↓
    messages
       ↓
    call_model(messages, TOOLS)
       ↓
    LLM 返回 text / tool_calls
       ↓
    ┌─────────────── 是否有 tool_calls？ ───────────────┐
    │                                                   │
    │ 无                                                │ 有
    ↓                                                   ↓
assistant text 写入 messages                  assistant(tool_calls) 写入 messages
    ↓                                                   ↓
run_agent() 返回                               execute_tool() 本地真实执行
                                                        ↓
                                               role="tool" 结果写回 messages
                                                        ↓
                                                 再次调用 LLM
                                                        ↓
                                                       循环

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
python3 mini_s01_agent_reviewed.py
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


# [USER评估：正确]
# 原理解：SYSTEM_PROMPT 是 S01 的最小版本，保留核心功能。
#
# 补充：
# SYSTEM_PROMPT 是“给模型看的行为级约束”，不是 Python 执行权限。
# 它告诉模型：你是什么 Agent、你有哪些能力倾向、涉及文件时应该优先使用工具。
# 但即使 system prompt 写“可以写文件”，真正能不能写仍由 Python 是否注册并执行
# write_file 决定。Prompt 是模型侧约束；Runtime 才是程序侧能力。
SYSTEM_PROMPT = (
    "You are HelloLLM, a minimal coding agent. "
    "You can read, write and edit files with tools. "
    "When a task involves files, use the tools."
)


# [USER评估：基本正确，但术语需要更精确]
# 原理解：
#   TOOLS 作为 prompt 进入 LLM 调用的 context / 上下文。
#
# 更精确的说法：
#   TOOLS 确实和 messages 一起发送给模型，都会影响模型下一步决策；
#   但在 OpenAI-compatible API 的“协议字段”上，TOOLS 不是 messages 里的普通文本 Prompt，
#   而是请求体中的独立 `tools` 字段。
#
# 因此可以从两个层面理解：
#   语义层：TOOLS 是模型上下文的一部分，模型会读取其 name/description/schema。
#   协议层：TOOLS 与 messages 是并列字段，不应说成“TOOLS 被塞进 messages”。
#
# 这份列表是真正发送给 LLM 的工具 Schema：
#   name        -> 模型返回 tool_calls 时使用的工具名
#   description -> 告诉模型何时使用该工具
#   parameters  -> 约束模型应该生成哪些参数
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


# 这是本地执行侧的工具实现注册表。
#
# TOOLS：
#   给模型看，告诉 LLM“有哪些工具、参数长什么样”。
#
# TOOL_IMPL：
#   给 Python Runtime 看，告诉程序“工具名具体对应哪个 Python 函数”。
#
# 两边通过同一个 name 连接：
#
#   TOOLS 中 name="read_file"
#             ↓
#   LLM 返回 tool_calls.name="read_file"
#             ↓
#   TOOL_IMPL["read_file"]
#             ↓
#   真正执行 Python read_file(...)
TOOL_IMPL = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
}


# [USER评估：错误，混入了 S01 尚不存在的 Permission System]
# 原理解：
#   “通过权限检查的 LLM 工具得以被执行。”
#
# 问题：
#   S01-basic-loop 明确还没有 Permission System。
#   这里没有“先审批 -> 再执行”的代码，也没有 allow/deny/ask 之类的权限门。
#
# 本函数真实业务逻辑是：
#   1. 接收 LLM 选择的工具名 name 和参数 arguments；
#   2. 在 TOOL_IMPL 中查找对应 Python 函数；
#   3. 找到就直接执行；
#   4. 未找到或执行失败，就把错误字符串作为 tool_result 返回给 LLM。
#
# 如果以后进入 Permission 阶段，调用链才会变成：
#   tool_call -> permission_check -> execute_tool -> tool_result
def execute_tool(name, arguments):
    fn = TOOL_IMPL.get(name)

    if fn is None:
        return f"错误：未知工具 {name}"

    try:
        return fn(**arguments)
    except Exception as e:
        # 工具失败不终止 Agent；错误作为 tool_result 回给模型继续处理。
        return f"错误：工具 {name} 执行失败：{e}"


# [USER评估：正确]
# 原理解：
#   load_config() 加载模型配置文件位置、API key、Model、Timeout、API 接口地址。
#
# 补充两个边界：
#   1. CONFIG_PATH 本身不是从 JSON 里加载，而是代码顶部已经固定为 ~/.hellollm/config.json；
#      load_config() 是“按照这个路径读取配置内容”。
#   2. api_base 是 API 的基础地址，不是完整 chat/completions URL；
#      完整 URL 在 call_model() 中由：
#          f"{api_base}/chat/completions"
#      拼出来。
def load_config():
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"未找到配置文件：{CONFIG_PATH}\n"
            '请创建：{"api_key":"sk-...","api_base":"https://api.deepseek.com",'
            '"model":"deepseek-v4-flash","timeout":120}'
        )

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    # API key：用于 HTTP Authorization: Bearer <api_key>。
    api_key = cfg.get("api_key", "")

    # api_base：基础 API 地址，不应在这里写完整 /chat/completions。
    # 例如本程序默认 DeepSeek base 为：
    #     https://api.deepseek.com
    #
    # 如果使用某个 OpenAI-compatible 服务，它若要求版本前缀，
    # base 可能类似：
    #     https://api.openai.com/v1
    #
    # 然后 call_model() 统一追加：
    #     /chat/completions
    #
    # 注意：原 user 注释把
    #   https://api.deepseek.com/chat/completions
    #   https://api.openai.com/v1/chat/completions
    # 当成 api_base 示例，这不准确；那些是“最终 endpoint”。
    # 此函数的 api_base 应只保存 endpoint 前面的基础部分。
    api_base = cfg.get("api_base", DEFAULT_API_BASE).rstrip("/")

    # model：发给服务端的模型标识。
    # 不能只看模型名字本身，还必须与 api_base 对应的服务商兼容，
    # 且本 Agent 依赖 tool_calls，因此所选模型还需要支持兼容的工具调用协议。
    model = cfg.get("model", DEFAULT_MODEL)

    # timeout：urllib 等待 HTTP 请求/流式读取的超时秒数。
    # 默认 120 秒。它不是“模型最大推理时间”的严格业务 SLA，
    # 而是本 HTTP 客户端用于判定网络 I/O 超时的参数。
    timeout = float(cfg.get("timeout", DEFAULT_TIMEOUT))

    if not api_key:
        raise SystemExit(f"{CONFIG_PATH} 中缺少 api_key")

    return api_key, api_base, model, timeout

'''
# [USER问题补充：SSE 流式协议是什么？还有哪些类似方式？]
#
# 本文件里的调用链是：
#
#   HTTP POST /chat/completions
#       +
#   stream=true
#       ↓
#   服务端保持 HTTP Response 打开
#       ↓
#   持续发送 SSE 风格的 data: {...}
#       ↓
#   客户端边收到边解析 delta
#
# SSE = Server-Sent Events。
#
# 对本 Agent 来说，它解决的是：
#   “LLM 一次回答可能很慢，不等整个 JSON 全部生成完，
#    服务端可以把正文 token / tool_call 参数增量不断推给客户端。”
#
# 需要区分：
#
#   HTTP      ：底层请求/响应通信机制；
#   SSE       ：在长连接 HTTP Response 上持续发送文本事件的一种事件流格式；
#   JSON      ：每个 data: 后面载荷的结构化编码格式；
#   tool_calls：LLM API 定义的业务字段，不是网络协议本身。
#
# 常见的其他模式：
#
#   1. 非流式 HTTP JSON
#      stream=false，一次请求，等待完整 JSON 后整体返回。
#
#   2. NDJSON / JSON Lines
#      一行一个 JSON 对象，适合增量流，但不使用 SSE 的 data: 前缀。
#
#   3. WebSocket
#      长连接、全双工；客户端和服务端都可以主动持续发送消息。
#      SSE 更偏“服务器持续推送给客户端”。
#
#   4. gRPC Streaming
#      常见于服务间通信，可做 server-stream / client-stream / bidirectional-stream。
#
#   5. HTTP Long Polling
#      客户端发请求，服务端长时间挂起直到有数据；返回后客户端再发下一次请求。
#
# 本 S01 代码实现的是：
#   OpenAI-compatible Chat Completions + HTTP POST + SSE 风格流式返回。
'''
def call_model(messages, api_key, api_base, model, timeout):

    # [USER评估：正确]
    # 原理解：
    #   真实发送给 LLM 的请求体包含：
    #   model / messages / TOOLS / stream。
    #
    # 补充：
    #   这里 `payload` 只是“准备好的 Python dict”；
    #   真正发送发生在后面的 urllib.request.urlopen(req, ...)。
    #
    # [USER评估：基本正确]
    # messages 的确是有顺序的消息列表，但完整运行时不只有：
    #   system / user / assistant
    # 还会包含：
    #   role="tool"
    #
    # 有工具调用时典型顺序是：
    #   system
    #   user
    #   assistant(tool_calls)
    #   tool(result)
    #   assistant(...)
    payload = {
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "stream": True,
    }

    # [USER问题：req 结合真实业务到底是什么？]
    #
    # req 只是“HTTP 请求对象”，此行尚未真正联网。
    #
    # 四个真实业务组成：
    #
    #   1. URL
    #      f"{api_base}/chat/completions"
    #      例如 api_base=https://api.deepseek.com
    #      最终得到 https://api.deepseek.com/chat/completions
    #
    #   2. body
    #      json.dumps(payload) 把 Python dict 序列化为 JSON 文本；
    #      .encode("utf-8") 再变成 HTTP 可以发送的 bytes。
    #
    #   3. Content-Type
    #      告诉服务端“请求 body 是 JSON”。
    #
    #   4. Authorization
    #      Bearer Token 鉴权，将 API key 放进请求头，而不是放进 messages。
    #
    # 真正发出请求是在后面的 urlopen(req, timeout=timeout)。
    req = urllib.request.Request(
        f"{api_base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    # text：把多次 content delta 累积成这一轮模型的完整正文。
    text = ""

    # tool_buf：把被 SSE 拆散的 tool_calls 增量临时拼起来。
    #
    # 为什么必须缓存？
    # 模型可能不是一次返回：
    #
    #   {"name":"write_file","arguments":"{\"path\":"}
    #   {"arguments":"\"a.txt\",\"content\":"}
    #   {"arguments":"\"hello\"}"}
    #
    # 而是分若干个 delta 到达。
    # 所以必须等流结束后，才能得到完整：
    #
    #   name = write_file
    #   arguments = {"path":"a.txt","content":"hello"}
    tool_buf = {}

    # [USER问题：try / urlopen 这一段的真实业务是什么？]
    #
    # urlopen(req, timeout=timeout) 才是真正执行 HTTP 请求：
    #   DNS / TCP/TLS / HTTP POST / 等待 Response 都从这里开始。
    #
    # 返回的 resp 是 HTTPResponse 对象：
    #   - 可以读取响应头；
    #   - 可以 read()；
    #   - 在这里也可以直接迭代，逐行消费流式响应。
    #
    # try/except 的业务目的：
    #   把 HTTP 错误、网络错误、超时错误统一翻译成 Agent 上层易处理的 RuntimeError。
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)

        # [USER评估：部分正确]
        # 原理解：
        #   resp 是一个迭代器，逐行读取 LLM 的流式响应数据，
        #   每行都是一个 SSE 事件。
        #
        # 正确部分：
        #   `for raw in resp` 确实按“响应中的行”读取 bytes。
        #
        # 需要修正：
        #   从通用 SSE 规范看，“一个事件”理论上可以由多行字段组成，
        #   空行才表示一个事件结束，因此不能抽象地说“每一行永远等于一个 SSE Event”。
        #
        # 但本 S01 针对 OpenAI-compatible API 的实际返回格式，
        # 通常每个有效数据块表现为：
        #
        #   data: {一段 JSON}
        #
        # 所以此处代码采用了“一行一个 data chunk”的简化解析策略。
        for raw in resp:
            # raw 是 bytes；网络传输来的字节必须先转 UTF-8 文本。
            # "replace" 表示遇到非法 UTF-8 时使用替换字符而不是直接抛异常。
            line = raw.decode("utf-8", "replace").strip()

            # SSE 可能包含空行、注释/心跳或其他字段。
            # 这个最小实现只关心 data: 行，其余直接跳过。
            if not line.startswith("data:"):
                continue

            # 去掉 SSE 的 "data:" 前缀，只保留后面的业务载荷。
            data = line[5:].strip()

            # OpenAI-compatible 流式接口常用 [DONE] 作为流结束哨兵。
            if data == "[DONE]":
                break

            try:
                # data: 后面是 JSON 文本，把它解析成 Python dict。
                event = json.loads(data)
            except json.JSONDecodeError:
                # 单个坏数据块不让整个 Agent 崩掉；最小版直接忽略。
                continue

            # [USER评估：方向正确，术语“标签”不精确]
            # 原理解：
            #   “第01环节：拆解 choices 列表标签。”
            #
            # 更精确：
            #   `event` 是服务端返回的一次 JSON chunk；
            #   `choices` 是该 JSON 对象中的一个字段，其值是列表。
            #   Chat Completions 通常把模型生成增量放在 choices[0]。
            choices = event.get("choices") or []

            if not choices:
                continue

            # delta 才是“这一小块增量内容”。
            # 它可能包含 content，也可能包含 tool_calls 等字段。
            delta = choices[0].get("delta") or {}

            # [USER评估：正确]
            # 这是第02类处理：正文 content 增量。
            if delta.get("content"):
                piece = delta["content"]

                # [USER评估：正确]
                # SSE 多个 content delta 被逐块累加，最终组成完整 text。
                text += piece

                # 同时立即打印 piece，所以用户能看到边生成边输出，而不是最后一次性显示。
                print(piece, end="", flush=True)

            # [USER评估：正确]
            # 原理解：
            #   这里是在处理 tool_calls 增量，把 LLM 分块返回的工具调用请求拼完整。
            #
            # 补充：
            #   tool_calls 与 content 是“同一个 delta 结构中不同类型的数据”。
            #   一个模型响应还可能同时存在多个 tool call，因此需要 index 做归组。
            #
            # reasoning_content 是部分推理模型的增量字段。
            # S01 原版会单独交给 UI；这个最小版不打印内部推理。
            #
            # [USER评估：正确]
            # “第03类处理：工具调用类型”这个理解成立。
            for tool_call_tag in delta.get("tool_calls") or []:
                # index 用来区分“这是第几个并行/同轮工具调用”的增量。
                index = tool_call_tag.get("index", 0)

                # function 内部携带工具 name 与 arguments 的增量。
                fn = tool_call_tag.get("function") or {}

                # [USER评估：正确]
                # setdefault 的作用：
                #   第一次看到某 index -> 创建空缓冲区；
                #   后续看到同一个 index -> 取回原来的缓冲区继续拼接。
                buf = tool_buf.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )

                # id 和 name 常常只在前面的 delta 出现一次，所以有值才覆盖保存。
                if tool_call_tag.get("id"):
                    buf["id"] = tool_call_tag["id"]

                if fn.get("name"):
                    buf["name"] = fn["name"]

                # arguments 最常被拆成很多字符串片段，因此必须使用 += 持续拼接。
                buf["arguments"] += fn.get("arguments", "")

    except urllib.error.HTTPError as e:
        # HTTP 4xx/5xx：
        # 例如鉴权、请求格式、额度、服务端异常。
        # 读取最多 500 字符错误体，便于定位问题又避免输出无限长。
        body = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {e.code}: {body}") from e

    except (urllib.error.URLError, OSError) as e:
        # 网络层异常：DNS、连接失败、socket 错误等。
        if isinstance(e, (socket.timeout, TimeoutError)):
            raise RuntimeError(f"请求超时（>{timeout} 秒）") from e

        raise RuntimeError(f"网络错误：{getattr(e, 'reason', e)}") from e

    if text:
        print()

    tool_calls = []

    # [USER问题：这里为什么又遍历 tool_buf？]
    #
    # 前面的循环解决的是“接收阶段”：
    #   把 SSE 碎片先按 index 缓存在 tool_buf 中。
    #
    # 这里解决的是“归一化阶段”：
    #   把缓存的字符串碎片转换成 Agent Loop 可以直接使用的 Python 数据结构。
    #
    # 两个阶段不能混为一谈：
    #
    #   SSE bytes/string fragments
    #             ↓
    #          tool_buf
    #             ↓
    #   parse JSON arguments
    #             ↓
    #      normalized tool_calls
    #
    # sorted(tool_buf) 让工具调用按 index 顺序稳定输出。
    for index in sorted(tool_buf):
        buf = tool_buf[index]

        # arguments 在流阶段一直是 JSON 字符串碎片；
        # 到这里已经拼完整，准备解析。
        raw_args = buf["arguments"].strip()

        try:
            # JSON 字符串 -> Python dict。
            arguments = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            # 如果模型生成的 arguments 不是合法 JSON，
            # 不直接让循环崩掉，保留原始内容供后续执行层返回错误。
            arguments = {"_raw": raw_args}

        # 转成 Agent 内部更方便使用的统一结构。
        tool_calls.append(
            {
                "id": buf["id"],
                "name": buf["name"],
                "arguments": arguments,
            }
        )

    # 返回本轮两个核心结果：
    #   text       -> 模型正文
    #   tool_calls -> 模型请求的零个或多个工具调用
    return text, tool_calls

'''
# ---------------------------------------------------------------------------
# [USER 对 run_agent 总结的逐项评估]
#
# 1. messages 追加顺序
#
# [正确，但需要更精确]
#
# 无工具调用：
#   assistant(text)
#
# 有工具调用：
#   assistant(content + tool_calls)
#   tool(result for call-1)
#   tool(result for call-2)
#   ...
#
# 注意：
#   不是把 `protocol_calls` 自己单独 append 成一条 message，
#   而是把它放在：
#
#       {
#           "role": "assistant",
#           "tool_calls": protocol_calls
#       }
#
#   这个 assistant message 中。
#
#
# 2. 最小 Agent Loop
#
# [2.1 部分正确]
# 不是“LLM 返回网络错误”，而是“调用 LLM API 的网络 I/O 发生异常”。
# LLM 正常返回一个 JSON 错误响应与 TCP/DNS/timeout 是不同层级的问题。
#
# 当前重试逻辑：
#   range(1, 4) = attempt 1,2,3
#
#   network / timeout -> 最多尝试 3 次
#   HTTP 4xx/5xx      -> 本代码直接 raise，不自动重试
#
# [2.2 正确]
# 没有 tool_calls：
#   将 assistant 文本写入 messages，然后 return；
#   这表示“当前这一条用户请求对应的 run_agent() 结束”。
#
# [2.3 正确]
# 有 tool_calls：
#   先记录 assistant(tool_calls)
#   -> 执行真实工具
#   -> 记录 role=tool 的结果
#   -> 下一次 for turn 重新调用模型。
#
# 这就是 Agent 比普通 Chat 多出来的关键闭环。
# ---------------------------------------------------------------------------
'''
def run_agent(messages, api_key, api_base, model, timeout):
    # Agent 核心循环：
    # model -> tool_calls? -> execute -> tool_result -> model -> ...
    #
    # 这里的 turn 更准确可称为 Agent internal step / iteration：
    # 同一个用户输入可能经历多个内部 turn。
    for turn in range(1, MAX_TURNS + 1):
        print(f"[Agent {turn}/{MAX_TURNS}] {model}")

        # [USER评估：部分正确]
        # 原理解：
        #   “HTTP 请求超时异常处理；如果 LLM 返回网络错误，就自动重试 range(1,4)。”
        #
        # 修正：
        #   这是 call_model() 的“有限网络重试层”。
        #   捕获的是 call_model() 转换后的 RuntimeError。
        #
        #   - HTTPError -> call_model() 转成 "HTTP xxx..."，这里不重试；
        #   - URLError/OSError/timeout -> 转成网络/超时 RuntimeError，这里重试；
        #   - attempt == 3 时仍失败 -> 抛给 main()。
        #
        # 所以 range(1,4) 的含义是“最多 3 次调用尝试”，
        # 不是“首次调用之后再额外重试 3 次”。
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

        # [USER评估：正确]
        # 原理解：
        #   无工具调用时，把 LLM 文本响应追加到 messages，并结束当前轮次。
        #
        # 更准确：
        #   这里的 return 结束的是本次 run_agent()，
        #   即“当前用户输入触发的完整 Agent 执行”。
        #
        #   外层 main() 的 REPL 仍继续运行，用户还能输入下一条消息。
        #
        # [USER补充：“这里显然可以优化”]
        # 可以优化，但不要破坏这个停止条件。可增加的生产级处理包括：
        #   - text 为空时明确报错；
        #   - 根据 finish_reason / stop_reason 更精细区分结束原因；
        #   - 对 context length 做裁剪；
        #   - 对异常响应做状态分类。
        #
        # S01 的最小逻辑仍然以“无 tool_calls”为主要停止条件。
        if not tool_calls:
            messages.append({"role": "assistant", "content": text})
            return

        # [USER问题：为什么要把 tool_calls 转成 protocol_calls？]
        #
        # call_model() 返回的是“Agent 内部归一化结构”：
        #
        #   {
        #       "id": "...",
        #       "name": "write_file",
        #       "arguments": {"path": "...", "content": "..."}
        #   }
        #
        # 这里的 arguments 已经是 Python dict，方便 Runtime 执行：
        #   execute_tool(name, arguments)
        #
        # 但是 OpenAI-compatible messages 协议要求 assistant.tool_calls 形如：
        #
        #   {
        #       "id": "...",
        #       "type": "function",
        #       "function": {
        #           "name": "...",
        #           "arguments": "{\"path\":\"...\"}"
        #       }
        #   }
        #
        # 也就是：
        #   内部执行格式 -> API 消息协议格式
        #
        # 所以 protocol_calls 不是“又生成一批新的工具调用”，
        # 而是把同一批 tool_calls 重新编码成“可以合法写回 messages”的格式。
        protocol_calls = []

        for tc in tool_calls:
            protocol_calls.append(
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        # Python dict -> JSON string
                        # 因为协议里的 function.arguments 是 JSON 字符串。
                        "arguments": json.dumps(
                            tc["arguments"], ensure_ascii=False
                        ),
                    },
                }
            )

        # [USER评估：正确]
        # 原理解：
        #   “将 LLM 请求调用的 protocol_calls 记录到 message 记录中？”
        #
        # 是。
        #
        # 但更准确地说：
        #   把“LLM 刚才作为 assistant 产生的工具调用动作”完整记录进对话历史。
        #
        # 为什么必须记录？
        # 因为下一条 role="tool" 结果必须能够回答：
        #   “我是对哪一个 assistant tool_call 的执行结果？”
        #
        # 协议通过 tool_call_id 建立对应关系：
        #
        #   assistant.tool_calls[i].id
        #              ↕
        #   tool.tool_call_id
        #
        # 如果只塞 tool result，却没有前面的 assistant.tool_calls，
        # 消息历史会失去因果链：
        #
        #   模型做了什么动作 -> 环境返回了什么观察结果
        messages.append(
            {
                "role": "assistant",
                "content": text or None,
                "tool_calls": protocol_calls,
            }
        )

        # [USER评估：正确]
        # 原理解：
        #   “为什么循环 tool_calls？因为 LLM 一次可能请求多个工具，
        #    每个都必须执行并返回结果。”
        #
        # 完全正确。
        #
        # 补充：
        #   当前实现是“串行执行”：
        #       call-1 -> result-1
        #       call-2 -> result-2
        #
        #   即使模型在同一响应里生成多个 tool_calls，也不是并行线程执行。
        #   生产系统可以根据工具副作用和依赖关系决定是否允许并行执行。
        for tc in tool_calls:
            print(f"[Tool] {tc['name']}({tc['arguments']})")

            result = execute_tool(tc["name"], tc["arguments"])

            print(f"[Result] {result[:300]}")

            # 把“真实环境观察结果”写回消息历史。
            # 这一步相当于：
            #
            #   Action      = assistant.tool_calls
            #   Observation = role="tool" result
            #
            # 下一轮模型调用就可以看到工具是否成功、文件内容是什么、错误是什么。
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )

    # MAX_TURNS 是防无限循环的硬停止条件。
    print(f"[停止] 达到最大循环轮数 {MAX_TURNS}")


# ---------------------------------------------------------------------------
# [USER 对 main()/messages 的理解评估]
#
# 原理解：
#   1. messages 是 Agent 会话状态；
#   2. system_prompt 固定，主要约束模型行为；
#   3. user_input 来自 CLI，是一次 Turn 的起点。
#
# 结论：
#   总体正确，但需要补上 role="tool" 和“Turn”概念边界。
#
# messages 在完整运行时最小可能出现四种 role：
#
#   1. system
#      系统级行为说明。
#
#   2. user
#      用户输入。
#
#   3. assistant
#      模型文本响应，或者模型产生的 tool_calls。
#
#   4. tool
#      Python Runtime 执行工具后的真实结果。
#
# 因此 messages 既是“对话历史”，也是最小版 Agent 的“工作状态存储”。
#
# 关于 Turn：
#
#   main() 中一次 user_text
#       = 用户层的一次输入/对话回合起点。
#
#   run_agent() 中 for turn in range(...)
#       = Agent 内部的一次迭代/step。
#
# 一个用户回合可以触发多个 Agent 内部 step：
#
#   user
#    ↓
#   model -> read_file
#    ↓
#   model -> edit_file
#    ↓
#   model -> final text
#
# 这仍然属于同一个用户请求触发的完整 Agent 执行。
# ---------------------------------------------------------------------------
def main():
    api_key, api_base, model, timeout = load_config()

    # [USER评估：正确]
    # messages 是最小 Agent 的会话状态。
    #
    # 初始化时只有 system；
    # 后面会按真实执行顺序追加：
    #   user
    #   assistant
    #   tool
    #   assistant
    #   ...
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        }
    ]

    print("HelloLLM S01 Minimal Agent")
    print("输入 quit / exit 结束。\n")

    while True:
        try:
            # [USER评估：基本正确]
            # user_text 确实来自 Interface / Surface 层。
            #
            # 当前这个具体 Surface 是 CLI / REPL 的 input()。
            # 如果换成 Web Chat Box、桌面 App、SDK、HTTP API，
            # 上层输入来源可以变化，但后面的 messages -> run_agent()
            # 核心循环可以保持不变。
            #
            # 注意：
            # “SDK-agent”更像一种程序接入方式，不完全等同于 UI surface；
            # 但它同样可以作为 Agent Loop 的输入入口。
            user_text = input("You> ").strip()

        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_text:
            continue

        if user_text.lower() in {"quit", "exit"}:
            break

        # [USER评估：正确]
        # 用户输入先以 role="user" 写进 messages。
        #
        # 从这一刻起，call_model() 才能在下一次请求中看到这条用户任务。
        messages.append(
            {
                "role": "user",
                "content": user_text,
            }
        )

        try:
            print("Agent> ", end="", flush=True)

            # [USER评估：正确]
            # 将同一个 messages 对象传给 run_agent()。
            #
            # run_agent() 不返回一个新 history；
            # 它直接在这个 list 上持续 append assistant/tool 消息。
            #
            # 所以 main() 和 run_agent() 看到的是同一份会话状态。
            run_agent(messages, api_key, api_base, model, timeout)

        except Exception as e:
            # API/网络错误只结束当前用户请求，不退出整个 REPL。
            print(f"\n[错误] {type(e).__name__}: {e}")

        print()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minimal_permission_agent_stream_full.py
=======================================

单文件、最小可运行 Coding Agent —— SSE Stream 完整版。

依据：
- Claude Code source：QueryEngine / Tool / permission boundary
- arXiv:2604.14228v2《Dive into Claude Code》
- HelloLLM/S02-basic-permission

本文件保留最小 Agent 的核心组件，并恢复真正的流式模型调用：

User                 -> input()
Interfaces            -> main() / REPL
Agent Loop            -> agent_loop()
Permission System     -> PermissionGate
Tools                 -> TOOLS + execute_tool()
State & Persistence   -> messages（仅进程内，不落盘）
Execution Environment -> 当前 Python 进程 + 本地文件系统

核心运动：

User
  ↓
messages
  ↓
LLM（SSE stream=True）
  ↓
text_delta / reasoning_delta / tool_call_delta
  ↓
聚合本轮 Assistant Message
  ↓
有 tool_calls ?
  ├─ No  -> 最终文本 -> STOP
  └─ Yes -> PermissionGate
               ↓
           execute_tool
               ↓
           tool_result
               ↓
            messages
               ↓
            下一轮 LLM

本版刻意不加入：
- Memory
- MCP
- Hooks
- Subagents
- Session 磁盘持久化
- Context Compression
- Sandbox

这些都属于更后面的 Agent Runtime 能力，不应污染 S02 的最小核心。

运行示例
--------

1. 使用环境变量：

    export AGENT_API_KEY="sk-..."
    export AGENT_API_BASE="https://api.deepseek.com"
    export AGENT_MODEL="deepseek-v4-flash"

    python3 minimal_permission_agent_stream_full.py

2. 使用命令行：

    python3 minimal_permission_agent_stream_full.py \
        --api-key "sk-..." \
        --api-base "https://api.deepseek.com" \
        --model "deepseek-v4-flash"

权限模式：

    默认：
        interactive
        read_file 自动允许
        write_file / edit_file 请求 y/N
        未知工具拒绝

    --read-only
        只允许 read_file

    --yes
        对“已注册工具”自动允许
        未知工具仍然 deny-first 拒绝

退出：
    /exit
    /quit
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator


# =============================================================================
# 1. Model / Agent 配置
# =============================================================================

DEFAULT_API_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_TURNS = 10

# 单次 read_file 最大读取字节数。
# 目的：避免一个工具结果把模型 Context 一次性塞满。
MAX_READ_BYTES = 200_000

SYSTEM_PROMPT = """You are a minimal coding agent.
Use the provided tools whenever a task requires reading or changing files.
Never pretend a tool succeeded: continue from the actual tool result.
After using tools, finish the user's task and report what you actually did.
"""


# =============================================================================
# 2. Tool Schemas —— 给 LLM 看的工具说明
# =============================================================================
#
# 这里非常重要：
#
# TOOLS
#   = “模型能看到的能力声明”
#
# read_file / write_file / edit_file
#   = “Python Runtime 真正执行的代码”
#
# LLM 本身不能直接调用 Python，也不能直接碰本地文件。
# 它只能产生结构化的 tool_call：
#
#     {
#         "name": "read_file",
#         "arguments": {"path": "README.md"}
#     }
#
# 然后由 Agent Harness / Runtime 决定是否允许、如何执行。
# =============================================================================

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative or absolute path of the text file",
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
            "description": "Create or overwrite a UTF-8 text file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Target file path",
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete text content to write",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace the first exact occurrence of old_string "
                "with new_string in a UTF-8 text file."
            ),
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


# =============================================================================
# 3. Tool Implementations —— 真正操作本地文件系统
# =============================================================================


def read_file(path: str) -> str:
    """
    只读工具：读取 UTF-8 文本文件。

    最小保护：
    - 文件不存在：返回错误 tool_result
    - 目录：返回错误 tool_result
    - 前 8KB 包含 NUL：按二进制文件处理
    - 超过 MAX_READ_BYTES：截断

    注意：
    工具异常尽量转换成“结果文本”返回给模型，而不是直接终止 Agent Loop。
    这样模型下一轮能够看到错误，再决定如何调整。
    """
    p = Path(path).expanduser()

    if not p.exists():
        return f"错误：文件不存在：{path}"

    if p.is_dir():
        return f"错误：{path} 是目录"

    data = p.read_bytes()

    if b"\x00" in data[:8192]:
        return f"错误：{path} 疑似二进制文件"

    text = data.decode("utf-8", "replace")

    if len(data) > MAX_READ_BYTES:
        text = text[:MAX_READ_BYTES] + "\n...（文件过大，已截断）"

    return text


def write_file(path: str, content: str) -> str:
    """写工具：创建或覆盖 UTF-8 文本文件。"""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")

    return f"已写入 {len(content.encode('utf-8'))} 字节到 {p}"


def edit_file(path: str, old_string: str, new_string: str) -> str:
    """
    写工具：精确替换第一处 old_string。

    为什么使用“精确匹配”：
    Coding Agent 的修改动作最好可预测、可审计。
    如果 old_string 不匹配，应让模型重新 read_file，再构造新的 edit_file，
    而不是由执行器自己猜测应该改哪里。
    """
    p = Path(path).expanduser()

    if not p.exists():
        return f"错误：文件不存在：{path}"

    text = p.read_text(encoding="utf-8")

    if old_string not in text:
        return (
            "错误：没有找到完全一致的 old_string；"
            "请先使用 read_file 查看当前文件内容后重新确认"
        )

    count = text.count(old_string)
    new_text = text.replace(old_string, new_string, 1)
    p.write_text(new_text, encoding="utf-8")

    return f"已修改 {p}：替换第 1/{count} 处匹配"


# Tool Registry：
# “模型工具名” -> “Python 实现函数”
TOOL_IMPL = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
}


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """
    工具执行分派器。

    LLM 只提供：
        name
        arguments

    Runtime 再根据 TOOL_IMPL 找到真正的 Python 函数。

    不使用 eval() / exec() 动态执行模型输出。
    未注册工具只会返回“未知工具”。
    """
    fn = TOOL_IMPL.get(name)

    if fn is None:
        return f"错误：未知工具 {name}"

    try:
        return fn(**arguments)
    except Exception as e:
        return f"错误：{name} 执行失败：{type(e).__name__}: {e}"


# =============================================================================
# 4. Permission System
# =============================================================================

READ = "read"
WRITE = "write"
DANGER = "danger"

INTERACTIVE = "interactive"
AUTO_ACCEPT = "auto-accept"
READ_ONLY = "read-only"

ALLOW = "allow"
ASK = "ask"
DENY = "deny"

# graduated trust：按副作用强弱对工具分类。
TOOL_POLICIES = {
    "read_file": READ,
    "write_file": WRITE,
    "edit_file": WRITE,
}


# region USER QUESTION 1 —— “user 负责决策，agent 负责执行”是否准确？
#
# 用户原注释：
#     user: 负责决策，决定是否允许工具执行。
#     agent: 负责执行，必须在执行前经过 user 的许可。
#
# 更准确的说法：
#
# 1. LLM / Agent Model
#       负责“提出动作”：
#           我要调用 write_file(path=..., content=...)
#
# 2. Permission System / Harness
#       负责先做机器策略判定：
#           ALLOW / ASK / DENY
#
# 3. User
#       只有当 Permission System 得到 ASK 时，才需要人工批准。
#       read_file 可以自动 ALLOW，因此并不是每个工具都必须询问用户。
#
# 4. Tool Runtime / Executor
#       才是真正执行 Python 文件操作的组件。
#
# 所以不能简单写成：
#
#       “Agent 负责执行”
#
# 更精确的是：
#
#       LLM 提议动作
#          ↓
#       PermissionGate 判定
#          ↓
#       必要时 User 批准
#          ↓
#       Python Runtime 执行动作
#
# endregion


class PermissionGate:
    """
    最小 Permission Gate。

    决策矩阵：

                    read      write      unknown/danger
    ---------------------------------------------------
    interactive     allow     ask        deny
    auto-accept     allow     allow      deny
    read-only       allow     deny       deny

    deny-first：
        TOOL_POLICIES 没有声明的工具默认 DANGER -> DENY。
    """

    def __init__(self, mode: str = INTERACTIVE):
        self.mode = mode

    # region USER QUESTION 2 —— check() 是否就是“判断权限层级、类型”？
    #
    # 用户原问题：
    #     user: 判断权限层级、类型
    #
    # 回答：
    #     不完全是。
    #
    #     level_for / TOOL_POLICIES 的职责才更接近：
    #         “这个工具属于 READ / WRITE / DANGER 哪一级？”
    #
    #     check() 做的是更进一步的“策略决策”：
    #
    #         工具等级 + 当前权限模式
    #                    ↓
    #             ALLOW / ASK / DENY
    #
    #     例如：
    #
    #         write_file
    #             工具等级 = WRITE
    #
    #         interactive 模式 -> ASK
    #         read-only 模式   -> DENY
    #         auto-accept 模式 -> ALLOW
    #
    #     因此 check() 的准确职责是：
    #
    #         Pure Policy Evaluation
    #         纯权限策略判定
    #
    #     它不执行 input()，也不执行工具，没有外部副作用。
    #
    # endregion
    def check(self, tool_name: str) -> str:
        level = TOOL_POLICIES.get(tool_name, DANGER)

        # 未知工具优先拒绝：deny-first。
        if level == DANGER:
            return DENY

        if self.mode == AUTO_ACCEPT:
            return ALLOW

        if level == READ:
            return ALLOW

        if self.mode == READ_ONLY:
            return DENY

        # 剩余情况：
        # WRITE + INTERACTIVE
        return ASK

    # region USER QUESTION 3 —— decide() 和 check() 到底有什么区别？
    #
    # 用户原问题：
    #     decide 函数与 check 有什么区别？
    #     仅仅是为了降低耦合度、class PermissionGate 分层管理吗？
    #
    # 回答：
    #     不只是“降低耦合”。
    #
    # check()
    # -------
    #     是“纯策略函数”：
    #
    #         tool_name
    #            ↓
    #       mode + policy
    #            ↓
    #       ALLOW / ASK / DENY
    #
    #     它不与用户交互。
    #
    # decide()
    # --------
    #     是“最终许可解析”：
    #
    #         check() == ALLOW -> True
    #         check() == DENY  -> False
    #         check() == ASK   -> 询问用户 y/N -> True / False
    #
    # 因此二者分离至少有四个意义：
    #
    # 1. Policy 与 UI 分离
    #       check() 不关心 CLI、Web、IDE 如何询问用户。
    #
    # 2. 纯函数容易测试
    #       测试 check("write_file") == ASK 不需要模拟 input()。
    #
    # 3. ASK 不是最终结果
    #       ASK 仍需要用户/审批系统解析成最终 True / False。
    #
    # 4. 未来容易替换审批方式
    #       当前 decide() 用 input()。
    #       生产系统可以替换成 GUI Dialog、Web Approval、Inbox 等。
    #
    # 所以：
    #
    #       check  = policy decision
    #       decide = permission resolution
    #
    # 这是职责边界，而不仅仅是“为了少耦合一点”。
    #
    # endregion
    def decide(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        decision = self.check(tool_name)

        if decision == ALLOW:
            return True

        if decision == DENY:
            return False

        # 只有 ASK 才会到这里。
        print("\n⚠ 权限请求")
        print(f"  工具：{tool_name}")
        print(f"  参数：{json.dumps(arguments, ensure_ascii=False)}")

        answer = input("  允许执行？[y/N] ").strip().lower()
        return answer in {"y", "yes"}


# =============================================================================
# 5. Model Client —— OpenAI-compatible SSE Stream
# =============================================================================
#
# 这里恢复 stream=True。
#
# 模型响应不再是一次性 JSON，而是 SSE：
#
#     data: {...delta...}
#     data: {...delta...}
#     data: {...tool_call fragment...}
#     data: [DONE]
#
# 因此必须处理三个重要增量：
#
#     text_delta
#     reasoning_delta
#     tool_call_delta
#
# 特别是 tool_call：
#
#     function.arguments
#
# 经常会被拆成很多 SSE 小块，所以不能拿到一块就 json.loads()；
# 必须先按 tool index 聚合完整 arguments 字符串，流结束后再解析。
# =============================================================================


def stream_chat(
    messages: list[dict[str, Any]],
    *,
    api_base: str,
    api_key: str,
    model: str,
    timeout: float,
) -> Iterator[dict[str, Any]]:
    """
    底层 SSE 客户端。

    只负责：
        HTTP 请求
        SSE 拆包
        把 provider delta 转换成内部统一事件

    产出事件：
        text_delta
        reasoning_delta
        tool_call_delta
    """
    payload = {
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",

        # ★ 用户明确要求：保持真正的流式模式。
        "stream": True,
    }

    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").strip()

                # SSE 空行 / 注释 / keep-alive
                if not line or line.startswith(":"):
                    continue

                if not line.startswith("data:"):
                    continue

                data = line[5:].strip()

                if data == "[DONE]":
                    break

                try:
                    packet = json.loads(data)
                except json.JSONDecodeError:
                    # 单个坏 SSE 包不直接杀死整个流。
                    continue

                choices = packet.get("choices") or []
                if not choices:
                    continue

                delta = choices[0].get("delta") or {}

                # 1. 普通答案文本增量
                content = delta.get("content")
                if content:
                    yield {
                        "type": "text_delta",
                        "text": content,
                    }

                # 2. 某些推理模型使用 reasoning_content
                reasoning = delta.get("reasoning_content")
                if reasoning:
                    yield {
                        "type": "reasoning_delta",
                        "text": reasoning,
                    }

                # 3. 工具调用增量
                #
                # 一个完整的 arguments JSON 可能被拆成：
                #
                #   '{"pa'
                #   'th":'
                #   '"README.md"}'
                #
                # 所以这里只产出 fragment，不在这里解析 JSON。
                for tool_delta in delta.get("tool_calls") or []:
                    function_delta = tool_delta.get("function") or {}

                    yield {
                        "type": "tool_call_delta",
                        "index": tool_delta.get("index", 0),
                        "id": tool_delta.get("id") or "",
                        "name": function_delta.get("name") or "",
                        "arguments": function_delta.get("arguments") or "",
                    }

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"HTTP {e.code}: {body}") from e

    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        raise RuntimeError(f"网络错误：{e}") from e


def consume_stream(events: Iterator[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """
    把 SSE 增量事件聚合成一条完整 Assistant Message。

    为什么需要这一层？

    stream_chat()
        处理“网络协议”。

    consume_stream()
        处理“模型消息聚合”。

    这是两个不同职责。

    最终额外产出：

        {
            "type": "model_response",
            "message": {
                "role": "assistant",
                "content": "...",
                "tool_calls": [...]
            }
        }
    """
    text_parts: list[str] = []
    reasoning_parts: list[str] = []

    # key = tool_call index
    #
    # 例如一个模型一轮并行提出两个工具：
    #     index 0 -> read_file(...)
    #     index 1 -> read_file(...)
    #
    # 每个工具的 arguments 都可能分块到达。
    tool_buffers: dict[int, dict[str, Any]] = {}

    for event in events:
        event_type = event["type"]

        if event_type == "text_delta":
            text = event["text"]
            text_parts.append(text)

            # 保持“真流式”：上层马上得到这个增量。
            yield event
            continue

        if event_type == "reasoning_delta":
            reasoning_parts.append(event["text"])

            # reasoning 也保持事件流，不在这里吞掉。
            yield event
            continue

        if event_type == "tool_call_delta":
            index = int(event.get("index", 0))

            buf = tool_buffers.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {
                        "name": "",
                        "arguments": "",
                    },
                },
            )

            # id 通常只在第一个 fragment 出现。
            if event.get("id"):
                buf["id"] = event["id"]

            # name 一般也是首块出现。
            # 使用 += 可以兼容极少数 provider 把 name 也拆块的情况。
            if event.get("name"):
                buf["function"]["name"] += event["name"]

            # arguments 是最典型的分块字段，必须不断拼接。
            buf["function"]["arguments"] += event.get("arguments") or ""

    content = "".join(text_parts)

    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": content or None,
    }

    if tool_buffers:
        assistant_message["tool_calls"] = [
            tool_buffers[i]
            for i in sorted(tool_buffers)
        ]

    yield {
        "type": "model_response",
        "message": assistant_message,
        # 仅供本地渲染/调试，不直接塞进标准 OpenAI assistant history。
        "reasoning_content": "".join(reasoning_parts),
    }


def call_model_stream(
    messages: list[dict[str, Any]],
    *,
    api_base: str,
    api_key: str,
    model: str,
    timeout: float,
    show_reasoning: bool = False,
) -> dict[str, Any]:
    """
    流式调用模型，并返回最终聚合后的 Assistant Message。

    注意：
        “stream=True”并不意味着 agent_loop 可以完全不要最终完整消息。

    原因：
        下一步 Permission / Tool Runtime 仍然需要完整 tool_calls：
            id
            name
            完整 arguments JSON

    所以正确结构是：

        SSE delta
          ↓
        边到边显示
          +
        后台持续聚合
          ↓
        完整 Assistant Message
    """
    final_message: dict[str, Any] | None = None

    # 用于控制输出格式。
    started_text = False
    started_reasoning = False

    events = stream_chat(
        messages,
        api_base=api_base,
        api_key=api_key,
        model=model,
        timeout=timeout,
    )

    for event in consume_stream(events):
        event_type = event["type"]

        if event_type == "text_delta":
            if not started_text:
                print("\nAssistant> ", end="", flush=True)
                started_text = True

            # 真正逐块输出，而不是等完整响应后一次 print。
            print(event["text"], end="", flush=True)
            continue

        if event_type == "reasoning_delta":
            # reasoning_content 是否显示由命令行决定。
            # 即使不显示，也仍然以 SSE 方式被正确消费。
            if show_reasoning:
                if not started_reasoning:
                    print("\n[Reasoning] ", end="", file=sys.stderr, flush=True)
                    started_reasoning = True

                print(
                    event["text"],
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
            continue

        if event_type == "model_response":
            final_message = event["message"]

    if started_text:
        print()

    if started_reasoning:
        print(file=sys.stderr)

    if final_message is None:
        raise RuntimeError("SSE 已结束，但没有得到完整 model_response")

    return final_message


# =============================================================================
# 6. Tool Arguments
# =============================================================================


def parse_arguments(raw: Any) -> dict[str, Any]:
    """
    将聚合完成后的 function.arguments 转成 dict。

    注意调用时机：

        错：
            每个 tool_call_delta 一到就 json.loads()

        对：
            所有 fragments 拼完
                ↓
            得到完整 JSON string
                ↓
            parse_arguments()
    """
    if isinstance(raw, dict):
        return raw

    if not isinstance(raw, str):
        raise ValueError(f"tool arguments 类型错误：{type(raw).__name__}")

    obj = json.loads(raw or "{}")

    if not isinstance(obj, dict):
        raise ValueError("tool arguments 必须是 JSON object")

    return obj


# =============================================================================
# 7. Agent Loop
# =============================================================================


def agent_loop(
    messages: list[dict[str, Any]],
    gate: PermissionGate,
    *,
    api_base: str,
    api_key: str,
    model: str,
    timeout: float,
    max_turns: int,
    show_reasoning: bool = False,
) -> str:
    """
    最小 Agent 状态机：

    while turn < max_turns:

        assistant = stream_model(messages, tools)

        if assistant 没有 tool_calls:
            STOP

        messages.append(assistant)

        for tool_call in assistant.tool_calls:

            PermissionGate
                ↓
            allow / ask / deny

            if allow:
                execute_tool()
            else:
                "permission denied"

            messages.append(tool_result)

        下一轮 LLM 看到真实 tool_result 后继续推理
    """

    for turn in range(1, max_turns + 1):
        print(f"\n[Agent Loop {turn}/{max_turns}] model={model}")

        # SSE stream=True：
        # 文本会在 call_model_stream() 内逐块显示。
        assistant = call_model_stream(
            messages,
            api_base=api_base,
            api_key=api_key,
            model=model,
            timeout=timeout,
            show_reasoning=show_reasoning,
        )

        # region USER QUESTION 4 —— assistant_message 这里到底在做什么？
        #
        # 用户原注释：
        #     “将 LLM 返回的 context/text 类型数据追加到
        #      assistant_message 列表中”
        #
        # 这里有两个地方需要纠正：
        #
        # 第一：
        #     assistant_message 不是 list，而是 dict。
        #
        # 第二：
        #     它也不是在这里把数据“追加到对话历史”。
        #
        # 更准确的过程是：
        #
        #     SSE text_delta
        #          ↓
        #     consume_stream()
        #          ↓
        #     聚合成完整 assistant dict
        #          ↓
        #     assistant_message
        #          ↓
        #     messages.append(assistant_message)
        #
        # assistant_message 表示“这一轮模型完整产生的 Assistant 消息”。
        #
        # content：
        #     本轮模型的自然语言正文。
        #
        # tool_calls：
        #     本轮模型提出的结构化工具调用。
        #
        # endregion

        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": assistant.get("content"),
        }

        # region USER QUESTION 5 —— tool_calls 是直接追加到 messages 吗？
        #
        # 用户原注释：
        #     “将 LLM 返回的 tool_calls/工具列表 类型
        #      追加到 messages 列表中”
        #
        # 准确过程不是这样。
        #
        # 下面这一句：
        #
        #     assistant_message["tool_calls"] = assistant["tool_calls"]
        #
        # 只是把 tool_calls 写入“当前 assistant_message 这个 dict”。
        #
        # 此时 messages 还没有变化。
        #
        # 真正把整条 Assistant Message 放进历史的是随后这一句：
        #
        #     messages.append(assistant_message)
        #
        # 最终 history 中保存的是一个整体：
        #
        #     {
        #         "role": "assistant",
        #         "content": "...",
        #         "tool_calls": [...]
        #     }
        #
        # 为什么必须保存成整体？
        #
        # 因为下一条 role="tool" 消息必须通过：
        #
        #     tool_call_id
        #
        # 与前面 Assistant Message 里的某个 tool call 配对。
        #
        # endregion

        if assistant.get("tool_calls"):
            assistant_message["tool_calls"] = assistant["tool_calls"]

        # ★ 到这里，才真正加入 Conversation History。
        messages.append(assistant_message)

        tool_calls = assistant.get("tool_calls") or []

        # ---------------------------------------------------------------------
        # Stop Condition 1：
        #
        # 模型没有提出工具调用。
        #
        # 说明本轮已经是最终自然语言答案，可以结束这个用户任务。
        # ---------------------------------------------------------------------
        if not tool_calls:
            return assistant.get("content") or ""

        # ---------------------------------------------------------------------
        # Tool Phase：
        #
        # 模型提出一个或多个 tool calls。
        # ---------------------------------------------------------------------
        for tc in tool_calls:
            call_id = tc.get("id") or ""
            fn = tc.get("function") or {}
            name = fn.get("name") or ""

            try:
                args = parse_arguments(fn.get("arguments", "{}"))

            except Exception as e:
                result = (
                    "错误：工具参数解析失败："
                    f"{type(e).__name__}: {e}"
                )

            else:
                print(
                    f"[Tool proposed] "
                    f"{name}({json.dumps(args, ensure_ascii=False)})"
                )

                # ★ S02 最关键的一行：
                #
                # PermissionGate 必须位于：
                #
                #     LLM tool proposal
                #              ↓
                #       PermissionGate
                #              ↓
                #        execute_tool
                #
                # 而不能：
                #
                #     execute_tool
                #         ↓
                #     再问权限
                #
                # 因为后者已经产生副作用，权限检查失去意义。
                if gate.decide(name, args):
                    print(f"[Permission] ALLOW {name}")

                    result = execute_tool(
                        name,
                        args,
                    )

                else:
                    print(f"[Permission] DENY {name}")

                    result = (
                        f"错误：权限拒绝；工具 {name} 没有执行；"
                        f"permission_mode={gate.mode}"
                    )

                print(f"[Tool result] {result[:500]}")

            # -----------------------------------------------------------------
            # Tool Result 回填
            # -----------------------------------------------------------------
            #
            # 无论：
            #     成功
            #     工具自身失败
            #     参数解析失败
            #     权限拒绝
            #
            # 都要把结果作为 role="tool" 回填模型。
            #
            # 这是 ReAct/Agent Loop 的核心：
            #
            #     action
            #       ↓
            #     observation
            #       ↓
            #     next reasoning
            #
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result,
                }
            )

    # -------------------------------------------------------------------------
    # Stop Condition 2：
    #
    # 防止模型一直：
    #
    #     tool -> model -> tool -> model -> ...
    #
    # 而永远不停止。
    # -------------------------------------------------------------------------
    text = f"达到最大 Agent Loop 轮数 {max_turns}，停止。"
    print(f"\n[Stop] {text}")

    return text


# =============================================================================
# 8. CLI / REPL
# =============================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Minimal single-file SSE streaming permission Agent"
    )

    parser.add_argument(
        "--api-key",
        default=os.getenv("AGENT_API_KEY", ""),
        help="API Key；默认读取 AGENT_API_KEY",
    )

    parser.add_argument(
        "--api-base",
        default=os.getenv(
            "AGENT_API_BASE",
            DEFAULT_API_BASE,
        ),
        help="OpenAI-compatible API Base",
    )

    parser.add_argument(
        "--model",
        default=os.getenv(
            "AGENT_MODEL",
            DEFAULT_MODEL,
        ),
        help="模型名称",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="HTTP/SSE 超时秒数",
    )

    parser.add_argument(
        "--max-turns",
        type=int,
        default=DEFAULT_MAX_TURNS,
        help="一次用户任务允许的最大 Agent Loop 轮数",
    )

    parser.add_argument(
        "--show-reasoning",
        action="store_true",
        help="如果 provider 返回 reasoning_content，则流式显示到 stderr",
    )

    permission_group = parser.add_mutually_exclusive_group()

    permission_group.add_argument(
        "--yes",
        action="store_true",
        help="自动批准所有已注册 read/write 工具",
    )

    permission_group.add_argument(
        "--read-only",
        action="store_true",
        help="只允许 read_file",
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    if not args.api_key:
        print("错误：未配置 API Key。")
        print()
        print("方式 1：")
        print("    export AGENT_API_KEY='sk-...'")
        print()
        print("方式 2：")
        print(
            "    python3 minimal_permission_agent_stream_full.py "
            "--api-key 'sk-...'"
        )
        return 1

    if args.max_turns < 1:
        print("错误：--max-turns 必须 >= 1")
        return 2

    if args.yes:
        permission_mode = AUTO_ACCEPT
    elif args.read_only:
        permission_mode = READ_ONLY
    else:
        permission_mode = INTERACTIVE

    gate = PermissionGate(permission_mode)

    # 最小 Conversation State。
    #
    # 同一个 REPL 进程内，messages 会持续存在：
    #
    # system
    # user
    # assistant
    # tool
    # assistant
    # user
    # ...
    #
    # 所以模型可以看到本次 REPL 之前发生过的对话。
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        }
    ]

    print("Minimal Permission Agent — SSE Stream")
    print(f"model={args.model}")
    print(f"api_base={args.api_base}")
    print(f"permission_mode={permission_mode}")
    print("stream=True")
    print("输入 /exit 或 /quit 退出。")

    while True:
        try:
            text = input("\nYou> ").strip()

        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            return 0

        if not text:
            continue

        if text.lower() in {
            "/exit",
            "/quit",
        }:
            print("退出。")
            return 0

        messages.append(
            {
                "role": "user",
                "content": text,
            }
        )

        try:
            agent_loop(
                messages,
                gate,
                api_base=args.api_base,
                api_key=args.api_key,
                model=args.model,
                timeout=args.timeout,
                max_turns=args.max_turns,
                show_reasoning=args.show_reasoning,
            )

        except Exception as e:
            # 当前请求失败，不直接结束整个 REPL。
            print(
                f"\n[Agent Error] "
                f"{type(e).__name__}: {e}"
            )


if __name__ == "__main__":
    raise SystemExit(main())

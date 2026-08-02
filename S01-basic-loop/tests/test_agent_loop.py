"""Agent-Loop 单测：注入假 call_model / execute_tool，不发真实 API。

覆盖验收场景：
1. 纯文本回复 → 一轮结束，reason=no_tool_use（§4.5 主要停止条件）
2. tool_use → execute → tool_result 回填 history → 继续循环
3. 持续 tool_use → 达到 max_turns 停止
4. 模型调用所见历史 = system + 全部 user 消息（assemble 正确性）
"""

from __future__ import annotations

import json

import pytest

from hello_llm.providers import ModelResponse, ToolCall
from hello_llm.query.agent_loop import Conversation, query_loop


def _text_model(text: str):
    """假 call_model：每次调用只返回纯文本。"""

    def call(messages, tools, cfg):
        return ModelResponse(text=text)

    return call


def _scripted_model(script):
    """假 call_model：按调用次数依次消费 script。

    script: [(kind, payload)...]，kind ∈ {"text", "tool"}。
    kind="tool" 时 payload 为 {"id", "name", "arguments"}。
    """
    state = {"n": 0}

    def call(messages, tools, cfg):
        n = min(state["n"], len(script) - 1)
        state["n"] += 1
        kind, payload = script[n]
        if kind == "text":
            return ModelResponse(text=payload)
        return ModelResponse(
            text="",
            tool_calls=[
                ToolCall(
                    id=payload["id"],
                    name=payload["name"],
                    arguments=payload["arguments"],
                )
            ],
        )

    return call


def test_text_only_stops_after_one_turn():
    conv = Conversation("sys")
    conv.add_user("hi")
    events = list(query_loop(conv, max_turns=5, call_model=_text_model("你好！")))

    assert [e["type"] for e in events] == ["text_delta", "turn_end", "done"]
    assert events[0]["text"] == "你好！"
    assert events[-1]["reason"] == "no_tool_use"
    # history：system + user + assistant
    assert [m["role"] for m in conv.messages] == ["system", "user", "assistant"]


def test_tool_use_executes_and_results_feed_back(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello world")
    script = [
        ("tool", {"id": "call_1", "name": "read_file", "arguments": {"path": str(f)}}),
        ("text", "文件内容是 hello world"),
    ]
    conv = Conversation("sys")
    conv.add_user("读一下文件")
    events = list(query_loop(conv, max_turns=5, call_model=_scripted_model(script)))

    kinds = [e["type"] for e in events]
    assert kinds == ["tool_use", "tool_result", "text_delta", "turn_end", "done"]
    assert events[0]["name"] == "read_file"
    assert "hello world" in events[1]["content"]
    # history：system + user + assistant(tool_calls) + tool + assistant(text)
    assert [m["role"] for m in conv.messages] == [
        "system", "user", "assistant", "tool", "assistant",
    ]
    tool_msg = conv.messages[3]
    assert tool_msg["tool_call_id"] == "call_1"
    assert "hello world" in tool_msg["content"]


def test_max_turns_stops_with_persistent_tool_calls():
    script = [("tool", {"id": "c", "name": "read_file", "arguments": {"path": "x"}})]
    conv = Conversation("sys")
    conv.add_user("反复调用工具")
    events = list(query_loop(conv, max_turns=3, call_model=_scripted_model(script)))

    assert events[-1]["type"] == "done"
    assert events[-1]["reason"] == "max_turns"
    assert sum(1 for e in events if e["type"] == "tool_use") == 3


def test_assemble_passes_system_and_all_history():
    """模型调用收到的 messages 应包含 system + 全部 user 消息。"""
    seen: dict = {}

    def call(messages, tools, cfg):
        seen["messages"] = list(messages)
        return ModelResponse(text="ok")

    conv = Conversation("sys")
    conv.add_user("第一问")
    conv.add_user("第二问")
    list(query_loop(conv, max_turns=1, call_model=call))

    assert [m["role"] for m in seen["messages"]] == ["system", "user", "user"]
    assert seen["messages"][0]["content"] == "sys"


def test_assistant_tool_calls_serialized_as_json_string():
    """history 中 assistant 消息的 arguments 必须是 JSON 字符串（OpenAI 协议）。"""
    faked = {"path": "/tmp/x.txt"}
    script = [
        ("tool", {"id": "c1", "name": "read_file", "arguments": faked}),
        ("text", "done"),
    ]
    conv = Conversation("sys")
    conv.add_user("q")
    list(query_loop(conv, max_turns=3, call_model=_scripted_model(script)))

    assistant_msg = conv.messages[2]
    assert isinstance(assistant_msg["tool_calls"][0]["function"]["arguments"], str)
    assert json.loads(assistant_msg["tool_calls"][0]["function"]["arguments"]) == faked


def test_invalid_max_turns_raises():
    conv = Conversation("sys")
    conv.add_user("q")
    with pytest.raises(ValueError):
        list(query_loop(conv, max_turns=0, call_model=_text_model("x")))


def test_sliding_window_trims_oldest_messages():
    """多轮对话的滑动窗口：超限时删最老的非 system 消息，system 永远保留。"""
    conv = Conversation("sys", max_context_chars=50)
    # 每条 user 消息 20 字符：3 条后总量 60 > 50，应裁掉最老的
    for i in range(5):
        conv.add_user(f"第{i}条消息内容比较长啊")
    roles = [m["role"] for m in conv.messages]
    assert roles[0] == "system"  # system 永不删除
    assert roles.count("user") >= 1  # 至少保留最新提问
    # 裁剪后总字符数回到预算内（system + 剩余消息）
    total = sum(len(m.get("content") or "") for m in conv.messages)
    assert total <= 50
    # 最新一条用户消息必须保留（模型要能看到当前问题）
    assert conv.messages[-1]["content"] == "第4条消息内容比较长啊"


# ── 协议一致性守卫：tool 消息必须与 assistant(tool_calls) 成对 ──


def _assert_protocol_valid(messages):
    """断言消息历史符合 OpenAI 协议：tool 消息前必有配对的 assistant(tool_calls)。

    回归保护：滑动窗口裁剪若把一对拆开，服务端会报 HTTP 400
    （"Messages with role 'tool' must be a response to a preceding
    message with 'tool_calls'"）—— 这是用户报告的线上 bug。
    """
    for i, m in enumerate(messages):
        if m["role"] == "tool":
            prev = messages[i - 1] if i > 0 else None
            assert prev is not None and prev["role"] == "assistant" and prev.get("tool_calls"), (
                f"发现孤立 tool 消息（索引 {i}）"
            )
        if m["role"] == "assistant" and m.get("tool_calls"):
            nxt = messages[i + 1] if i + 1 < len(messages) else None
            assert nxt is not None and nxt["role"] == "tool", (
                f"assistant(tool_calls) 缺少 tool 响应（索引 {i}）"
            )


def test_prune_orphan_tool_message(monkeypatch):
    """孤立的 tool 消息（前面无 assistant(tool_calls)）必须被清理。"""
    conv = Conversation("sys")
    # 手工构造被裁剪拆散的畸形历史：assistant(tool_calls) 被删，tool 残留
    conv.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "tool", "tool_call_id": "call_1", "content": "孤儿工具结果"},
        {"role": "user", "content": "u2"},
    ]
    conv.add_user("u3")  # 触发裁剪流程（含协议守卫）
    roles = [m["role"] for m in conv.messages]
    assert "tool" not in roles  # 孤儿 tool 消息已被清除
    _assert_protocol_valid(conv.messages)


def test_prune_assistant_tool_calls_without_result():
    """带 tool_calls 但无 tool 响应的 assistant 消息必须整组删除。"""
    conv = Conversation("sys")
    conv.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "user", "content": "u2"},
    ]
    conv.add_user("u3")
    # 无结果的 assistant(tool_calls) 整组被删，u1 保留
    roles = [m["role"] for m in conv.messages]
    assert roles == ["system", "user", "user", "user"]
    _assert_protocol_valid(conv.messages)


def test_trim_keeps_tool_pairs_intact():
    """强制裁剪多轮工具对话后，历史仍满足协议（无孤立消息）。

    回归保护：这是用户报告的 HTTP 400 的完整触发路径 ——
    多轮工具对话 + 滑动窗口裁剪把 assistant(tool_calls) 删掉留下 tool。
    """
    conv = Conversation("sys", max_context_chars=80)  # 小预算强制裁剪
    # 模拟 3 轮工具对话 + 用户提问
    for n in range(3):
        conv.messages.append({"role": "user", "content": f"第{n}轮提问内容比较长啊"})
        conv.messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": f"c{n}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
        })
        conv.messages.append({"role": "tool", "tool_call_id": f"c{n}", "content": f"第{n}轮工具结果内容"})
    conv.add_user("最新提问内容")
    _assert_protocol_valid(conv.messages)
    assert conv.messages[-1]["role"] == "user"  # 最新提问必须保留

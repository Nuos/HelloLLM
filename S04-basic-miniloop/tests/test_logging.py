"""日志提示层单测：裁剪/轮次/工具/轮数限制/额度限制等事件提示输出。

验证要求：出现异常或遇到限制时，stderr 必须有醒目提示通知用户。
"""

import pytest

from hello_llm.utils import logging as log_events  # 日志事件函数（直接测输出）
from hello_llm.services.api import ModelResponse, ToolCall  # 模型数据结构（构造假模型）
from hello_llm.query.agent_loop import Conversation, query_loop  # 会话 + 循环


def _scripted_model(script):
    """假 call_model：按调用次数依次消费 script（同 test_agent_loop）。"""

    def call(messages, tools, cfg):
        state = {"n": 0}
        n = min(state["n"], len(script) - 1)
        state["n"] += 1
        kind, payload = script[n]
        if kind == "text":
            return ModelResponse(text=payload)
        return ModelResponse(
            text="",
            tool_calls=[ToolCall(id=payload["id"], name=payload["name"], arguments=payload["arguments"])],
        )

    return call


def test_context_trimmed_notice(capsys):
    """裁剪触发时 stderr 出现"超预算 + 已裁剪 N 条"提示。"""
    conv = Conversation("sys", max_context_chars=50)  # 小预算强制裁剪
    for i in range(5):
        conv.add_user(f"第{i}条消息内容比较长啊")
    err = capsys.readouterr().err
    assert "上下文超预算" in err  # 提示包含预算事件
    assert "已裁剪" in err  # 提示包含裁剪统计


def test_loop_turn_and_tool_notices(capsys):
    """query_loop 运行时：轮次提示 + 工具触发提示 + 工具失败提示。"""
    script = [
        ("tool", {"id": "c1", "name": "read_file", "arguments": {"path": "/tmp/不存在_日志测试.txt"}}),
        ("text", "done"),
    ]
    conv = Conversation("sys")
    conv.add_user("q")
    list(query_loop(conv, max_turns=3, call_model=_scripted_model(script)))
    err = capsys.readouterr().err
    assert "Agent-Loop 第 1/3 轮" in err  # 模块触发日志（轮次+模型）
    assert "工具触发：read_file" in err  # 工具调用日志
    assert "工具失败：read_file" in err  # 工具失败日志（限制/异常通知）


def test_max_turns_notice(capsys):
    """持续工具调用达到 max_turns 时出现轮数限制提示。"""
    script = [("tool", {"id": "c", "name": "read_file", "arguments": {"path": "x"}})]
    conv = Conversation("sys")
    conv.add_user("q")
    list(query_loop(conv, max_turns=2, call_model=_scripted_model(script)))
    err = capsys.readouterr().err
    assert "达到最大轮数限制" in err  # 循环限制通知


def test_rate_limited_output(capsys):
    """额度/频率限制（HTTP 429）提示输出。"""
    log_events.rate_limited(429, "rate limit exceeded")
    err = capsys.readouterr().err
    assert "额度/频率限制" in err
    assert "HTTP 429" in err
    assert "稍后重试" in err  # 含处理建议


def test_notice_warn_error_levels(capsys):
    """三个基本级别的输出前缀可区分（信息/警告/错误）。"""
    log_events.notice("信息事件")
    log_events.warn("警告事件")
    log_events.error("错误事件")
    err = capsys.readouterr().err
    assert "信息事件" in err
    assert "警告事件" in err
    assert "错误事件" in err

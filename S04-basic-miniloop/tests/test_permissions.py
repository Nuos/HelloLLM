"""权限系统最小子集单测（S02-basic-permission）。

覆盖：策略映射（deny-first）、gate 三态决策矩阵、模式切换、
asker 回调、agent_loop 集成（拒绝不执行 / 批准放行）。
"""

import pytest  # 测试框架

from hello_llm.entrypoints.cli import _make_gate, build_parser  # cli 的权限门构建 + 参数解析
from hello_llm.permissions import (  # 权限模块（S02 新增）
    PermissionGate,  # 权限门
    AUTO_ACCEPT,  # 模式：自动接受
    READ_ONLY,  # 模式：只读
    INTERACTIVE,  # 模式：交互
    level_for,  # 策略等级查询
    READ,  # 等级：只读
    WRITE,  # 等级：写
    DANGER,  # 等级：危险
)
from hello_llm.services.api import ModelResponse, ToolCall  # 模型数据结构（假模型）
from hello_llm.query.agent_loop import Conversation, query_loop  # 会话 + 循环


# 一、策略映射（policies.py）


def test_policy_mapping_read_write_danger():
    """工具策略等级映射：读→read、写→write、未知→danger（deny-first）。"""
    assert level_for("read_file") == READ  # 只读工具
    assert level_for("write_file") == WRITE  # 写工具
    assert level_for("edit_file") == WRITE  # 编辑工具
    assert level_for("unknown_tool") == DANGER  # 未知工具按危险级（拒绝优先）


# 二、gate 决策矩阵（gate.py）


def test_gate_interactive_matrix():
    """交互模式决策矩阵：read→allow、write→ask、danger/未知→deny。"""
    g = PermissionGate(mode=INTERACTIVE)
    assert g.check("read_file") == "allow"  # 只读自动放行
    assert g.check("write_file") == "ask"  # 写需批准
    assert g.check("edit_file") == "ask"  # 编辑需批准
    assert g.check("unknown_tool") == "deny"  # 未知拒绝


def test_gate_auto_accept_allows_everything():
    """auto-accept 模式：全部放行（含 danger）。"""
    g = PermissionGate(mode=AUTO_ACCEPT)
    assert g.check("read_file") == "allow"
    assert g.check("write_file") == "allow"
    assert g.check("unknown_tool") == "allow"  # 自动接受连未知工具也放行


def test_gate_read_only_denies_writes():
    """read-only 模式：只读放行、写/危险拒绝。"""
    g = PermissionGate(mode=READ_ONLY)
    assert g.check("read_file") == "allow"
    assert g.check("write_file") == "deny"
    assert g.check("edit_file") == "deny"
    assert g.check("unknown_tool") == "deny"


def test_decide_with_asker_callback():
    """ask 决策交给 asker 回调：True=放行、False=拒绝。"""
    g_yes = PermissionGate(mode=INTERACTIVE, asker=lambda name, args: True)
    g_no = PermissionGate(mode=INTERACTIVE, asker=lambda name, args: False)
    assert g_yes.decide("write_file", {"path": "/tmp/x"}) is True
    assert g_no.decide("write_file", {"path": "/tmp/x"}) is False


def test_decide_without_asker_denies_ask():
    """无交互通道（asker=None）时 ask 决策 → 拒绝（deny-first 安全默认）。"""
    g = PermissionGate(mode=INTERACTIVE, asker=None)
    assert g.decide("write_file", {"path": "/tmp/x"}) is False  # ask→拒绝
    assert g.decide("read_file", {"path": "/tmp/x"}) is True  # allow→放行


def test_decide_deny_level_never_asks():
    """danger 级（含未知工具）在任何情况下都拒绝，asker 不会被调用。"""
    called = {"n": 0}  # called：记录 asker 被调用次数

    def asker(name, args):  # asker：测试回调
        called["n"] += 1
        return True

    g = PermissionGate(mode=INTERACTIVE, asker=asker)
    assert g.decide("unknown_tool", {}) is False  # 未知→deny
    assert called["n"] == 0  # asker 未被调用（deny 不询问）


# 三、agent_loop 集成（query/agent_loop.py 的权限门）


def _tool_model(tool_name="write_file"):
    """构造假模型：第一轮请求工具，第二轮纯文本。"""
    script = [
        ("tool", {"id": "c1", "name": tool_name, "arguments": {"path": "/tmp/p_test.txt"}}),
        ("text", "done"),
    ]
    state = {"n": 0}  # state：调用计数

    def call(messages, tools, cfg):  # call：假 call_model
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


def test_loop_denied_tool_not_executed():
    """权限拒绝：工具不执行，tool_result 回填"权限拒绝"给模型。"""
    conv = Conversation("sys")
    conv.add_user("q")
    gate = PermissionGate(mode=READ_ONLY)  # 只读模式：write_file 必拒绝
    events = list(
        query_loop(conv, max_turns=3, call_model=_tool_model(), permission_gate=gate)
    )
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert tool_results  # 有 tool_result 事件
    assert "权限拒绝" in tool_results[0]["content"]  # 拒绝原因回填模型
    assert "write_file" in tool_results[0]["content"]  # 指明被拒工具


def test_loop_approved_tool_executes():
    """权限批准：write 级经 asker 同意后正常执行。"""
    conv = Conversation("sys")
    conv.add_user("q")
    gate = PermissionGate(mode=INTERACTIVE, asker=lambda name, args: True)  # 批准
    events = list(
        query_loop(conv, max_turns=3, call_model=_tool_model(), permission_gate=gate)
    )
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert tool_results
    assert "权限拒绝" not in tool_results[0]["content"]  # 未被拒绝


def test_loop_no_gate_backward_compatible():
    """无权限门（gate=None）：行为与 S01 完全一致（向后兼容）。"""
    conv = Conversation("sys")
    conv.add_user("q")
    events = list(query_loop(conv, max_turns=3, call_model=_tool_model()))
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert tool_results
    assert "权限拒绝" not in tool_results[0]["content"]  # 照常执行


# 四、cli 集成（_make_gate：--yes / --read-only 参数）


def _parse(*argv):
    """用 cli 的 parser 解析参数，返回 Namespace。"""
    return build_parser().parse_args(list(argv))


def test_make_gate_default_interactive():
    """默认（无参数）：interactive 模式，REPL 时注入 asker。"""
    args = _parse()  # 无 -p → REPL
    g = _make_gate(args)
    assert g.mode == INTERACTIVE
    assert g.asker is not None  # REPL 注入了 Y/N 回调


def test_make_gate_yes_and_read_only():
    """--yes → auto-accept；--read-only → read-only。"""
    g_yes = _make_gate(_parse("--yes", "-p", "hi"))
    assert g_yes.mode == AUTO_ACCEPT
    g_ro = _make_gate(_parse("--read-only", "-p", "hi"))
    assert g_ro.mode == READ_ONLY


def test_make_gate_headless_no_asker():
    """无头模式（-p）：interactive 下 asker 为 None（ask 自动拒绝）。"""
    args = _parse("-p", "hi")  # 无头
    g = _make_gate(args)
    assert g.mode == INTERACTIVE
    assert g.asker is None  # 无交互通道

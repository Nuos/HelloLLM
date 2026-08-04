"""hook 机制最小子集单测（S03-basic-hook）。

覆盖：配置加载（fail-open）、匹配、subprocess 协议、HookManager 决策
（PreToolUse 拦截/改写、PostToolUse 改写）、loop 集成（拒绝回填/兼容）。
"""

import json  # 构造 hook 输入输出
import pathlib  # 临时脚本路径
import sys  # sys.executable

import pytest  # 测试框架

from hello_llm.hooks import (  # hook 模块（S03 新增）
    HookManager,  # 调度器
    load_hook_rules,  # 配置加载
    match_hook,  # 规则匹配
    run_hook_command,  # subprocess 执行
)
from hello_llm.query.agent_loop import Conversation, query_loop  # 会话 + 循环
from hello_llm.services.api import ModelResponse, ToolCall  # 模型数据结构


# 一、配置加载与匹配（config.py）


def test_load_rules_missing_file_returns_empty():
    """配置文件不存在 → 空规则（fail-open，不影响主流程）。"""
    assert load_hook_rules(explicit="/tmp/no_such_hooks_file.json") == {}


def test_load_rules_broken_json_returns_empty():
    """JSON 损坏 → 空规则（fail-open）。"""
    p = pathlib.Path("/tmp/broken_hooks.json")
    p.write_text("not json", encoding="utf-8")
    assert load_hook_rules(explicit=str(p)) == {}


def test_match_hook_substring():
    """matcher 子串匹配：空=全匹配、包含=匹配、不含=不匹配。"""
    assert match_hook({"matcher": ""}, "read_file") is True
    assert match_hook({"matcher": "write"}, "write_file") is True
    assert match_hook({"matcher": "read"}, "write_file") is False


# 二、subprocess 协议（runner.py，用临时脚本测真实协议）


def _write_script(code: str) -> str:
    """创建临时 hook 脚本，返回路径。"""
    p = pathlib.Path("/tmp/hook_test_script.py")
    p.write_text(code, encoding="utf-8")
    return str(p)


def test_run_hook_command_allow_and_deny():
    """真实 subprocess：hook 返回 allow / deny。"""
    script = _write_script(
        "import sys,json; d=json.load(sys.stdin); "
        "print(json.dumps({'decision':'deny' if d['tool_name']=='write_file' else 'allow'}))"
    )
    cmd = f"{sys.executable} {script}"
    assert run_hook_command(cmd, {"tool_name": "read_file"})["decision"] == "allow"
    assert run_hook_command(cmd, {"tool_name": "write_file"})["decision"] == "deny"


def test_run_hook_command_updated_input():
    """hook 改写输入：updatedInput 透传。"""
    script = _write_script(
        "import sys,json; d=json.load(sys.stdin); "
        "d['arguments']['path']='/tmp/hooked.txt'; "
        "print(json.dumps({'decision':'allow','updatedInput':d['arguments']}))"
    )
    cmd = f"{sys.executable} {script}"
    out = run_hook_command(cmd, {"tool_name": "write_file", "arguments": {"path": "/tmp/x"}})
    assert out["updatedInput"]["path"] == "/tmp/hooked.txt"


def test_run_hook_command_bad_json_is_error():
    """hook 输出非 JSON → decision=error（fail-open 降级）。"""
    script = _write_script("print('not json')")
    assert run_hook_command(f"{sys.executable} {script}", {})["decision"] == "error"


def test_run_hook_command_timeout_is_error():
    """hook 超时 → decision=error（fail-open 降级）。"""
    script = _write_script("import time; time.sleep(30)")
    out = run_hook_command(f"{sys.executable} {script}", {})
    assert out["decision"] == "error"


# 三、HookManager 决策（manager.py，注入假 runner）


def _fake_runner(results):
    """构造假 hook 执行器：按调用次数依次返回结果。"""
    state = {"n": 0}

    def runner(command, payload):
        r = results[min(state["n"], len(results) - 1)]
        state["n"] += 1
        return r

    return runner


def test_manager_no_rules_allow():
    """无 hook 规则 → 放行原样。"""
    m = HookManager(rules={}, runner=_fake_runner([{"decision": "allow"}]))
    pre = m.run_pre_tool("write_file", {"path": "/tmp/x"})
    assert pre["allow"] is True and pre["arguments"] == {"path": "/tmp/x"}


def test_manager_pre_deny():
    """PreToolUse deny → 拒绝（reason 带出）。"""
    m = HookManager(
        rules={"PreToolUse": [{"matcher": "write", "command": "x"}]},
        runner=_fake_runner([{"decision": "deny", "reason": "测试拦截"}]),
    )
    pre = m.run_pre_tool("write_file", {"path": "/tmp/x"})
    assert pre["allow"] is False and "测试拦截" in pre["reason"]


def test_manager_pre_updated_input():
    """PreToolUse updatedInput → 参数改写。"""
    m = HookManager(
        rules={"PreToolUse": [{"matcher": "", "command": "x"}]},
        runner=_fake_runner([{"decision": "allow", "updatedInput": {"path": "/tmp/new"}}]),
    )
    pre = m.run_pre_tool("write_file", {"path": "/tmp/old"})
    assert pre["arguments"]["path"] == "/tmp/new"


def test_manager_pre_error_fail_open():
    """hook 失败（error）→ 放行（fail-open）。"""
    m = HookManager(
        rules={"PreToolUse": [{"matcher": "", "command": "x"}]},
        runner=_fake_runner([{"decision": "error"}]),
    )
    assert m.run_pre_tool("write_file", {})["allow"] is True


def test_manager_post_updated_output():
    """PostToolUse updatedOutput → 结果改写。"""
    m = HookManager(
        rules={"PostToolUse": [{"matcher": "", "command": "x"}]},
        runner=_fake_runner([{"decision": "allow", "updatedOutput": "已改写"}]),
    )
    assert m.run_post_tool("read_file", {}, "原始输出") == "已改写"


# 四、loop 集成（query_loop 的 hook_manager 参数）


def _tool_model():
    """假模型：第一轮请求工具，第二轮纯文本。"""
    script = [
        ("tool", {"id": "c1", "name": "write_file", "arguments": {"path": "/tmp/h.txt", "content": "c"}}),
        ("text", "done"),
    ]
    state = {"n": 0}

    def call(messages, tools, cfg):
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


def test_loop_hook_denied_not_executed():
    """hook 拒绝：工具不执行，tool_result 回填拒绝原因。"""
    conv = Conversation("sys")
    conv.add_user("q")
    m = HookManager(
        rules={"PreToolUse": [{"matcher": "write", "command": "x"}]},
        runner=_fake_runner([{"decision": "deny", "reason": "禁止写文件"}]),
    )
    events = list(query_loop(conv, max_turns=3, call_model=_tool_model(), hook_manager=m))
    results = [e for e in events if e["type"] == "tool_result"]
    assert results and "hook 拒绝" in results[0]["content"]


def test_loop_hook_allowed_executes():
    """hook 放行：工具正常执行（无拒绝字样）。"""
    conv = Conversation("sys")
    conv.add_user("q")
    m = HookManager(rules={}, runner=_fake_runner([{"decision": "allow"}]))
    events = list(query_loop(conv, max_turns=3, call_model=_tool_model(), hook_manager=m))
    results = [e for e in events if e["type"] == "tool_result"]
    assert results and "hook 拒绝" not in results[0]["content"]


def test_loop_no_hook_backward_compatible():
    """无 hook_manager（None）：行为与 S01 完全一致（向后兼容）。"""
    conv = Conversation("sys")
    conv.add_user("q")
    events = list(query_loop(conv, max_turns=3, call_model=_tool_model()))
    results = [e for e in events if e["type"] == "tool_result"]
    assert results and "hook 拒绝" not in results[0]["content"]

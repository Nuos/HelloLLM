"""state 持久化最小子集单测（S05-basic-statepersist）。

覆盖：会话名校验、新建、追加、恢复、列表、损坏容错、超限、路径安全。
全部用临时会话目录隔离（monkeypatch SESSIONS_DIR），不碰真实用户目录。
"""

import json  # 构造/解析转录行
import pathlib  # 临时目录与文件操作
import sys  # 导入路径

import pytest  # 参数化/临时目录 fixture

# 双模式导入：脚本模式（pytest 直接跑）用绝对导入，包模式用相对导入
if __package__ in (None, ""):
    from hello_llm.state.store import (  # 会话存储（JSONL 转录）
        SESSIONS_DIR,  # 会话目录（测试中指向临时目录）
        get_transcript_path,  # 转录路径计算
        validate_name,  # 名校验
        create_session,  # 新建会话
        append_message,  # 追加消息
        load_session,  # 恢复会话
        list_sessions,  # 会话列表
    )
    from hello_llm.state.session import generate_name, make_title  # 会话名/标题
else:
    from ..hello_llm.state.store import (  # noqa: F401（双模式同源）
        SESSIONS_DIR,
        get_transcript_path,
        validate_name,
        create_session,
        append_message,
        load_session,
        list_sessions,
    )
    from ..hello_llm.state.session import generate_name, make_title  # noqa: F401


@pytest.fixture(autouse=True)
def _tmp_sessions(tmp_path, monkeypatch):
    """把所有会话文件隔离到临时目录，测试互不污染。"""
    monkeypatch.setattr(sys.modules["hello_llm.state.store"], "SESSIONS_DIR", tmp_path)
    return tmp_path


def test_validate_name_rejects_dangerous():
    """非法会话名（路径注入字符）必须被拒绝。"""
    for bad in ["a/b", "..", "../evil", "a b", "a.b", "", "x" * 65]:
        with pytest.raises(ValueError):
            validate_name(bad)


def test_validate_name_accepts_legal():
    """合法会话名（字母/数字/下划线/连字符）放行。"""
    for good in ["demo", "20260803_143000", "my-session_1"]:
        validate_name(good)  # 不抛即通过


def test_create_and_load_roundtrip(tmp_path):
    """新建会话 → 追加消息 → 恢复，内容必须一致。"""
    create_session("roundtrip", "你是助手", "deepseek-v4-flash")
    append_message("roundtrip", {"role": "user", "content": "你好"})
    append_message("roundtrip", {"role": "assistant", "content": "你好呀"})
    state = load_session("roundtrip")
    assert state["system_prompt"] == "你是助手"
    assert state["model"] == "deepseek-v4-flash"
    assert [m["content"] for m in state["messages"]] == ["你好", "你好呀"]


def test_create_duplicate_raises():
    """同名会话重复新建必须报错（防覆盖）。"""
    create_session("dup", "s", "m")
    with pytest.raises(ValueError):
        create_session("dup", "s2", "m2")


def test_append_message_auto_creates(tmp_path):
    """没建会话直接追加：自动建会话，数据不丢。"""
    append_message("auto", {"role": "user", "content": "hi"})
    state = load_session("auto")
    assert state["messages"][0]["content"] == "hi"


def test_load_missing_session(tmp_path):
    """不存在的会话：返回空状态，不抛异常。"""
    state = load_session("ghost")
    assert state["messages"] == [] and state["system_prompt"] == ""


def test_load_tolerates_corrupt_lines(tmp_path):
    """转录文件含坏行（非法 JSON）时跳过坏行、不中断恢复。"""
    path = get_transcript_path("corrupt")
    path.write_text(
        '{"type": "meta", "system_prompt": "s", "model": "m"}\n'
        '{"role": "user", "content": "good"}\n'
        "this is not json\n"
        '{"role": "assistant", "content": "also good"}\n',
        encoding="utf-8",
    )
    state = load_session("corrupt")
    assert [m["content"] for m in state["messages"]] == ["good", "also good"]


def test_list_sessions_sorted_by_mtime(tmp_path):
    """会话列表按最近修改倒序，消息数正确（meta 行不计）。"""
    create_session("older", "s", "m")
    append_message("older", {"role": "user", "content": "a"})
    append_message("older", {"role": "assistant", "content": "b"})
    create_session("newer", "s", "m")
    append_message("newer", {"role": "user", "content": "c"})
    sessions = list_sessions()
    assert [s["name"] for s in sessions] == ["newer", "older"]
    by_name = {s["name"]: s for s in sessions}
    assert by_name["older"]["messages"] == 2
    assert by_name["newer"]["messages"] == 1


def test_generate_name_unique_and_safe():
    """自动会话名：合法字符集 + 前缀非法字符剔除。"""
    a = generate_name()
    assert len(a) == 15  # YYYYMMDD_HHMMSS = 15 字符
    validate_name(a)  # 合法字符集
    prefixed = generate_name("重构 项目/v1")
    validate_name(prefixed)  # 非法字符已被剔除
    assert "重构" not in prefixed and "/" not in prefixed
    assert prefixed.startswith(a)  # 前缀拼在时间戳后


def test_make_title_shortens():
    """标题提炼：折叠空白 + 截断 + 空消息兜底。"""
    assert make_title("  你好  世界  ") == "你好 世界"
    assert make_title("长" * 50)[:25].rstrip("…") == "长" * 20  # 截到 20 字
    assert make_title("") == "（空会话）"


def test_transcript_is_jsonl(tmp_path):
    """转录文件格式：每行一条合法 JSON，首行是 meta。"""
    create_session("fmt", "你是助手", "m")
    append_message("fmt", {"role": "user", "content": "x"})
    lines = get_transcript_path("fmt").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "meta"
    assert json.loads(lines[1])["role"] == "user"

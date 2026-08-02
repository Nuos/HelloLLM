"""文件工具（read_file / write_file / edit_file）单测。"""

from __future__ import annotations

from hello_llm import tools


def test_write_then_read(tmp_path):
    p = tmp_path / "sub" / "b.txt"  # 父目录不存在，验证自动创建
    r = tools.execute("write_file", {"path": str(p), "content": "第一行\n第二行"})
    assert "已写入" in r
    assert p.read_text(encoding="utf-8") == "第一行\n第二行"

    out = tools.execute("read_file", {"path": str(p)})
    assert out == "第一行\n第二行"


def test_read_missing_file():
    out = tools.execute("read_file", {"path": "/nonexistent/xyz.txt"})
    assert "不存在" in out


def test_read_directory(tmp_path):
    out = tools.execute("read_file", {"path": str(tmp_path)})
    assert "目录" in out


def test_read_binary_rejected(tmp_path):
    p = tmp_path / "bin.dat"
    p.write_bytes(b"\x00\x01\x02\x03")
    out = tools.execute("read_file", {"path": str(p)})
    assert "二进制" in out


def test_edit_first_occurrence_only(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text("aaa bbb aaa")
    r = tools.execute(
        "edit_file",
        {"path": str(p), "old_string": "aaa", "new_string": "xxx"},
    )
    assert "1/2" in r  # 共 2 处匹配，只替换第 1 处
    assert p.read_text() == "xxx bbb aaa"


def test_edit_missing_old_string(tmp_path):
    p = tmp_path / "d.txt"
    p.write_text("hello")
    r = tools.execute(
        "edit_file",
        {"path": str(p), "old_string": "nope", "new_string": "x"},
    )
    assert "中找到" in r


def test_unknown_tool():
    assert "未知工具" in tools.execute("frobnicate", {})


def test_tool_exception_captured_as_error_text():
    r = tools.execute("write_file", {})  # 缺必填参数 → TypeError 被捕获
    assert "执行失败" in r

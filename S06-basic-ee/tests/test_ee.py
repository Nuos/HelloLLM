"""Execution Environment 最小实现单测（S06-basic-ee）。

覆盖：run_command 成功/失败/超时/cwd/env、命令语义分类、超时上限钳制、bash 工具注册与执行。
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hello_llm.ee import (
    CommandResult,
    run_command,
    command_semantics,
    is_read_only,
    clamp_timeout,
    resolve_cwd,
    merge_env,
)
from hello_llm.tools.registry import TOOLS, _IMPL, execute


def test_run_command_success():
    result = run_command("echo hello")
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert not result.timed_out


def test_run_command_failure():
    result = run_command("exit 3")
    assert result.exit_code == 3
    assert not result.timed_out


def test_run_command_timeout():
    result = run_command("sleep 5", timeout=1)
    assert result.timed_out
    assert result.exit_code == -1


def test_run_command_cwd():
    with tempfile.TemporaryDirectory() as tmp:
        result = run_command("pwd", cwd=tmp)
        assert result.stdout.strip() == Path(tmp).resolve().as_posix()


def test_run_command_env():
    result = run_command("echo $EE_TEST_VAR", env={"EE_TEST_VAR": "probe"})
    assert "probe" in result.stdout


def test_run_command_oserror():
    result = run_command("", cwd="/nonexistent_dir_xyz")
    assert result.exit_code == -2


def test_command_semantics_read():
    assert command_semantics("cat file.txt") == "read"
    assert command_semantics("head -5 data.csv") == "read"


def test_command_semantics_search():
    assert command_semantics("grep -r foo src") == "search"
    assert command_semantics("find . -name '*.py'") == "search"


def test_command_semantics_write():
    assert command_semantics("rm -rf /tmp/x") == "write"
    assert command_semantics("mkdir -p out") == "write"


def test_command_semantics_neutral_and_other():
    assert command_semantics("echo hi") == "neutral"
    assert command_semantics("ls -la") == "other"
    assert command_semantics("") == "empty"


def test_is_read_only():
    assert is_read_only("cat a.txt")
    assert is_read_only("echo hi")
    assert not is_read_only("rm a.txt")


def test_clamp_timeout():
    assert clamp_timeout(None) == 120
    assert clamp_timeout(0) == 1
    assert clamp_timeout(9999) == 600
    assert clamp_timeout(30) == 30


def test_resolve_cwd():
    assert resolve_cwd() == str(Path.cwd())
    with tempfile.TemporaryDirectory() as tmp:
        assert resolve_cwd(tmp) == Path(tmp).resolve().as_posix()


def test_merge_env():
    env = merge_env({"X": "1"})
    assert env["X"] == "1"
    assert "PATH" in env


def test_bash_tool_registered():
    names = [t["function"]["name"] for t in TOOLS]
    assert "bash" in names
    assert callable(_IMPL["bash"])


def test_bash_tool_execute():
    output = execute("bash", {"command": "echo from-bash"})
    assert "from-bash" in output
    assert "退出码 0" in output


def test_bash_tool_timeout():
    output = execute("bash", {"command": "sleep 5", "timeout": 1})
    assert "超时" in output


def test_bash_tool_unknown():
    output = execute("nope", {})
    assert "未知工具" in output

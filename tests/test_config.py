"""配置模块单测：本地配置文件加载、优先级合并、API key 缺失检测。"""

import json
from types import SimpleNamespace

import pytest

import hello_llm.config.loader as app_config
from hello_llm.config.loader import build_model_config, load_config
from hello_llm.providers import ConfigError, ModelConfig


def _args(**overrides):
    """构造模拟 argparse 参数对象（命令行全部未指定时）。"""
    base = dict(config="", api_base="", api_key="", model="", timeout=None)
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    """把默认配置文件路径隔离到临时目录，避免读到用户真实配置。"""
    monkeypatch.setattr(app_config, "DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(app_config, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    return tmp_path / "config.json"


def _write_config(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ── API key 缺失检测（fail-fast）──


def test_missing_api_key_raises_config_error(isolated_config):
    """无配置文件、无命令行参数 → require_api_key 必须抛 ConfigError。"""
    cfg = ModelConfig(file_config={})
    with pytest.raises(ConfigError):
        cfg.require_api_key()


def test_cli_missing_key_fails_fast(isolated_config, capsys):
    """build_model_config 全空 → 校验失败，退出码 1，指引含配置文件路径。"""
    from hello_llm.entrypoints.cli import main

    rc = main(["-p", "hi"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "未配置 API Key" in err
    assert "config.json" in err  # 指引指向配置文件


# ── 配置文件提供配置 ──


def test_file_config_provides_api_key(isolated_config):
    """配置文件里的 api_key 生效。"""
    _write_config(isolated_config, {"api_key": "sk-file"})
    cfg = ModelConfig(file_config=load_config())
    cfg.require_api_key()  # 不应抛异常
    assert cfg.api_key == "sk-file"


def test_build_model_config_reads_default_path(isolated_config):
    """build_model_config 从默认路径读取配置文件。"""
    _write_config(isolated_config, {"api_key": "sk-file", "model": "m-file"})
    cfg = build_model_config(_args())
    cfg.require_api_key()
    assert cfg.api_key == "sk-file"
    assert cfg.model == "m-file"


def test_explicit_config_path_wins(isolated_config, tmp_path):
    """--config 显式指定优先于默认路径。"""
    other = _write_config(tmp_path / "other.json", {"api_key": "sk-other"})
    _write_config(isolated_config, {"api_key": "sk-default"})
    cfg = build_model_config(_args(config=str(other)))
    assert cfg.api_key == "sk-other"


def test_cli_arg_wins_over_file_config(isolated_config):
    """命令行显式参数优先于配置文件。"""
    _write_config(isolated_config, {"api_key": "sk-file", "model": "m-file"})
    cfg = build_model_config(_args(api_key="sk-cli", model="m-cli"))
    assert cfg.api_key == "sk-cli"
    assert cfg.model == "m-cli"


def test_file_config_defaults_for_base_and_timeout(isolated_config):
    """api_base / timeout 从配置文件回填；未配置的 model 用内置默认。"""
    _write_config(isolated_config, {"api_base": "https://example.com/", "timeout": 30})
    cfg = build_model_config(_args(api_key="sk-x"))
    assert cfg.api_base == "https://example.com"  # 尾部斜杠去除
    assert cfg.timeout == 30


# ── 无任何配置时的内置默认 ──


def test_builtin_defaults(isolated_config):
    """无文件无参数：api_base/model 用默认值，key 为空。"""
    cfg = build_model_config(_args())
    assert cfg.api_base == "https://api.deepseek.com"
    assert cfg.model == "deepseek-v4-flash"
    assert cfg.api_key == ""
    assert cfg.timeout == 120.0


def test_load_config_missing_or_broken_file(isolated_config):
    """文件缺失 / 非法 JSON → 返回空字典，不抛异常。"""
    assert load_config() == {}
    isolated_config.write_text("{ 不是合法JSON", encoding="utf-8")
    assert load_config() == {}

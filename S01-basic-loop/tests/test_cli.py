"""CLI 入口单测：API key 缺失时 fail-fast（不发请求、退出码 1、给出指引）。"""

import hello_llm.config.loader as app_config


def _isolate(monkeypatch, tmp_path):
    """把默认配置文件路径隔离到临时目录，避免读到用户真实配置。"""
    monkeypatch.setattr(app_config, "DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(app_config, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")


def test_missing_key_headless_fails_fast(monkeypatch, tmp_path, capsys):
    """无头模式缺 key：立即失败，退出码 1，提示配置方式。"""
    _isolate(monkeypatch, tmp_path)
    from hello_llm.entrypoints.cli import main

    rc = main(["-p", "hi"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "未配置 API Key" in err
    assert "config.json" in err  # 指引包含配置文件路径


def test_missing_key_repl_fails_fast(monkeypatch, tmp_path, capsys):
    """交互模式缺 key：同样立即失败，不进入 REPL 循环。"""
    _isolate(monkeypatch, tmp_path)
    from hello_llm.entrypoints.cli import main

    rc = main([])
    assert rc == 1
    assert "未配置 API Key" in capsys.readouterr().err

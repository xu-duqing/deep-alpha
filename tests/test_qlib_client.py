import logging
import sys
import types

from deep_alpha.qlib_client import QlibClient


def install_fake_qlib(monkeypatch, calls):
    qlib = types.ModuleType("qlib")
    setattr(qlib, "init", lambda **kwargs: calls.append(kwargs))
    config = types.ModuleType("qlib.config")
    setattr(config, "REG_CN", "cn")
    data = types.ModuleType("qlib.data")
    setattr(data, "D", object())
    monkeypatch.setitem(sys.modules, "qlib", qlib)
    monkeypatch.setitem(sys.modules, "qlib.config", config)
    monkeypatch.setitem(sys.modules, "qlib.data", data)


def make_provider(path):
    (path / "calendars").mkdir(parents=True)
    (path / "features").mkdir()
    (path / "instruments").mkdir()
    (path / "calendars/day.txt").write_text("2024-01-02\n")


def test_qlib_logging_is_warning_by_default(tmp_path, monkeypatch):
    provider = tmp_path / "provider"
    make_provider(provider)
    calls = []
    install_fake_qlib(monkeypatch, calls)
    monkeypatch.setattr(logging.getLogger(), "isEnabledFor", lambda level: False)
    QlibClient(provider).init()
    assert calls[0]["logging_level"] == logging.WARNING


def test_qlib_logging_is_debug_in_debug_mode(tmp_path, monkeypatch):
    provider = tmp_path / "provider"
    make_provider(provider)
    calls = []
    install_fake_qlib(monkeypatch, calls)
    monkeypatch.setattr(
        logging.getLogger(), "isEnabledFor", lambda level: level == logging.DEBUG
    )
    QlibClient(provider).init()
    assert calls[0]["logging_level"] == logging.DEBUG

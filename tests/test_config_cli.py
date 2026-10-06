import json

import pytest

from jev_linkedin.cli import main
from jev_linkedin.config import Task, linkedin_url
from jev_linkedin.connection import configure_connection


def test_config_rejects_lookalike_host_and_invalid_cap():
    assert not linkedin_url("https://linkedin.com.evil.example/in/person")
    assert not linkedin_url("https://linkedin.com@evil.example/")
    with pytest.raises(ValueError):
        Task("Mercor", "https://www.linkedin.com/", max_invitations=True)


def test_missing_key_stops_before_connect(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    config = tmp_path / "task.json"
    config.write_text(json.dumps({"company": "Mercor", "start_url": "https://www.linkedin.com/"}))
    assert (
        main(
            [
                "--env-file",
                str(tmp_path / "absent"),
                "preview",
                "--task",
                str(config),
                "--db",
                str(tmp_path / "memory.sqlite"),
            ]
        )
        == 2
    )
    assert "no browser opened" in capsys.readouterr().out


def test_explicit_endpoint_not_replaced(monkeypatch):
    monkeypatch.setenv("BU_CDP_WS", "ws://127.0.0.1:9222/devtools/browser/test")
    assert configure_connection() == "explicit"


def test_invalid_endpoint_rejected(monkeypatch):
    monkeypatch.setenv("BU_CDP_WS", "https://example.com/")
    with pytest.raises(ValueError):
        configure_connection()

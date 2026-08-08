"""CLI prefs store for tutorial completion flags."""

from __future__ import annotations

import json

from app.cli_pkg import prefs


def test_tutorial_prefs_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "prefs.json"
    monkeypatch.setenv("AIO_PREFS", str(path))

    assert prefs.is_tutorial_done("a@local.test") is False
    prefs.mark_tutorial_done("a@local.test")
    assert prefs.is_tutorial_done("a@local.test") is True
    assert prefs.is_tutorial_done("A@Local.Test") is True  # case-insensitive

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["tutorial"]["a@local.test"]["completed"] is True
    assert raw["tutorial"]["a@local.test"]["version"] == prefs.TUTORIAL_VERSION

    prefs.clear_tutorial_done("a@local.test")
    assert prefs.is_tutorial_done("a@local.test") is False


def test_tutorial_prefs_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / "prefs.json"
    path.write_text("not-json{{{", encoding="utf-8")
    monkeypatch.setenv("AIO_PREFS", str(path))
    assert prefs.is_tutorial_done("x@y.z") is False
    prefs.mark_tutorial_done("x@y.z")
    assert prefs.is_tutorial_done("x@y.z") is True


def test_tutorial_prefs_old_version_not_done(tmp_path, monkeypatch):
    path = tmp_path / "prefs.json"
    monkeypatch.setenv("AIO_PREFS", str(path))
    path.write_text(
        json.dumps({"tutorial": {"a@x": {"completed": True, "version": 0}}}),
        encoding="utf-8",
    )
    assert prefs.is_tutorial_done("a@x", version=1) is False

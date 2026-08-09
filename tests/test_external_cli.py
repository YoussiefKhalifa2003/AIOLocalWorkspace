"""Tests for launching interactive Claude/Codex in a new terminal."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def test_launch_argv_prefers_windows_terminal(monkeypatch):
    from app.cli_pkg.tui import external_cli as ext

    monkeypatch.setattr(ext.sys, "platform", "win32")
    monkeypatch.setattr(
        ext.shutil,
        "which",
        lambda name: "C:\\wt.exe" if name in ("wt", "wt.exe") else None,
    )
    argv = ext.launch_argv(r"C:\npm\claude.cmd", cwd=r"C:\work")
    assert argv[0] == "C:\\wt.exe"
    assert "nt" in argv
    assert argv[-1] == r"C:\npm\claude.cmd"
    assert r"C:\work" in argv


def test_launch_argv_falls_back_to_cmd_start(monkeypatch):
    from app.cli_pkg.tui import external_cli as ext

    monkeypatch.setattr(ext.sys, "platform", "win32")
    monkeypatch.setattr(ext.shutil, "which", lambda name: None)
    argv = ext.launch_argv(r"C:\npm\codex.cmd", cwd=r"C:\work")
    assert argv[:3] == ["cmd.exe", "/c", "start"]
    assert argv[-1] == r"C:\npm\codex.cmd"


def test_launch_coding_cli_missing_binary(monkeypatch):
    from app.cli_pkg.tui import external_cli as ext

    monkeypatch.setattr(ext, "resolve_cli_bin", lambda name: None)
    ok, msg = ext.launch_coding_cli("claude")
    assert ok is False
    assert "not on PATH" in msg


def test_launch_coding_cli_spawns(monkeypatch, tmp_path):
    from app.cli_pkg.tui import external_cli as ext

    calls: list[dict] = []

    def fake_popen(argv, **kwargs):
        calls.append({"argv": argv, "kwargs": kwargs})
        return object()

    monkeypatch.setattr(ext, "resolve_cli_bin", lambda name: str(tmp_path / "claude.cmd"))
    monkeypatch.setattr(ext.sys, "platform", "win32")
    monkeypatch.setattr(ext.shutil, "which", lambda name: None)
    monkeypatch.setattr(ext.subprocess, "Popen", fake_popen)
    ok, msg = ext.launch_coding_cli("claude", cwd=str(tmp_path))
    assert ok is True
    assert "opened Claude" in msg
    assert calls and calls[0]["argv"][0] == "cmd.exe"

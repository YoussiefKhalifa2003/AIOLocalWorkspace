"""CLI ping sound helpers."""

from __future__ import annotations

from app.cli_pkg.tui import ping_sound


def test_play_ping_sound_swallows_errors(monkeypatch):
    def boom():
        raise RuntimeError("no audio")

    monkeypatch.setattr(ping_sound, "_ping_windows", boom)
    monkeypatch.setattr(ping_sound, "_ping_macos", boom)
    monkeypatch.setattr(ping_sound, "_ping_linux", boom)
    monkeypatch.setattr(ping_sound.sys, "platform", "win32")
    ping_sound.play_ping_sound()  # must not raise


def test_play_ping_sound_calls_windows(monkeypatch):
    seen = {"n": 0}

    def win():
        seen["n"] += 1

    monkeypatch.setattr(ping_sound.sys, "platform", "win32")
    monkeypatch.setattr(ping_sound, "_ping_windows", win)
    # play_ping_sound starts a thread - run the sync path directly for the unit test
    ping_sound._play_ping_sound_sync()
    assert seen["n"] == 1


def test_soft_ping_wav_bytes_nonempty():
    data = ping_sound._soft_ping_wav_bytes()
    assert data[:4] == b"RIFF"
    assert len(data) > 100


def test_unread_rise_flash_only_on_increase():
    assert ping_sound.unread_rise_flash(None, 5, [{"from": "A"}]) == (False, "")
    assert ping_sound.unread_rise_flash(0, 0, []) == (False, "")
    assert ping_sound.unread_rise_flash(1, 1, [{"from": "A"}]) == (False, "")
    ping, msg = ping_sound.unread_rise_flash(0, 1, [{"from": "Alice"}])
    assert ping is True
    assert msg == "Alice pinged you"
    ping2, msg2 = ping_sound.unread_rise_flash(1, 3, [{"from": "Bob"}])
    assert ping2 is True
    assert msg2 == "Bob pinged you"

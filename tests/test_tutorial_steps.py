"""Tour step lists: member shared path + owner extras."""

from __future__ import annotations

from app.cli_pkg.tui.tutorial import MEMBER_STEPS, OWNER_EXTRA_STEPS, build_tour_steps


def test_member_tour_excludes_owner_steps():
    steps = build_tour_steps(is_owner=False)
    assert steps == MEMBER_STEPS
    assert all(not s.owner_only for s in steps)
    assert len(steps) == 8


def test_owner_tour_appends_extras():
    steps = build_tour_steps(is_owner=True)
    assert steps[: len(MEMBER_STEPS)] == MEMBER_STEPS
    assert steps[len(MEMBER_STEPS) :] == OWNER_EXTRA_STEPS
    assert len(steps) == len(MEMBER_STEPS) + len(OWNER_EXTRA_STEPS)
    assert any(s.id == "dash" for s in steps)
    assert any(s.id == "live" for s in steps)


def test_tour_steps_have_spotlights():
    for s in build_tour_steps(is_owner=True):
        assert s.title
        assert s.body
        assert len(s.body) <= 120
        assert s.spotlight  # every planned step has a target
    assert any(s.spotlight == "#tabs-row" for s in MEMBER_STEPS)
    assert any(s.spotlight == "#composer-row" for s in MEMBER_STEPS)
    assert any(s.id == "voice" for s in MEMBER_STEPS)
    assert "[ ]" in next(s.body for s in MEMBER_STEPS if s.id == "board")

"""Spotlight Tour for the CLI — shared member path + owner extras."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Button, Static


@dataclass(frozen=True)
class TourStep:
    id: str
    title: str
    body: str
    tab: str | None = None
    spotlight: str | None = None
    owner_only: bool = False
    action: str | None = None  # e.g. select_private


MEMBER_STEPS: list[TourStep] = [
    TourStep(
        id="tabs",
        title="Tabs",
        body="Chat · Board · Agents · People. Owners also get Dash and Live.",
        tab="chat",
        spotlight="#tabs-row",
    ),
    TourStep(
        id="rooms",
        title="Rooms",
        body="#channels are the team. ◆ my room is private AI.",
        tab="chat",
        spotlight="#chat-list",
    ),
    TourStep(
        id="type",
        title="Type here",
        body="Type in this box: / for AI · ! for commands · @ to ping.",
        tab="chat",
        spotlight="#composer-row",
    ),
    TourStep(
        id="attach",
        title="Attach",
        body="The plus (+) on the left of the composer picks a file for your next message. Mic on the right starts voice.",
        tab="chat",
        spotlight="#composer-row",
    ),
    TourStep(
        id="voice",
        title="Voice",
        body="ctrl+m records (or pick an audio file) and fills the composer.",
        tab="chat",
        spotlight="#composer-row",
    ),
    TourStep(
        id="pings",
        title="Pings",
        body="When someone @mentions you: sound + @N on this bar. Then press ctrl+n.",
        tab="chat",
        spotlight="#status-line",
    ),
    TourStep(
        id="board",
        title="Board",
        body="Cards are work. [ ] shifts columns; s jumps to any status.",
        tab="board",
        spotlight="#columns",
    ),
    TourStep(
        id="my-room",
        title="My room",
        body="Run /ask /code /status here. Notes stay quiet.",
        tab="chat",
        spotlight="#chat",
        action="select_private",
    ),
]

OWNER_EXTRA_STEPS: list[TourStep] = [
    TourStep(
        id="people",
        title="People",
        body="Invite teammates. Change roles. Remove members.",
        tab="people",
        spotlight="#people-invite",
    ),
    TourStep(
        id="dash",
        title="Dash",
        body="Owner tables: people, models, tokens, open work.",
        tab="dashboard",
        spotlight="#dashboard",
    ),
    TourStep(
        id="live",
        title="Live",
        body="Live gauges and WIP. Polls while you watch.",
        tab="live",
        spotlight="#live",
    ),
    TourStep(
        id="invite-cmd",
        title="Invite cmd",
        body="!invite mints a join link. Email when configured.",
        tab="chat",
        spotlight="#composer",
    ),
]


def build_tour_steps(*, is_owner: bool) -> list[TourStep]:
    steps = list(MEMBER_STEPS)
    if is_owner:
        steps.extend(OWNER_EXTRA_STEPS)
    return steps


class TutorialCoach(Vertical):
    """Bottom coach card: step text + Back / Next / Skip + pulsing white glow."""

    DEFAULT_CSS = """
    TutorialCoach {
        height: auto;
        display: none;
        padding: 1 1 1 1;
        background: #161616;
        border-top: solid #e8e8e8;
    }
    TutorialCoach.-active { display: block; }
    #tour-head { height: 1; color: #ffffff; text-style: bold; }
    #tour-body { height: auto; color: #d0d0d0; }
    #tour-hint {
        height: 1;
        color: #a0a0a0;
        display: none;
    }
    #tour-hint.-show { display: block; }
    #tour-actions { height: 3; align: left middle; }
    #tour-actions Button { margin-right: 1; }
    """

    def __init__(self) -> None:
        super().__init__(id="tour-coach")
        self._steps: list[TourStep] = []
        self._index = 0
        self._spotlight_node: Any = None
        self._faded_nodes: list[Any] = []
        self._on_finished: Callable[[bool], None] | None = None
        self._glow_on = False
        self._glow_timer: Timer | None = None
        self._head = Static("", id="tour-head", markup=True)
        self._body = Static("", id="tour-body", markup=True)
        self._hint = Static("", id="tour-hint", markup=True)

    def compose(self) -> ComposeResult:
        yield self._head
        yield self._body
        yield self._hint
        with Horizontal(id="tour-actions"):
            yield Button("Back", id="tour-back")
            yield Button("Next", variant="primary", id="tour-next")
            yield Button("Skip", id="tour-skip")

    @property
    def active(self) -> bool:
        return self.has_class("-active")

    def start(
        self,
        steps: list[TourStep],
        *,
        on_finished: Callable[[bool], None] | None = None,
    ) -> None:
        if not steps:
            return
        self.stop(completed=False)
        self._steps = list(steps)
        self._index = 0
        self._on_finished = on_finished
        self.add_class("-active")
        self._enter_step()

    def stop(self, *, completed: bool) -> None:
        self._stop_glow()
        self._clear_spotlight()
        self.remove_class("-active")
        self._hint.remove_class("-show")
        self._hint.update("")
        cb = self._on_finished
        self._on_finished = None
        self._steps = []
        self._index = 0
        if cb:
            cb(completed)

    def _stop_glow(self) -> None:
        if self._glow_timer is not None:
            try:
                self._glow_timer.stop()
            except Exception:
                pass
            self._glow_timer = None
        self._glow_on = False

    @staticmethod
    def _is_same_or_under(node: Any, ancestor: Any) -> bool:
        cur = node
        while cur is not None:
            if cur is ancestor:
                return True
            cur = getattr(cur, "parent", None)
        return False

    # Regions we can safely fade without covering the spotlight target.
    _DIM_SELECTORS = (
        "#tabs-row",
        "#chat-sidebar",
        "#transcript",
        "#picker",
        "#llm-wait",
        "#attach-pending",
        "#chat-title",
        "#composer-row",
        "#composer",
        "#chat-attach",
        "#columns",
        "#detail",
        "#people",
        "#agents",
        "#dashboard",
        "#live",
        "#status-line",
        "#chat-list",
        "#member-list",
    )

    def _clear_faded(self) -> None:
        for node in self._faded_nodes:
            try:
                node.remove_class("tour-faded")
            except Exception:
                pass
        self._faded_nodes = []

    def _apply_fades(self, spotlight: Any) -> None:
        """Dim everything except the spotlight widget (and its ancestors)."""
        self._clear_faded()
        app = self.app
        for sel in self._DIM_SELECTORS:
            try:
                region = app.query_one(sel)
            except Exception:
                continue
            # Don't fade the target or a container that wraps it
            if self._is_same_or_under(spotlight, region):
                continue
            # Don't fade a child of the spotlight (e.g. #composer inside #composer-row)
            if self._is_same_or_under(region, spotlight):
                continue
            try:
                region.add_class("tour-faded")
                self._faded_nodes.append(region)
            except Exception:
                pass

    def _clear_spotlight(self) -> None:
        self._stop_glow()
        self._clear_faded()
        node = self._spotlight_node
        self._spotlight_node = None
        if node is not None:
            try:
                node.remove_class("tour-spotlight")
                node.remove_class("tour-glow")
                node.remove_class("tour-glow-dim")
            except Exception:
                pass

    def _enter_step(self) -> None:
        if not self._steps:
            return
        step = self._steps[self._index]
        n = len(self._steps)
        self._head.update(
            f"Tour  {self._index + 1}/{n}  ·  {escape(step.title)}"
        )
        self._body.update(escape(step.body))
        # Extra key hint for steps that need a shortcut callout
        if step.id == "pings":
            self._hint.update(
                "[b white]→[/] watch [b]@N[/] on the status bar  ·  then [b]ctrl+n[/]"
            )
            self._hint.add_class("-show")
        elif step.id in ("type", "attach"):
            tip = (
                "[b white]→[/] the glowing box is where you type"
                if step.id == "type"
                else "[b white]→[/] plus (+) left · mic right · type in the middle"
            )
            self._hint.update(tip)
            self._hint.add_class("-show")
        else:
            self._hint.remove_class("-show")
            self._hint.update("")

        app = self.app
        if step.tab and hasattr(app, "show_tab"):
            app.show_tab(step.tab)  # type: ignore[attr-defined]
        if step.action == "select_private" and hasattr(app, "chat_view"):
            chat_view = app.chat_view  # type: ignore[attr-defined]
            private = next(
                (c for c in getattr(chat_view, "chats", []) if c.get("kind") == "private"),
                None,
            )
            if private is not None:
                chat_view.select_chat(int(private["id"]))

        self.call_after_refresh(self._apply_spotlight, step.spotlight)

    def _apply_spotlight(self, selector: str | None) -> None:
        self._clear_spotlight()
        if not selector:
            return
        try:
            node = self.app.query_one(selector)
        except Exception:
            return
        try:
            self._apply_fades(node)
            node.add_class("tour-spotlight")
            node.add_class("tour-glow")
            self._spotlight_node = node
            self._glow_on = True
            try:
                node.scroll_visible(animate=False)
            except Exception:
                pass
            self._glow_timer = self.set_interval(0.55, self._tick_glow)
        except Exception:
            self._spotlight_node = None

    def _tick_glow(self) -> None:
        node = self._spotlight_node
        if node is None:
            return
        self._glow_on = not self._glow_on
        try:
            node.set_class(self._glow_on, "tour-glow")
            node.set_class(not self._glow_on, "tour-glow-dim")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "tour-skip":
            event.stop()
            self.stop(completed=False)
            return
        if bid == "tour-back":
            event.stop()
            if self._index > 0:
                self._index -= 1
                self._enter_step()
            return
        if bid == "tour-next":
            event.stop()
            if self._index >= len(self._steps) - 1:
                self.stop(completed=True)
                return
            self._index += 1
            self._enter_step()

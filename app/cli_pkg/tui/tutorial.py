"""Spotlight Tour for the CLI — shared member path + owner extras."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
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
        body="Chat Board Agents People. Owners also get Dash and Live.",
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
        title="Type",
        body="/ AI skills · ! board commands · @ ping someone.",
        tab="chat",
        spotlight="#composer",
    ),
    TourStep(
        id="attach",
        title="Attach",
        body="Attach files the AI can read with your next message.",
        tab="chat",
        spotlight="#chat-attach",
    ),
    TourStep(
        id="pings",
        title="Pings",
        body="When @'d: sound + badge. Open with ctrl+n.",
        tab="chat",
        spotlight="#status-line",
    ),
    TourStep(
        id="board",
        title="Board",
        body="Cards are work. Claim, move, done.",
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
        owner_only=True,
    ),
    TourStep(
        id="dash",
        title="Dash",
        body="Owner tables: people, models, tokens, open work.",
        tab="dashboard",
        spotlight="#dashboard",
        owner_only=True,
    ),
    TourStep(
        id="live",
        title="Live",
        body="Live gauges and WIP. Polls while you watch.",
        tab="live",
        spotlight="#live",
        owner_only=True,
    ),
    TourStep(
        id="invite-cmd",
        title="Invite cmd",
        body="!invite mints a join link. Email when configured.",
        tab="chat",
        spotlight="#composer",
        owner_only=True,
    ),
]


def build_tour_steps(*, is_owner: bool) -> list[TourStep]:
    steps = list(MEMBER_STEPS)
    if is_owner:
        steps.extend(OWNER_EXTRA_STEPS)
    return steps


class TutorialCoach(Vertical):
    """Bottom coach card: step text + Back / Next / Skip."""

    DEFAULT_CSS = """
    TutorialCoach {
        height: auto;
        display: none;
        padding: 0 1 1 1;
        background: $panel;
        border-top: thick #ff2ea6;
    }
    TutorialCoach.-active { display: block; }
    #tour-head { height: 1; }
    #tour-body { height: auto; color: $text-muted; }
    #tour-actions { height: 3; align: left middle; }
    #tour-actions Button { margin-right: 1; }
    """

    def __init__(self) -> None:
        super().__init__(id="tour-coach")
        self._steps: list[TourStep] = []
        self._index = 0
        self._spotlight_node: Any = None
        self._on_finished: Callable[[bool], None] | None = None
        self._head = Static("", id="tour-head", markup=True)
        self._body = Static("", id="tour-body", markup=True)

    def compose(self) -> ComposeResult:
        yield self._head
        yield self._body
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
        self._clear_spotlight()
        self.remove_class("-active")
        try:
            self.app.query_one("#body").remove_class("tour-dim")
        except Exception:
            pass
        cb = self._on_finished
        self._on_finished = None
        self._steps = []
        self._index = 0
        if cb:
            cb(completed)

    def _clear_spotlight(self) -> None:
        node = self._spotlight_node
        self._spotlight_node = None
        if node is not None:
            try:
                node.remove_class("tour-spotlight")
            except Exception:
                pass

    def _enter_step(self) -> None:
        if not self._steps:
            return
        step = self._steps[self._index]
        n = len(self._steps)
        self._head.update(
            f"[b]Tour[/b]  {self._index + 1}/{n}  ·  [b]{escape(step.title)}[/b]"
        )
        self._body.update(escape(step.body))

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
        try:
            body = self.app.query_one("#body")
            body.add_class("tour-dim")
        except Exception:
            pass
        if not selector:
            return
        try:
            node = self.app.query_one(selector)
        except Exception:
            return
        try:
            node.add_class("tour-spotlight")
            self._spotlight_node = node
            node.scroll_visible(animate=False)
        except Exception:
            self._spotlight_node = None

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

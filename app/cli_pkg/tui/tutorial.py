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
        body="Chat · Board · Agents. Owners also get People · Dash · Live. @N · Tour · Log out sit on the right.",
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
        id="new-channel",
        title="+ channel",
        body="Create a room with + channel (or ctrl+shift+n).",
        tab="chat",
        spotlight="#chat-new",
    ),
    TourStep(
        id="type",
        title="Type here",
        body="/ AI skills · ! commands · @ ping. Try !claude or !codex to open those apps.",
        tab="chat",
        spotlight="#composer-row",
    ),
    TourStep(
        id="attach",
        title="Attach",
        body="Plus (+) on the left picks a file for your next message.",
        tab="chat",
        spotlight="#chat-attach",
    ),
    TourStep(
        id="voice",
        title="Mic",
        body="Mic on the right records or picks audio into the box.",
        tab="chat",
        spotlight="#chat-mic",
    ),
    TourStep(
        id="edit",
        title="Edit · delete",
        body="Hover your own line for edit · delete. Edits keep later messages.",
        tab="chat",
        spotlight="#transcript",
    ),
    TourStep(
        id="pings",
        title="Mentions",
        body="When someone @pings you: sound + @N up here. Click it → jump to the message.",
        tab="chat",
        spotlight="#mentions-btn",
    ),
    TourStep(
        id="board",
        title="Board",
        body="You only see your cards. Select one → press a → pick Codex, Claude, or llm.",
        tab="board",
        spotlight="#columns",
    ),
    TourStep(
        id="agents",
        title="Agents",
        body="Choose which model backs /ask /code and friends, then Save.",
        tab="agents",
        spotlight="#agents",
    ),
    TourStep(
        id="my-room",
        title="My room",
        body="Run /ask /code /status here. Notes stay quiet.",
        tab="chat",
        spotlight="#chat",
        action="select_private",
    ),
    TourStep(
        id="logout",
        title="Log out",
        body="Log out clears saved sign-in on this machine.",
        tab="chat",
        spotlight="#logout-btn",
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
        title="Invite",
        body="!invite mints a join link (use a public tunnel URL for off-LAN).",
        tab="chat",
        spotlight="#composer-row",
        owner_only=True,
    ),
    TourStep(
        id="owner-board",
        title="Owner board",
        body="You see everyone’s cards. Merge PRs with m when a card is mergeable.",
        tab="board",
        spotlight="#columns",
        owner_only=True,
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
    #tour-actions {
        height: auto;
        min-height: 3;
        width: 100%;
        align: left middle;
    }
    #tour-actions Button {
        margin-right: 1;
        min-width: 8;
        display: block;
    }
    """

    # Chrome targets already on-screen — scrolling them can shove the coach off-view.
    _NO_SCROLL_SELECTORS = frozenset(
        {
            "#tabs-row",
            "#tour-btn",
            "#logout-btn",
            "#mentions-btn",
            "#status-line",
            "#composer-row",
            "#composer",
            "#chat-attach",
            "#chat-mic",
            "#chat-new",
            "#chat-title",
        }
    )

    def __init__(self) -> None:
        super().__init__(id="tour-coach")
        self._steps: list[TourStep] = []
        self._index = 0
        self._spotlight_node: Any = None
        self._faded_nodes: list[Any] = []
        self._on_finished: Callable[[bool], None] | None = None
        self._glow_on = False
        self._glow_timer: Timer | None = None
        self._forced_mentions_btn = False
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
        "#tour-btn",
        "#logout-btn",
        "#mentions-btn",
        "#chat-sidebar",
        "#transcript",
        "#picker",
        "#llm-wait",
        "#attach-pending",
        "#chat-title",
        "#composer-row",
        "#composer",
        "#chat-attach",
        "#chat-mic",
        "#chat-new",
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

    def _restore_mentions_btn(self) -> None:
        if not self._forced_mentions_btn:
            return
        self._forced_mentions_btn = False
        app = self.app
        if hasattr(app, "_paint_mentions_btn"):
            try:
                app._paint_mentions_btn()  # type: ignore[attr-defined]
                return
            except Exception:
                pass
        try:
            btn = app.query_one("#mentions-btn", Button)
            btn.display = False
            btn.set_class(False, "has-unread")
            btn.label = "@"
        except Exception:
            pass

    def _clear_spotlight(self) -> None:
        self._stop_glow()
        self._clear_faded()
        self._restore_mentions_btn()
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
        last = self._index >= len(self._steps) - 1
        try:
            nxt = self.query_one("#tour-next", Button)
            skip = self.query_one("#tour-skip", Button)
            back = self.query_one("#tour-back", Button)
            nxt.display = True
            back.display = True
            # Skip is pointless on the final step (Next is already Done).
            skip.display = not last
            nxt.label = "Done" if last else "Next"
            skip.disabled = False
        except Exception:
            pass
        # Extra key hint for steps that need a shortcut callout
        if step.id == "pings":
            self._hint.update(
                "[b white]→[/] glowing [b]@N[/] · click to open mentions"
            )
            self._hint.add_class("-show")
        elif step.id == "type":
            self._hint.update(
                "[b white]→[/] [b]/[/] AI · [b]![/] commands · [b]!claude[/] / [b]!codex[/]"
            )
            self._hint.add_class("-show")
        elif step.id == "attach":
            self._hint.update("[b white]→[/] glowing [b]+[/] attaches a file")
            self._hint.add_class("-show")
        elif step.id == "voice":
            self._hint.update("[b white]→[/] glowing [b]mic[/]")
            self._hint.add_class("-show")
        elif step.id == "edit":
            self._hint.update("[b white]→[/] hover your own lines for [b]edit[/] / [b]delete[/]")
            self._hint.add_class("-show")
        elif step.id == "board":
            self._hint.update("[b white]→[/] select a card · press [b]a[/] · pick a runner")
            self._hint.add_class("-show")
        elif step.id == "owner-board":
            self._hint.update("[b white]→[/] [b]m[/] merges when a card says mergeable")
            self._hint.add_class("-show")
        elif step.id == "logout":
            self._hint.update("[b white]→[/] [b]Log out[/] (top right)")
            self._hint.add_class("-show")
        elif step.id == "new-channel":
            self._hint.update("[b white]→[/] [b]+ channel[/]")
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

    def _ensure_mentions_btn_visible(self) -> None:
        """@N is hidden with zero unread — show a demo badge for the tour step."""
        try:
            btn = self.app.query_one("#mentions-btn", Button)
        except Exception:
            return
        btn.display = True
        btn.label = "@1"
        btn.set_class(True, "has-unread")
        self._forced_mentions_btn = True

    def _apply_spotlight(self, selector: str | None) -> None:
        self._clear_spotlight()
        if not selector:
            return
        if selector == "#mentions-btn":
            self._ensure_mentions_btn_visible()
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
            if selector not in self._NO_SCROLL_SELECTORS:
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

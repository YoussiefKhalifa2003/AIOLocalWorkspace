"""AIO in the terminal: the whole workspace as a full-screen app.

Chat, Board, Agents, People, Dashboard and Live are the same surfaces as the
web UI (plus a chart board) and talk to the same API. Every member can run it;
owner-only surfaces (Dashboard, Live, merge) are gated individually.
"""

from __future__ import annotations

from typing import Any

from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    ContentSwitcher,
    Footer,
    Header,
    Input,
    Label,
    Static,
    Tab,
    Tabs,
)

from app.cli_pkg.prefs import is_tutorial_done, mark_tutorial_done
from app.cli_pkg.session import Credentials, load_credentials, save_credentials
from app.cli_pkg.tui.client import ApiClient, ApiError, Workspace, login
from app.cli_pkg.tui.client import board_fingerprint  # noqa: F401  (re-export for callers)
from app.cli_pkg.tui.ping_sound import play_ping_sound, unread_rise_flash
from app.cli_pkg.tui.tutorial import TutorialCoach, build_tour_steps
from app.cli_pkg.tui.views.agents import AgentsView
from app.cli_pkg.tui.views.board import BoardView
from app.cli_pkg.tui.views.chat import ChatView
from app.cli_pkg.tui.views.dashboard import DashboardView
from app.cli_pkg.tui.views.live import LiveView
from app.cli_pkg.tui.views.people import PeopleView
from app.cli_pkg.tui.widgets import ConfirmModal, HelpModal, MentionsModal
from app.config import get_settings

TABS: list[tuple[str, str]] = [
    ("chat", "Chat  c"),
    ("board", "Board  b"),
    ("agents", "Agents  g"),
    ("people", "People  p"),
    ("dashboard", "Dash  d"),
    ("live", "Live  v"),
]

# Letter shortcuts work whenever you are not typing a message; the ctrl+ pair
# works everywhere, including mid-sentence in the chat box.
TAB_KEYS: list[tuple[str, str, str]] = [
    ("c", "ctrl+t", "chat"),
    ("b", "ctrl+b", "board"),
    ("g", "ctrl+g", "agents"),
    ("p", "ctrl+e", "people"),
    ("d", "ctrl+d", "dashboard"),
    ("v", "ctrl+v", "live"),
]

STYLES = """
Screen { background: $surface; }

Header { background: $panel; }
#tabs { background: $panel; }
#status-line { height: 1; padding: 0 1; color: $text-muted; background: $panel; }
#body { height: 1fr; }
#body.tour-dim { opacity: 0.45; }
/* Tour spotlight — hot magenta, thick frame, obvious fill (not amber $accent) */
.tour-spotlight {
    border: thick #ff2ea6;
    background: #ff2ea6 28%;
    padding: 0 1;
}
#tabs-row {
    height: auto;
    min-height: 3;
    background: $panel;
    padding: 0;
}
#tabs-row.tour-spotlight {
    /* Border the row, never the Tabs widget (border on Tabs crushes labels). */
    border: thick #ff2ea6;
    background: #3b0a2e;
    padding: 0 1;
}
#tabs-row #tabs { width: 1fr; background: transparent; }
#tour-btn { width: 10; margin: 0 1; }

.view-head { text-style: bold; padding: 1 1 0 1; }
.view-sub, #agent-info { color: $text-muted; padding: 0 1 1 1; }
.table-head { text-style: bold; color: $text-muted; padding: 1 1 0 1; }

/* chat ------------------------------------------------------------------ */
#chat-body { height: 1fr; }
#chat-sidebar { width: 26; border-right: solid $panel-lighten-2; }
.side-head { color: $accent 70%; text-style: bold; padding: 1 1 0 1; }
#chat-list { height: auto; max-height: 40%; background: transparent; }
#chat-list ListItem { padding: 0 1; height: 1; }
/* Always show cursor + active room (even when composer has focus) */
#chat-list ListItem.-highlight {
    background: #22d3ee 35%;
}
#chat-list ListItem.active-chat {
    background: #22d3ee 28%;
    border-left: tall #22d3ee;
    text-style: bold;
}
#chat-list ListItem.active-chat.-highlight {
    background: #22d3ee 50%;
}
#member-list { height: auto; background: transparent; }
#member-list ListItem { padding: 0 1; }
#member-list ListItem.-highlight {
    background: #a78bfa 30%;
}
#chat-main { width: 1fr; }
#chat-title { height: 1; padding: 0 1; background: $panel; }
#transcript { height: 1fr; padding: 0 2; }
#transcript > Static { padding: 0 0 1 0; }
.agent-msg { border-left: outer $accent 30%; padding-left: 1; }
.whisper-msg { color: $text-muted; }
.pending-msg { color: $accent; }
MessageView { height: auto; }
MessageView .msg-chart { height: 18; width: 100%; }
#llm-wait { height: auto; padding: 0 1 1 1; display: none; }
#llm-wait-label { height: 1; color: $accent; }
#llm-wait-bar { width: 100%; height: 1; margin: 0 0 1 0; }
#attach-pending {
    height: auto;
    padding: 0 1;
    color: $accent;
    display: none;
}
#composer-row {
    height: auto;
    padding: 0 1 1 1;
    align: left middle;
}
#composer-row #composer { width: 1fr; border: round $panel-lighten-2; }
#composer-row #composer:focus { border: round $accent; }
#chat-attach { width: 12; margin-left: 1; height: 3; }
#picker {
    height: auto; max-height: 10; margin: 0 1;
    border: round #22d3ee; background: $surface;
}
#picker ListItem { padding: 0 1; height: 1; }
/* Picker keeps focus on composer — still paint the highlighted row */
#picker ListItem.-highlight {
    background: #22d3ee 45%;
    border-left: tall #22d3ee;
    text-style: bold;
}

/* people ---------------------------------------------------------------- */
#people-list { height: 1fr; }
#people-list ListItem { padding: 0 1; }
#people-note { padding: 0 1; }
#people-actions { height: 3; padding: 0 1; }
#people-actions Button { margin-right: 1; }

/* board ----------------------------------------------------------------- */
BoardView { height: 1fr; layout: horizontal; }
#columns { width: 1fr; height: 1fr; padding: 0 1 0 0; }
BoardColumn {
    width: 34; height: 1fr;
    border: round $panel-lighten-2;
    padding: 0 1;
    margin-right: 1;
}
BoardColumn:focus-within { border: round $accent; }
#detail {
    width: 44; height: 1fr;
    border: round $panel-lighten-2;
    padding: 0 1 1 1;
    margin-left: 1;
}
#detail-toolbar { height: 3; }
#detail-title { width: 1fr; padding: 1 0 0 0; }
#detail-edit { width: auto; min-width: 8; margin-right: 1; }
#detail-hide { width: auto; min-width: 8; }
.detail-label { color: $text-muted; text-style: bold; margin-top: 1; height: 1; }
#detail-header { margin: 0 0 1 0; }
#detail-meta { margin-bottom: 1; }
#detail-desc, #detail-subs { margin-bottom: 1; }
#detail-actions { margin-top: 1; color: $text-muted; }
#detail-bar { width: 100%; height: 1; margin: 0 0 1 0; }
#detail-links { height: auto; margin-bottom: 1; }
.detail-link { color: $accent; text-style: underline; height: 1; margin-bottom: 0; }
ListView { background: transparent; height: 1fr; }
ListItem { padding: 0; background: transparent; }
ListItem.board-card {
    border: round $panel-lighten-2;
    margin: 0 0 1 0;
    padding: 1 1;
    height: auto;
    background: $surface;
}
/* Only the focused column shows a selection — otherwise every column
   lights up its ListView cursor and the board looks multi-selected. */
ListItem.board-card.-highlight {
    border: round $panel-lighten-2;
    background: $surface;
}
ListView:focus > ListItem.board-card.-highlight {
    border: round $accent;
    background: $accent 30%;
}
.card-body { height: auto; }

/* setup / invite modals ------------------------------------------------- */
#setup-box {
    width: 72; height: auto; max-height: 90%; padding: 1 2;
    border: thick $accent; background: $surface;
}
#setup-box TextArea { height: 6; margin-bottom: 1; }
#setup-subs-head { height: 3; }
#setup-subs-head Button { margin-left: 1; }
#setup-subs { height: auto; max-height: 10; }
.setup-sub-row { height: 3; margin-bottom: 0; }
.setup-sub-row Input { width: 1fr; }
.setup-rm { width: 5; min-width: 5; }
#setup-actions { height: 3; margin-top: 1; }
#setup-actions Button { margin-right: 1; }

/* agents ---------------------------------------------------------------- */
.agent-row { height: 3; padding: 0 1; }
.agent-name { width: 22; padding: 1 0 0 0; }
#agent-rows { height: 1fr; }
#agent-actions { height: 3; padding: 0 1; }
#agent-actions Button { margin-right: 1; }

/* dashboard ------------------------------------------------------------- */
#stat-row { height: 5; padding: 0 1; }
.stat {
    width: 1fr; height: 4; content-align: center middle;
    border: round $panel-lighten-2; margin-right: 1; text-align: center;
}
DataTable { height: auto; max-height: 14; margin: 0 1; }
#dash-note { padding: 0 1; }

/* live ------------------------------------------------------------------ */
#live-note { padding: 0 1; }
#gauge-row { height: 6; padding: 0 1; }
.gauge-card {
    width: 1fr; height: 5; margin-right: 1;
    border: round $panel-lighten-2; padding: 0 1;
}
.gauge-value { text-align: center; height: 1; }
#spark-row { height: 16; padding: 0 1; margin-top: 1; }
.spark-panel {
    width: 1fr; height: 1fr; margin-right: 1;
    border: round $panel-lighten-2; padding: 0 1;
}
#wip-col { width: 1fr; height: 1fr; }
.spark-label { color: $text-muted; height: 1; }
Sparkline { height: 2; margin-bottom: 1; }
#bar-row { height: 12; padding: 0 1; margin-top: 1; }
.bar-panel {
    width: 1fr; height: 1fr; margin-right: 1;
    border: round $panel-lighten-2; padding: 0 1;
}

/* modals ---------------------------------------------------------------- */
#confirm-box, #help-box, #login-box {
    width: 66; height: auto; padding: 1 2;
    border: thick $accent; background: $surface;
}
#help-box { width: 78; height: 80%; }
#confirm-title { text-style: bold; padding-bottom: 1; }
#login-box Input { margin-bottom: 1; }
#login-err { color: $error; }
LoginScreen { align: center middle; }
ModalScreen { align: center middle; }
"""


class LoginScreen(Screen[Credentials]):
    """Sign in without leaving the app."""

    def __init__(self, base_url: str, email: str = "") -> None:
        super().__init__()
        self.base_url = base_url
        self._email = email

    def compose(self) -> ComposeResult:
        with Vertical(id="login-box"):
            yield Label("AIO", id="confirm-title")
            yield Static(f"[dim]{escape(self.base_url)}[/dim]", markup=True)
            yield Input(value=self._email, placeholder="email", id="login-email")
            yield Input(placeholder="password", password=True, id="login-password")
            yield Button("Sign in", variant="primary", id="login-go")
            yield Static("", id="login-err", markup=True)

    def on_mount(self) -> None:
        self.query_one("#login-email", Input).focus()

    def on_input_submitted(self) -> None:
        self._attempt()

    def on_button_pressed(self) -> None:
        self._attempt()

    def _attempt(self) -> None:
        email = self.query_one("#login-email", Input).value.strip()
        password = self.query_one("#login-password", Input).value
        if not email or not password:
            self.query_one("#login-err", Static).update("[red]email and password required[/red]")
            return
        self.query_one("#login-err", Static).update("[dim]signing in…[/dim]")
        self._login_worker(email, password)

    @work(thread=True)
    def _login_worker(self, email: str, password: str) -> None:
        try:
            data = login(email, password, self.base_url)
            creds = Credentials(
                api_base_url=self.base_url,
                email=str(data.get("email") or email),
                api_key=str(data.get("api_key") or ""),
                user_id=int(data.get("user_id") or 0),
            )
            self.app.call_from_thread(self.dismiss, creds)
        except ApiError as exc:
            self.app.call_from_thread(
                self.query_one("#login-err", Static).update, f"[red]{escape(str(exc))}[/red]"
            )


class AioApp(App[None]):
    """The workspace, full-screen, in the terminal."""

    CSS = STYLES
    TITLE = "AIO"

    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        ("ctrl+q", "quit", "quit"),
        ("ctrl+r", "refresh_all", "refresh"),
        ("ctrl+w", "help", "help"),
        ("ctrl+n", "mentions", "mentions"),
        ("ctrl+f", "attach_file", "attach"),
        ("f1", "start_tour", "tour"),
        # Letters: the fast way around when you are not typing a message.
        ("c", "tab_chat", "c chat"),
        ("b", "tab_board", "b board"),
        ("g", "tab_agents", "g agents"),
        ("p", "tab_people", "p people"),
        ("d", "tab_dashboard", "d dash"),
        ("v", "tab_live", "v live"),
        ("question_mark", "help", "? help"),
        ("r", "refresh_all", ""),
        ("q", "quit", ""),
        # Mentions: ctrl+n only — bare @ must stay free for the chat picker.
        # Same tabs while typing, so you never have to leave the message box.
        ("ctrl+t", "tab_chat", ""),
        ("ctrl+b", "tab_board", ""),
        ("ctrl+g", "tab_agents", ""),
        ("ctrl+e", "tab_people", ""),
        ("ctrl+d", "tab_dashboard", ""),
        ("ctrl+v", "tab_live", ""),
        ("1", "tab_chat", ""),
        ("2", "tab_board", ""),
        ("3", "tab_agents", ""),
        ("4", "tab_people", ""),
        ("5", "tab_dashboard", ""),
        ("6", "tab_live", ""),
        # board
        ("j", "board_down", ""),
        ("k", "board_up", ""),
        ("h", "board_left", ""),
        ("l", "board_right", ""),
        ("n", "board_add", ""),
        ("s", "board_status", ""),
        ("a", "board_agent", ""),
        ("m", "board_merge", ""),
        ("o", "board_open_pr", ""),
        ("g", "board_open_repo", ""),
        ("y", "board_copy_pr", ""),
        ("i", "board_toggle_detail", ""),
        ("e", "board_edit", ""),
    ]

    def __init__(self, client: ApiClient, *, poll_seconds: float = 3.0) -> None:
        super().__init__()
        self.client = client
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.ws = Workspace()
        self.jobs_today = 0
        self._message = ""
        self._board_error = ""
        self._prev_unread: int | None = None
        self._tour_offered = False
        self.chat_view = ChatView(client)
        self.board_view = BoardView(client)
        self.agents_view = AgentsView(client)
        self.people_view = PeopleView(client)
        self.dashboard_view = DashboardView(client)
        self.live_view = LiveView(client)
        self.tour_coach = TutorialCoach()
        self.tour_btn = Button("Tour", id="tour-btn")
        self.status_line = Static("", id="status-line", markup=True)
        self.switcher = ContentSwitcher(initial="chat", id="body")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="tabs-row"):
            yield Tabs(*[Tab(label, id=key) for key, label in TABS], id="tabs")
            yield self.tour_btn
        with self.switcher:
            yield self.chat_view
            yield self.board_view
            yield self.agents_view
            yield self.people_view
            yield self.dashboard_view
            yield self.live_view
        yield self.tour_coach
        yield self.status_line
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f"project {self.client.project_id}"
        self.refresh_workspace()
        self.refresh_board()
        self.set_interval(6.0, self.refresh_workspace)
        self.set_interval(self.poll_seconds, self.refresh_board)
        self.set_interval(10.0, self._tick_dashboard)
        self.chat_view.composer.focus()

    # tabs ----------------------------------------------------------------

    @property
    def active_tab(self) -> str:
        return self.switcher.current or "chat"

    def show_tab(self, key: str) -> None:
        if key in ("dashboard", "live") and not self.ws.is_owner:
            label = "Dashboard" if key == "dashboard" else "Live"
            self.set_status(f"[yellow]{label} is owner-only[/yellow]")
            return
        prev = self.switcher.current
        if prev == "live" and key != "live":
            self.live_view.stop_polling()
        self.switcher.current = key
        tabs = self.query_one("#tabs", Tabs)
        if tabs.active != key:
            tabs.active = key
        # The pane is still hidden this frame, so focus has to wait for layout.
        if key == "chat":
            self.call_after_refresh(self.chat_view.composer.focus)
        elif key == "board":
            self.call_after_refresh(self.board_view.focus_selected)
        elif key == "people":
            self.call_after_refresh(self.people_view.list_view.focus)
        elif key == "dashboard":
            self.dashboard_view.load()
        elif key == "live":
            self.live_view.start_polling()
        self.set_status("")

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        key = event.tab.id or "chat"
        if key != self.switcher.current:
            self.show_tab(key)

    def action_tab_chat(self) -> None:
        self.show_tab("chat")

    def action_tab_board(self) -> None:
        self.show_tab("board")

    def action_tab_agents(self) -> None:
        self.show_tab("agents")

    def action_tab_people(self) -> None:
        self.show_tab("people")

    def action_tab_dashboard(self) -> None:
        self.show_tab("dashboard")

    def action_tab_live(self) -> None:
        self.show_tab("live")

    def action_help(self) -> None:
        self.push_screen(HelpModal())

    # polling --------------------------------------------------------------

    @work(thread=True, exclusive=True, group="workspace")
    def refresh_workspace(self) -> None:
        ws = self.client.workspace()
        self.call_from_thread(self._apply_workspace, ws)

    def _apply_workspace(self, ws: Workspace) -> None:
        if ws.error:
            self.set_status(f"[red]{escape(ws.error)}[/red]")
            return
        prev = self._prev_unread
        unread = int(ws.unread or 0)
        should_ping, flash = unread_rise_flash(prev, unread, ws.mentions)
        if should_ping:
            play_ping_sound()
            if flash:
                self._message = f"[yellow]{escape(flash)}[/yellow]"
        self._prev_unread = unread
        self.ws = ws
        self.sub_title = f"{ws.me.get('email', '')} · project {self.client.project_id}"
        self.chat_view.set_workspace(ws.chats, ws.members, str(ws.me.get("email") or ""))
        self.people_view.set_members(ws.members, ws.me)
        self._paint_status()
        self._maybe_offer_tour()

    def _maybe_offer_tour(self) -> None:
        if self._tour_offered or self.tour_coach.active:
            return
        email = str(self.ws.me.get("email") or "")
        if not email or is_tutorial_done(email):
            return
        self._tour_offered = True
        self.call_after_refresh(self._offer_tour_modal)

    def _offer_tour_modal(self) -> None:
        def done(ok: bool | None) -> None:
            email = str(self.ws.me.get("email") or "")
            if ok:
                self.action_start_tour()
            elif email:
                # Don't nag again; Tour button still replays.
                mark_tutorial_done(email)

        self.push_screen(
            ConfirmModal(
                "Take a 2-minute tour?",
                "Spotlight walkthrough of Chat, Board, Attach, and pings.",
                "You can replay anytime with the Tour button (or F1).",
                "Start tour",
            ),
            done,
        )

    def action_start_tour(self) -> None:
        if self.tour_coach.active:
            return
        steps = build_tour_steps(is_owner=self.ws.is_owner)
        email = str(self.ws.me.get("email") or "")

        def finished(completed: bool) -> None:
            self.tour_btn.disabled = False
            if completed and email:
                mark_tutorial_done(email)
                self.set_status("[green]tour complete[/green]")
            else:
                self.set_status("tour skipped")

        self.tour_btn.disabled = True
        self.tour_coach.start(steps, on_finished=finished)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "tour-btn":
            event.stop()
            self.action_start_tour()

    @work(thread=True, exclusive=True, group="board-poll")
    def refresh_board(self) -> None:
        try:
            board = self.client.board()
            jobs = self.client.jobs_total()
            error = ""
        except ApiError as exc:
            board, jobs, error = {}, 0, str(exc)
        self.call_from_thread(self._apply_board, board, jobs, error)

    def _apply_board(self, board: dict[str, Any], jobs: int, error: str) -> None:
        self._board_error = error
        if error:
            self._paint_status()
            return
        self.jobs_today = jobs
        self.board_view.apply(board, jobs)
        self._paint_status()

    def _tick_dashboard(self) -> None:
        if self.active_tab == "dashboard" and self.ws.is_owner:
            self.dashboard_view.load()
        # Live has its own 2s timer via start_polling().

    def action_refresh_all(self) -> None:
        self.board_view.invalidate()
        self.set_status("refreshing…")
        self.refresh_workspace()
        self.refresh_board()
        if self.active_tab == "dashboard":
            self.dashboard_view.load()
        elif self.active_tab == "live":
            self.live_view.load()

    def after_mutation(self, message: str) -> None:
        self.board_view.invalidate()
        self.set_status(message)
        self.refresh_board()

    # status ---------------------------------------------------------------

    def set_status(self, message: str = "") -> None:
        self._message = message
        self._paint_status()

    def _paint_status(self) -> None:
        board = self.board_view.board
        repo = board.get("github_repo") or "no repo"
        runner = get_settings().coding_backend or "llm"
        working = self.board_view.agent_working
        mentions = self.ws.unread
        parts = [
            f"[dim]repo[/dim] {escape(str(repo))}",
            f"[dim]jobs[/dim] {self.jobs_today}",
            f"[dim]agent working[/dim] {working}",
            f"[dim]runner[/dim] {runner}",
            f"[dim]@[/dim] {mentions}" if not mentions else f"[yellow]@{mentions}[/yellow]",
        ]
        if self._board_error:
            parts.append(f"[red]{escape(self._board_error)}[/red]")
        line = "  ·  ".join(parts)
        if self._message:
            line = f"{line}   {self._message}"
        self.status_line.update(line)

    # mentions -------------------------------------------------------------

    def action_mentions(self) -> None:
        def done(result: dict[str, Any] | None) -> None:
            if not result:
                return
            action = result.get("action")
            if action == "mark_all":
                self._mark_read_worker([])
                return
            if action != "open":
                return
            mention_id = int(result.get("mention_id") or 0)
            chat_id = int(result.get("chat_id") or 0)
            message_id = int(result.get("message_id") or 0)
            if mention_id:
                self._mark_read_worker([mention_id])
            if chat_id:
                self.show_tab("chat")
                self.chat_view.open_mention(chat_id, message_id)

        self.push_screen(MentionsModal(self.ws.mentions), done)

    @work(thread=True, group="mentions")
    def _mark_read_worker(self, ids: list[int]) -> None:
        try:
            self.client.mark_mentions_read(ids)
        except ApiError:
            return
        self.call_from_thread(self.refresh_workspace)

    # board actions (only while the board tab is up) -----------------------

    def _board_action(self, name: str) -> None:
        if self.active_tab != "board":
            return
        getattr(self.board_view, name)()

    def action_board_down(self) -> None:
        if self.active_tab == "board":
            self.board_view.move_card(1)

    def action_board_up(self) -> None:
        if self.active_tab == "board":
            self.board_view.move_card(-1)

    def action_board_left(self) -> None:
        if self.active_tab == "board":
            self.board_view.move_column(-1)

    def action_board_right(self) -> None:
        if self.active_tab == "board":
            self.board_view.move_column(1)

    def action_board_add(self) -> None:
        self._board_action("add_card")

    def action_board_status(self) -> None:
        self._board_action("change_status")

    def action_board_agent(self) -> None:
        self._board_action("send_to_agent")

    def action_board_merge(self) -> None:
        self._board_action("merge_card")

    def action_board_open_pr(self) -> None:
        self._board_action("open_pr")

    def action_board_open_repo(self) -> None:
        self._board_action("open_repo")

    def action_board_copy_pr(self) -> None:
        self._board_action("copy_pr")

    def action_board_toggle_detail(self) -> None:
        if self.active_tab == "board":
            self.board_view.toggle_detail()

    def action_board_edit(self) -> None:
        self._board_action("edit_card")

    def action_attach_file(self) -> None:
        self.show_tab("chat")
        self.chat_view.action_attach_file()


# Kept so `from ... import AioTui` in older scripts still works.
AioTui = AioApp


def run_app(
    project_id: int = 0,
    *,
    poll_seconds: float = 3.0,
    api_key: str = "",
    email: str = "",
) -> int:
    """Sign in if needed, then hand the terminal to the app."""
    from app.cli_pkg.session import resolve_base_url, resolve_project_id

    creds = load_credentials()
    base_url = resolve_base_url()
    key = api_key or creds.api_key
    who = email or creds.email

    if not key:
        picked = _prompt_login(base_url, who)
        if picked is None:
            return 2
        save_credentials(picked)
        key, who = picked.api_key, picked.email

    pid = int(project_id or resolve_project_id() or 1)
    client = ApiClient(project_id=pid, api_key=key, email=who)
    try:
        client.me()
    except ApiError as exc:
        print(f"cannot start: {exc}")
        print("try `aio login` (is the API running? `uvicorn app.main:app --port 8000`)")
        return 2

    AioApp(client, poll_seconds=poll_seconds).run()
    return 0


def _prompt_login(base_url: str, email: str) -> Credentials | None:
    """Tiny standalone login app, shown only when there are no credentials."""

    class LoginApp(App[Credentials]):
        CSS = STYLES

        def on_mount(self) -> None:
            def done(creds: Credentials | None) -> None:
                self.exit(creds)

            self.push_screen(LoginScreen(base_url, email), done)

    return LoginApp().run()


def run_tui(project_id: int = 0, *, poll_seconds: float = 3.0, api_key: str = "", email: str = "") -> int:
    """Backwards-compatible entry point for `aio tui`."""
    return run_app(project_id, poll_seconds=poll_seconds, api_key=api_key, email=email)

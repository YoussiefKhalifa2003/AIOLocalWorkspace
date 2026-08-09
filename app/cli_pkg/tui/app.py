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
from app.cli_pkg.session import (
    Credentials,
    auth_headers,
    clear_credentials,
    load_credentials,
    save_credentials,
)
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

# Owner-only chrome — members never see these tabs.
OWNER_ONLY_TABS = frozenset({"people", "dashboard", "live"})

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

/* Tour: fade siblings — never opacity on #body (that dims the spotlight too) */
.tour-faded {
    opacity: 0.28;
}
.tour-spotlight {
    opacity: 1;
    border: double #ffffff;
    background: #ffffff 22%;
    padding: 0 1;
}
.tour-spotlight.tour-glow {
    border: double #ffffff;
    background: #ffffff 40%;
}
.tour-spotlight.tour-glow-dim {
    border: wide #d8d8d8;
    background: #ffffff 12%;
}
/* Single-line targets: left bar only (full box collapses to two lines) */
#status-line.tour-spotlight,
#chat-title.tour-spotlight {
    border: none;
    border-left: tall #ffffff;
    background: #ffffff 30%;
    color: #ffffff;
    text-style: bold;
    height: 1;
    padding: 0 1;
    opacity: 1;
}
#status-line.tour-spotlight.tour-glow,
#chat-title.tour-spotlight.tour-glow {
    background: #ffffff 55%;
    border-left: tall #ffffff;
}
#status-line.tour-spotlight.tour-glow-dim,
#chat-title.tour-spotlight.tour-glow-dim {
    background: #ffffff 18%;
    border-left: tall #d0d0d0;
}
/* Compact chrome (height:1): soft wash like Invite — no double box (collapses label) */
#logout-btn.tour-spotlight,
#tour-btn.tour-spotlight,
#mentions-btn.tour-spotlight,
#chat-new.tour-spotlight {
    border: none;
    border-left: tall #ffffff;
    padding: 0 1;
    background: #ffffff 30%;
    color: #ffffff;
    text-style: bold;
    opacity: 1;
    height: 1;
    max-height: 1;
}
#logout-btn.tour-spotlight.tour-glow,
#tour-btn.tour-spotlight.tour-glow,
#mentions-btn.tour-spotlight.tour-glow,
#chat-new.tour-spotlight.tour-glow {
    border: none;
    border-left: tall #ffffff;
    background: #ffffff 55%;
    color: #ffffff;
}
#logout-btn.tour-spotlight.tour-glow-dim,
#tour-btn.tour-spotlight.tour-glow-dim,
#mentions-btn.tour-spotlight.tour-glow-dim,
#chat-new.tour-spotlight.tour-glow-dim {
    border: none;
    border-left: tall #d8d8d8;
    background: #ffffff 18%;
    color: #f0f0f0;
}
/* Tiny + / mic: soft wash only — border/padding collapses content (Textual crash) */
#chat-attach.tour-spotlight,
#chat-mic.tour-spotlight {
    border: none;
    padding: 0;
    background: #ffffff 40%;
    color: #ffffff;
    text-style: bold;
    opacity: 1;
}
#chat-attach.tour-spotlight {
    width: 5;
    min-width: 5;
}
#chat-mic.tour-spotlight {
    width: 7;
    min-width: 7;
}
#chat-attach.tour-spotlight.tour-glow,
#chat-mic.tour-spotlight.tour-glow {
    border: none;
    padding: 0;
    background: #ffffff 55%;
    color: #ffffff;
}
#chat-attach.tour-spotlight.tour-glow-dim,
#chat-mic.tour-spotlight.tour-glow-dim {
    border: none;
    padding: 0;
    background: #ffffff 22%;
    color: #f0f0f0;
}
/* Beat shell rules so Tour glow is visible */
#composer-row.tour-spotlight,
#composer-row.tour-spotlight.tour-glow {
    border: double #ffffff;
    background: #ffffff 18%;
    padding: 0 1;
    opacity: 1;
}
#composer-row.tour-spotlight.tour-glow-dim {
    border: wide #d0d0d0;
    background: #ffffff 8%;
}
#composer-row.tour-spotlight #composer,
#composer.tour-spotlight,
#composer.tour-spotlight.tour-glow {
    border: none;
    background: #ffffff 12%;
    opacity: 1;
}
#composer.tour-spotlight.tour-glow-dim {
    border: none;
    background: #ffffff 8%;
}
#tabs-row {
    height: auto;
    min-height: 3;
    background: $panel;
    padding: 0 1;
    align: left middle;
    border-bottom: solid $panel-lighten-2;
}
#tabs-row.tour-spotlight,
#tabs-row.tour-spotlight.tour-glow {
    border: double #ffffff;
    border-bottom: double #ffffff;
    background: #2a2a2a;
    padding: 0 1;
    opacity: 1;
}
#tabs-row.tour-spotlight.tour-glow-dim {
    border: wide #c8c8c8;
    border-bottom: wide #c8c8c8;
    background: #222222;
}
#tabs-row #tabs { width: 1fr; background: transparent; }
#tour-btn {
    width: auto;
    min-width: 6;
    max-height: 1;
    height: 1;
    padding: 0 1;
    margin: 0 1 0 0;
    border: none;
    background: transparent;
    color: $text-muted;
}
#mentions-btn {
    width: auto;
    min-width: 4;
    max-height: 1;
    height: 1;
    padding: 0 1;
    margin: 0 1 0 0;
    border: none;
    background: transparent;
    color: $text-muted;
}
#mentions-btn.has-unread {
    color: $warning;
    text-style: bold;
}
#logout-btn {
    width: auto;
    min-width: 8;
    max-height: 1;
    height: 1;
    padding: 0 1;
    margin: 0 0 0 0;
    border: none;
    background: transparent;
    color: $text-muted;
}
#tour-btn:hover,
#mentions-btn:hover,
#logout-btn:hover {
    color: $text;
    text-style: underline;
}
#body {
    height: 1fr;
}

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
#member-list { height: auto; background: transparent; padding: 0 0 1 0; }
#member-list .member-row {
    height: 1;
    width: 100%;
    padding: 0 1;
    margin-bottom: 0;
    align: left middle;
}
#member-list .member-row:hover {
    background: $boost;
}
#member-list .member-name { width: 1fr; height: 1; }
/* Discord-style: kick stays invisible until you hover the row */
#member-list .member-kick {
    width: 6;
    min-width: 6;
    height: 1;
    margin-left: 1;
    color: transparent;
    content-align: right middle;
}
#member-list .member-row:hover .member-kick,
#member-list .member-row:focus-within .member-kick {
    color: $text-muted;
}
#member-list .member-kick:hover,
#member-list .member-kick:focus {
    color: #f87171;
    text-style: underline;
}
#chat-side-actions {
    height: auto;
    margin: 0 1 1 1;
    align: left middle;
}
#chat-new {
    width: 1fr;
    margin: 0 1 0 0;
    min-width: 10;
}
#chat-del {
    width: auto;
    min-width: 8;
    margin: 0;
}
#chat-main { width: 1fr; }
#chat-title { height: 1; padding: 0 1; background: $panel; }
#transcript { height: 1fr; padding: 0 2 0 1; }
#transcript > Static { padding: 0 0 1 0; }
.whisper-msg { color: $text-muted; }
.pending-msg { color: $accent; padding: 1 1; margin: 1 0 0 0; }
SpeakerBlock { height: auto; }
SpeakerBlock.agent-block { background: $panel; }
MessageLine { height: auto; }
MessageLine .msg-chart { height: 18; width: 100%; }
#llm-wait { height: auto; padding: 0 1 1 1; display: none; }
#llm-wait-label { height: 1; color: $accent; }
#llm-wait-bar { width: 100%; height: 1; margin: 0 0 1 0; }
#attach-pending {
    height: auto;
    padding: 0 1;
    color: $accent;
    display: none;
}
#composer-ghost {
    height: 1;
    padding: 0 1;
    color: $text-muted;
    display: none;
}
#typing-line {
    height: 1;
    padding: 0 1;
    color: $text-muted;
    display: none;
}
/* One ChatGPT-style shell: + | input | mic */
#composer-row {
    height: auto;
    min-height: 3;
    margin: 0 1 1 1;
    padding: 0 1;
    align: left middle;
    border: round $panel-lighten-2;
    background: $surface;
}
#composer-row:focus-within {
    border: round $accent;
}
#composer-row.recording {
    border: round $error;
}
#composer-row #composer {
    width: 1fr;
    border: none;
    background: transparent;
    padding: 0 1;
}
#composer-row #composer:focus {
    border: none;
}
#chat-attach {
    width: 3;
    min-width: 3;
    height: 3;
    margin: 0;
    border: none;
    background: transparent;
    color: $text-muted;
    content-align: center middle;
}
#chat-mic {
    width: 5;
    min-width: 5;
    height: 3;
    margin: 0;
    border: none;
    background: transparent;
    color: $text-muted;
    content-align: center middle;
}
#chat-attach:hover,
#chat-mic:hover {
    color: $text;
    background: $boost;
}
#chat-mic.recording {
    width: 5;
    min-width: 5;
    color: $error;
    text-style: bold;
}
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
BoardColumn.col-flash { border: round $success; background: $success 10%; }
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
#dash-assign {
    height: 3;
    padding: 0 1;
    align: left middle;
}
#dash-assign-label { width: 8; padding: 1 0 0 0; }
#dash-obj, #dash-member { width: 1fr; margin-right: 1; }
#dash-assign-btn { width: 12; }

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
#confirm-box #edit-body { height: 10; margin: 1 0; }
#confirm-box Button { margin-top: 1; margin-right: 1; }
#login-box Input { margin-bottom: 1; }
#login-err { color: $error; }
LoginScreen { align: center middle; }
ModalScreen { align: center middle; }
"""


def _normalize_server_url(raw: str) -> str:
    """Strip whitespace/trailing slash; require http(s) scheme."""
    url = (raw or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = f"http://{url}"
    return url.rstrip("/")


class LoginScreen(Screen[Credentials]):
    """Sign in gate shown every time the terminal app starts."""

    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, base_url: str, email: str = "") -> None:
        super().__init__()
        self.base_url = _normalize_server_url(base_url) or "http://127.0.0.1:8000"
        self._email = email

    def compose(self) -> ComposeResult:
        with Vertical(id="login-box"):
            yield Label("Sign in to AIO", id="confirm-title")
            yield Static(
                "[dim]New here? Open your invite link first, create an account, "
                "then sign in here. Paste Server from the Done page if you joined "
                "off the company network.[/dim]",
                markup=True,
            )
            yield Input(
                value=self.base_url,
                placeholder="server (e.g. http://127.0.0.1:8000)",
                id="login-server",
            )
            yield Input(value=self._email, placeholder="email", id="login-email")
            yield Input(placeholder="password", password=True, id="login-password")
            yield Button("Sign in", variant="primary", id="login-go")
            yield Static("", id="login-err", markup=True)

    def on_mount(self) -> None:
        email = self.query_one("#login-email", Input)
        if email.value.strip():
            self.query_one("#login-password", Input).focus()
        else:
            email.focus()

    def on_input_submitted(self) -> None:
        self._attempt()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "login-go":
            self._attempt()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _attempt(self) -> None:
        server = _normalize_server_url(self.query_one("#login-server", Input).value)
        email = self.query_one("#login-email", Input).value.strip()
        password = self.query_one("#login-password", Input).value
        if not server:
            self.query_one("#login-err", Static).update("[red]server URL required[/red]")
            return
        if not email or not password:
            self.query_one("#login-err", Static).update("[red]email and password required[/red]")
            return
        self.base_url = server
        self.query_one("#login-err", Static).update("[dim]signing in…[/dim]")
        self._login_worker(server, email, password)

    @work(thread=True)
    def _login_worker(self, server: str, email: str, password: str) -> None:
        try:
            data = login(email, password, server)
            creds = Credentials(
                api_base_url=server,
                email=str(data.get("email") or email),
                api_key=str(data.get("api_key") or ""),
                user_id=int(data.get("user_id") or 0),
                project_id=int(data.get("project_id") or 0),
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
        ("ctrl+r", "refresh_all", ""),
        ("ctrl+w", "help", ""),
        ("ctrl+n", "mentions", ""),
        ("ctrl+f", "attach_file", ""),
        ("f1", "start_tour", ""),
        # Letters: tabs are on-screen; keep keys but hide from Footer.
        ("c", "tab_chat", ""),
        ("b", "tab_board", ""),
        ("g", "tab_agents", ""),
        ("p", "tab_people", ""),
        ("d", "tab_dashboard", ""),
        ("v", "tab_live", ""),
        ("question_mark", "help", "help"),
        ("r", "refresh_all", ""),
        ("q", "quit", ""),
        # Mentions: ctrl+n — bare @ must stay free for the chat picker.
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
        # board — only `a` is non-obvious enough for the Footer
        ("j", "board_down", ""),
        ("k", "board_up", ""),
        ("h", "board_left", ""),
        ("l", "board_right", ""),
        ("n", "board_add", ""),
        ("s", "board_status", ""),
        ("a", "board_agent", "a agent"),
        ("m", "board_merge", ""),
        ("o", "board_open_pr", ""),
        ("g", "board_open_repo", ""),
        ("y", "board_copy_pr", ""),
        ("i", "board_toggle_detail", ""),
        ("e", "board_edit", ""),
        ("left_square_bracket", "board_shift_left", ""),
        ("right_square_bracket", "board_shift_right", ""),
        ("ctrl+m", "voice_toggle", ""),
        ("ctrl+shift+n", "new_channel", ""),
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
        self.tour_btn = Button("Tour", id="tour-btn", compact=True)
        self.mentions_btn = Button("@", id="mentions-btn", compact=True)
        self.logout_btn = Button("Log out", id="logout-btn", compact=True)
        self.status_line = Static("", id="status-line", markup=True)
        self.switcher = ContentSwitcher(initial="chat", id="body")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="tabs-row"):
            yield Tabs(*[Tab(label, id=key) for key, label in TABS], id="tabs")
            yield self.mentions_btn
            yield self.tour_btn
            yield self.logout_btn
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
        self._set_header(f"project {self.client.project_id}")
        self.mentions_btn.display = False
        self.refresh_workspace()
        self.refresh_board()
        self.set_interval(1.5, self.refresh_workspace)
        self.set_interval(self.poll_seconds, self.refresh_board)
        self.set_interval(10.0, self._tick_dashboard)
        self.chat_view.composer.focus()

    def _set_header(self, detail: str) -> None:
        """Title + detail without Textual Header's default em dash separator."""
        detail = (detail or "").strip()
        self.title = f"AIO · {detail}" if detail else "AIO"
        self.sub_title = ""

    # tabs ----------------------------------------------------------------

    @property
    def active_tab(self) -> str:
        return self.switcher.current or "chat"

    def show_tab(self, key: str) -> None:
        if key in OWNER_ONLY_TABS and not self.ws.is_owner:
            labels = {"people": "People", "dashboard": "Dashboard", "live": "Live"}
            self.set_status(f"[yellow]{labels.get(key, key)} is owner-only[/yellow]")
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

    def _sync_owner_tabs(self) -> None:
        """Show People / Dash / Live only for workspace owners."""
        try:
            tabs = self.query_one("#tabs", Tabs)
        except Exception:
            return
        owner = bool(self.ws.is_owner)
        for key in OWNER_ONLY_TABS:
            try:
                tab = tabs.query_one(f"#{key}", Tab)
            except Exception:
                continue
            tab.display = owner
        # If a member landed on an owner tab (e.g. after role change), bounce home.
        if not owner and self.switcher.current in OWNER_ONLY_TABS:
            self.show_tab("chat")

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
        self._set_header(f"{ws.me.get('email', '')} · project {self.client.project_id}")
        self.chat_view.set_workspace(ws.chats, ws.members, str(ws.me.get("email") or ""))
        if ws.presence:
            self.chat_view.set_presence(ws.presence)
        self.people_view.set_members(ws.members, ws.me, presence=ws.presence or self.chat_view.presence)
        self._sync_owner_tabs()
        self._paint_status()
        self._maybe_offer_tour()

    def apply_presence(self, users: list[dict[str, Any]]) -> None:
        """Fast presence poll callback — keep People + status in sync."""
        self.ws.presence = users
        self.people_view.set_members(self.ws.members, self.ws.me, presence=users)
        self._paint_status()

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
                "Spotlight walkthrough of Chat, Board, Mentions, plus (+), mic, and Log out.",
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

    def action_logout(self) -> None:
        def confirmed(yes: bool | None) -> None:
            if not yes:
                self.set_status("logout cancelled")
                return
            self._begin_logout()

        self.push_screen(
            ConfirmModal(
                "Log out",
                "Sign out on this machine?",
                "Clears saved credentials. You can sign back in without restarting.",
                "Log out",
            ),
            confirmed,
        )

    def _begin_logout(self) -> None:
        if self.tour_coach.active:
            try:
                self.tour_coach.stop(completed=False)
            except Exception:
                pass
        try:
            self.client.post_presence(offline=True)
        except Exception:
            pass
        self.chat_view.reset_session_state()
        last_email = str(self.ws.me.get("email") or "")
        base_url = self.client.base_url
        clear_credentials()
        self.set_status("[dim]signed out - sign in to continue[/dim]")

        def on_done(creds: Credentials | None) -> None:
            if creds is None or creds.is_empty():
                self.exit()
                return
            save_credentials(creds)
            self.client.headers = auth_headers(creds.api_key, creds.email)
            if creds.project_id:
                self.client.project_id = int(creds.project_id)
            self._set_header(f"project {self.client.project_id}")
            self.ws = Workspace()
            self.chat_view.chat_id = None
            self.chat_view.chats = []
            self.chat_view.members = []
            self.chat_view.my_email = ""
            self.refresh_workspace()
            self.refresh_board()
            self.show_tab("chat")
            self.call_after_refresh(self.chat_view.composer.focus)
            self.set_status(f"[green]signed in as {escape(creds.email)}[/green]")

        self.push_screen(LoginScreen(base_url, last_email), on_done)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "tour-btn":
            event.stop()
            self.action_start_tour()
        elif event.button.id == "mentions-btn":
            event.stop()
            self.action_mentions()
        elif event.button.id == "logout-btn":
            event.stop()
            self.action_logout()

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
        presence = self.ws.presence or self.chat_view.presence
        online_n = sum(1 for u in presence if u.get("online"))
        total_n = len(presence) if presence else len(self.ws.members)
        parts = [
            f"[dim]repo[/dim] {escape(str(repo))}",
            f"[dim]jobs[/dim] {self.jobs_today}",
            f"[dim]agent working[/dim] {working}",
            f"[dim]runner[/dim] {runner}",
            f"[dim]online[/dim] {online_n}/{total_n}",
        ]
        if self._board_error:
            parts.append(f"[red]{escape(self._board_error)}[/red]")
        line = "  ·  ".join(parts)
        if self._message:
            line = f"{line}   {self._message}"
        self.status_line.update(line)
        self._paint_mentions_btn()

    def _paint_mentions_btn(self) -> None:
        n = int(self.ws.unread or 0)
        btn = self.mentions_btn
        if n > 0:
            btn.label = f"@{n}"
            btn.set_class(True, "has-unread")
            btn.display = True
            btn.disabled = False
        else:
            btn.label = "@"
            btn.set_class(False, "has-unread")
            btn.display = False

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

    def action_board_shift_left(self) -> None:
        if self.active_tab == "board":
            self.board_view.shift_column(-1)

    def action_board_shift_right(self) -> None:
        if self.active_tab == "board":
            self.board_view.shift_column(1)

    def action_voice_toggle(self) -> None:
        self.show_tab("chat")
        self.chat_view.action_voice_toggle()

    def action_new_channel(self) -> None:
        self.show_tab("chat")
        self.chat_view.action_new_channel()

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
    """Always open the sign-in gate, then hand the terminal to the app.

    Pass both ``api_key`` and ``email`` only for scripts/tests that must skip the UI.
    """
    from app.cli_pkg.session import resolve_base_url, resolve_project_id

    creds = load_credentials()
    base_url = resolve_base_url()
    key = (api_key or "").strip()
    who = (email or "").strip()

    # Explicit credentials (CLI flags / automation) skip the login screen.
    if not (key and who):
        picked = _prompt_login(base_url, who or (creds.email or "").strip())
        if picked is None or not (picked.api_key or "").strip():
            return 2
        save_credentials(picked)
        key, who = picked.api_key, picked.email
        if (picked.api_base_url or "").strip():
            base_url = picked.api_base_url.strip().rstrip("/")

    pid = int(project_id or resolve_project_id() or 1)
    client = ApiClient(project_id=pid, api_key=key, email=who, base_url=base_url)
    try:
        client.me()
    except ApiError as exc:
        print(f"cannot start: {exc}")
        print("try again with `aio` (is the API running? `uvicorn app.main:app --port 8000`)")
        return 2

    AioApp(client, poll_seconds=poll_seconds).run()
    return 0


def _prompt_login(base_url: str, email: str) -> Credentials | None:
    """Full-screen sign-in gate (shown on every normal `aio` launch)."""

    class LoginApp(App[Credentials]):
        CSS = STYLES
        TITLE = "AIO"

        def on_mount(self) -> None:
            def done(creds: Credentials | None) -> None:
                self.exit(creds)

            self.push_screen(LoginScreen(base_url, email), done)

    return LoginApp().run()


def run_tui(project_id: int = 0, *, poll_seconds: float = 3.0, api_key: str = "", email: str = "") -> int:
    """Backwards-compatible entry point for `aio tui`."""
    return run_app(project_id, poll_seconds=poll_seconds, api_key=api_key, email=email)

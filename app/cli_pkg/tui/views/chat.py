"""Chat tab: team channels, private room, members, live message stream."""

from __future__ import annotations

import re
import tempfile
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.markup import escape
from textual import events, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, ListItem, ListView, Markdown, ProgressBar, Static

from app.cli_pkg.tui.client import ApiClient, ApiError
from app.cli_pkg.tui.file_picker import pick_attachment_files

# Mirrors looksLikeAgentWork() in the web client: which sends spin up a model.
_SKILL_RE = re.compile(
    r"^/(ask|deepresearch|deep-research|deep_research|code|research|write|web|review|checklist|status)\b",
    re.I,
)
_CLEAR_RE = re.compile(r"^[/!]clear\b", re.I)

@dataclass(frozen=True)
class Candidate:
    """One row in the autocomplete dropdown."""

    insert: str
    label: str
    blurb: str


# Same catalogue the web composer offers, in the same order.
COMMANDS: tuple[Candidate, ...] = (
    Candidate("!add ", "!add", "new board card"),
    Candidate("!list", "!list", "show my cards"),
    Candidate("!set ", "!set", "move card status"),
    Candidate("!done ", "!done", "mark card done"),
    Candidate("!remove ", "!remove", "delete a card"),
    Candidate("!assign ", "!assign", "give card away"),
    Candidate("!link ", "!link", "attach branch/PR"),
    Candidate("!claim ", "!claim", "lock a file"),
    Candidate("!release ", "!release", "free a file"),
    Candidate("!go", "!go", "run despite claim"),
    Candidate("!issue ", "!issue", "log a blocker"),
    Candidate("!issues", "!issues", "show blockers"),
    Candidate("!resolve ", "!resolve", "close blocker"),
    Candidate("!invite ", "!invite", "invite link or email@domain"),
    Candidate("!attach", "!attach", "open file picker"),
    Candidate("!attach-clear", "!attach-clear", "clear staged files"),
    Candidate("!clear", "!clear", "clear chat (you only in #general)"),
    Candidate("!help", "!help", "list commands"),
)

SKILLS: tuple[Candidate, ...] = (
    Candidate("/ask ", "/ask", "just ask anything"),
    Candidate("/deepresearch ", "/deepresearch", "deep dive with sources"),
    Candidate("/code ", "/code", "build or patch"),
    Candidate("/write ", "/write", "draft clear prose"),
    Candidate("/review ", "/review", "check the diff"),
    Candidate("/checklist ", "/checklist", "break into ticks"),
    Candidate("/status ", "/status", "AI member catch-up"),
    Candidate("/clear", "/clear", "clear chat (you only in #general)"),
)

# Channels only run these two skills; the rest are private-room only.
CHANNEL_SKILLS: tuple[Candidate, ...] = tuple(
    c for c in SKILLS if c.label in ("/status", "/clear")
)


def active_prefix(text: str, cursor: int) -> tuple[str, int, str] | None:
    """The `/`, `!` or `@` token the cursor sits in, if any.

    Returns (trigger char, start index, what has been typed after it).
    """
    head = text[: max(0, cursor)]
    for i in range(len(head) - 1, -1, -1):
        ch = head[i]
        if ch.isspace():
            return None
        if ch in "/!@":
            if i and not head[i - 1].isspace():
                return None  # mid-word, e.g. an email address
            typed = head[i + 1 :]
            return (ch, i, typed) if " " not in typed else None
    return None


def _filter(catalog: tuple[Candidate, ...], typed: str) -> list[Candidate]:
    t = typed.lower()
    return [c for c in catalog if not t or c.label[1:].lower().startswith(t)][:16]


def candidates_for(
    trigger: str, typed: str, *, members: list[str], chat_kind: str
) -> list[Candidate]:
    """What the dropdown should show for this trigger."""
    if trigger == "@":
        t = typed.lower()
        out = [Candidate("@team ", "@team", "ping whole team")] if "team".startswith(t) else []
        for name in members:
            handle = name.strip().split("@")[0]
            if handle and (not t or handle.lower().startswith(t)):
                out.append(Candidate(f"@{handle} ", f"@{handle}", name))
        return out[:12]

    catalog = COMMANDS if trigger == "!" else (
        SKILLS if chat_kind == "private" else CHANNEL_SKILLS
    )
    # An exact match means the command is complete; args come after a space.
    if any(c.label[1:].lower() == typed.lower() for c in catalog):
        return []
    return _filter(catalog, typed)


class CommandPicker(VerticalScroll):
    """Dropdown above the composer. Hidden unless there is something to pick."""

    def __init__(self) -> None:
        super().__init__(id="picker")
        self.list_view = ListView()
        self.items: list[Candidate] = []
        self.start = 0
        self.display = False

    def compose(self) -> ComposeResult:
        yield self.list_view

    @property
    def open(self) -> bool:
        return bool(self.items) and self.display

    def show(self, items: list[Candidate], start: int) -> None:
        if not items:
            self.close()
            return
        if items != self.items:
            self.items = items
            self.start = start
            self.list_view.clear()
            for c in items:
                self.list_view.append(
                    ListItem(Static(f"[b]{escape(c.label)}[/b]  [dim]{escape(c.blurb)}[/dim]",
                                    markup=True))
                )
            self.list_view.index = 0
        self.start = start
        self.display = True
        self.styles.height = min(len(items), 8) + 2

    def close(self) -> None:
        self.items = []
        self.display = False
        self.list_view.clear()

    def move(self, delta: int) -> None:
        if not self.items:
            return
        index = ((self.list_view.index or 0) + delta) % len(self.items)
        self.list_view.index = index

    @property
    def selected(self) -> Candidate | None:
        if not self.items:
            return None
        return self.items[(self.list_view.index or 0) % len(self.items)]


class Composer(Input):
    """The message box, with dropdown navigation wired into the key handling."""

    def __init__(self, picker: CommandPicker, **kwargs) -> None:
        super().__init__(**kwargs)
        self.picker = picker

    async def _on_key(self, event: events.Key) -> None:
        if self.picker.open:
            if event.key in ("up", "down"):
                self.picker.move(-1 if event.key == "up" else 1)
                event.prevent_default()
                event.stop()
                return
            if event.key in ("enter", "tab", "right"):
                chosen = self.picker.selected
                if chosen is not None:
                    self._accept(chosen)
                    event.prevent_default()
                    event.stop()
                    return
            if event.key == "escape":
                self.picker.close()
                event.prevent_default()
                event.stop()
                return
        elif event.key == "escape":
            chat_view = getattr(self.app, "chat_view", None)
            if chat_view is not None and getattr(chat_view, "_pending_attachments", None):
                chat_view._pending_attachments.pop()
                chat_view._render_pending_attachments()
                self.app.set_status("removed last attachment")
                event.prevent_default()
                event.stop()
                return
            # Step out of the message box so the plain letter shortcuts work.
            self.screen.focus_next()
            event.prevent_default()
            event.stop()
            return
        await super()._on_key(event)

    def _accept(self, candidate: Candidate) -> None:
        start = self.picker.start
        rest = self.value[self.cursor_position :]
        self.value = self.value[:start] + candidate.insert + rest
        self.cursor_position = start + len(candidate.insert)
        self.picker.close()


def skill_name_from_body(body: str) -> str:
    text = (body or "").strip()
    m = _SKILL_RE.match(text)
    if m:
        return m.group(1).lower().replace("-", "").replace("_", "")
    m2 = re.match(r"^(?:force\s+)?(code|ask|deepresearch|research|write|review)\b", text, re.I)
    return m2.group(1).lower() if m2 else ""


def looks_like_agent_work(body: str, chat_kind: str) -> bool:
    text = (body or "").strip()
    if not text or _CLEAR_RE.match(text):
        return False
    if _SKILL_RE.match(text):
        return True
    return chat_kind == "private" and text.startswith("/")


def _inline(line: str) -> str:
    line = re.sub(r"\*\*(.+?)\*\*", r"[b]\1[/b]", line)
    line = re.sub(r"`([^`]+?)`", r"[#a5b4fc]\1[/#a5b4fc]", line)
    return line


def render_markdown(text: str) -> str:
    """Agents answer in markdown; give the terminal the shape without the syntax.

    Deliberately small: headings, bold, bullets, fenced code. Anything fancier
    is left as written rather than risking mangled output.
    """
    out: list[str] = []
    in_code = False
    for line in escape(text).split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            lang = stripped[3:].strip()
            out.append(f"[dim]{'┄' * 3} {lang or 'code'} {'┄' * 3}[/dim]" if in_code else "[dim]┄┄┄[/dim]")
            continue
        if in_code:
            out.append(f"[#a5b4fc]{line}[/#a5b4fc]")
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            out.append(f"[b #7dd3fc]{_inline(heading.group(2))}[/]")
            continue
        if stripped.startswith("> "):
            out.append(f"[dim i]{_inline(stripped[2:])}[/dim i]")
            continue
        out.append(_inline(re.sub(r"^(\s*)[-*]\s+", r"\1• ", line)))
    return "\n".join(out)


def short_time(iso: str) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    except ValueError:
        return ""


def _strip_control_markers(raw: str) -> str:
    raw = re.sub(r"\n?\[\[setup:\d+\]\]\s*", "\n", raw)
    raw = re.sub(r"\n?\[\[confirm:[0-9,\s]+\]\]\s*", "\n", raw)
    raw = re.sub(r"\n?\[\[charts:[\d,\s]+\]\]\s*", "\n", raw)
    return raw.rstrip()


def _cache_attachment_png(client: ApiClient, att: dict[str, Any]) -> Path | None:
    """Download an image attachment into a temp file for textual-image."""
    url = str(att.get("url") or "")
    att_id = att.get("id")
    if not url and att_id:
        url = f"/attachments/{att_id}"
    if not url:
        return None
    ctype = str(att.get("content_type") or "").lower()
    name = str(att.get("filename") or "")
    if not (
        ctype.startswith("image/")
        or name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
    ):
        return None
    try:
        data = client.download_bytes(url, timeout=60.0)
    except ApiError:
        return None
    if not data:
        return None
    suffix = Path(name).suffix.lower() if name else ".png"
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        suffix = ".png"
    path = Path(tempfile.gettempdir()) / f"aio-att-{att_id or 'x'}{suffix}"
    try:
        path.write_bytes(data)
    except OSError:
        return None
    return path


class MessageView(Vertical):
    """One chat message: text (+ markdown links) and optional inline chart images."""

    DEFAULT_CSS = """
    MessageView { height: auto; padding: 0 0 1 0; }
    MessageView.agent-msg { border-left: outer $accent 30%; padding-left: 1; }
    MessageView.whisper-msg { color: $text-muted; }
    MessageView .msg-head { height: 1; }
    MessageView .msg-body { height: auto; }
    MessageView .msg-chart {
        height: 18;
        width: 100%;
        margin: 1 0 0 0;
    }
    MessageView .msg-file { color: $text-muted; height: 1; }
    """

    def __init__(
        self, message: dict[str, Any], my_email: str, client: ApiClient | None = None
    ) -> None:
        super().__init__()
        self.message_id = int(message["id"])
        self.my_email = my_email
        self.client = client
        self.message = message
        self._head = Static("", classes="msg-head", markup=True)
        self._body_static = Static("", classes="msg-body", markup=True)
        self._body_md = Markdown("", classes="msg-body")
        self._chart_ids: tuple[int, ...] = ()

    def compose(self) -> ComposeResult:
        yield self._head
        yield self._body_static
        yield self._body_md

    def on_mount(self) -> None:
        self.update_message(self.message)

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        href = (event.href or "").strip()
        if href.startswith(("http://", "https://", "mailto:")):
            webbrowser.open(href)
            event.stop()

    def update_message(self, message: dict[str, Any]) -> None:
        self.message = message
        self.set_class(bool(message.get("agent")), "agent-msg")
        self.set_class(bool(message.get("visibility") == "whisper"), "whisper-msg")

        agent = message.get("agent")
        if agent:
            who = f"[b #7dd3fc]@{escape(str(agent))}[/]"
        else:
            name = str(message.get("sender") or message.get("sender_email") or "user")
            mine = str(message.get("sender_email") or "") == self.my_email
            colour = "#a7f3d0" if mine else "#fbbf24"
            who = f"[b {colour}]{escape(name)}[/]"

        meta = [short_time(str(message.get("created_at") or ""))]
        if message.get("edited_at"):
            meta.append("edited")
        if message.get("visibility") == "whisper":
            meta.append("only you")
        self._head.update(f"{who} [dim]{' · '.join(x for x in meta if x)}[/dim]")

        if message.get("deleted_at"):
            self._body_md.display = False
            self._body_static.display = True
            self._body_static.update("[dim i]message deleted[/dim i]")
            self._clear_extras()
            return

        raw = _strip_control_markers(str(message.get("body") or ""))
        use_md = bool(agent) and bool(raw.strip())
        if use_md:
            self._body_static.display = False
            self._body_md.display = True
            self._body_md.update(raw)
        else:
            self._body_md.display = False
            self._body_static.display = True
            # User messages: linkify bare URLs as markdown so they stay clickable
            if re.search(r"https?://|mailto:", raw):
                linked = re.sub(
                    r"(https?://[^\s<]+)|(mailto:[^\s<]+)",
                    lambda m: f"[{m.group(0)}]({m.group(0)})",
                    raw,
                )
                self._body_static.display = False
                self._body_md.display = True
                self._body_md.update(linked)
            else:
                self._body_static.update(escape(raw) if raw else "")

        self._sync_attachments(message.get("attachments") or [])

    def _clear_extras(self) -> None:
        for child in list(self.children):
            if child in (self._head, self._body_static, self._body_md):
                continue
            child.remove()
        self._chart_ids = ()

    def _sync_attachments(self, attachments: list[dict[str, Any]]) -> None:
        image_atts = []
        file_atts = []
        for a in attachments:
            ctype = str(a.get("content_type") or "").lower()
            name = str(a.get("filename") or "")
            if ctype.startswith("image/") or name.lower().endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp")
            ):
                image_atts.append(a)
            else:
                file_atts.append(a)

        new_ids = tuple(int(a["id"]) for a in image_atts if a.get("id") is not None)
        if new_ids != self._chart_ids:
            self._clear_extras()
            self._chart_ids = new_ids
            if image_atts and self.client is not None:
                self._mount_charts(image_atts)
            for a in file_atts:
                self.mount(
                    Static(
                        f"[dim]📎 {escape(str(a.get('filename') or ''))}[/dim]",
                        markup=True,
                        classes="msg-file",
                    )
                )
        elif file_atts and not any(
            getattr(c, "classes", None) and "msg-file" in c.classes for c in self.children
        ):
            for a in file_atts:
                self.mount(
                    Static(
                        f"[dim]📎 {escape(str(a.get('filename') or ''))}[/dim]",
                        markup=True,
                        classes="msg-file",
                    )
                )

    @work(thread=True, exclusive=False, group="msg-charts")
    def _mount_charts(self, image_atts: list[dict[str, Any]]) -> None:
        if self.client is None:
            return
        paths: list[tuple[dict[str, Any], Path]] = []
        for att in image_atts:
            path = _cache_attachment_png(self.client, att)
            if path is not None:
                paths.append((att, path))
        if paths:
            self.app.call_from_thread(self._add_chart_widgets, paths)

    def _add_chart_widgets(self, paths: list[tuple[dict[str, Any], Path]]) -> None:
        try:
            from textual_image.widget import Image as TerminalImage
        except ImportError:
            for att, path in paths:
                self.mount(
                    Static(
                        f"[dim]🖼 {escape(str(att.get('filename') or path.name))}[/dim]",
                        markup=True,
                        classes="msg-file",
                    )
                )
            return
        for att, path in paths:
            try:
                self.mount(TerminalImage(path, classes="msg-chart"))
            except Exception:
                self.mount(
                    Static(
                        f"[dim]🖼 {escape(str(att.get('filename') or path.name))}[/dim]",
                        markup=True,
                        classes="msg-file",
                    )
                )


class ChatView(Vertical):
    """Sidebar + transcript + composer."""

    POLL_SECONDS = 1.5
    BINDINGS = [
        ("ctrl+f", "attach_file", "attach file"),
    ]

    def __init__(self, client: ApiClient) -> None:
        super().__init__(id="chat")
        self.client = client
        self.chat_id: int | None = None
        self.chats: list[dict[str, Any]] = []
        self.members: list[dict[str, Any]] = []
        self.my_email = ""
        self._views: dict[int, MessageView] = {}
        self._last_id = 0
        self._last_sync = ""
        # Per-chat in-flight sends / LLM jobs (other rooms stay typable)
        self._sending_chats: set[int] = set()
        self._llm_jobs: dict[int, str] = {}
        self._pending: Static | None = None
        self._pending_attachments: list[dict[str, Any]] = []
        self._setup_opened: set[int] = set()
        self._setup_busy = False
        self._attach_busy = False

        self.sidebar = VerticalScroll(id="chat-sidebar")
        self.chat_list = ListView(id="chat-list")
        self.member_list = ListView(id="member-list")
        self.transcript = VerticalScroll(id="transcript")
        self.title_bar = Static("", id="chat-title", markup=True)
        self.picker = CommandPicker()
        self.llm_label = Static("", id="llm-wait-label", markup=True)
        self.llm_bar = ProgressBar(
            total=None, show_eta=False, show_percentage=False, id="llm-wait-bar"
        )
        self.attach_pending = Static("", id="attach-pending", markup=True)
        self.composer = Composer(
            self.picker,
            placeholder="type /  !  or  @  · Attach button · enter to send",
            id="composer",
        )
        self.attach_btn = Button("Attach", id="chat-attach", variant="primary")

    def compose(self) -> ComposeResult:
        with Horizontal(id="chat-body"):
            with self.sidebar:
                yield Label("CHATS", classes="side-head")
                yield self.chat_list
                yield Label("MEMBERS", classes="side-head")
                yield self.member_list
            with Vertical(id="chat-main"):
                yield self.title_bar
                yield self.transcript
                yield self.picker
                with Vertical(id="llm-wait"):
                    yield self.llm_label
                    yield self.llm_bar
                yield self.attach_pending
                with Horizontal(id="composer-row"):
                    yield self.composer
                    yield self.attach_btn

    def on_mount(self) -> None:
        self.set_interval(self.POLL_SECONDS, self.poll_messages)
        self._sync_llm_ui()
        self._render_pending_attachments()

    # sidebar -------------------------------------------------------------

    def set_workspace(self, chats: list[dict], members: list[dict], my_email: str) -> None:
        self.my_email = my_email
        changed = [(c["id"], c.get("name"), c.get("kind")) for c in chats] != [
            (c["id"], c.get("name"), c.get("kind")) for c in self.chats
        ]
        self.chats = chats
        if changed:
            self._render_chat_list()
        if members != self.members:
            self.members = members
            self._render_members()
        if self.chat_id is None and chats:
            default = next((c for c in chats if c.get("kind") == "private"), None)
            default = default or next((c for c in chats if c.get("name") == "general"), chats[0])
            self.select_chat(int(default["id"]))

    def _render_chat_list(self) -> None:
        index = self.chat_list.index or 0
        self.chat_list.clear()
        for chat in self.chats:
            cid = int(chat["id"])
            working = " [dim]…[/dim]" if cid in self._llm_jobs else ""
            label = (
                f"[#7dd3fc]#[/#7dd3fc] {escape(str(chat.get('name') or ''))}{working}"
                if chat.get("kind") == "channel"
                else f"[#c4b5fd]◆[/#c4b5fd] my room{working}"
            )
            item = ListItem(Static(label, markup=True))
            item.chat = chat
            self.chat_list.append(item)
        if self.chats:
            self.chat_list.index = min(index, len(self.chats) - 1)

    def _render_members(self) -> None:
        self.member_list.clear()
        for m in self.members:
            crown = " [yellow]★[/yellow]" if m.get("role") == "owner" else ""
            name = escape(str(m.get("name") or m.get("email") or ""))
            self.member_list.append(ListItem(Static(f"[dim]●[/dim] {name}{crown}", markup=True)))

    @property
    def current_chat(self) -> dict[str, Any]:
        return next((c for c in self.chats if int(c["id"]) == self.chat_id), {})

    def select_chat(self, chat_id: int) -> None:
        if chat_id == self.chat_id:
            return
        self.chat_id = chat_id
        self._views.clear()
        self._last_id = 0
        self._last_sync = ""
        self.transcript.remove_children()
        self._pending = None
        self._pending_attachments = []
        self._render_pending_attachments()
        chat = self.current_chat
        if chat.get("kind") == "private":
            self.title_bar.update("[b]my room[/b]  [dim]/skills · Attach · notes stay quiet[/dim]")
        else:
            name = escape(str(chat.get("name") or ""))
            self.title_bar.update(f"[b]#{name}[/b]  [dim]@people · !commands · Attach[/dim]")
        self._sync_llm_ui()
        self.poll_messages()

    # attachments ---------------------------------------------------------

    def _render_pending_attachments(self) -> None:
        rows = self._pending_attachments
        if not rows:
            self.attach_pending.update("")
            self.attach_pending.display = False
            return
        names = ", ".join(escape(str(a.get("filename") or f"#{a.get('id')}")) for a in rows)
        self.attach_pending.display = True
        self.attach_pending.update(
            f"[b]attached[/b] {names}  "
            f"[dim]({len(rows)}/5) · Attach add · esc clear last · !attach-clear[/dim]"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "chat-attach":
            event.stop()
            self.action_attach_file()

    def action_attach_file(self) -> None:
        """Open the OS file chooser (Attach button / ctrl+f / !attach)."""
        self._start_attach_picker()

    def _start_attach_picker(self) -> None:
        if self.chat_id is None:
            self.app.set_status("[yellow]pick a chat first[/yellow]")
            return
        remaining = 5 - len(self._pending_attachments)
        if remaining <= 0:
            self.app.set_status("[yellow]at most 5 attachments per message[/yellow]")
            return
        if self._attach_busy:
            return
        self._attach_busy = True
        self.attach_btn.disabled = True
        self.app.set_status("[dim]choose a file…[/dim]")
        self._pick_and_upload_worker(int(self.chat_id), remaining)

    def _upload_pending(self, path: str) -> None:
        """Upload a known path (optional !attach <path> for scripts/tests)."""
        if self.chat_id is None:
            return
        if len(self._pending_attachments) >= 5:
            self.app.set_status("[yellow]at most 5 attachments per message[/yellow]")
            return
        if self._attach_busy:
            return
        self._attach_busy = True
        self.attach_btn.disabled = True
        self.app.set_status(f"[dim]uploading {escape(path)}…[/dim]")
        self._upload_paths_worker(int(self.chat_id), [path])

    @work(thread=True, group="chat-attach")
    def _pick_and_upload_worker(self, chat_id: int, max_files: int) -> None:
        try:
            paths = pick_attachment_files(title="Attach file to chat", max_files=max_files)
        except Exception as exc:
            self.app.call_from_thread(self._finish_attach_batch, chat_id, [], str(exc))
            return
        if not paths:
            self.app.call_from_thread(self._finish_attach_batch, chat_id, [], "")
            return
        self._upload_paths_sync(chat_id, [str(p) for p in paths])

    @work(thread=True, group="chat-attach")
    def _upload_paths_worker(self, chat_id: int, paths: list[str]) -> None:
        self._upload_paths_sync(chat_id, paths)

    def _upload_paths_sync(self, chat_id: int, paths: list[str]) -> None:
        uploaded: list[dict] = []
        error = ""
        for path in paths:
            try:
                att = self.client.upload_attachment(chat_id, path)
                uploaded.append(att)
            except ApiError as exc:
                error = str(exc)
                break
        self.app.call_from_thread(self._finish_attach_batch, chat_id, uploaded, error)

    def _finish_attach_batch(self, chat_id: int, uploaded: list[dict], error: str) -> None:
        self._attach_busy = False
        self.attach_btn.disabled = False
        if self.chat_id != chat_id:
            self.app.set_status("[yellow]chat changed — attachment discarded[/yellow]")
            self.composer.focus()
            return
        for att in uploaded:
            if att.get("id") is None:
                continue
            if not any(int(a.get("id") or 0) == int(att["id"]) for a in self._pending_attachments):
                self._pending_attachments.append(att)
        self._render_pending_attachments()
        if error:
            self.app.set_status(f"[red]attach failed: {escape(error)}[/red]")
        elif uploaded:
            names = ", ".join(str(a.get("filename") or "") for a in uploaded)
            self.app.set_status(f"[green]attached {escape(names)}[/green]")
        else:
            self.app.set_status("attach cancelled")
        self.composer.focus()

    def _clear_pending_attachments(self) -> None:
        self._pending_attachments = []
        self._render_pending_attachments()
        self.app.set_status("attachments cleared")

    def _try_local_attach_command(self, body: str) -> bool:
        """Handle !attach / !attach-clear in the TUI (local disk → upload)."""
        lower = body.strip()
        if lower in ("!attach-clear", "!attach clear", "!attach-clear "):
            self._clear_pending_attachments()
            return True
        m = re.match(r"^!attach(?:\s+(.+))?$", body.strip(), re.I)
        if not m:
            return False
        path = (m.group(1) or "").strip().strip('"').strip("'")
        if not path:
            self._start_attach_picker()
        else:
            self._upload_pending(path)
        return True

    def _active_llm_skill(self) -> str | None:
        if self.chat_id is None:
            return None
        return self._llm_jobs.get(int(self.chat_id))

    def _sync_llm_ui(self) -> None:
        """Show indeterminate progress only for the room that has an LLM job."""
        skill = self._active_llm_skill()
        wait = self.query_one("#llm-wait", Vertical)
        if skill is None:
            wait.display = False
            self.llm_label.update("")
            if self._pending is not None:
                try:
                    self._pending.remove()
                except Exception:
                    pass
                self._pending = None
            self.composer.disabled = False
            return
        wait.display = True
        label = skill.lstrip("/")
        self.llm_label.update(
            f"[b #7dd3fc]/{escape(label)}[/] [dim]model working…[/dim]  "
            "[dim i]other chats stay open[/dim i]"
        )
        # Remount a thinking bubble if this room's transcript was rebuilt
        if self._pending is None or self._pending not in self.transcript.children:
            self._pending = Static(
                f"[b #7dd3fc]@{escape(label)}[/] [dim]thinking…[/dim]\n"
                "[dim i]generating a reply — switch rooms to keep chatting[/dim i]",
                markup=True,
                classes="pending-msg",
            )
            self.transcript.mount(self._pending)
            self.call_after_refresh(self.transcript.scroll_end, animate=False)
        self.composer.disabled = True
        self._render_chat_list()

    # messages ------------------------------------------------------------

    @work(thread=True, exclusive=True, group="chat-poll")
    def poll_messages(self) -> None:
        if self.chat_id is None:
            return
        chat_id = self.chat_id
        try:
            rows = self.client.messages(chat_id, after_id=self._last_id, since=self._last_sync)
        except ApiError as exc:
            self.app.call_from_thread(self.app.set_status, f"[red]{escape(str(exc))}[/red]")
            return
        stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self.app.call_from_thread(self._apply_messages, chat_id, rows, stamp)

    def _apply_messages(self, chat_id: int, rows: list[dict], stamp: str) -> None:
        if chat_id != self.chat_id:
            return  # the user switched chats while the request was in flight
        self._last_sync = stamp
        at_bottom = self.transcript.scroll_offset.y >= self.transcript.max_scroll_y - 2
        for row in rows:
            mid = int(row["id"])
            self._last_id = max(self._last_id, mid)
            existing = self._views.get(mid)
            if existing is not None:
                if row.get("deleted_at"):
                    existing.remove()
                    self._views.pop(mid, None)
                else:
                    existing.update_message(row)
                continue
            if row.get("deleted_at"):
                continue
            view = MessageView(row, self.my_email, client=self.client)
            self._views[mid] = view
            self.transcript.mount(view)
            self._maybe_open_setup(row)
        # Keep / remount thinking bubble if this room still has an LLM job
        if self._active_llm_skill() and (
            self._pending is None or self._pending not in self.transcript.children
        ):
            self._sync_llm_ui()
        if rows and at_bottom:
            self.call_after_refresh(self.transcript.scroll_end, animate=False)

    def _maybe_open_setup(self, row: dict[str, Any]) -> None:
        if self._setup_busy:
            return
        body = str(row.get("body") or "")
        match = re.search(r"\[\[setup:(\d+)\]\]", body)
        if not match:
            return
        oid = int(match.group(1))
        if oid in self._setup_opened:
            return
        self._setup_opened.add(oid)
        title_m = re.search(r"Added objective #\d+:\s*(.+?)(?:\s*\(yours\))?$", body, re.M)
        title = (title_m.group(1).strip() if title_m else f"Objective #{oid}")
        self._setup_busy = True

        def done(result: dict[str, Any] | None) -> None:
            self._setup_busy = False
            if result is None:
                result = {"dismiss": True}
            self._setup_worker(oid, result)

        from app.cli_pkg.tui.widgets import ObjectiveSetupModal

        self.app.push_screen(ObjectiveSetupModal(oid, title), done)

    @work(thread=True, group="chat-setup")
    def _setup_worker(self, objective_id: int, result: dict[str, Any]) -> None:
        try:
            if result.get("dismiss"):
                self.client.setup_objective(objective_id, dismiss=True)
                msg = f"#{objective_id} setup skipped"
            else:
                self.client.setup_objective(
                    objective_id,
                    description=str(result.get("description") or ""),
                    subtasks=list(result.get("subtasks") or []),
                )
                msg = f"#{objective_id} setup saved"
        except ApiError as exc:
            msg = f"[red]setup failed: {escape(str(exc))}[/red]"
        self.app.call_from_thread(self.app.set_status, msg)
        self.app.call_from_thread(self.app.refresh_workspace)

    # sending -------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input is not self.composer:
            return
        self.refresh_picker()

    def refresh_picker(self) -> None:
        hit = active_prefix(self.composer.value, self.composer.cursor_position)
        if hit is None:
            self.picker.close()
            return
        trigger, start, typed = hit
        names = [str(m.get("name") or m.get("email") or "") for m in self.members]
        items = candidates_for(
            trigger, typed, members=names, chat_kind=str(self.current_chat.get("kind") or "")
        )
        self.picker.show(items, start)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view is self.picker.list_view:
            chosen = self.picker.selected
            if chosen is not None:
                self.composer._accept(chosen)
                self.composer.focus()
            return
        chat = getattr(event.item, "chat", None)
        if chat:
            self.select_chat(int(chat["id"]))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input is not self.composer:
            return
        body = event.value.strip()
        if self.chat_id is None:
            return
        chat_id = int(self.chat_id)
        # Only block the room that already has a job / send in flight
        if chat_id in self._sending_chats or chat_id in self._llm_jobs:
            return
        if body == "?":
            self.composer.value = ""
            self.app.action_help()
            return
        if body and self._try_local_attach_command(body):
            self.composer.value = ""
            self.picker.close()
            return
        if not body and not self._pending_attachments:
            return
        self.composer.value = ""
        self.picker.close()
        self._start_send(chat_id, body)

    def _start_send(self, chat_id: int, body: str) -> None:
        attachment_ids = [
            int(a["id"]) for a in self._pending_attachments if a.get("id") is not None
        ]
        # Consume pending so a retry doesn't double-send the same ids
        self._pending_attachments = []
        self._render_pending_attachments()

        self._sending_chats.add(chat_id)
        chat = next((c for c in self.chats if int(c["id"]) == chat_id), {})
        working = looks_like_agent_work(body, str(chat.get("kind") or ""))
        if working:
            skill = skill_name_from_body(body) or (body.split()[0].lstrip("/") if body else "skill")
            self._llm_jobs[chat_id] = skill
            if self.chat_id == chat_id:
                self._sync_llm_ui()
            else:
                self._render_chat_list()
            if self.chat_id != chat_id:
                self.app.set_status(f"[#7dd3fc]/{escape(skill)} running in another room…[/]")
            else:
                self.app.set_status(f"[#7dd3fc]/{escape(skill)} running…[/]")
        elif self.chat_id == chat_id:
            self.composer.disabled = True
        self._send_worker(chat_id, body, attachment_ids)

    @work(thread=True, group="chat-send")
    def _send_worker(self, chat_id: int, body: str, attachment_ids: list[int]) -> None:
        try:
            data = self.client.send_message(chat_id, body, attachment_ids=attachment_ids)
            error = ""
        except ApiError as exc:
            data, error = {}, str(exc)
        self.app.call_from_thread(self._after_send, chat_id, data, error)

    def _after_send(self, chat_id: int, data: dict, error: str) -> None:
        self._sending_chats.discard(chat_id)
        self._llm_jobs.pop(chat_id, None)
        viewing = self.chat_id == chat_id
        if viewing:
            if self._pending is not None:
                try:
                    self._pending.remove()
                except Exception:
                    pass
                self._pending = None
            self._sync_llm_ui()
            self.composer.disabled = False
            self.composer.focus()
        else:
            self._render_chat_list()
            self._sync_llm_ui()
        if error:
            self.app.set_status(f"[red]{escape(error)}[/red]")
            return
        if data.get("cleared"):
            if viewing:
                self._views.clear()
                self._last_id = 0
                self._last_sync = ""
                self.transcript.remove_children()
            self.app.set_status("chat cleared")
        created = data.get("created_chat_id")
        deleted = data.get("deleted_chat_id")
        if created or deleted:
            self.app.refresh_workspace()
            if deleted and int(deleted) == chat_id and viewing:
                self.chat_id = None
                return
            if created and viewing:
                self.select_chat(int(created))
                return
        self.app.set_status("")
        if viewing:
            self.poll_messages()
        else:
            self.app.set_status(f"[dim]agent finished in chat #{chat_id}[/dim]")
        self.app.refresh_workspace()

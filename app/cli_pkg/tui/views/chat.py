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
from textual.binding import Binding
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
        self._paint_cursor()

    def close(self) -> None:
        self.items = []
        self.display = False
        self.list_view.clear()

    def move(self, delta: int) -> None:
        if not self.items:
            return
        index = ((self.list_view.index or 0) + delta) % len(self.items)
        self.list_view.index = index
        self._paint_cursor()

    def _paint_cursor(self) -> None:
        """Force a visible highlight row even while the composer has focus."""
        idx = self.list_view.index or 0
        for i, child in enumerate(self.list_view.children):
            if isinstance(child, ListItem):
                child.set_class(i == idx, "-highlight")

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
                chat_view = getattr(self.app, "chat_view", None)
                if chat_view is not None:
                    chat_view.refresh_ghost()
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
                chat_view = getattr(self.app, "chat_view", None)
                if chat_view is not None:
                    chat_view.refresh_ghost()
                event.prevent_default()
                event.stop()
                return
        elif event.key == "tab":
            chat_view = getattr(self.app, "chat_view", None)
            if chat_view is not None and chat_view.accept_ghost_hint():
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
        chat_view = getattr(self.app, "chat_view", None)
        if chat_view is not None:
            chat_view.refresh_ghost()


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


_ARG_HINTS: dict[str, str] = {
    "/ask": "your question…",
    "/deepresearch": "topic…",
    "/code": "what to build…",
    "/write": "what to draft…",
    "/review": "what to check…",
    "/checklist": "goal…",
    "/status": "optional focus…",
    "!add": "card title…",
    "!set": "#id status…",
    "!done": "#id…",
    "!remove": "#id…",
    "!assign": "#id @person…",
    "!link": "#id url…",
    "!claim": "path…",
    "!release": "path…",
    "!issue": "blocker…",
    "!resolve": "#id…",
    "!invite": "email@domain or seats…",
    "!attach": "optional path…",
}


def _format_table(rows: list[list[str]], max_width: int = 72) -> list[str]:
    """Monospace-align a simple GFM table; clip overly wide columns."""
    if not rows:
        return []
    cols = max(len(r) for r in rows)
    widths = [0] * cols
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))
    # Shrink proportionally if too wide
    total = sum(widths) + 3 * max(0, cols - 1)
    if total > max_width and total > 0:
        scale = max_width / total
        widths = [max(4, int(w * scale)) for w in widths]

    def cell(text: str, w: int) -> str:
        if len(text) > w:
            return text[: max(1, w - 1)] + "…"
        return text.ljust(w)

    out: list[str] = []
    for i, r in enumerate(rows):
        padded = [cell(r[j] if j < len(r) else "", widths[j]) for j in range(cols)]
        line = " │ ".join(padded)
        out.append(f"[#cbd5e1]{line}[/#cbd5e1]")
        if i == 0:
            out.append("[dim]" + "─┼─".join("─" * w for w in widths) + "[/dim]")
    return out


def render_markdown(text: str) -> str:
    """Agents answer in markdown; give the terminal the shape without the syntax.

    Headings, bold, bullets, numbered lists, fenced code, hr, simple GFM tables.
    """
    out: list[str] = []
    in_code = False
    table_buf: list[list[str]] = []

    def flush_table() -> None:
        nonlocal table_buf
        if table_buf:
            out.extend(_format_table(table_buf))
            table_buf = []

    for line in escape(text).split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_table()
            in_code = not in_code
            lang = stripped[3:].strip()
            out.append(
                f"[dim]{'┄' * 3} {lang or 'code'} {'┄' * 3}[/dim]"
                if in_code
                else "[dim]┄┄┄[/dim]"
            )
            continue
        if in_code:
            out.append(f"[#a5b4fc]{line}[/#a5b4fc]")
            continue
        # GFM table row (skip separator lines like |---|---|)
        if "|" in stripped and stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if cells and all(re.match(r"^:?-+:?$", c or "") for c in cells):
                continue  # alignment row
            table_buf.append(cells)
            continue
        flush_table()
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            out.append("[dim]────────────────[/dim]")
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            out.append(f"[b #7dd3fc]{_inline(heading.group(2))}[/]")
            continue
        if stripped.startswith("> "):
            out.append(f"[dim i]{_inline(stripped[2:])}[/dim i]")
            continue
        numbered = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if numbered:
            out.append(
                f"{numbered.group(1)}[b]{numbered.group(2)}.[/b] {_inline(numbered.group(3))}"
            )
            continue
        out.append(_inline(re.sub(r"^(\s*)[-*]\s+", r"\1• ", line)))
    flush_table()
    return "\n".join(out)


def short_time(iso: str) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    except ValueError:
        return ""


# Same palette as the web client (stable color per person / agent).
_MEMBER_COLORS: tuple[str, ...] = (
    "#5b9fd4",  # blue
    "#d4a05b",  # amber
    "#c75b8a",  # rose
    "#5bc4a8",  # teal
    "#9b7bd4",  # violet
    "#d47a5b",  # coral
    "#7bb05b",  # olive
    "#5b8ad4",  # indigo
)
_AGENT_COLORS: dict[str, str] = {
    "lead": "#66aa66",
    "ask": "#44aa99",
    "deepresearch": "#338888",
    "research": "#44aa99",
    "writing": "#aa8844",
    "write": "#aa8844",
    "coding": "#44aaff",
    "code": "#44aaff",
    "code_review": "#4488aa",
    "review": "#4488aa",
    "checklist": "#aa66aa",
    "status": "#77aa99",
}


def _hash_str(s: str) -> int:
    h = 0
    for ch in s:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
        if h >= 0x80000000:
            h -= 0x100000000
    return abs(int(h))


def color_for_member(email: str, *, member_emails: list[str] | None = None) -> str:
    """Stable name/rail color — mirrors web colorForMember."""
    key = (email or "").strip().lower()
    if not key:
        return "#888888"
    if member_emails:
        for i, em in enumerate(member_emails):
            if str(em or "").strip().lower() == key:
                return _MEMBER_COLORS[i % len(_MEMBER_COLORS)]
    return _MEMBER_COLORS[_hash_str(key) % len(_MEMBER_COLORS)]


def color_for_message(
    message: dict[str, Any], *, member_emails: list[str] | None = None
) -> str:
    agent = message.get("agent")
    if agent:
        return _AGENT_COLORS.get(str(agent).lower(), "#66aa66")
    return color_for_member(
        str(message.get("sender_email") or message.get("sender") or ""),
        member_emails=member_emails,
    )

# Discord-style: same speaker stacks until someone else / an agent / ~4 minutes pass.
_GROUP_GAP_SECONDS = 4 * 60


def message_speaker_key(message: dict[str, Any]) -> str:
    """Identity used to decide whether two bubbles share one name header."""
    if message.get("agent"):
        return f"agent:{message.get('agent')}"
    email = str(message.get("sender_email") or "").strip().lower()
    if email:
        return f"user:{email}"
    name = str(message.get("sender") or "").strip().lower()
    return f"user:{name or '?'}"


def _message_created_at(message: dict[str, Any]) -> datetime | None:
    raw = str(message.get("created_at") or "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def should_group_with_previous(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> bool:
    """True when `current` should hide its name (continuation of `previous`)."""
    if previous is None or current.get("deleted_at"):
        return False
    if previous.get("deleted_at"):
        return False
    if message_speaker_key(previous) != message_speaker_key(current):
        return False
    t0 = _message_created_at(previous)
    t1 = _message_created_at(current)
    if t0 is not None and t1 is not None:
        gap = abs((t1 - t0).total_seconds())
        if gap > _GROUP_GAP_SECONDS:
            return False
    return True


def group_messages(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split ordered messages into speaker blocks (same rules as should_group_with_previous)."""
    groups: list[list[dict[str, Any]]] = []
    for row in rows:
        if not groups:
            groups.append([row])
            continue
        prev = groups[-1][-1]
        if should_group_with_previous(prev, row):
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups


def blocks_fingerprint(groups: list[list[dict[str, Any]]]) -> tuple[Any, ...]:
    """Stable id for block structure (ids + speaker). Content changes do not alter this."""
    return tuple(
        (tuple(int(m["id"]) for m in g), message_speaker_key(g[0]) if g else "")
        for g in groups
    )


def message_content_key(message: dict[str, Any]) -> tuple[Any, ...]:
    """Detect body/attachment edits without treating polls as structural changes."""
    atts = message.get("attachments") or []
    att_ids = tuple(
        sorted(int(a["id"]) for a in atts if isinstance(a, dict) and a.get("id") is not None)
    )
    return (
        str(message.get("body") or ""),
        str(message.get("edited_at") or ""),
        str(message.get("deleted_at") or ""),
        str(message.get("visibility") or ""),
        att_ids,
    )


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


class AttachmentFile(Static):
    """Clickable non-image attachment row."""

    BINDINGS = [
        ("enter", "open_attachment", "open"),
        ("o", "open_attachment", "open"),
    ]

    def __init__(self, att: dict[str, Any], client: ApiClient) -> None:
        name = str(att.get("filename") or "file")
        super().__init__(
            f"[underline]📎 {escape(name)}[/underline]  [dim]enter/o open[/dim]",
            markup=True,
            classes="msg-file",
        )
        self.can_focus = True
        self.att = att
        self.client = client

    def on_click(self) -> None:
        self.action_open_attachment()

    def action_open_attachment(self) -> None:
        chat = getattr(self.app, "chat_view", None)
        if chat is not None:
            chat.open_attachment(self.att)


class MemberKick(Static):
    """Quiet text action beside a member — reveals on row hover."""

    DEFAULT_CSS = """
    MemberKick {
        width: 6;
        height: 1;
        content-align: right middle;
        color: transparent;
    }
    """

    def __init__(self, member: dict[str, Any]) -> None:
        super().__init__("kick", classes="member-kick")
        self.can_focus = True
        self.member = member
        self.user_id = int(member.get("user_id") or 0)

    def on_click(self) -> None:
        chat = getattr(self.app, "chat_view", None)
        if chat is not None:
            chat.begin_kick(self.member)

    def on_key(self, event: events.Key) -> None:
        if event.key in ("enter", "space"):
            self.on_click()
            event.stop()


class MsgTool(Static):
    """Inline edit/delete text control on own messages (not a chunky Button)."""

    def __init__(self, label: str, action: str) -> None:
        super().__init__(label, classes=f"msg-tool msg-tool-{action}")
        self.can_focus = False
        self._action = action  # "edit" | "delete"

    def on_click(self) -> None:
        parent = self.parent
        while parent is not None and not isinstance(parent, MessageLine):
            parent = parent.parent
        if not isinstance(parent, MessageLine):
            return
        if self._action == "edit":
            parent.action_edit_own()
        else:
            parent.action_delete_own()


class MessageLine(Vertical):
    """One body line inside a SpeakerBlock — no rail, no name header."""

    can_focus = True

    BINDINGS = [
        Binding("e", "edit_own", "edit", show=False),
        Binding("delete", "delete_own", "delete", show=False),
        Binding("backspace", "delete_own", "delete", show=False),
    ]

    DEFAULT_CSS = """
    MessageLine {
        height: auto;
        padding: 0 0 0 1;
        margin: 0;
    }
    MessageLine.whisper-msg { color: $text-muted; }
    MessageLine.highlight-ping {
        background: $warning 20%;
    }
    MessageLine .msg-line {
        height: auto;
        width: 100%;
        align: left top;
    }
    MessageLine .msg-body-col {
        width: 1fr;
        height: auto;
    }
    MessageLine .msg-body { height: auto; }
    MessageLine .msg-edited-flag {
        width: auto;
        height: 1;
        margin-left: 1;
        color: $text-muted;
        display: none;
        content-align: left middle;
    }
    MessageLine.is-edited .msg-edited-flag {
        display: block;
    }
    MessageLine .msg-tools {
        display: none;
        width: auto;
        height: 1;
        align: right top;
        padding-left: 1;
    }
    /* Instant Discord-style: CSS :hover covers the line + edit/delete children (no trail timers). */
    MessageLine.mine-msg:hover .msg-tools {
        display: block;
    }
    MessageLine .msg-tool {
        width: auto;
        min-width: 6;
        height: 1;
        margin-left: 1;
        content-align: right middle;
    }
    MessageLine.mine-msg:hover .msg-tool-edit {
        color: #7dd3fc;
        text-style: underline;
    }
    MessageLine.mine-msg:hover .msg-tool-delete {
        color: #f87171;
        text-style: underline;
    }
    MessageLine .msg-chart {
        height: 18;
        width: 100%;
        margin: 1 0 0 0;
    }
    MessageLine .msg-file { color: $text-muted; height: 1; }
    MessageLine .msg-file:focus { color: $accent; text-style: bold; }
    """

    def __init__(
        self,
        message: dict[str, Any],
        my_email: str,
        client: ApiClient | None = None,
        *,
        member_emails: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.message_id = int(message["id"])
        self.my_email = my_email
        self.client = client
        self.message = message
        self._member_emails_cache = list(member_emails or [])
        self._tools = Horizontal(classes="msg-tools")
        self._edit_btn = MsgTool("edit", "edit")
        self._del_btn = MsgTool("delete", "delete")
        self._body_static = Static("", classes="msg-body", markup=True)
        self._body_md = Markdown("", classes="msg-body")
        self._edited_flag = Static("[dim]· edited[/dim]", classes="msg-edited-flag", markup=True)
        self._chart_ids: tuple[int, ...] = ()
        self._tools_ready = False

    @property
    def is_mine(self) -> bool:
        if self.message.get("agent") or self.message.get("deleted_at"):
            return False
        return str(self.message.get("sender_email") or "") == self.my_email

    def _member_emails(self) -> list[str]:
        if self._member_emails_cache:
            return self._member_emails_cache
        chat = getattr(self.app, "chat_view", None)
        if chat is None:
            return []
        return [str(m.get("email") or "") for m in getattr(chat, "members", [])]

    def compose(self) -> ComposeResult:
        with Horizontal(classes="msg-line"):
            with Vertical(classes="msg-body-col"):
                yield self._body_static
                yield self._body_md
            yield self._edited_flag
            with self._tools:
                yield self._edit_btn
                yield self._del_btn

    def on_mount(self) -> None:
        self._tools_ready = True
        self.update_message(self.message)

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        href = (event.href or "").strip()
        if href.startswith(("http://", "https://", "mailto:")):
            webbrowser.open(href)
            event.stop()

    def action_edit_own(self) -> None:
        if not self.is_mine:
            return
        chat = getattr(self.app, "chat_view", None)
        if chat is not None:
            chat.begin_edit_message(self)

    def action_delete_own(self) -> None:
        if not self.is_mine:
            return
        chat = getattr(self.app, "chat_view", None)
        if chat is not None:
            chat.begin_delete_message(self)

    def update_message(self, message: dict[str, Any]) -> None:
        self.message = message
        self.set_class(bool(message.get("agent")), "agent-msg")
        self.set_class(bool(message.get("visibility") == "whisper"), "whisper-msg")
        mine = self.is_mine
        self.set_class(mine, "mine-msg")
        edited = bool(message.get("edited_at")) and not message.get("deleted_at")
        self.set_class(edited, "is-edited")

        if message.get("deleted_at"):
            self.set_class(False, "mine-msg")
            self.set_class(False, "is-edited")
            self._body_md.display = False
            self._body_static.display = True
            self._body_static.update("[dim i]message deleted[/dim i]")
            self._clear_extras()
            self._sync_parent_block()
            return

        agent = message.get("agent")
        raw = _strip_control_markers(str(message.get("body") or ""))
        use_md = bool(agent) and bool(raw.strip())
        if use_md:
            self._body_static.display = False
            self._body_md.display = True
            self._body_md.update(raw)
        else:
            self._body_md.display = False
            self._body_static.display = True
            if re.search(r"https?://|mailto:", raw):
                linked = re.sub(
                    r"(https?://[^\s<]+)|(mailto:[^\s<]+)",
                    lambda m: f"[{m.group(0)}]({m.group(0)})",
                    raw,
                )
                self._body_static.display = False
                self._body_md.display = True
                self._body_md.update(linked)
            elif raw and (
                "```" in raw
                or "|" in raw
                or re.search(r"^#+\s", raw, re.M)
                or re.search(r"^\d+\.\s", raw, re.M)
            ):
                self._body_static.update(render_markdown(raw))
            else:
                self._body_static.update(escape(raw) if raw else "")

        self._sync_attachments(message.get("attachments") or [])
        self._sync_parent_block()

    def _sync_parent_block(self) -> None:
        """Keep SpeakerBlock header/messages list in sync when this line changes."""
        parent = self.parent
        if not isinstance(parent, SpeakerBlock):
            return
        for i, line in enumerate(parent.lines):
            if line is self:
                if i < len(parent.messages):
                    parent.messages[i] = self.message
                if i == 0:
                    parent._render_head()
                break

    def _clear_extras(self) -> None:
        for child in list(self.children):
            if "msg-line" in child.classes:
                continue
            if child in (self._body_static, self._body_md):
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
                if self.client is not None:
                    self.mount(AttachmentFile(a, self.client))
                else:
                    self.mount(
                        Static(
                            f"[dim]📎 {escape(str(a.get('filename') or ''))}[/dim]",
                            markup=True,
                            classes="msg-file",
                        )
                    )
        elif file_atts and not any(isinstance(c, AttachmentFile) for c in self.children):
            for a in file_atts:
                if self.client is not None:
                    self.mount(AttachmentFile(a, self.client))
                else:
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


class SpeakerBlock(Vertical):
    """One speaker turn: single colored rail + name header + stacked MessageLines."""

    DEFAULT_CSS = """
    SpeakerBlock {
        height: auto;
        padding: 1 1 1 1;
        margin: 1 0 0 0;
        border-left: wide transparent;
        background: transparent;
    }
    SpeakerBlock.agent-block {
        background: $panel;
    }
    SpeakerBlock.has-mine:hover {
        background: $boost;
    }
    SpeakerBlock .block-head {
        height: 1;
        width: 100%;
        margin: 0 0 0 0;
        text-style: bold;
    }
    """

    def __init__(
        self,
        messages: list[dict[str, Any]],
        my_email: str,
        client: ApiClient | None = None,
        *,
        member_emails: list[str] | None = None,
    ) -> None:
        super().__init__(classes="speaker-block")
        self.messages = list(messages)
        self.my_email = my_email
        self.client = client
        self.member_emails = list(member_emails or [])
        first = self.messages[0]
        self.speaker_color = color_for_message(first, member_emails=self.member_emails)
        self._head = Static("", classes="block-head", markup=True)
        self.lines = [
            MessageLine(m, my_email, client=client, member_emails=self.member_emails)
            for m in self.messages
        ]

    def compose(self) -> ComposeResult:
        yield self._head
        for line in self.lines:
            yield line

    def on_mount(self) -> None:
        try:
            self.styles.border_left = ("wide", self.speaker_color)
        except Exception:
            try:
                self.styles.border_left = ("tall", self.speaker_color)
            except Exception:
                pass
        if any(line.is_mine for line in self.lines):
            self.add_class("has-mine")
        if self.messages and self.messages[0].get("agent"):
            self.add_class("agent-block")
        self._render_head()

    def append_line(self, message: dict[str, Any]) -> MessageLine:
        """Add a continuation line without remounting the block (keeps scroll stable)."""
        line = MessageLine(
            message,
            self.my_email,
            client=self.client,
            member_emails=self.member_emails,
        )
        self.messages.append(message)
        self.lines.append(line)
        self.mount(line)
        if line.is_mine:
            self.add_class("has-mine")
        return line

    def _render_head(self) -> None:
        message = self.messages[0]
        color = self.speaker_color
        agent = message.get("agent")
        if agent:
            who = f"[b {color}]@{escape(str(agent))}[/]"
        else:
            name = str(message.get("sender") or message.get("sender_email") or "user")
            who = f"[b {color}]{escape(name)}[/]"
        meta = [short_time(str(message.get("created_at") or ""))]
        if message.get("edited_at"):
            meta.append("edited")
        if message.get("visibility") == "whisper":
            meta.append("only you")
        self._head.update(f"{who}  [dim]{escape(' · '.join(x for x in meta if x))}[/dim]")


class ChatView(Vertical):
    """Sidebar + transcript + composer."""

    POLL_SECONDS = 2.0
    BINDINGS = [
        ("ctrl+f", "attach_file", "attach file"),
        ("ctrl+m", "voice_toggle", "voice"),
        ("ctrl+shift+n", "new_channel", "new channel"),
    ]

    def __init__(self, client: ApiClient) -> None:
        super().__init__(id="chat")
        self.client = client
        self.chat_id: int | None = None
        self.chats: list[dict[str, Any]] = []
        self.members: list[dict[str, Any]] = []
        self.my_email = ""
        self._rows: dict[int, dict[str, Any]] = {}
        self._views: dict[int, MessageLine] = {}
        self._blocks_fp: tuple[Any, ...] | None = None
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
        self._recording = False
        self._voice_path: Path | None = None
        self._voice = None  # lazily created VoiceRecorder

        self.sidebar = VerticalScroll(id="chat-sidebar")
        self.chat_list = ListView(id="chat-list")
        self.member_list = VerticalScroll(id="member-list")
        self.new_chat_btn = Button("+ channel", id="chat-new")
        self.transcript = VerticalScroll(id="transcript")
        self.title_bar = Static("", id="chat-title", markup=True)
        self.picker = CommandPicker()
        self.llm_label = Static("", id="llm-wait-label", markup=True)
        self.llm_bar = ProgressBar(
            total=None, show_eta=False, show_percentage=False, id="llm-wait-bar"
        )
        self.attach_pending = Static("", id="attach-pending", markup=True)
        self.composer_ghost = Static("", id="composer-ghost", markup=True)
        self.attach_btn = Button("+", id="chat-attach", compact=True, tooltip="attach file")
        self.mic_btn = Button("mic", id="chat-mic", compact=True, tooltip="voice (ctrl+m)")
        self.composer = Composer(
            self.picker,
            placeholder="Ask anything — /  !  or  @",
            id="composer",
        )

    def compose(self) -> ComposeResult:
        with Horizontal(id="chat-body"):
            with self.sidebar:
                yield Label("CHATS", classes="side-head")
                yield self.chat_list
                yield self.new_chat_btn
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
                yield self.composer_ghost
                with Horizontal(id="composer-row"):
                    yield self.attach_btn
                    yield self.composer
                    yield self.mic_btn
    def on_mount(self) -> None:
        self.set_interval(self.POLL_SECONDS, self.poll_messages)
        self._sync_llm_ui()
        self._render_pending_attachments()

    def reset_session_state(self) -> None:
        """Clear transcript / pending attach / voice when signing out."""
        self.chat_id = None
        self.chats = []
        self.members = []
        self.my_email = ""
        self._rows.clear()
        self._views.clear()
        self._blocks_fp = None
        self._last_id = 0
        self._last_sync = ""
        self._sending_chats.clear()
        self._llm_jobs.clear()
        self._pending = None
        self._pending_attachments = []
        self._setup_opened.clear()
        self._setup_busy = False
        self._attach_busy = False
        self._recording = False
        self._voice_path = None
        try:
            self.transcript.remove_children()
        except Exception:
            pass
        try:
            self.chat_list.clear()
        except Exception:
            pass
        try:
            self.member_list.remove_children()
        except Exception:
            pass
        self.title_bar.update("")
        self.composer.value = ""
        self.composer.disabled = False
        self.attach_btn.disabled = False
        self.mic_btn.label = "mic"
        self.mic_btn.remove_class("recording")
        self.picker.close()
        self._render_pending_attachments()
        self._sync_llm_ui()
        self.refresh_ghost()

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
        active_idx = 0
        self.chat_list.clear()
        for i, chat in enumerate(self.chats):
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
            if self.chat_id is not None and cid == int(self.chat_id):
                active_idx = i
        if self.chats:
            self.chat_list.index = active_idx
        self._sync_chat_list_selection()

    def _sync_chat_list_selection(self) -> None:
        """Mark the open room so it stays visible when focus is in the composer."""
        for item in self.chat_list.children:
            if not isinstance(item, ListItem):
                continue
            chat = getattr(item, "chat", None)
            active = (
                chat is not None
                and self.chat_id is not None
                and int(chat.get("id") or 0) == int(self.chat_id)
            )
            item.set_class(active, "active-chat")

    def _render_members(self) -> None:
        self.member_list.remove_children()
        ws = getattr(self.app, "ws", None)
        is_owner = bool(ws and getattr(ws, "is_owner", False))
        me_id = int((ws.me or {}).get("user_id") or 0) if ws else 0
        emails = [str(x.get("email") or "") for x in self.members]
        for m in self.members:
            user_id = int(m.get("user_id") or 0)
            crown = " [yellow]★[/yellow]" if m.get("role") == "owner" else ""
            name = escape(str(m.get("name") or m.get("email") or ""))
            dot = color_for_member(str(m.get("email") or ""), member_emails=emails)
            label = Static(
                f"[{dot}]●[/] {name}{crown}",
                markup=True,
                classes="member-name",
            )
            row = Horizontal(classes="member-row")
            row.member = m
            row.user_id = user_id
            self.member_list.mount(row)
            row.mount(label)
            if is_owner and user_id and user_id != me_id:
                row.mount(MemberKick(m))

    @property
    def current_chat(self) -> dict[str, Any]:
        return next((c for c in self.chats if int(c["id"]) == self.chat_id), {})

    def select_chat(self, chat_id: int) -> None:
        if chat_id == self.chat_id:
            return
        self.chat_id = chat_id
        self._rows.clear()
        self._views.clear()
        self._blocks_fp = None
        self._last_id = 0
        self._last_sync = ""
        self.transcript.remove_children()
        self._pending = None
        self._pending_attachments = []
        self._render_pending_attachments()
        chat = self.current_chat
        if chat.get("kind") == "private":
            self.title_bar.update("[b]my room[/b]  [dim]/skills · notes stay quiet[/dim]")
        else:
            name = escape(str(chat.get("name") or ""))
            self.title_bar.update(f"[b]#{name}[/b]  [dim]@people · !commands[/dim]")
        self._sync_llm_ui()
        self.poll_messages()
        self._sync_chat_list_selection()
        # Keep list cursor on the open room
        for i, chat in enumerate(self.chats):
            if int(chat["id"]) == int(chat_id):
                self.chat_list.index = i
                break

    def open_mention(self, chat_id: int, message_id: int) -> None:
        """Open a channel and scroll/highlight the pinged message."""
        if chat_id != self.chat_id:
            self.select_chat(chat_id)
        self.focus_message(int(message_id), attempt=0)

    def focus_message(self, message_id: int, attempt: int = 0) -> None:
        view = self._views.get(int(message_id))
        if view is not None:
            # clear any prior highlight
            for other in self._views.values():
                other.set_class(False, "highlight-ping")
            view.set_class(True, "highlight-ping")
            try:
                view.scroll_visible(animate=True)
            except Exception:
                pass
            self.set_timer(3.5, lambda: view.set_class(False, "highlight-ping"))
            return
        if attempt == 0 and self.chat_id is not None:
            # Force a full history pull so older pings are present
            self._last_id = 0
            self._last_sync = ""
            self.poll_messages()
        if attempt >= 8:
            self.app.set_status(f"[yellow]message #{message_id} not loaded yet[/yellow]")
            return
        self.set_timer(
            0.25,
            lambda mid=message_id, n=attempt: self.focus_message(mid, n + 1),
        )

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
            f"[dim]({len(rows)}/5) · + add · esc clear last · !attach-clear[/dim]"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "chat-attach":
            event.stop()
            self.action_attach_file()
        elif event.button.id == "chat-mic":
            event.stop()
            self.action_voice_toggle()
        elif event.button.id == "chat-new":
            event.stop()
            self.action_new_channel()

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
        # Empty poll: do nothing (was re-rendering every message every tick → lag)
        if not rows:
            return
        at_bottom = self.transcript.scroll_offset.y >= self.transcript.max_scroll_y - 2
        touch_ids: list[int] = []
        for row in rows:
            mid = int(row["id"])
            self._last_id = max(self._last_id, mid)
            if row.get("deleted_at"):
                self._rows.pop(mid, None)
                continue
            prev = self._rows.get(mid)
            self._rows[mid] = row
            if prev is None or message_content_key(prev) != message_content_key(row):
                touch_ids.append(mid)
            self._maybe_open_setup(row)
        self._refresh_transcript_blocks(
            scroll_bottom=at_bottom,
            touch_ids=touch_ids,
        )
        # Keep / remount thinking bubble if this room still has an LLM job
        if self._active_llm_skill() and (
            self._pending is None or self._pending not in self.transcript.children
        ):
            self._sync_llm_ui()

    def _ordered_rows(self) -> list[dict[str, Any]]:
        return [self._rows[k] for k in sorted(self._rows)]

    def _member_emails_list(self) -> list[str]:
        return [str(m.get("email") or "") for m in self.members]

    def _flat_ids_from_fp(self) -> list[int]:
        if not self._blocks_fp:
            return []
        out: list[int] = []
        for part in self._blocks_fp:
            try:
                ids = part[0]
            except (TypeError, IndexError):
                continue
            out.extend(int(x) for x in ids)
        return out

    def _mount_in_transcript(self, widget: SpeakerBlock | Static) -> None:
        """Mount ahead of the LLM pending bubble so new lines stay above 'thinking…'."""
        pending = self._pending
        if pending is not None and pending in self.transcript.children:
            self.transcript.mount(widget, before=pending)
        else:
            self.transcript.mount(widget)

    def _append_message_rows(self, rows: list[dict[str, Any]]) -> None:
        """Grow the transcript in place — no remove_children (avoids blank flash / scroll jump)."""
        emails = self._member_emails_list()
        for row in rows:
            blocks = [c for c in self.transcript.children if isinstance(c, SpeakerBlock)]
            last = blocks[-1] if blocks else None
            if last is not None and should_group_with_previous(last.messages[-1], row):
                line = last.append_line(row)
            else:
                block = SpeakerBlock(
                    [row],
                    self.my_email,
                    client=self.client,
                    member_emails=emails,
                )
                self._mount_in_transcript(block)
                line = block.lines[0]
            self._views[line.message_id] = line

    def _rebuild_transcript_blocks(self, groups: list[list[dict[str, Any]]]) -> None:
        self.transcript.remove_children()
        self._views.clear()
        self._pending = None
        emails = self._member_emails_list()
        for group in groups:
            block = SpeakerBlock(
                group,
                self.my_email,
                client=self.client,
                member_emails=emails,
            )
            self.transcript.mount(block)
            for line in block.lines:
                self._views[line.message_id] = line
        if self._active_llm_skill():
            self._sync_llm_ui()

    def _refresh_transcript_blocks(
        self,
        *,
        scroll_bottom: bool = False,
        force: bool = False,
        touch_ids: list[int] | None = None,
    ) -> None:
        """Patch or append when possible; full remount only for deletes / regroup / force."""
        rows = self._ordered_rows()
        groups = group_messages(rows)
        fp = blocks_fingerprint(groups)
        touch = list(touch_ids or ())

        if not force and fp == self._blocks_fp:
            for mid in touch:
                view = self._views.get(mid)
                row = self._rows.get(mid)
                if view is not None and row is not None:
                    view.update_message(row)
            if scroll_bottom and touch:
                self.call_after_refresh(self.transcript.scroll_end, animate=False)
            return

        old_flat = self._flat_ids_from_fp()
        new_flat = [int(m["id"]) for g in groups for m in g]
        can_append = (
            not force
            and bool(old_flat)
            and len(new_flat) > len(old_flat)
            and new_flat[: len(old_flat)] == old_flat
        )
        if can_append:
            suffix = [self._rows[mid] for mid in new_flat[len(old_flat) :] if mid in self._rows]
            if suffix:
                self._append_message_rows(suffix)
                self._blocks_fp = fp
                suffix_set = {int(r["id"]) for r in suffix}
                for mid in touch:
                    if mid in suffix_set:
                        continue
                    view = self._views.get(mid)
                    row = self._rows.get(mid)
                    if view is not None and row is not None:
                        view.update_message(row)
                if self._active_llm_skill() and (
                    self._pending is None or self._pending not in self.transcript.children
                ):
                    self._sync_llm_ui()
                if scroll_bottom:
                    self.call_after_refresh(self.transcript.scroll_end, animate=False)
                return

        # Trailing deletes (e.g. edit removed later agent replies) — trim without blank remount.
        can_trim = (
            not force
            and bool(old_flat)
            and len(old_flat) > len(new_flat)
            and old_flat[: len(new_flat)] == new_flat
        )
        if can_trim:
            for mid in old_flat[len(new_flat) :]:
                self._unmount_message(mid)
            self._blocks_fp = fp
            for mid in touch:
                view = self._views.get(mid)
                row = self._rows.get(mid)
                if view is not None and row is not None:
                    view.update_message(row)
            if scroll_bottom and touch:
                self.call_after_refresh(self.transcript.scroll_end, animate=False)
            return

        self._blocks_fp = fp
        self._rebuild_transcript_blocks(groups)
        if scroll_bottom:
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
        self.refresh_ghost()

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

    def refresh_ghost(self) -> None:
        """Dim hint above the composer: active picker row or next-arg tip."""
        if self.picker.open:
            chosen = self.picker.selected
            if chosen is not None:
                self.composer_ghost.display = True
                self.composer_ghost.update(
                    f"[dim]{escape(chosen.insert)}[/dim]  [dim]{escape(chosen.blurb)}[/dim]"
                )
                return
        text = self.composer.value
        # Command complete (picker closed) — show arg hint
        for label, hint in _ARG_HINTS.items():
            if text == label or text.startswith(label + " "):
                if text.rstrip() == label.rstrip() or text == label:
                    # needs trailing space or args
                    self.composer_ghost.display = True
                    self.composer_ghost.update(f"[dim]{escape(label)} {escape(hint)}[/dim]")
                    return
                if text.startswith(label + " ") and not text[len(label) + 1 :].strip():
                    self.composer_ghost.display = True
                    self.composer_ghost.update(f"[dim]{escape(hint)}[/dim]")
                    return
        self.composer_ghost.update("")
        self.composer_ghost.display = False

    def accept_ghost_hint(self) -> bool:
        """Tab with picker closed: ensure trailing space after a bare command."""
        text = self.composer.value
        for label in _ARG_HINTS:
            if text == label.rstrip() or text == label:
                insert = label if label.endswith(" ") else label + " "
                self.composer.value = insert
                self.composer.cursor_position = len(insert)
                self.refresh_picker()
                self.refresh_ghost()
                return True
        return False

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        # Keep picker cursor paint in sync if Textual moves highlight itself
        if event.list_view is self.picker.list_view and self.picker.open:
            self.picker._paint_cursor()
            self.refresh_ghost()

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
                self._rows.clear()
                self._views.clear()
                self._blocks_fp = None
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

    # edit / delete / open / channel / kick / voice -----------------------

    def begin_edit_message(self, view: MessageLine) -> None:
        if self.chat_id is None or not view.is_mine:
            return
        body = str(view.message.get("body") or "")
        chat_id = int(self.chat_id)
        mid = int(view.message_id)
        if chat_id in self._llm_jobs or chat_id in self._sending_chats:
            self.app.set_status("[yellow]wait for the current job to finish[/yellow]")
            return

        def done(new_body: str | None) -> None:
            if new_body is None:
                self.app.set_status("edit cancelled")
                return
            text = new_body.strip()
            if not text:
                self.app.set_status("[yellow]body required[/yellow]")
                return
            self._start_edit(chat_id, mid, text)

        from app.cli_pkg.tui.widgets import MessageEditModal

        self.app.push_screen(MessageEditModal(body), done)

    def _start_edit(self, chat_id: int, message_id: int, body: str) -> None:
        """Same LLM wait UI as send when the edit re-triggers an agent skill."""
        chat = next((c for c in self.chats if int(c["id"]) == chat_id), {})
        working = looks_like_agent_work(body, str(chat.get("kind") or ""))
        if working:
            skill = skill_name_from_body(body) or (
                body.split()[0].lstrip("/") if body else "skill"
            )
            self._llm_jobs[chat_id] = skill
            if self.chat_id == chat_id:
                self._sync_llm_ui()
                self.app.set_status(f"[#7dd3fc]/{escape(skill)} running…[/]")
            else:
                self._render_chat_list()
                self.app.set_status(
                    f"[#7dd3fc]/{escape(skill)} running in another room…[/]"
                )
        else:
            self.app.set_status("[dim]saving edit…[/dim]")
        self._edit_worker(chat_id, message_id, body)

    @work(thread=True, group="chat-edit")
    def _edit_worker(self, chat_id: int, message_id: int, body: str) -> None:
        try:
            data = self.client.edit_message(chat_id, message_id, body)
            error = ""
        except ApiError as exc:
            data, error = {}, str(exc)
        self.app.call_from_thread(self._apply_edit_result, chat_id, data, error)

    def begin_delete_message(self, view: MessageLine) -> None:
        if self.chat_id is None or not view.is_mine:
            return
        chat_id = int(self.chat_id)
        mid = int(view.message_id)
        snippet = escape(str(view.message.get("body") or "")[:80])

        def confirmed(yes: bool | None) -> None:
            if not yes:
                self.app.set_status("delete cancelled")
                return
            self.app.set_status("[dim]deleting…[/dim]")
            self._delete_worker(chat_id, mid)

        from app.cli_pkg.tui.widgets import ConfirmModal

        self.app.push_screen(
            ConfirmModal(
                "Delete message",
                snippet or "[dim](empty)[/dim]",
                "Removes this message and following agent replies.",
                "Delete",
            ),
            confirmed,
        )

    @work(thread=True, group="chat-edit")
    def _delete_worker(self, chat_id: int, message_id: int) -> None:
        try:
            data = self.client.delete_message(chat_id, message_id)
            error = ""
        except ApiError as exc:
            data, error = {}, str(exc)
        self.app.call_from_thread(self._apply_delete_result, chat_id, message_id, data, error)

    def _drop_ids(self, ids: list[Any]) -> None:
        for raw in ids:
            try:
                mid = int(raw)
            except (TypeError, ValueError):
                continue
            self._rows.pop(mid, None)
            self._unmount_message(mid)

    def _unmount_message(self, mid: int) -> None:
        """Remove one line (and its SpeakerBlock if it becomes empty) without wiping the room."""
        view = self._views.pop(mid, None)
        if view is None:
            return
        block = view.parent
        try:
            view.remove()
        except Exception:
            pass
        if not isinstance(block, SpeakerBlock):
            return
        block.lines = [line for line in block.lines if line.message_id != mid]
        block.messages = [
            m for m in block.messages if int(m.get("id") or 0) != mid
        ]
        if not block.lines:
            try:
                block.remove()
            except Exception:
                pass
            return
        if any(line.is_mine for line in block.lines):
            block.add_class("has-mine")
        else:
            block.remove_class("has-mine")
        block._render_head()

    def _resync_blocks_fingerprint(self) -> None:
        self._blocks_fp = blocks_fingerprint(group_messages(self._ordered_rows()))

    def _mount_replies(self, replies: list[dict]) -> None:
        for row in replies:
            if not row or row.get("id") is None or row.get("deleted_at"):
                continue
            mid = int(row["id"])
            self._rows[mid] = row
            self._last_id = max(self._last_id, mid)
        self._refresh_transcript_blocks(scroll_bottom=True)

    def _apply_edit_result(self, chat_id: int, data: dict, error: str) -> None:
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
        else:
            self._render_chat_list()
            self._sync_llm_ui()
        if error:
            self.app.set_status(f"[red]edit failed: {escape(error)}[/red]")
            return
        if not viewing:
            self.app.set_status("edit saved (other chat)")
            return

        at_bottom = self.transcript.scroll_offset.y >= self.transcript.max_scroll_y - 2
        removed = list(data.get("removed_ids") or [])
        self._drop_ids(removed)

        msg = data.get("message") or {}
        mid = int(msg.get("id") or 0)
        touch: list[int] = []
        if mid and msg:
            self._rows[mid] = msg
            touch.append(mid)
            view = self._views.get(mid)
            if view is not None:
                view.update_message(msg)

        for row in list(data.get("replies") or []):
            if not row or row.get("id") is None or row.get("deleted_at"):
                continue
            rid = int(row["id"])
            self._rows[rid] = row
            self._last_id = max(self._last_id, rid)

        # Fingerprint matches the post-drop tree so new replies append (no blank remount).
        self._resync_blocks_fingerprint()
        self._refresh_transcript_blocks(scroll_bottom=at_bottom, touch_ids=touch)
        self.app.set_status("[green]message updated[/green]")
        # Don't force a second full history rebuild — next poll will pick up stragglers.
        self.poll_messages()

    def _apply_delete_result(
        self, chat_id: int, message_id: int, data: dict, error: str
    ) -> None:
        if error:
            self.app.set_status(f"[red]delete failed: {escape(error)}[/red]")
            return
        if chat_id != self.chat_id:
            return
        removed = list(data.get("removed_ids") or [])
        removed.append(message_id)
        self._drop_ids(removed)
        self._resync_blocks_fingerprint()
        self.app.set_status("message deleted")

    def open_attachment(self, att: dict[str, Any]) -> None:
        self.app.set_status("[dim]opening…[/dim]")
        self._open_attachment_worker(att)

    @work(thread=True, group="chat-open-att")
    def _open_attachment_worker(self, att: dict[str, Any]) -> None:
        name = str(att.get("filename") or "file")
        url = str(att.get("url") or "")
        att_id = att.get("id")
        if not url and att_id:
            url = f"/attachments/{att_id}"
        try:
            if not url:
                raise ApiError("no attachment url")
            data = self.client.download_bytes(url, timeout=120.0)
            suffix = Path(name).suffix or ".bin"
            path = Path(tempfile.gettempdir()) / f"aio-open-{att_id or 'x'}{suffix}"
            path.write_bytes(data)
            webbrowser.open(path.as_uri())
            msg = f"[green]opened {escape(name)}[/green]"
        except (ApiError, OSError) as exc:
            msg = f"[red]open failed: {escape(str(exc))}[/red]"
        self.app.call_from_thread(self.app.set_status, msg)

    def action_new_channel(self) -> None:
        def done(name: str | None) -> None:
            if not name:
                self.app.set_status("channel cancelled")
                return
            self.app.set_status(f"[dim]creating #{escape(name)}…[/dim]")
            self._create_channel_worker(name)

        from app.cli_pkg.tui.widgets import PromptModal

        self.app.push_screen(PromptModal("Channel name", "standup"), done)

    @work(thread=True, group="chat-new")
    def _create_channel_worker(self, name: str) -> None:
        try:
            chat = self.client.create_chat(name)
            error = ""
        except ApiError as exc:
            chat, error = {}, str(exc)
        self.app.call_from_thread(self._after_create_channel, chat, error)

    def _after_create_channel(self, chat: dict, error: str) -> None:
        if error or not chat:
            self.app.set_status(f"[red]create failed: {escape(error or 'unknown')}[/red]")
            return
        new_id = int(chat.get("id") or 0)
        self.app.refresh_workspace()
        if new_id:
            self.select_chat(new_id)
        self.app.set_status(f"[green]#{escape(str(chat.get('name') or ''))} created[/green]")
        self.composer.focus()

    def begin_kick(self, member: dict[str, Any]) -> None:
        ws = getattr(self.app, "ws", None)
        if ws is None or not ws.is_owner:
            self.app.set_status("[yellow]owner only[/yellow]")
            return
        user_id = int(member.get("user_id") or 0)
        if not user_id:
            return
        me = int((ws.me or {}).get("user_id") or 0)
        if user_id == me:
            self.app.set_status("[yellow]you can't remove yourself[/yellow]")
            return
        email = str(member.get("email") or "")
        label = escape(str(member.get("name") or email))

        def confirmed(yes: bool | None) -> None:
            if not yes:
                self.app.set_status("kick cancelled")
                return
            self._kick_worker(user_id, email)

        from app.cli_pkg.tui.widgets import ConfirmModal

        self.app.push_screen(
            ConfirmModal(
                "Remove from workspace",
                f"{label}\n{escape(email)}",
                "They lose access immediately. Their messages stay.",
                "Remove",
            ),
            confirmed,
        )

    @work(thread=True, group="chat-kick")
    def _kick_worker(self, user_id: int, email: str) -> None:
        try:
            self.client.remove_member(user_id)
            msg = f"removed {escape(email)}"
        except ApiError as exc:
            msg = f"[red]{escape(str(exc))}[/red]"
        self.app.call_from_thread(self.app.set_status, msg)
        self.app.call_from_thread(self.app.refresh_workspace)

    def action_voice_toggle(self) -> None:
        if self._recording:
            self._stop_voice_and_transcribe()
            return
        self._start_voice_or_fallback()

    def _set_recording_ui(self, on: bool) -> None:
        self._recording = on
        try:
            row = self.query_one("#composer-row", Horizontal)
        except Exception:
            row = None
        if row is not None:
            row.set_class(on, "recording")
        self.composer.set_class(on, "recording")
        self.mic_btn.set_class(on, "recording")
        self.mic_btn.label = "mic" if not on else "rec"

    def _start_voice_or_fallback(self) -> None:
        from app.cli_pkg.tui.voice import VoiceError, VoiceRecorder

        if self._voice is None:
            self._voice = VoiceRecorder()
        try:
            self._voice.start()
        except VoiceError as exc:
            self.app.set_status(f"[yellow]mic unavailable ({escape(str(exc))}) — pick audio file[/yellow]")
            self._voice_file_fallback()
            return
        self._set_recording_ui(True)
        self.app.set_status("[red]recording…[/red] mic / ctrl+m to stop")

    def _stop_voice_and_transcribe(self) -> None:
        from app.cli_pkg.tui.voice import VoiceError

        self._set_recording_ui(False)
        try:
            path = self._voice.stop() if self._voice else None
        except VoiceError as exc:
            self.app.set_status(f"[red]record failed: {escape(str(exc))}[/red]")
            return
        if path is None:
            return
        self.app.set_status("[dim]transcribing…[/dim]")
        self._transcribe_worker(str(path))

    def _voice_file_fallback(self) -> None:
        self._pick_audio_transcribe_worker()

    @work(thread=True, group="chat-voice")
    def _pick_audio_transcribe_worker(self) -> None:
        from app.cli_pkg.tui.file_picker import pick_audio_files

        try:
            paths = pick_audio_files()
        except Exception as exc:
            self.app.call_from_thread(
                self.app.set_status, f"[red]audio picker: {escape(str(exc))}[/red]"
            )
            return
        if not paths:
            self.app.call_from_thread(self.app.set_status, "voice cancelled")
            return
        self.app.call_from_thread(self.app.set_status, "[dim]transcribing…[/dim]")
        self._transcribe_sync(str(paths[0]))

    @work(thread=True, group="chat-voice")
    def _transcribe_worker(self, path: str) -> None:
        self._transcribe_sync(path)

    def _transcribe_sync(self, path: str) -> None:
        try:
            text = self.client.transcribe(path)
            error = ""
        except ApiError as exc:
            text, error = "", str(exc)
        self.app.call_from_thread(self._after_transcribe, text, error)

    def _after_transcribe(self, text: str, error: str) -> None:
        if error:
            self.app.set_status(f"[red]stt failed: {escape(error)}[/red]")
            return
        if not text:
            self.app.set_status("[yellow]empty transcript[/yellow]")
            return
        cur = self.composer.value
        if cur and not cur.endswith(" "):
            cur += " "
        self.composer.value = cur + text
        self.composer.cursor_position = len(self.composer.value)
        self.composer.focus()
        self.refresh_picker()
        self.refresh_ghost()
        self.app.set_status("[green]transcript ready[/green]")

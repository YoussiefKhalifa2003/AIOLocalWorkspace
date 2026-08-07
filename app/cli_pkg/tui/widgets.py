"""Widgets for the owner TUI."""

from __future__ import annotations

from typing import Any

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, ProgressBar, Static, TextArea


def card_badges(card: dict[str, Any], *, compact: bool = True) -> str:
    """Compact badges for the card face; keep the noisiest bits off the board."""
    bits: list[str] = []
    if card.get("status") == "agent_backlog":
        bits.append("[green]working[/green]")
    if card.get("pr_number"):
        bits.append(f"[cyan]PR #{card['pr_number']}[/cyan]")
    elif card.get("status") == "in_review":
        bits.append("[dim]no PR[/dim]")
    if card.get("open_issue_count"):
        bits.append(f"[red]{card['open_issue_count']}⚠[/red]")
    if card.get("can_merge"):
        bits.append("[yellow]merge[/yellow]")
    if not compact and card.get("repo_url"):
        bits.append("[#60a5fa]repo[/#60a5fa]")
    return " · ".join(bits)


class CardItem(ListItem):
    """One board card: bordered tile with title + short meta."""

    def __init__(self, card: dict[str, Any]) -> None:
        self.card = card
        title = escape(str(card.get("title") or ""))
        if len(title) > 42:
            title = title[:41] + "…"
        owner = escape(str(card.get("owner_email") or "").split("@")[0] or "-")
        pct = int(card.get("progress_percent") or 0)
        badges = card_badges(card)
        body = f"[b]#{card['id']}[/b]  {title}\n[dim]{owner}[/dim]  [dim]{pct}%[/dim]"
        if badges:
            body += f"\n{badges}"
        super().__init__(Static(body, markup=True, classes="card-body"))
        self.add_class("board-card")


class BoardColumn(VerticalScroll):
    """One board column with a selectable list of cards."""

    def __init__(self, status: str) -> None:
        super().__init__(id=f"col-{status}")
        self.status = status
        self.border_title = status.replace("_", " ")
        self.list_view = ListView()

    def compose(self) -> ComposeResult:
        yield self.list_view

    def set_cards(self, cards: list[dict[str, Any]]) -> None:
        index = self.list_view.index or 0
        self.list_view.clear()
        for card in cards:
            self.list_view.append(CardItem(card))
        label = self.status.replace("_", " ")
        self.border_title = f"{label} · {len(cards)}"
        if cards:
            self.list_view.index = min(index, len(cards) - 1)

    @property
    def selected(self) -> dict[str, Any] | None:
        idx = self.list_view.index
        if idx is None:
            return None
        try:
            item = self.list_view.children[idx]
        except IndexError:
            return None
        return getattr(item, "card", None)


def _progress_bar(pct: int, width: int = 16) -> str:
    pct = max(0, min(100, int(pct)))
    filled = int(round(width * pct / 100))
    return "█" * filled + "░" * (width - filled)


class DetailPane(VerticalScroll):
    """Sectioned card inspector — only shows what exists."""

    def __init__(self) -> None:
        super().__init__(id="detail")
        self.border_title = "card"
        self.header = Static("Select a card.", id="detail-header", markup=True)
        self.meta = Static("", id="detail-meta", markup=True)
        self.progress = Static("", id="detail-progress", markup=True)
        self.github = Static("", id="detail-github", markup=True)
        self.extra = Static("", id="detail-extra", markup=True)
        self.actions = Static("", id="detail-actions", markup=True)
        self._bar = ProgressBar(total=100, show_eta=False, show_percentage=False, id="detail-bar")

    def compose(self) -> ComposeResult:
        yield self.header
        yield self.meta
        yield Label("progress", classes="detail-label")
        yield self._bar
        yield self.progress
        yield Label("github", classes="detail-label")
        yield self.github
        yield self.extra
        yield self.actions

    def show(self, card: dict[str, Any] | None, *, is_owner: bool = False) -> None:
        if not card:
            self.border_title = "card"
            self.header.update("[dim]Select a card to inspect it.[/dim]")
            self.meta.update("")
            self.progress.update("")
            self.github.update("[dim]—[/dim]")
            self.extra.update("")
            self.actions.update(
                "[dim]j/k cards · h/l columns[/dim]"
                + ("\n[dim]s move · a agent · n new[/dim]" if is_owner else "")
            )
            self._bar.update(progress=0)
            return

        oid = card["id"]
        title = escape(str(card.get("title") or ""))
        status = escape(str(card.get("status") or "-").replace("_", " "))
        self.border_title = f"#{oid}"
        self.header.update(f"[b]#{oid}[/b]  {title}\n[dim]{status}[/dim]")

        owner = escape(str(card.get("owner_email") or "-"))
        blockers = int(card.get("open_issue_count") or 0)
        meta_bits = [f"[dim]owner[/dim]  {owner}"]
        if blockers:
            meta_bits.append(f"[red]{blockers} blocker{'s' if blockers != 1 else ''}[/red]")
        self.meta.update("\n".join(meta_bits))

        pct = int(card.get("progress_percent") or 0)
        closed = int(card.get("checklist_closed") or 0)
        total = int(card.get("checklist_total") or 0)
        self._bar.update(total=100, progress=pct)
        self.progress.update(
            f"{_progress_bar(pct)}  [b]{pct}%[/b]"
            + (f"  [dim]{closed}/{total} subtasks[/dim]" if total else "")
        )

        gh_lines: list[str] = []
        if card.get("repo_url"):
            gh_lines.append(f"[dim]repo[/dim]    {escape(str(card['repo_url']))}")
        if card.get("pr_url"):
            gh_lines.append(
                f"[dim]pr[/dim]      [cyan]#{card.get('pr_number') or '?'}[/cyan]  "
                f"{escape(str(card['pr_url']))}"
            )
        branch = card.get("github_branch") or card.get("branch_url")
        if branch:
            gh_lines.append(f"[dim]branch[/dim]  {escape(str(branch))}")
        if card.get("github_merged_at"):
            gh_lines.append(f"[dim]merged[/dim]  {escape(str(card['github_merged_at']))}")
        gh_lines.append(f"[dim]path[/dim]    data/workspaces/obj-{oid}")
        self.github.update("\n".join(gh_lines) if gh_lines else "[dim]no github links[/dim]")

        extra: list[str] = []
        if card.get("description"):
            extra += ["[dim]notes[/dim]", escape(str(card["description"])), ""]
        subs = card.get("subtasks") or []
        if subs:
            extra.append("[dim]subtasks[/dim]")
            extra += [
                f"  [{'x' if t.get('done') else ' '}] {escape(str(t.get('title') or ''))}"
                for t in subs
            ]
        claims = card.get("claimed_paths") or []
        if claims:
            if extra:
                extra.append("")
            extra.append("[dim]claims[/dim]")
            extra += [f"  {escape(str(p))}" for p in claims]
        self.extra.update("\n".join(extra).rstrip())

        hints: list[str] = ["[dim]j/k[/dim] cards  [dim]h/l[/dim] columns"]
        if is_owner:
            hints.append("[dim]s[/dim] move  [dim]a[/dim] agent  [dim]n[/dim] new")
            if card.get("can_merge"):
                hints.append("[yellow]m[/yellow] merge & done")
            elif card.get("pr_url"):
                hints.append("[dim]o[/dim] open PR  [dim]y[/dim] copy")
        elif card.get("pr_url"):
            hints.append("[dim]o[/dim] open PR  [dim]y[/dim] copy")
        else:
            hints.append("[dim]view only · owner moves cards[/dim]")
        self.actions.update("\n".join(hints))


class ObjectiveSetupModal(ModalScreen[dict[str, Any] | None]):
    """Post-create brief: description + optional subtasks (or skip)."""

    BINDINGS = [("escape", "skip", "Skip")]

    def __init__(self, objective_id: int, title: str) -> None:
        super().__init__()
        self.objective_id = objective_id
        self._title = title
        self._sub_count = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-box"):
            yield Label(f"New objective #{self.objective_id}", id="confirm-title")
            yield Static(
                f"[b]{escape(self._title)}[/b]\n"
                "[dim]Optional — add a short brief and subtasks, or skip.[/dim]",
                markup=True,
            )
            yield Label("Description")
            yield TextArea(id="setup-desc")
            with Horizontal(id="setup-subs-head"):
                yield Label("Subtasks")
                yield Button("+ add", id="setup-add-sub")
            yield Vertical(id="setup-subs")
            with Horizontal(id="setup-actions"):
                yield Button("Save", variant="primary", id="setup-save")
                yield Button("Skip", id="setup-skip")

    def on_mount(self) -> None:
        self.query_one("#setup-desc", TextArea).focus()

    def _add_sub(self) -> None:
        self._sub_count += 1
        box = self.query_one("#setup-subs", Vertical)
        row = Horizontal(classes="setup-sub-row")
        inp = Input(placeholder="Subtask…", id=f"setup-sub-{self._sub_count}")
        rm = Button("×", id=f"setup-rm-{self._sub_count}", classes="setup-rm")
        box.mount(row)
        row.mount(inp)
        row.mount(rm)
        inp.focus()

    def _collect_subtasks(self) -> list[str]:
        out: list[str] = []
        for inp in self.query("#setup-subs Input"):
            val = str(getattr(inp, "value", "") or "").strip()
            if val:
                out.append(val)
        return out

    def _save(self) -> None:
        desc = self.query_one("#setup-desc", TextArea).text
        self.dismiss({"dismiss": False, "description": desc, "subtasks": self._collect_subtasks()})

    def action_skip(self) -> None:
        self.dismiss({"dismiss": True})

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "setup-add-sub":
            self._add_sub()
            return
        if bid.startswith("setup-rm-"):
            row = event.button.parent
            if row is not None:
                row.remove()
            return
        if bid == "setup-save":
            self._save()
            return
        if bid == "setup-skip":
            self.dismiss({"dismiss": True})


class InviteEmailModal(ModalScreen[dict[str, Any] | None]):
    """Ask for a @tatweermea.com address (+ seats), then email the invite via Outlook."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, domain: str = "tatweermea.com") -> None:
        super().__init__()
        self.domain = domain

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label("Email invite", id="confirm-title")
            yield Static(
                f"[dim]Only @{escape(self.domain)} addresses. "
                "Outlook Web sends the mail (free — no SMTP billing).[/dim]",
                markup=True,
            )
            yield Input(placeholder=f"colleague@{self.domain}", id="invite-email")
            yield Input(value="1", placeholder="seats (1-50)", id="invite-seats")
            with Horizontal():
                yield Button("Send invite", variant="primary", id="invite-send")
                yield Button("Link only", id="invite-link-only")
                yield Button("Cancel", id="invite-cancel")

    def on_mount(self) -> None:
        self.query_one("#invite-email", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "invite-cancel":
            self.dismiss(None)
            return
        email = self.query_one("#invite-email", Input).value.strip()
        seats_raw = self.query_one("#invite-seats", Input).value.strip() or "1"
        try:
            seats = max(1, min(50, int(seats_raw)))
        except ValueError:
            seats = 1
        if bid == "invite-link-only":
            self.dismiss({"email": "", "seats": seats, "send_email": False})
            return
        if not email:
            self.query_one("#invite-email", Input).focus()
            return
        self.dismiss({"email": email, "seats": seats, "send_email": True})

    def on_input_submitted(self) -> None:
        email = self.query_one("#invite-email", Input).value.strip()
        seats_raw = self.query_one("#invite-seats", Input).value.strip() or "1"
        try:
            seats = max(1, min(50, int(seats_raw)))
        except ValueError:
            seats = 1
        if not email:
            self.query_one("#invite-email", Input).focus()
            return
        self.dismiss({"email": email, "seats": seats, "send_email": True})

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """Yes/no gate for irreversible actions."""

    BINDINGS = [("escape", "dismiss_false", "Cancel")]

    def __init__(self, title: str, detail: str, warning: str, confirm_label: str) -> None:
        super().__init__()
        self._title = title
        self._detail = detail
        self._warning = warning
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._title, id="confirm-title")
            yield Static(self._detail, markup=True)
            yield Static(f"[yellow]{self._warning}[/yellow]", markup=True)
            yield Button(self._confirm_label, variant="error", id="confirm-yes")
            yield Button("Cancel", id="confirm-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_dismiss_false(self) -> None:
        self.dismiss(False)


class ChoiceModal(ModalScreen[str]):
    """Pick one option (status column, coding runner)."""

    BINDINGS = [("escape", "dismiss_empty", "Cancel")]

    def __init__(self, title: str, options: list[str]) -> None:
        super().__init__()
        self._title = title
        self._options = options

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._title, id="confirm-title")
            lv = ListView(*[ListItem(Static(o)) for o in self._options], id="choice-list")
            yield lv

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index or 0
        self.dismiss(self._options[idx] if idx < len(self._options) else "")

    def action_dismiss_empty(self) -> None:
        self.dismiss("")


class PromptModal(ModalScreen[str]):
    """Ask for one line of text."""

    BINDINGS = [("escape", "dismiss_empty", "Cancel")]

    def __init__(self, title: str, placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._title, id="confirm-title")
            yield Input(placeholder=self._placeholder, id="prompt-input")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_dismiss_empty(self) -> None:
        self.dismiss("")


HELP_TEXT = """[b]Tabs[/b] — press the letter, or click the tab
  c  Chat        b  Board        g  Agents
  p  People      d  Dashboard    v  Live
  (1-6 work too)

  While you are typing a message, letters are just letters. Use
  ctrl+t chat · ctrl+b board · ctrl+g agents · ctrl+e people ·
  ctrl+d dash · ctrl+v live, or press [b]esc[/b] then the letter.

[b]Anywhere[/b]
  ?  help (or ctrl+w)   ctrl+n  unread mentions
  r  refresh (ctrl+r)   q  quit (ctrl+q)

[b]Chat[/b]
  Type [b]/[/b] [b]![/b] or [b]@[/b] — a dropdown opens (just like the website).
  up/down to choose, enter or tab to pick, esc to close.
  /ask /deepresearch /code /write /review /checklist /status /clear
  !add !list !set !done !claim !issue !invite !help
  @name pings a person · @team pings everyone

[b]Board[/b]
  j k          card up/down     h l    column left/right
  Owner only:  n new · s move · a agent · m merge
  Anyone:      o open PR · y copy PR link
  After n / !add a setup popup asks for description + subtasks.

[b]People[/b]  owners: email invite (@domain only via Outlook), roles, remove.
[b]Agents[/b]  pick the model behind each /skill, then Save.
[b]Dashboard[/b]  owner-only tables: people, models, tokens, open work.
[b]Live[/b]  owner-only charts: gauges, sparklines, WIP bars (polls every 2s).
"""


class HelpModal(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss_none", "Close"), ("question_mark", "dismiss_none", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Label("AIO — keys and commands", id="confirm-title")
            yield VerticalScroll(Static(HELP_TEXT, markup=True))
            yield Button("Close", id="help-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class MentionsModal(ModalScreen[int]):
    """Unread @mentions; picking one returns its chat id."""

    BINDINGS = [("escape", "dismiss_zero", "Close")]

    def __init__(self, mentions: list[dict[str, Any]]) -> None:
        super().__init__()
        self._mentions = mentions

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(f"Mentions ({len(self._mentions)})", id="confirm-title")
            if not self._mentions:
                yield Static("[dim]nothing unread[/dim]", markup=True)
                yield Button("Close", id="mentions-close")
                return
            items = []
            for m in self._mentions:
                who = escape(str(m.get("from") or "?"))
                where = escape(str(m.get("chat_name") or ""))
                snippet = escape(str(m.get("snippet") or "").replace("\n", " ")[:70])
                items.append(
                    ListItem(Static(f"[b]{who}[/b] [dim]#{where}[/dim]\n{snippet}", markup=True))
                )
            yield ListView(*items, id="mention-list")
            yield Button("Mark all read", id="mentions-read")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index or 0
        if idx < len(self._mentions):
            self.dismiss(int(self._mentions[idx].get("chat_id") or 0))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(-1 if event.button.id == "mentions-read" else 0)

    def action_dismiss_zero(self) -> None:
        self.dismiss(0)

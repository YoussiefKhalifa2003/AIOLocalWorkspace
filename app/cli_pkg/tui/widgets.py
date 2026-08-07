"""Widgets for the owner TUI."""

from __future__ import annotations

from typing import Any

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Link, ListItem, ListView, ProgressBar, Static, TextArea


def card_badges(card: dict[str, Any]) -> str:
    """At most one status cue on the card face — rest lives in the detail pane."""
    if card.get("can_merge"):
        return "[yellow]mergeable[/yellow]"
    if card.get("status") == "agent_backlog":
        return "[green]working[/green]"
    if card.get("pr_number"):
        return f"[cyan]PR #{card['pr_number']}[/cyan]"
    if card.get("open_issue_count"):
        return f"[red]{card['open_issue_count']} blocker[/red]"
    return ""


class CardItem(ListItem):
    """One board card: airy tile — title + owner, optional single badge."""

    def __init__(self, card: dict[str, Any]) -> None:
        self.card = card
        title = escape(str(card.get("title") or ""))
        if len(title) > 36:
            title = title[:35] + "…"
        owner = escape(str(card.get("owner_email") or "").split("@")[0] or "-")
        pct = int(card.get("progress_percent") or 0)
        closed = int(card.get("checklist_closed") or 0)
        total = int(card.get("checklist_total") or 0)
        badge = card_badges(card)
        meta = f"[dim]{owner} · {pct}%[/dim]"
        if total:
            meta += f"  [dim]{closed}/{total} tasks[/dim]"
        body = f"[b]#{card['id']}[/b]  {title}\n{meta}"
        if badge:
            body += f"  {badge}"
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


def _progress_bar(pct: int, width: int = 14) -> str:
    pct = max(0, min(100, int(pct)))
    filled = int(round(width * pct / 100))
    return "█" * filled + "░" * (width - filled)


def _short_url(url: str, limit: int = 42) -> str:
    text = (url or "").removeprefix("https://").removeprefix("http://")
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


class DetailPane(VerticalScroll):
    """Card inspector with clickable GitHub links; can be hidden with i / Hide."""

    def __init__(self) -> None:
        super().__init__(id="detail")
        self.border_title = "detail"
        self._card: dict[str, Any] | None = None
        self._can_edit = False
        self.header = Static("Select a card.", id="detail-header", markup=True)
        self.meta = Static("", id="detail-meta", markup=True)
        self.progress = Static("", id="detail-progress", markup=True)
        self.desc = Static("", id="detail-desc", markup=True)
        self.subs = Static("", id="detail-subs", markup=True)
        self.links = Vertical(id="detail-links")
        self.actions = Static("", id="detail-actions", markup=True)
        self._bar = ProgressBar(total=100, show_eta=False, show_percentage=False, id="detail-bar")
        self._prog_label = Label("progress", classes="detail-label", id="detail-prog-label")
        self._desc_label = Label("description", classes="detail-label", id="detail-desc-label")
        self._subs_label = Label("subtasks", classes="detail-label", id="detail-subs-label")
        self._links_label = Label("links", classes="detail-label", id="detail-links-label")

    def compose(self) -> ComposeResult:
        with Horizontal(id="detail-toolbar"):
            yield Static("[b]detail[/b]", id="detail-title", markup=True)
            yield Button("Edit", id="detail-edit")
            yield Button("Hide", id="detail-hide")
        yield self.header
        yield self.meta
        yield self._prog_label
        yield self._bar
        yield self.progress
        yield self._desc_label
        yield self.desc
        yield self._subs_label
        yield self.subs
        yield self._links_label
        yield self.links
        yield self.actions

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        parent = self.parent
        if bid == "detail-hide":
            if parent is not None and hasattr(parent, "toggle_detail"):
                parent.toggle_detail()  # type: ignore[union-attr]
            event.stop()
            return
        if bid == "detail-edit":
            if parent is not None and hasattr(parent, "edit_card"):
                parent.edit_card()  # type: ignore[union-attr]
            event.stop()

    def show(
        self,
        card: dict[str, Any] | None,
        *,
        is_owner: bool = False,
        can_edit: bool = False,
    ) -> None:
        self._card = card
        self._can_edit = can_edit
        try:
            self.links.remove_children()
        except Exception:  # noqa: BLE001
            pass

        try:
            self.query_one("#detail-edit", Button).display = bool(card and can_edit)
        except Exception:  # noqa: BLE001
            pass

        if not card:
            self.border_title = "detail"
            self.header.update("[dim]Click a card, or use j/k · h/l.[/dim]")
            self.meta.update("")
            self.progress.update("")
            self.desc.update("")
            self.subs.update("")
            self.actions.update(
                "[dim]i[/dim] hide/show detail"
                + ("  [dim]n[/dim] new" if is_owner else "")
            )
            self._bar.update(progress=0)
            self._links_label.display = False
            return

        oid = card["id"]
        title = escape(str(card.get("title") or ""))
        status = escape(str(card.get("status") or "-").replace("_", " "))
        self.border_title = f"#{oid}"
        self.header.update(f"[b]#{oid}[/b]  {title}\n[dim]{status}[/dim]")

        owner = escape(str(card.get("owner_email") or "-"))
        blockers = int(card.get("open_issue_count") or 0)
        meta = f"[dim]owner[/dim]  {owner}"
        if blockers:
            meta += f"\n[red]{blockers} blocker{'s' if blockers != 1 else ''}[/red]"
        self.meta.update(meta)

        pct = int(card.get("progress_percent") or 0)
        closed = int(card.get("checklist_closed") or 0)
        total = int(card.get("checklist_total") or 0)
        self._bar.update(total=100, progress=pct)
        self.progress.update(
            f"{_progress_bar(pct)}  [b]{pct}%[/b]"
            + (f"  [dim]{closed}/{total}[/dim]" if total else "  [dim]no subtasks yet[/dim]")
        )

        desc = str(card.get("description") or "").strip()
        self.desc.update(escape(desc) if desc else "[dim]No description yet.[/dim]")

        subs = card.get("subtasks") or []
        if subs:
            self.subs.update(
                "\n".join(
                    f"  [{'x' if t.get('done') else ' '}] {escape(str(t.get('title') or ''))}"
                    for t in subs
                )
            )
        else:
            self.subs.update("[dim]No subtasks yet.[/dim]")

        repo = str(card.get("repo_url") or "").strip()
        pr = str(card.get("pr_url") or "").strip()
        branch = str(card.get("github_branch") or "").strip()
        branch_url = str(card.get("branch_url") or "").strip()
        if not branch_url and repo and branch:
            branch_url = f"{repo.rstrip('/')}/tree/{branch}"

        try:
            if repo:
                self.links.mount(
                    Link(f"repo  {_short_url(repo)}", url=repo, tooltip=repo, classes="detail-link")
                )
            if pr:
                label = f"PR #{card.get('pr_number') or '?'}  {_short_url(pr)}"
                self.links.mount(Link(label, url=pr, tooltip=pr, classes="detail-link"))
            if branch_url:
                blabel = branch or _short_url(branch_url)
                self.links.mount(
                    Link(
                        f"branch  {blabel}",
                        url=branch_url,
                        tooltip=branch_url,
                        classes="detail-link",
                    )
                )
            self.links.mount(
                Static(f"[dim]path[/dim]  data/workspaces/obj-{oid}", markup=True)
            )
        except Exception:  # noqa: BLE001
            pass
        self._links_label.display = True

        hints = ["[dim]i[/dim] hide  [dim]g[/dim] repo  [dim]o[/dim] PR"]
        if can_edit:
            hints.append("[cyan]e[/cyan] edit description & subtasks")
        if is_owner:
            hints.append("[dim]s[/dim] move  [dim]a[/dim] agent  [dim]n[/dim] new")
            if card.get("can_merge"):
                hints.append("[yellow]m[/yellow] merge & done")
        elif not can_edit:
            hints.append("[dim]view only on others' cards[/dim]")
        self.actions.update("\n".join(hints))


class ObjectiveSetupModal(ModalScreen[dict[str, Any] | None]):
    """Create/edit description + subtasks (Skip only on first create)."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        objective_id: int,
        title: str,
        *,
        description: str = "",
        subtasks: list[str] | None = None,
        editing: bool = False,
    ) -> None:
        super().__init__()
        self.objective_id = objective_id
        self._title = title
        self._description = description or ""
        self._initial_subs = [s for s in (subtasks or []) if str(s).strip()]
        self._editing = editing
        self._sub_count = 0

    def compose(self) -> ComposeResult:
        head = "Edit objective" if self._editing else "New objective"
        hint = (
            "Update the brief and subtasks, then Save."
            if self._editing
            else "Optional — add a short brief and subtasks, or skip."
        )
        with Vertical(id="setup-box"):
            yield Label(f"{head} #{self.objective_id}", id="confirm-title")
            yield Static(
                f"[b]{escape(self._title)}[/b]\n[dim]{hint}[/dim]",
                markup=True,
            )
            yield Label("Description")
            yield TextArea(self._description, id="setup-desc")
            with Horizontal(id="setup-subs-head"):
                yield Label("Subtasks")
                yield Button("+ add", id="setup-add-sub")
            yield Vertical(id="setup-subs")
            with Horizontal(id="setup-actions"):
                yield Button("Save", variant="primary", id="setup-save")
                yield Button("Cancel" if self._editing else "Skip", id="setup-skip")

    def on_mount(self) -> None:
        for title in self._initial_subs:
            self._add_sub(title)
        if not self._initial_subs and self._editing:
            self._add_sub("")
        self.query_one("#setup-desc", TextArea).focus()

    def _add_sub(self, value: str = "") -> None:
        self._sub_count += 1
        box = self.query_one("#setup-subs", Vertical)
        row = Horizontal(classes="setup-sub-row")
        inp = Input(value=value, placeholder="Subtask…", id=f"setup-sub-{self._sub_count}")
        rm = Button("×", id=f"setup-rm-{self._sub_count}", classes="setup-rm")
        box.mount(row)
        row.mount(inp)
        row.mount(rm)
        if not value:
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

    def action_cancel(self) -> None:
        if self._editing:
            self.dismiss(None)
        else:
            self.dismiss({"dismiss": True})

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "setup-add-sub":
            self._add_sub("")
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
            self.action_cancel()


class InviteEmailModal(ModalScreen[dict[str, Any] | None]):
    """Ask for an email (+ seats), then send the invite via Outlook."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, domain: str = "") -> None:
        super().__init__()
        self.domain = (domain or "").lstrip("@")

    def compose(self) -> ComposeResult:
        if self.domain:
            hint = (
                f"[dim]Only @{escape(self.domain)} addresses. "
                "Outlook Web sends the mail (free — no SMTP billing).[/dim]"
            )
            placeholder = f"colleague@{self.domain}"
        else:
            hint = (
                "[dim]Any email. Outlook Web sends the mail "
                "(free — no SMTP billing).[/dim]"
            )
            placeholder = "colleague@email.com"
        with Vertical(id="confirm-box"):
            yield Label("Email invite", id="confirm-title")
            yield Static(hint, markup=True)
            yield Input(placeholder=placeholder, id="invite-email")
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
  click a card to update detail · i hide/show detail
  e            edit description & subtasks (your cards; owners: any)
  Owner only:  n new · s move · a agent · m merge
  Anyone:      g open repo · o open PR · y copy PR
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

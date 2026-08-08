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

    def __init__(self, title: str, placeholder: str = "", *, value: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self._title, id="confirm-title")
            yield Input(
                value=self._value,
                placeholder=self._placeholder,
                id="prompt-input",
            )

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_dismiss_empty(self) -> None:
        self.dismiss("")


class CreateChatModal(ModalScreen[dict[str, Any] | None]):
    """Create a chat: name + visibility (owner) + ops vs LLM mode."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, *, is_owner: bool) -> None:
        super().__init__()
        self._is_owner = is_owner
        self._kind = "channel" if is_owner else "private"
        self._mode = "ops"

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label("New chat", id="confirm-title")
            if self._is_owner:
                yield Static(
                    "[dim]Public = whole team · Private = only you[/dim]",
                    markup=True,
                )
            else:
                yield Static(
                    "[dim]Members create private chats only (only you can see them).[/dim]",
                    markup=True,
                )
            yield Input(placeholder="name (e.g. standup)", id="create-name")
            with Horizontal(id="create-vis-row"):
                yield Button(
                    "Public" if self._is_owner else "Private (locked)",
                    id="create-kind",
                    variant="primary" if self._is_owner else "default",
                    disabled=not self._is_owner,
                )
            yield Static(
                "[dim]Purpose: ! board commands only, or / AI skills[/dim]",
                markup=True,
            )
            with Horizontal(id="create-mode-row"):
                yield Button("! commands", id="create-mode-ops", variant="primary")
                yield Button("/ AI skills", id="create-mode-llm")
            with Horizontal():
                yield Button("Create", variant="primary", id="create-go")
                yield Button("Cancel", id="create-cancel")

    def on_mount(self) -> None:
        self.query_one("#create-name", Input).focus()
        self._paint_kind()
        self._paint_mode()

    def _paint_kind(self) -> None:
        btn = self.query_one("#create-kind", Button)
        if not self._is_owner:
            btn.label = "Private (only you)"
            return
        if self._kind == "channel":
            btn.label = "Public · everyone"
            btn.variant = "primary"
        else:
            btn.label = "Private · only you"
            btn.variant = "default"

    def _paint_mode(self) -> None:
        ops = self.query_one("#create-mode-ops", Button)
        llm = self.query_one("#create-mode-llm", Button)
        if self._mode == "ops":
            ops.variant = "primary"
            llm.variant = "default"
        else:
            ops.variant = "default"
            llm.variant = "primary"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "create-cancel":
            self.dismiss(None)
            return
        if bid == "create-kind" and self._is_owner:
            self._kind = "private" if self._kind == "channel" else "channel"
            self._paint_kind()
            return
        if bid == "create-mode-ops":
            self._mode = "ops"
            self._paint_mode()
            return
        if bid == "create-mode-llm":
            self._mode = "llm"
            self._paint_mode()
            return
        if bid == "create-go":
            name = self.query_one("#create-name", Input).value.strip()
            if not name:
                return
            self.dismiss({"name": name, "kind": self._kind, "mode": self._mode})

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "create-name":
            name = event.value.strip()
            if name:
                self.dismiss({"name": name, "kind": self._kind, "mode": self._mode})

    def action_cancel(self) -> None:
        self.dismiss(None)


class MessageEditModal(ModalScreen[str | None]):
    """Multi-line edit for an own chat message."""

    BINDINGS = [("escape", "dismiss_none", "Cancel")]

    def __init__(self, body: str) -> None:
        super().__init__()
        self._body = body or ""

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label("Edit message", id="confirm-title")
            yield Static(
                "[dim]Later messages stay. Following AI replies to this line are "
                "removed and re-run if needed.[/dim]",
                markup=True,
            )
            area = TextArea(self._body, id="edit-body")
            yield area
            with Horizontal():
                yield Button("Save", variant="primary", id="edit-save")
                yield Button("Cancel", id="edit-cancel")

    def on_mount(self) -> None:
        self.query_one("#edit-body", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "edit-save":
            self.dismiss(self.query_one("#edit-body", TextArea).text)
        else:
            self.dismiss(None)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


HELP_TEXT = """[b]Tabs[/b] — press the letter, or click the tab
  c  Chat        b  Board        g  Agents
  p  People      d  Dashboard    v  Live
  (1-6 work too)

  While you are typing a message, letters are just letters. Use
  ctrl+t chat · ctrl+b board · ctrl+g agents · ctrl+e people ·
  ctrl+d dash · ctrl+v live, or press [b]esc[/b] then the letter.

[b]Anywhere[/b]
  ?  help (or ctrl+w)   ctrl+n  unread mentions
  F1 or slim [b]Tour[/b]  spotlight walkthrough
  [b]Log out[/b] (tabs row) or [b]ctrl+shift+l[/b]
  r  refresh (ctrl+r)   q  quit (ctrl+q)

[b]Chat[/b]
  Type [b]/[/b] [b]![/b] or [b]@[/b] — a dropdown opens (just like the website).
  up/down to choose, enter or tab to pick, esc to close.
  Ghost line above the box shows the selected completion or next arg.
  /ask /deepresearch /code /write /review /checklist /status /clear
  !add !list !set !done !claim !issue !invite !attach !attach-clear !help
  @name pings a person · @team pings everyone
  Click [b]+[/b] on the left of the composer (or [b]ctrl+f[/b] / [b]!attach[/b]) —
  same shell on Mac, Windows, and Linux — opens a native file dialog
  (code, pdf, docx, images…). Then send your message / skill.
  [b]mic[/b] on the right of the composer (or [b]ctrl+m[/b]) records / picks audio.
  Non-image attachments: focus the 📎 row, [b]enter[/b] or [b]o[/b] to open
  in your default app.
  Messages group by speaker (~4 min): one name + colored rail per turn.
  Own messages: hover a line — [b]edit[/b] / [b]delete[/b] appear on the
  right. Edited lines show a dim [b]· edited[/b] tag. Keys e / delete still
  work when the line is focused. Editing never deletes later user messages;
  only following AI replies to that line are cleared/re-run.
  [b]+ channel[/b] or [b]ctrl+shift+n[/b] creates a room (!newchat still works).
  [b]ctrl+m[/b] voice → mic (or audio file) → fills the composer via STT.
  Owner: [b]kick[/b] next to a name under MEMBERS removes them from the workspace.
  When someone [@]pings you: a sound plays and [@]N appears in the status
  line. Press [b]ctrl+n[/b] to open the list (who · time · chat · snippet),
  pick one to jump to that message (highlighted).

[b]Board[/b]
  j k          card up/down     h l    column left/right
  [ ]          shift card to previous / next column (DnD stand-in)
  click a card to update detail · i hide/show detail
  e            edit description & subtasks (your cards; owners: any)
  Owner only:  n new · s move · a agent · m merge · [ ] column-shift
  Anyone:      g open repo · o open PR · y copy PR
  After n / !add a setup popup asks for description + subtasks.

[b]People[/b]  owners: email invite (@domain only via Outlook), roles, remove.
[b]Agents[/b]  pick the model behind each /skill, then Save.
[b]Dashboard[/b]  owner-only tables: people, models, tokens, open work.
  Assign strip: pick open task + teammate → Assign.
[b]Live[/b]  owner-only charts: gauges, sparklines, WIP bars (polls every 2s).

[b]Mac smoke[/b]  Terminal/iTerm may ask for mic (ctrl+m); file STT always works.
  Open PDF/code attachments with enter/o. Board [ ] needs owner. Tour: F1.
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


def _mention_time_label(created_at: str | None) -> str:
    """Format mention created_at ISO as HH:MM (or Y-m-d HH:MM if not today)."""
    if not created_at:
        return ""
    try:
        from datetime import datetime

        raw = str(created_at).replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is not None:
            local = dt.astimezone()
        else:
            local = dt
        now = datetime.now(local.tzinfo) if local.tzinfo else datetime.now()
        if local.date() == now.date():
            return local.strftime("%H:%M")
        return local.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(created_at)[:16]


class MentionsModal(ModalScreen[dict[str, Any] | None]):
    """Unread @mentions; picking one opens that chat message."""

    BINDINGS = [("escape", "dismiss_none", "Close")]

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
                when = escape(_mention_time_label(m.get("created_at")))
                meta = f"{when} · #{where}" if when else f"#{where}"
                snippet = escape(str(m.get("snippet") or "").replace("\n", " ")[:70])
                items.append(
                    ListItem(Static(f"[b]{who}[/b]  [dim]{meta}[/dim]\n{snippet}", markup=True))
                )
            yield ListView(*items, id="mention-list")
            yield Button("Mark all read", id="mentions-read")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index or 0
        if idx < len(self._mentions):
            m = self._mentions[idx]
            self.dismiss(
                {
                    "action": "open",
                    "chat_id": int(m.get("chat_id") or 0),
                    "message_id": int(m.get("message_id") or 0),
                    "mention_id": int(m.get("id") or 0),
                }
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "mentions-read":
            self.dismiss({"action": "mark_all"})
        else:
            self.dismiss(None)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

"""Widgets for the owner TUI."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

BADGE_ORDER = ("working", "pr", "repo", "branch", "blockers")


def card_badges(card: dict[str, Any]) -> str:
    bits: list[str] = []
    if card.get("status") == "agent_backlog":
        bits.append("[green]agent working[/green]")
    if card.get("pr_number"):
        bits.append(f"[cyan]PR #{card['pr_number']}[/cyan]")
    elif card.get("status") == "in_review":
        bits.append("[dim]no PR yet[/dim]")
    if card.get("repo_url"):
        bits.append("[#60a5fa]repo[/#60a5fa]")
    if card.get("github_branch"):
        branch = str(card["github_branch"]).split("/")[-1]
        bits.append(f"[#c084fc]{branch[:14]}[/#c084fc]")
    if card.get("open_issue_count"):
        bits.append(f"[red]{card['open_issue_count']} blocker[/red]")
    if card.get("can_merge"):
        bits.append("[yellow]mergeable[/yellow]")
    return "  ".join(bits)


class CardItem(ListItem):
    def __init__(self, card: dict[str, Any]) -> None:
        from rich.markup import escape

        self.card = card
        title = escape(str(card.get("title") or ""))
        owner = escape(str(card.get("owner_email") or "").split("@")[0])
        pct = card.get("progress_percent") or 0
        badges = card_badges(card)
        body = f"[b]#{card['id']}[/b]  {title}\n[dim]{owner} · {pct}%[/dim]"
        if badges:
            body += f"\n{badges}"
        super().__init__(Static(body, markup=True))


class BoardColumn(VerticalScroll):
    """One board column with a selectable list of cards."""

    def __init__(self, status: str) -> None:
        super().__init__(id=f"col-{status}")
        self.status = status
        self.border_title = status
        self.list_view = ListView()

    def compose(self) -> ComposeResult:
        yield self.list_view

    def set_cards(self, cards: list[dict[str, Any]]) -> None:
        index = self.list_view.index or 0
        self.list_view.clear()
        for card in cards:
            self.list_view.append(CardItem(card))
        self.border_title = f"{self.status} ({len(cards)})"
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


class DetailPane(VerticalScroll):
    def __init__(self) -> None:
        super().__init__(id="detail")
        self.border_title = "detail"
        self.body = Static("Select a card.", markup=True)

    def compose(self) -> ComposeResult:
        yield self.body

    def show(self, card: dict[str, Any] | None) -> None:
        if not card:
            self.body.update("Select a card.")
            return
        lines = [
            f"[b]#{card['id']} {card.get('title') or ''}[/b]",
            f"[dim]{card.get('status')}[/dim]",
            "",
        ]
        if card.get("description"):
            lines += [str(card["description"]), ""]
        lines += [
            f"owner:     {card.get('owner_email') or '-'}",
            f"progress:  {card.get('progress_percent', 0)}% "
            f"({card.get('checklist_closed', 0)}/{card.get('checklist_total', 0)})",
            f"blockers:  {card.get('open_issue_count', 0)}",
            f"repo:      {card.get('repo_url') or '-'}",
            f"pr:        {card.get('pr_url') or '-'}",
            f"branch:    {card.get('branch_url') or card.get('github_branch') or '-'}",
            f"merged:    {card.get('github_merged_at') or '-'}",
            f"workspace: data/workspaces/obj-{card['id']}",
        ]
        subs = card.get("subtasks") or []
        if subs:
            lines += ["", "subtasks:"]
            lines += [f"  [{'x' if t['done'] else ' '}] {t['title']}" for t in subs]
        claims = card.get("claimed_paths") or []
        if claims:
            lines += ["", "claims:"] + [f"  {p}" for p in claims]
        self.body.update("\n".join(lines))


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
  p  People      d  Dashboard    (1-5 work too)

  While you are typing a message, letters are just letters. Use
  ctrl+t chat · ctrl+b board · ctrl+g agents · ctrl+e people · ctrl+d dash,
  or press [b]esc[/b] to step out of the message box and use plain letters.

[b]Anywhere[/b]
  ?  help (ctrl+w)      @  mentions (ctrl+n)
  r  refresh (ctrl+r)   q  quit (ctrl+q)

[b]Chat[/b]
  Type and press enter. Typing [b]/[/b] [b]![/b] or [b]@[/b] opens a menu:
  up/down to choose, enter or tab to pick, esc to close.
  /ask /deepresearch /code /write /review /checklist /status /clear
  !add !list !set !done !claim !issue !invite !help
  @name pings a person · @team pings everyone

[b]Board[/b]
  j k          card up/down     h l    column left/right
  n            new objective    s      move to a status
  a            hand to a coding agent
  m            merge the PR and finish the card (owner)
  o            open PR in a browser      y  copy PR link

[b]People[/b]  owners: make owner / make member / remove, and mint invite links.
[b]Agents[/b]  pick the model behind each /skill, then Save.
[b]Dashboard[/b]  owner-only: people, models, tokens, open work.
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
        from rich.markup import escape

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

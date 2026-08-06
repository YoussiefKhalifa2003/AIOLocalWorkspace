"""Widgets for the owner TUI."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView, Static

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
        bits.append("[blue]repo[/blue]")
    if card.get("github_branch"):
        branch = str(card["github_branch"])
        bits.append(f"[magenta]{branch[:18]}[/magenta]")
    if card.get("open_issue_count"):
        bits.append(f"[red]{card['open_issue_count']} blocker[/red]")
    if card.get("can_merge"):
        bits.append("[yellow]mergeable[/yellow]")
    return "  ".join(bits)


class CardItem(ListItem):
    def __init__(self, card: dict[str, Any]) -> None:
        self.card = card
        title = str(card.get("title") or "")
        owner = str(card.get("owner_email") or "")
        pct = card.get("progress_percent") or 0
        badges = card_badges(card)
        body = f"[b]#{card['id']}[/b] {title}\n[dim]{owner} · {pct}%[/dim]"
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

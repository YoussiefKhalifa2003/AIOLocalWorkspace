"""Board tab: columns, card detail, agent dispatch, confirm-merge, objective setup."""

from __future__ import annotations

import webbrowser
from typing import Any

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, HorizontalScroll
from textual.widgets import Input, ListView

from app.cli_pkg.tui.client import ApiClient, ApiError, board_fingerprint
from app.cli_pkg.tui.widgets import (
    BoardColumn,
    ChoiceModal,
    ConfirmModal,
    DetailPane,
    ObjectiveSetupModal,
    PromptModal,
)
from app.config import get_settings
from app.services.board import BOARD_COLUMNS

MERGE_WARNING = "Merging into the default branch cannot be easily undone."


class BoardView(Horizontal):
    def __init__(self, client: ApiClient) -> None:
        super().__init__(id="board")
        self.client = client
        self.columns: dict[str, BoardColumn] = {}
        self.detail = DetailPane()
        self.board: dict[str, Any] = {}
        self._fingerprint = ""
        self._order = list(BOARD_COLUMNS)
        self._index = 0
        self._detail_open = True
        self._applying = False

    @property
    def is_owner(self) -> bool:
        return bool(getattr(self.app, "ws", None) and self.app.ws.is_owner)

    @property
    def my_user_id(self) -> int:
        ws = getattr(self.app, "ws", None)
        if ws is None:
            return 0
        return int(ws.me.get("user_id") or 0)

    def can_edit_card(self, card: dict[str, Any] | None) -> bool:
        """Workspace owners edit any card; members only their own."""
        if not card:
            return False
        if self.is_owner:
            return True
        me = self.my_user_id
        if not me:
            return False
        return me in {
            int(card.get("user_id") or 0),
            int(card.get("assignee_user_id") or 0),
        }

    def _show_detail(self, card: dict[str, Any] | None = None) -> None:
        card = self.current_card if card is None else card
        self.detail.show(
            card,
            is_owner=self.is_owner,
            can_edit=self.can_edit_card(card),
        )

    def compose(self) -> ComposeResult:
        with HorizontalScroll(id="columns"):
            for status in self._order:
                col = BoardColumn(status)
                self.columns[status] = col
                yield col
        yield self.detail

    def on_mount(self) -> None:
        self.detail.display = self._detail_open

    def toggle_detail(self) -> None:
        self._detail_open = not self._detail_open
        self.detail.display = self._detail_open
        self.app.set_status("detail shown" if self._detail_open else "detail hidden | press i to show")

    def _sync_column_from_list(self, list_view: ListView) -> bool:
        """When the user clicks a card, track which column it belongs to."""
        for i, status in enumerate(self._order):
            if self.columns[status].list_view is list_view:
                self._index = i
                return True
        return False

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        # Ignore rebuild noise: only react when the user is focused in that column.
        if self._applying or not event.list_view.has_focus:
            return
        if not self._sync_column_from_list(event.list_view):
            return
        self._show_detail()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self._applying:
            return
        if not self._sync_column_from_list(event.list_view):
            return
        if not self._detail_open:
            self.toggle_detail()
        self._show_detail()

    def apply(self, board: dict[str, Any], jobs_today: int) -> None:
        self.board = board
        fp = board_fingerprint(board, jobs_today)
        if fp == self._fingerprint:
            self._show_detail()
            return
        self._fingerprint = fp
        self._applying = True
        try:
            for col in board.get("columns", []):
                widget = self.columns.get(col["id"])
                if widget is None:
                    continue
                widget.set_cards(
                    [{**c, "status": c.get("status") or col["id"]} for c in col.get("cards", [])]
                )
        finally:
            # Highlighted events can land after set_cards returns - clear on next paint.
            self.call_after_refresh(self._finish_apply)

    def _finish_apply(self) -> None:
        self._applying = False
        self._show_detail()

    def invalidate(self) -> None:
        self._fingerprint = ""

    @property
    def agent_working(self) -> int:
        for col in self.board.get("columns", []):
            if col.get("id") == "agent_backlog":
                return len(col.get("cards", []))
        return 0

    @property
    def current_column(self) -> BoardColumn:
        return self.columns[self._order[self._index]]

    @property
    def current_card(self) -> dict[str, Any] | None:
        return self.current_column.selected

    def focus_column(self, index: int) -> None:
        self._index = index % len(self._order)
        column = self.current_column
        column.list_view.focus()
        column.scroll_visible(animate=False)
        self._show_detail()

    def focus_selected(self) -> None:
        self.focus_column(self._index)

    def move_column(self, delta: int) -> None:
        self.focus_column(self._index + delta)

    def move_card(self, delta: int) -> None:
        lv = self.current_column.list_view
        lv.action_cursor_down() if delta > 0 else lv.action_cursor_up()
        self._show_detail()

    def refresh_detail(self) -> None:
        self._show_detail()

    def _require_owner(self) -> bool:
        if self.is_owner:
            return True
        self.app.set_status("[yellow]owner only - members see their own cards[/yellow]")
        return False

    def edit_card(self) -> None:
        """Edit description + subtasks on your card (or any card if workspace owner)."""
        card = self.current_card
        if not card:
            self.app.set_status("pick a card first")
            return
        if not self.can_edit_card(card):
            self.app.set_status("[yellow]you can only edit your own cards[/yellow]")
            return
        oid = int(card["id"])
        title = str(card.get("title") or "")
        desc = str(card.get("description") or "")
        subs = [str(t.get("title") or "") for t in (card.get("subtasks") or [])]

        def done(result: dict[str, Any] | None) -> None:
            if result is None or result.get("dismiss"):
                self.app.set_status("edit cancelled")
                return
            self._setup_worker(oid, result)

        self.app.push_screen(
            ObjectiveSetupModal(
                oid,
                title,
                description=desc,
                subtasks=subs,
                editing=True,
            ),
            done,
        )

    def add_card(self) -> None:
        if not self._require_owner():
            return

        def done(title: str | None) -> None:
            if title:
                self._add_worker(title)

        self.app.push_screen(PromptModal("New objective", "title"), done)

    @work(thread=True, group="board")
    def _add_worker(self, title: str) -> None:
        try:
            obj = self.client.add_objective(title)
            self.app.call_from_thread(self._after_add, obj, "")
        except ApiError as exc:
            self.app.call_from_thread(self._after_add, None, str(exc))

    def _after_add(self, obj: dict[str, Any] | None, error: str) -> None:
        if error or not obj:
            self.app.set_status(f"[red]{escape(error or 'add failed')}[/red]")
            return
        oid = int(obj["id"])
        title = str(obj.get("title") or "")

        def setup_done(result: dict[str, Any] | None) -> None:
            if result is None:
                result = {"dismiss": True}
            self._setup_worker(oid, result)
            self.app.after_mutation(f"added #{oid} {title}")

        self.app.push_screen(ObjectiveSetupModal(oid, title), setup_done)

    @work(thread=True, group="board")
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
                n = len([s for s in (result.get("subtasks") or []) if str(s).strip()])
                msg = f"#{objective_id} saved ({n} subtask{'s' if n != 1 else ''})"
        except ApiError as exc:
            msg = f"[red]setup failed: {escape(str(exc))}[/red]"
        self.app.call_from_thread(self.app.after_mutation, msg)

    def shift_column(self, delta: int) -> None:
        """Move current card to previous/next column (CLI stand-in for drag-and-drop)."""
        if not self._require_owner():
            return
        card = self.current_card
        if not card:
            self.app.set_status("pick a card first")
            return
        status = str(card.get("status") or "")
        try:
            idx = self._order.index(status)
        except ValueError:
            self.app.set_status(f"[yellow]unknown status {escape(status)}[/yellow]")
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self._order):
            self.app.set_status("[dim]end of board[/dim]")
            return
        new_status = self._order[new_idx]
        dest = self.columns.get(new_status)
        if dest is not None:
            dest.add_class("col-flash")
            self.set_timer(0.45, lambda c=dest: c.remove_class("col-flash"))
        self.apply_status(card, new_status)

    def change_status(self) -> None:
        if not self._require_owner():
            return
        card = self.current_card
        if not card:
            return

        def done(choice: str | None) -> None:
            if choice:
                self.apply_status(card, choice)

        self.app.push_screen(ChoiceModal(f"Move #{card['id']} to…", list(BOARD_COLUMNS)), done)

    def send_to_agent(self) -> None:
        """Any member can send a card they own/are assigned to (Codex, Claude, or llm)."""
        card = self.current_card
        if not card:
            self.app.set_status("pick a card first")
            return
        if not self.can_edit_card(card):
            self.app.set_status(
                "[yellow]you can only send your own cards to the agent[/yellow]"
            )
            return
        from app.cli_pkg.doctor import available_coding_runners

        runners = available_coding_runners()
        if len(runners) <= 1:
            self.app.set_status(
                "[dim]sending to agent (llm). "
                "Install Codex/Claude CLIs to choose them (aio doctor).[/dim]"
            )
            self.apply_status(card, "agent_backlog")
            return

        def done(choice: str | None) -> None:
            if choice:
                self.apply_status(card, "agent_backlog", runner=choice)

        self.app.push_screen(ChoiceModal(f"Runner for #{card['id']}", runners), done)

    def apply_status(self, card: dict[str, Any], status: str, runner: str = "") -> None:
        self.app.set_status(f"#{card['id']} -> {status}…")
        self._status_worker(int(card["id"]), status, runner)

    @work(thread=True, group="board")
    def _status_worker(self, objective_id: int, status: str, runner: str) -> None:
        try:
            self.client.set_status(objective_id, status, runner=runner)
            note = f" via {runner}" if runner else ""
            msg = f"#{objective_id} -> {status}{note}"
        except ApiError as exc:
            msg = f"[red]{escape(str(exc))}[/red]"
        self.app.call_from_thread(self.app.after_mutation, msg)

    def merge_card(self) -> None:
        if not self._require_owner():
            return
        card = self.current_card
        if not card:
            return
        if not card.get("can_merge"):
            self.app.set_status(
                f"[red]#{card['id']} is not mergeable "
                f"(status={card.get('status')}, pr={card.get('pr_url') or 'none'})[/red]"
            )
            return
        detail = (
            f"#{card['id']} {escape(str(card.get('title') or ''))}\n"
            f"PR #{card.get('pr_number')} | {card.get('pr_url')}\n"
            f"branch {card.get('github_branch') or '-'} | method {get_settings().merge_method}"
        )

        def done(confirmed: bool | None) -> None:
            if not confirmed:
                self.app.set_status("merge cancelled")
                return
            self.app.set_status(f"merging PR #{card.get('pr_number')}…")
            self._merge_worker(int(card["id"]))

        self.app.push_screen(
            ConfirmModal("Merge & done", detail, MERGE_WARNING, "Merge & move to done"), done
        )

    @work(thread=True, group="board")
    def _merge_worker(self, objective_id: int) -> None:
        try:
            data = self.client.merge(objective_id)
            sha = str(data.get("sha") or "")[:8]
            msg = f"#{objective_id} merged into {data.get('base')} ({sha}) | card is done"
        except ApiError as exc:
            msg = f"[red]{escape(str(exc))}[/red]"
        self.app.call_from_thread(self.app.after_mutation, msg)

    def open_pr(self) -> None:
        url = (self.current_card or {}).get("pr_url")
        if not url:
            self.app.set_status("no PR on this card")
            return
        webbrowser.open(url)
        self.app.set_status(f"opened {url}")

    def open_repo(self) -> None:
        card = self.current_card or {}
        url = card.get("repo_url")
        if not url:
            self.app.set_status("no repo on this card")
            return
        webbrowser.open(str(url))
        self.app.set_status(f"opened {url}")

    def copy_pr(self) -> None:
        url = (self.current_card or {}).get("pr_url")
        if not url:
            self.app.set_status("no PR on this card")
            return
        try:
            self.app.copy_to_clipboard(url)
            self.app.set_status(f"copied {url}")
        except Exception:  # noqa: BLE001
            self.app.set_status(url)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()

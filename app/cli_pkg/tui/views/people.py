"""People tab: who is in the workspace. Owners can promote, demote and remove."""

from __future__ import annotations

from typing import Any

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Label, ListItem, ListView, Static

from app.cli_pkg.tui.client import ApiClient, ApiError
from app.cli_pkg.tui.widgets import ConfirmModal, InviteEmailModal
from app.config import get_settings


class MemberItem(ListItem):
    def __init__(self, member: dict[str, Any], is_me: bool) -> None:
        self.member = member
        role = str(member.get("role") or "member")
        badge = "[yellow]★ owner[/yellow]" if role == "owner" else "[dim]member[/dim]"
        you = "  [#7dd3fc](you)[/#7dd3fc]" if is_me else ""
        name = escape(str(member.get("name") or "").strip() or "—")
        email = escape(str(member.get("email") or ""))
        super().__init__(Static(f"[b]{name}[/b]{you}\n[dim]{email}[/dim]  ·  {badge}", markup=True))


class PeopleView(VerticalScroll):
    def __init__(self, client: ApiClient) -> None:
        super().__init__(id="people")
        self.client = client
        self.members: list[dict[str, Any]] = []
        self.me: dict[str, Any] = {}
        self.list_view = ListView(id="people-list")
        self.note = Static("", id="people-note", markup=True)

    def compose(self) -> ComposeResult:
        yield Label("PEOPLE", classes="view-head")
        yield Static(
            "Owners can change roles, remove people, and mint invite links.",
            classes="view-sub",
        )
        yield self.list_view
        yield self.note
        with Horizontal(id="people-actions"):
            yield Button("Invite", variant="primary", id="people-invite")
            yield Button("Make owner", id="people-promote")
            yield Button("Make member", id="people-demote")
            yield Button("Remove", variant="error", id="people-remove")

    @property
    def is_owner(self) -> bool:
        return bool(self.me.get("is_owner"))

    @property
    def selected(self) -> dict[str, Any] | None:
        index = self.list_view.index
        if index is None or index >= len(self.members):
            return None
        return self.members[index]

    def set_members(self, members: list[dict[str, Any]], me: dict[str, Any]) -> None:
        self.me = me
        signature = [(m.get("user_id"), m.get("role"), m.get("email"), m.get("name")) for m in members]
        if signature == getattr(self, "_signature", None):
            self._sync_buttons()
            return
        self._signature = signature
        index = self.list_view.index or 0
        self.members = members
        self.list_view.clear()
        my_id = me.get("user_id")
        for m in members:
            self.list_view.append(MemberItem(m, is_me=m.get("user_id") == my_id))
        if members:
            self.list_view.index = min(index, len(members) - 1)
        owners = sum(1 for m in members if m.get("role") == "owner")
        self.note.update(
            f"[dim]{len(members)} member(s) · {owners} owner(s) · pick someone, then use the buttons[/dim]"
            if self.is_owner
            else "[dim]Only owners can invite, change roles, or remove people.[/dim]"
        )
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        """Owners see the admin buttons; members don't."""
        for button_id in ("people-invite", "people-promote", "people-demote", "people-remove"):
            try:
                btn = self.query_one(f"#{button_id}", Button)
            except Exception:
                continue
            btn.display = self.is_owner

    # actions --------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "people-invite": self.invite,
            "people-promote": lambda: self.set_role("owner"),
            "people-demote": lambda: self.set_role("member"),
            "people-remove": self.remove_member,
        }
        action = actions.get(event.button.id or "")
        if action:
            action()

    def _guard(self) -> dict[str, Any] | None:
        if not self.is_owner:
            self.app.set_status("[yellow]owner only[/yellow]")
            return None
        member = self.selected
        if member is None:
            self.app.set_status("pick someone first")
        return member

    def set_role(self, role: str) -> None:
        member = self._guard()
        if member is None:
            return
        if str(member.get("role")) == role:
            self.app.set_status(f"{member.get('email')} is already a {role}")
            return
        self._role_worker(int(member["user_id"]), role, str(member.get("email") or ""))

    @work(thread=True, group="people")
    def _role_worker(self, user_id: int, role: str, email: str) -> None:
        try:
            self.client.set_member_role(user_id, role)
            msg = f"{email} is now a {role}"
        except ApiError as exc:
            msg = f"[red]{escape(str(exc))}[/red]"
        self.app.call_from_thread(self._done, msg)

    def remove_member(self) -> None:
        member = self._guard()
        if member is None:
            return
        if member.get("user_id") == self.me.get("user_id"):
            self.app.set_status("[yellow]you can't remove yourself[/yellow]")
            return
        email = str(member.get("email") or "")

        def confirmed(yes: bool | None) -> None:
            if not yes:
                self.app.set_status("removal cancelled")
                return
            self._remove_worker(int(member["user_id"]), email)

        self.app.push_screen(
            ConfirmModal(
                "Remove from workspace",
                f"{escape(str(member.get('name') or ''))}\n{escape(email)}",
                "They lose access immediately. Their messages stay.",
                "Remove",
            ),
            confirmed,
        )

    @work(thread=True, group="people")
    def _remove_worker(self, user_id: int, email: str) -> None:
        try:
            self.client.remove_member(user_id)
            msg = f"removed {email}"
        except ApiError as exc:
            msg = f"[red]{escape(str(exc))}[/red]"
        self.app.call_from_thread(self._done, msg)

    def invite(self) -> None:
        if not self.is_owner:
            self.app.set_status("[yellow]owner only[/yellow]")
            return
        domain = (get_settings().invite_allowed_domain or "tatweermea.com").lstrip("@")

        def got(result: dict[str, Any] | None) -> None:
            if result is None:
                self.app.set_status("invite cancelled")
                return
            seats = int(result.get("seats") or 1)
            if result.get("send_email") and result.get("email"):
                self._invite_email_worker(str(result["email"]), seats)
            else:
                self._invite_worker(seats)

        self.app.push_screen(InviteEmailModal(domain=domain), got)

    @work(thread=True, group="people")
    def _invite_worker(self, seats: int) -> None:
        try:
            data = self.client.invite_link(seats)
            url = str(data.get("invite_url") or "")
            msg = f"invite ({seats} seat(s)): {url}"
        except ApiError as exc:
            msg = f"[red]{escape(str(exc))}[/red]"
        self.app.call_from_thread(self._show_invite, msg)

    @work(thread=True, group="people")
    def _invite_email_worker(self, email: str, seats: int) -> None:
        try:
            data = self.client.invite_email(email, seats)
            url = str(data.get("invite_url") or "")
            outlook = data.get("outlook") or {}
            if outlook.get("ok"):
                msg = f"emailed {email}: {url}"
            elif data.get("email_error"):
                msg = f"[yellow]link minted, email failed:[/yellow] {data['email_error']}\n{url}"
            else:
                msg = f"invite ({seats}): {url}"
        except ApiError as exc:
            msg = f"[red]{escape(str(exc))}[/red]"
        self.app.call_from_thread(self._show_invite, msg)

    def _show_invite(self, message: str) -> None:
        self.note.update(escape(message))
        self.app.set_status(message.split(": ")[-1] if ": " in message else message)

    def _done(self, message: str) -> None:
        self.app.set_status(message)
        self.members = []  # force a redraw on the next poll
        self.app.refresh_workspace()

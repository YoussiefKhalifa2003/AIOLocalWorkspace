"""Short `!` command verbs - no LLM, board/ops only."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    Chat,
    ChatMember,
    ChatMessage,
    Job,
    Objective,
    TaskItem,
    Tenant,
    User,
    WorkIssue,
    utcnow,
)
from app.services.auth import AuthContext
from app.services.chat_access import ensure_channel_membership, is_workspace_owner, list_visible_chats
from app.services.workspace_invite import mint_invite_link
from app.services.work_requests import create_work_request
from app.worker import drain_queue

# IntentResult imported lazily-shaped from orchestrator to avoid cycles - use duck type
# Callers pass IntentResult constructor


HELP_TEXT = """Commands (type ! then pick, or type fully). In #general only you see these.

Work
  !add <text>              new board card
  !list                    show my cards
  !set <id> <status>       todo|doing|blocked|done|agent_backlog|in_review
  !done <id>               mark card done
  !remove <id>             delete a card
  !assign <id> <name>      give card away (owner)
  !link <id> branch <name> attach branch
  !link <id> pr <url>      attach PR

Files
  !claim <path>            lock a file
  !release <path>          free a file
  !go                      run despite claim conflict

Issues
  !issue <text>            log a blocker
  !issues                  show blockers
  !resolve <id>            close blocker

Room
  !invite [N]              mint invite link (N uses, default 1)
  !status <name>           member catch-up (owner/self)
  !clear                   wipe this chat
  !help                    this list

Private room: use /skills for AI (e.g. /code ...). Board tab shows the board."""


def _parse_invite_uses(raw: str) -> int:
    """Parse `invite` / `invite 5` → max uses (default 1)."""
    from app.services.workspace_invite import clamp_invite_uses

    m = re.match(r"invite(?:\s+(\d+))?\s*$", raw.strip(), re.I)
    if not m:
        return 1
    if m.group(1):
        return clamp_invite_uses(int(m.group(1)))
    return 1


def _invite_reply(data: dict) -> str:
    uses = int(data.get("max_uses") or 1)
    left = int(data.get("uses_left") or uses)
    url = data["invite_url"]
    if uses <= 1:
        msg = (
            "Invite link (1 use). After someone registers it expires — "
            "run `!invite` or `!invite 5` for more seats:\n"
            f"{url}"
        )
    else:
        msg = (
            f"Invite link ({uses} uses). Seat count drops as people register; "
            f"expires after the last signup ({left} left now):\n"
            f"{url}"
        )
    teams = data.get("teams") or {}
    if teams.get("ok"):
        msg += "\n\nPosted to Teams channel."
    elif teams.get("skipped"):
        pass
    elif teams:
        reason = teams.get("reason") or f"HTTP {teams.get('status_code')}"
        msg += f"\n\nTeams notify failed: {reason}"
    return msg


def _progress_bar(done: int, total: int) -> str:
    if total <= 0:
        return "[----------] 0/0"
    filled = int(round(10 * done / total))
    return f"[{'#' * filled}{'-' * (10 - filled)}] {done}/{total}"


def _can_manage_objective(db: Session, auth: AuthContext, obj: Objective) -> bool:
    if obj.user_id == auth.user_id or obj.assignee_user_id == auth.user_id:
        return True
    return is_workspace_owner(db, auth)


def try_bang_command(db: Session, auth: AuthContext, chat: Chat, text: str, IntentResult, pending_coding: dict, run_agent_force):
    """Parse `!verb ...`. text may include leading !. Returns IntentResult or None."""
    raw = (text or "").strip()
    if raw.startswith("!"):
        raw = raw[1:].lstrip()
    if not raw:
        return IntentResult(True, "Pick a command, e.g. `!add ...` or `!help`.")

    lower = raw.lower()
    project_id = chat.project_id or 1

    if lower in ("help", "commands", "?"):
        return IntentResult(True, HELP_TEXT)

    # !add <title>
    m = re.match(r"add\s+(.+)$", lower)
    if m:
        title = raw[m.start(1) :].strip()
        count = (
            db.query(Objective)
            .filter(
                Objective.tenant_id == auth.tenant_id,
                Objective.project_id == project_id,
                Objective.user_id == auth.user_id,
            )
            .count()
        )
        obj = Objective(
            tenant_id=auth.tenant_id,
            project_id=project_id,
            user_id=auth.user_id,
            assignee_user_id=auth.user_id,
            title=title,
            status="todo",
            sort_order=count + 1,
        )
        db.add(obj)
        db.flush()
        return IntentResult(True, f"Added objective #{obj.id}: {obj.title} (yours)")

    if lower in ("list", "list objectives", "objectives"):
        from app.services.status import objectives_for_user

        rows = objectives_for_user(
            db, tenant_id=auth.tenant_id, project_id=project_id, user_id=auth.user_id
        )
        done = sum(1 for r in rows if r.done)
        lines = [f"[{'x' if r.done else ' '}] {r.id}. {r.title}" for r in rows] or ["(none)"]
        return IntentResult(
            True, f"YOUR OBJECTIVES {_progress_bar(done, len(rows))}\n" + "\n".join(lines)
        )

    m = re.match(r"set\s+(\d+)\s+(todo|doing|blocked|done|agent_backlog|in_review)$", lower)
    if m:
        oid = int(m.group(1))
        status = m.group(2)
        obj = (
            db.query(Objective)
            .filter(
                Objective.id == oid,
                Objective.tenant_id == auth.tenant_id,
                Objective.project_id == project_id,
            )
            .one_or_none()
        )
        if obj is None:
            return IntentResult(True, f"Objective {oid} not found.")
        if not _can_manage_objective(db, auth, obj):
            return IntentResult(True, "That objective belongs to someone else.")
        from app.services.board import set_objective_status
        from app.services.file_claims import (
            auto_claim_from_objective,
            release_claims_for_objective,
        )

        prev = obj.status
        set_objective_status(obj, status)
        if status == "doing":
            auto_claim_from_objective(db, obj)
        if status in ("done", "todo"):
            release_claims_for_objective(db, obj.id)
        if status == "agent_backlog" and prev != "agent_backlog":
            from app.services.agent_backlog import enqueue_agent_backlog

            enqueue_agent_backlog(db, auth, obj)
        return IntentResult(True, f"Objective #{oid} → {status}.")

    m = re.match(r"done\s+(\d+)$", lower)
    if m:
        oid = int(m.group(1))
        obj = (
            db.query(Objective)
            .filter(
                Objective.id == oid,
                Objective.tenant_id == auth.tenant_id,
                Objective.project_id == project_id,
            )
            .one_or_none()
        )
        if obj is None:
            return IntentResult(True, f"Objective #{oid} not found.")
        if not _can_manage_objective(db, auth, obj):
            return IntentResult(True, f"Objective #{oid} not found (or not yours).")
        if obj.done:
            return IntentResult(True, f"Objective #{oid} was already done.")
        from app.services.board import set_objective_status
        from app.services.file_claims import release_claims_for_objective

        set_objective_status(obj, "done")
        release_claims_for_objective(db, obj.id)
        return IntentResult(True, f"Marked objective #{oid} done: {obj.title}")

    m = re.match(r"keep\s+(\d+)$", lower)
    if m:
        oid = int(m.group(1))
        obj = (
            db.query(Objective)
            .filter(
                Objective.id == oid,
                Objective.tenant_id == auth.tenant_id,
                Objective.project_id == project_id,
            )
            .one_or_none()
        )
        if obj is None:
            return IntentResult(True, f"Objective #{oid} not found.")
        if not _can_manage_objective(db, auth, obj):
            return IntentResult(True, f"Objective #{oid} not found (or not yours).")
        return IntentResult(
            True,
            f"Kept objective #{oid} open: {obj.title}.",
        )

    m = re.match(r"remove\s+(\d+)$", lower)
    if m:
        oid = int(m.group(1))
        obj = (
            db.query(Objective)
            .filter(
                Objective.id == oid,
                Objective.tenant_id == auth.tenant_id,
                Objective.project_id == project_id,
            )
            .one_or_none()
        )
        if obj is None:
            return IntentResult(True, f"Objective {oid} not found.")
        if not _can_manage_objective(db, auth, obj):
            return IntentResult(True, "That objective belongs to someone else.")
        from app.services.file_claims import release_claims_for_objective

        release_claims_for_objective(db, obj.id)
        db.delete(obj)
        return IntentResult(True, f"Removed objective {oid}.")

    m = re.match(r"assign\s+(\d+)\s+(?:to\s+)?(\S+)$", lower)
    if m:
        oid = int(m.group(1))
        who = m.group(2)
        if not is_workspace_owner(db, auth):
            return IntentResult(True, "Only the workspace owner can reassign objectives.")
        from app.services.status import resolve_member

        target = resolve_member(db, auth.tenant_id, who)
        if target is None:
            return IntentResult(True, f"No member matching '{who}'.")
        obj = (
            db.query(Objective)
            .filter(
                Objective.id == oid,
                Objective.tenant_id == auth.tenant_id,
                Objective.project_id == project_id,
            )
            .one_or_none()
        )
        if obj is None:
            return IntentResult(True, f"Objective {oid} not found.")
        obj.assignee_user_id = target.id
        return IntentResult(True, f"Assigned objective #{oid} to {target.email}.")

    m = re.match(r"link\s+(\d+)\s+pr\s+(\S+)$", lower)
    if m:
        oid = int(m.group(1))
        url = m.group(2)
        obj = (
            db.query(Objective)
            .filter(
                Objective.id == oid,
                Objective.tenant_id == auth.tenant_id,
                Objective.project_id == project_id,
            )
            .one_or_none()
        )
        if obj is None:
            return IntentResult(True, f"Objective {oid} not found.")
        if not _can_manage_objective(db, auth, obj):
            return IntentResult(True, "That objective belongs to someone else.")
        obj.github_pr_url = url
        num = re.search(r"/pull/(\d+)", url)
        if num:
            obj.github_pr_number = int(num.group(1))
        return IntentResult(True, f"Linked objective #{oid} to PR {url}")

    m = re.match(r"link\s+(\d+)\s+branch\s+(\S+)$", lower)
    if m:
        oid = int(m.group(1))
        branch = m.group(2)
        obj = (
            db.query(Objective)
            .filter(
                Objective.id == oid,
                Objective.tenant_id == auth.tenant_id,
                Objective.project_id == project_id,
            )
            .one_or_none()
        )
        if obj is None:
            return IntentResult(True, f"Objective {oid} not found.")
        if not _can_manage_objective(db, auth, obj):
            return IntentResult(True, "That objective belongs to someone else.")
        obj.github_branch = branch
        return IntentResult(True, f"Linked objective #{oid} to branch `{branch}`")

    m = re.match(r"claim\s+(\S+)$", lower)
    if m:
        path = m.group(1)
        from app.services.file_claims import claim_path

        claim_path(
            db,
            tenant_id=auth.tenant_id,
            project_id=project_id,
            user_id=auth.user_id,
            path_pattern=path,
        )
        return IntentResult(True, f"Claimed `{path}`.")

    m = re.match(r"release\s+(\S+)$", lower)
    if m:
        path = m.group(1)
        from app.services.file_claims import release_user_path

        n = release_user_path(
            db,
            tenant_id=auth.tenant_id,
            project_id=project_id,
            user_id=auth.user_id,
            path_pattern=path,
        )
        return IntentResult(True, f"Released {n} claim(s) for `{path}`.")

    if lower in ("go", "proceed", "force") or lower.startswith("go "):
        key = (auth.user_id, chat.id)
        pending = pending_coding.pop(key, None)
        if lower.startswith("go "):
            pending = raw[3:].strip() or pending
        if not pending:
            return IntentResult(True, "Nothing pending. Use /code ... then !go if claims conflict.")
        return run_agent_force(db, auth, chat, pending)

    m = re.match(r"issue\s+(.+)$", lower)
    if m:
        title = raw[m.start(1) :].strip()
        issue = WorkIssue(
            tenant_id=auth.tenant_id,
            project_id=project_id,
            owner_user_id=auth.user_id,
            title=title[:255],
            detail="",
            status="open",
            source_chat_id=chat.id,
        )
        db.add(issue)
        db.flush()
        return IntentResult(True, f"Logged issue #{issue.id}: {issue.title}")

    if lower in ("issues",):
        from app.services.status import issues_for_user

        rows = issues_for_user(
            db, tenant_id=auth.tenant_id, project_id=project_id, user_id=auth.user_id
        )
        lines = [f"! #{i.id} {i.title}" + (f" - {i.detail}" if i.detail else "") for i in rows] or [
            "(none)"
        ]
        return IntentResult(True, "ISSUES\n" + "\n".join(lines))

    m = re.match(r"resolve\s+(\d+)$", lower)
    if m:
        iid = int(m.group(1))
        issue = (
            db.query(WorkIssue)
            .filter(
                WorkIssue.id == iid,
                WorkIssue.tenant_id == auth.tenant_id,
                WorkIssue.project_id == project_id,
            )
            .one_or_none()
        )
        if issue is None:
            return IntentResult(True, f"Issue {iid} not found.")
        if issue.owner_user_id != auth.user_id and not is_workspace_owner(db, auth):
            return IntentResult(True, "That issue belongs to someone else.")
        issue.status = "resolved"
        issue.resolved_at = utcnow()
        return IntentResult(True, f"Resolved issue #{iid}.")

    if lower == "invite" or re.match(r"invite\s+\d+\s*$", lower):
        try:
            uses = _parse_invite_uses(raw)
            tenant = db.query(Tenant).filter(Tenant.id == auth.tenant_id).one()
            data = mint_invite_link(db, tenant, max_uses=uses)
            return IntentResult(True, _invite_reply(data))
        except Exception as exc:  # noqa: BLE001
            return IntentResult(True, f"Invite link failed: {exc}")

    m = re.match(r"status\s+(\S+)$", lower)
    if m:
        from app.services.status import can_view_user_status, format_user_status, resolve_member

        target = resolve_member(db, auth.tenant_id, m.group(1))
        if target is None:
            return IntentResult(True, f"No member matching '{m.group(1)}'.")
        if not can_view_user_status(db, auth, target.id):
            return IntentResult(True, "Only the workspace owner can view another member's status.")
        return IntentResult(
            True,
            format_user_status(
                db, tenant_id=auth.tenant_id, project_id=project_id, user=target
            ),
        )

    if lower in ("team", "team status", "team report"):
        if not is_workspace_owner(db, auth):
            return IntentResult(True, "Team report is for workspace owners. Ask an owner.")
        from app.services.status import format_team_report

        return IntentResult(
            True, format_team_report(db, tenant_id=auth.tenant_id, project_id=project_id)
        )

    if lower in ("clear", "clear chat"):
        db.query(ChatMessage).filter(ChatMessage.chat_id == chat.id).delete()
        db.flush()
        return IntentResult(True, "Chat cleared.", cleared_chat=True)

    # Advanced / still supported if typed
    m = re.match(r"newchat\s+(.+)$", lower)
    if m:
        name = raw[m.start(1) :].strip() or "untitled"
        new_chat = Chat(
            tenant_id=auth.tenant_id,
            project_id=project_id,
            name=name,
            kind="channel",
            owner_user_id=None,
        )
        db.add(new_chat)
        db.flush()
        ensure_channel_membership(db, new_chat)
        return IntentResult(
            True,
            f"Created team chat #{new_chat.id} '{new_chat.name}'. Switching you over.",
            created_chat_id=new_chat.id,
        )

    if lower in ("chats",):
        rows = list_visible_chats(db, auth)
        lines = [f"#{c.id} {c.name} ({c.kind})" for c in rows] or ["(no chats)"]
        return IntentResult(True, "Chats:\n" + "\n".join(lines))

    return None

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Artifact, Chat, ChatMessage, Job, Objective, TaskItem, User, WorkIssue, utcnow
from app.router.classify import RoutePlan, classify_request
from app.services.auth import AuthContext
from app.services.chat_access import (
    ensure_channel_membership,
    is_workspace_owner,
    list_visible_chats,
)
from app.services.seed import invite_user_by_email
from app.services.tts import TTSError, synthesize_speech
from app.services.work_requests import create_work_request
from app.worker import drain_queue
from app.db.models import ChatMember, WorkspaceMember

AGENT_ALIASES = {
    "lead": "lead",
    "orchestrator": "lead",
    "research": "research",
    "writing": "writing",
    "write": "writing",
    "code": "coding",
    "coding": "coding",
    "programmer": "coding",
    "programming": "coding",
    "review": "code_review",
    "codereview": "code_review",
    "checklist": "checklist",
    "team": "__team__",
}

MENTION_RE = re.compile(r"@([A-Za-z0-9_.+-]+)\b")


@dataclass
class IntentResult:
    handled: bool
    reply: str
    agent_slug: str = "lead"
    created_chat_id: int | None = None
    deleted_chat_id: int | None = None
    confirm_objective_ids: list[int] | None = None
    cleared_chat: bool = False


# (user_id, chat_id) -> pending coding prompt after claim warning
_PENDING_CODING: dict[tuple[int, int], str] = {}


def _can_manage_objective(db: Session, auth: AuthContext, obj: Objective) -> bool:
    from app.services.board import can_edit_objective

    return can_edit_objective(db, auth, obj)

@dataclass
class ParsedMention:
    kind: str  # agent | user | team | none
    agent_slug: str | None = None
    user: User | None = None
    rest: str = ""


def _parse_mention(db: Session, auth: AuthContext, text: str) -> ParsedMention:
    m = MENTION_RE.search(text)
    if not m:
        return ParsedMention(kind="none", rest=text.strip())
    raw = m.group(1)
    rest = (text[: m.start()] + text[m.end() :]).strip()
    key = raw.lower()
    if key in AGENT_ALIASES:
        slug = AGENT_ALIASES[key]
        if slug == "__team__":
            return ParsedMention(kind="team", rest=rest)
        return ParsedMention(kind="agent", agent_slug=slug, rest=rest)
    from app.services.status import resolve_member

    user = resolve_member(db, auth.tenant_id, raw)
    if user is not None:
        return ParsedMention(kind="user", user=user, rest=rest)
    # unknown @token - leave as plain text
    return ParsedMention(kind="none", rest=text.strip())


def _progress_bar(done: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return f"[{'-' * width}] 0/0"
    filled = int(round(width * done / total))
    pct = int(round(100 * done / total))
    return f"[{'#' * filled}{'-' * (width - filled)}] {done}/{total} ({pct}%)"


def _candidate_objectives(
    db: Session, *, tenant_id: int, project_id: int, user_id: int, request_text: str
) -> list[Objective]:
    rows = (
        db.query(Objective)
        .filter(
            Objective.tenant_id == tenant_id,
            Objective.project_id == project_id,
            Objective.user_id == user_id,
            Objective.done.is_(False),
        )
        .order_by(Objective.id.desc())
        .all()
    )
    if not rows:
        return []
    req_words = {w for w in re.findall(r"[a-z0-9]+", request_text.lower()) if len(w) > 2}
    scored: list[tuple[int, Objective]] = []
    for obj in rows:
        title_words = {w for w in re.findall(r"[a-z0-9]+", obj.title.lower()) if len(w) > 2}
        score = len(req_words & title_words)
        scored.append((score, obj))
    scored.sort(key=lambda x: (-x[0], -x[1].id))
    # Prefer overlaps; otherwise most recent open objectives
    matched = [o for s, o in scored if s > 0][:3]
    return matched or [o for _, o in scored[:3]]


def _with_confirm_footer(reply: str, objectives: list[Objective]) -> tuple[str, list[int]]:
    if not objectives:
        return reply, []
    ids = [o.id for o in objectives]
    lines = [
        reply.rstrip(),
        "",
        "---",
        "Has your requirement been met?",
    ]
    for o in objectives:
        lines.append(f"  Objective #{o.id}: {o.title}")
    lines.append("Click Yes/No below, or type: yes <id>   /   no <id>")
    lines.append(f"[[confirm:{','.join(str(i) for i in ids)}]]")
    return "\n".join(lines), ids


def try_nl_command(db: Session, auth: AuthContext, chat: Chat, text: str) -> IntentResult | None:
    """Natural-language workspace commands. Returns None if not a command."""
    t = text.strip()
    lower = t.lower()
    project_id = chat.project_id or 1

    m = re.match(r"(?:yes|confirm(?:\s+objective)?)\s+#?(\d+)$", lower)
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

    m = re.match(r"(?:no|reject(?:\s+objective)?)\s+#?(\d+)$", lower)
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
            f"Kept objective #{oid} open: {obj.title}. Say what still needs fixing and I'll continue.",
        )

    # invite
    m = re.match(r"invite\s+(\S+@\S+)", lower)
    if m:
        email = m.group(1)
        try:
            result = invite_user_by_email(
                db,
                tenant_id=auth.tenant_id,
                inviter_user_id=auth.user_id,
                email=email,
            )
            mail = result.get("email_detail") or ""
            accept = result.get("accept_url") or ""
            return IntentResult(
                True,
                f"Invited {result['email']}. They must Accept invite in the email first."
                f"{(' Email: ' + mail) if mail else ''}"
                f"{(' Link: ' + accept) if accept else ''}\n"
                "Then log in on /app with that email + shared join key.",
            )
        except ValueError as exc:
            return IntentResult(True, f"Invite failed: {exc}")

    m = re.match(r"(?:create(?:\s+a)?(?:\s+new)?|new)\s+chat(?:\s+(.+))?$", lower)
    if m:
        name = (m.group(1) or "").strip()
        if name:
            idx = lower.rfind(name.lower())
            if idx >= 0:
                name = text.strip()[idx : idx + len(name)].strip()
        else:
            name = "untitled"
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

    m = re.match(r"delete(?:\s+this)?\s+chat(?:\s+(\d+))?$", lower)
    if m:
        target_id = int(m.group(1)) if m.group(1) else chat.id
        target = (
            db.query(Chat)
            .filter(Chat.id == target_id, Chat.tenant_id == auth.tenant_id)
            .one_or_none()
        )
        if target is None:
            return IntentResult(True, f"Chat #{target_id} not found.")
        if target.kind == "private" and target.owner_user_id != auth.user_id:
            return IntentResult(True, "Cannot delete another user's private room.")
        if target.name == "general" and target.kind == "channel":
            return IntentResult(True, "Cannot delete the general channel.")
        name = target.name
        db.query(ChatMessage).filter(ChatMessage.chat_id == target_id).delete()
        db.query(ChatMember).filter(ChatMember.chat_id == target_id).delete()
        db.delete(target)
        db.flush()
        return IntentResult(
            True,
            f"Deleted chat #{target_id} '{name}'.",
            deleted_chat_id=target_id,
        )

    if lower in ("list chats", "show chats", "chats"):
        rows = list_visible_chats(db, auth)
        lines = [f"#{c.id} {c.name} ({c.kind})" for c in rows] or ["(no chats)"]
        return IntentResult(True, "Chats:\n" + "\n".join(lines))

    # objectives
    m = re.match(r"add objective\s+(.+)$", lower)
    if m:
        idx = lower.find("add objective")
        title = text[idx + len("add objective") :].strip()
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

    if lower in ("board", "show board", "objective board"):
        from app.services.board import board_text_summary

        return IntentResult(
            True,
            board_text_summary(db, tenant_id=auth.tenant_id, project_id=project_id),
        )

    m = re.match(r"assign objective\s+(\d+)\s+to\s+(\S+)$", lower)
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

    m = re.match(r"set objective\s+(\d+)\s+(todo|doing|blocked|done|agent_backlog|in_review)$", lower)
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

    m = re.match(r"link objective\s+(\d+)\s+pr\s+(\S+)$", lower)
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

    m = re.match(r"link objective\s+(\d+)\s+branch\s+(\S+)$", lower)
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

    m = re.match(r"claim path\s+(\S+)$", lower)
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

    m = re.match(r"release claim\s+(\S+)$", lower)
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

    if lower in ("proceed", "force") or lower.startswith("force code ") or lower.startswith("proceed "):
        key = (auth.user_id, chat.id)
        pending = _PENDING_CODING.pop(key, None)
        if lower.startswith("force code "):
            pending = text[len("force code ") :].strip() or pending
        elif lower.startswith("proceed ") and lower != "proceed":
            pending = text[len("proceed ") :].strip() or pending
        if not pending:
            return IntentResult(True, "Nothing pending to proceed. Use @Code ... or force code <request>.")
        return _run_agent_branch(db, auth, chat, pending, forced_agent="coding", force=True)
    if lower in ("show objectives", "list objectives", "objectives", "show all objectives"):
        from app.services.status import objectives_for_user

        if lower == "show all objectives" and is_workspace_owner(db, auth):
            rows = (
                db.query(Objective)
                .filter(Objective.tenant_id == auth.tenant_id, Objective.project_id == project_id)
                .order_by(Objective.user_id, Objective.sort_order, Objective.id)
                .all()
            )
            users = {u.id: u for u in db.query(User).filter(User.tenant_id == auth.tenant_id).all()}
            done = sum(1 for r in rows if r.done)
            lines = []
            for r in rows:
                owner = users.get(r.user_id)
                who = owner.email if owner else str(r.user_id)
                lines.append(f"[{'x' if r.done else ' '}] {r.id}. {r.title} ({who})")
            if not lines:
                lines = ["(none)"]
            return IntentResult(
                True, f"ALL OBJECTIVES {_progress_bar(done, len(rows))}\n" + "\n".join(lines)
            )
        rows = objectives_for_user(
            db, tenant_id=auth.tenant_id, project_id=project_id, user_id=auth.user_id
        )
        done = sum(1 for r in rows if r.done)
        lines = [f"[{'x' if r.done else ' '}] {r.id}. {r.title}" for r in rows] or ["(none)"]
        return IntentResult(
            True, f"YOUR OBJECTIVES {_progress_bar(done, len(rows))}\n" + "\n".join(lines)
        )

    m = re.match(r"remove objective\s+(\d+)$", lower)
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

    m = re.match(r"remove checklist\s+(\d+)$", lower)
    if m:
        iid = int(m.group(1))
        item = (
            db.query(TaskItem)
            .filter(
                TaskItem.id == iid,
                TaskItem.tenant_id == auth.tenant_id,
                TaskItem.project_id == project_id,
            )
            .one_or_none()
        )
        if item is None:
            return IntentResult(True, f"Checklist item {iid} not found.")
        if item.owner_user_id and item.owner_user_id != auth.user_id and not is_workspace_owner(
            db, auth
        ):
            return IntentResult(True, "That checklist item belongs to someone else.")
        db.delete(item)
        return IntentResult(True, f"Removed checklist #{iid}.")

    if lower in ("clear", "clear chat", "clear messages"):
        # Wipe transcript in this chat (keep the chat itself)
        db.query(ChatMessage).filter(ChatMessage.chat_id == chat.id).delete()
        db.flush()
        return IntentResult(True, "Chat cleared.", cleared_chat=True)

    m = re.match(r"run objective\s+(\d+)$", lower)
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
        if obj.user_id != auth.user_id and not is_workspace_owner(db, auth):
            return IntentResult(True, "That objective belongs to someone else.")
        if obj.done:
            return IntentResult(True, f"Objective {oid} already done.")
        req, job_ids, plan = create_work_request(
            db,
            tenant_id=auth.tenant_id,
            project_id=project_id,
            user_id=auth.user_id,
            text=obj.title,
        )
        obj.request_id = req.id
        db.flush()
        db.commit()
        n = drain_queue(max_jobs=30)
        obj = db.query(Objective).filter(Objective.id == oid).one()
        jobs = db.query(Job).filter(Job.request_id == req.id).all()
        failed = [j for j in jobs if j.status == "failed"]
        if failed:
            issue = WorkIssue(
                tenant_id=auth.tenant_id,
                project_id=project_id,
                owner_user_id=obj.user_id,
                title=f"Objective {oid} failed",
                detail=(failed[0].error or "")[:500],
                status="open",
                source_chat_id=chat.id,
            )
            db.add(issue)
            return IntentResult(
                True,
                f"Ran objective {oid} via {plan.agents}; worker={n}; FAILED: {failed[0].error}\nLogged issue #{issue.id}.",
                agent_slug=plan.agents[0] if plan.agents else "lead",
            )
        arts = (
            db.query(Artifact)
            .filter(Artifact.job_id.in_([j.id for j in jobs] or [-1]))
            .order_by(Artifact.id)
            .all()
        )
        obj.done = True
        obj.completed_at = utcnow()
        body = f"Objective {oid} COMPLETE ({plan.agents}).\n"
        for a in arts:
            body += f"\n--- {a.agent_type}: {a.title} ---\n{a.content}\n"
        return IntentResult(True, body.strip(), agent_slug=plan.agents[0] if plan.agents else "lead")

    if lower in ("show checklist", "list checklist", "checklist"):
        from app.services.status import checklist_for_user

        items = checklist_for_user(
            db, tenant_id=auth.tenant_id, project_id=project_id, user_id=auth.user_id
        )
        done = sum(1 for i in items if i.done)
        lines = [f"[{'x' if i.done else ' '}] {i.id}. {i.title}" for i in items] or ["(empty)"]
        return IntentResult(
            True, f"YOUR CHECKLIST {_progress_bar(done, len(items))}\n" + "\n".join(lines)
        )

    if lower in ("clear checklist",):
        deleted = (
            db.query(TaskItem)
            .filter(
                TaskItem.tenant_id == auth.tenant_id,
                TaskItem.project_id == project_id,
                TaskItem.owner_user_id == auth.user_id,
            )
            .delete()
        )
        return IntentResult(True, f"Cleared {deleted} of your checklist items.")

    m = re.match(r"(?:checklist )?done\s+(\d+)$", lower)
    if m:
        iid = int(m.group(1))
        item = (
            db.query(TaskItem)
            .filter(
                TaskItem.id == iid,
                TaskItem.tenant_id == auth.tenant_id,
                TaskItem.project_id == project_id,
            )
            .one_or_none()
        )
        if item is None:
            return IntentResult(True, f"Checklist item {iid} not found.")
        if item.owner_user_id not in (None, auth.user_id) and not is_workspace_owner(db, auth):
            return IntentResult(True, "That checklist item belongs to someone else.")
        item.done = True
        return IntentResult(True, f"Marked checklist #{iid} done.")

    # issues
    m = re.match(r"log issue\s+(.+)$", lower)
    if m:
        idx = lower.find("log issue")
        title = text[idx + len("log issue") :].strip()
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

    if lower in ("show issues", "list issues", "issues"):
        from app.services.status import issues_for_user

        if is_workspace_owner(db, auth) and lower == "list issues":
            rows = (
                db.query(WorkIssue)
                .filter(
                    WorkIssue.tenant_id == auth.tenant_id,
                    WorkIssue.project_id == project_id,
                    WorkIssue.status == "open",
                )
                .order_by(WorkIssue.id)
                .all()
            )
        else:
            rows = issues_for_user(
                db, tenant_id=auth.tenant_id, project_id=project_id, user_id=auth.user_id
            )
        lines = [f"! #{i.id} {i.title}" + (f" - {i.detail}" if i.detail else "") for i in rows] or [
            "(none)"
        ]
        return IntentResult(True, "ISSUES\n" + "\n".join(lines))

    m = re.match(r"resolve issue\s+(\d+)$", lower)
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

    if lower in ("team status", "team report", "status team"):
        if not is_workspace_owner(db, auth):
            return IntentResult(True, "Team report is for workspace owners. Ask an owner.")
        from app.services.status import format_team_report

        return IntentResult(True, format_team_report(db, tenant_id=auth.tenant_id, project_id=project_id))

    m = re.match(r"status(?:\s+for)?\s+(\S+)$", lower)
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

    if lower in ("help", "commands", "?"):
        return IntentResult(
            True,
            "Commands:\n"
            "- In TEAM chats: type board/ops commands bare "
            "(e.g. `add objective ...`, `board`, `claim path ...`)\n"
            "- In TEAM chats: start with `/` only to wake AI "
            "(e.g. `/help`, `/@Omar status`, `/@Research one tip`)\n"
            "- Plain non-command messages in TEAM are human-only (no AI)\n"
            "- In MY ROOM: AI always listens (no `/` needed); commands work bare too\n"
            "- add objective <text> / show objectives / run objective <id>\n"
            "- remove objective <id> / remove checklist <id>\n"
            "- clear / clear chat - wipe messages in this chat\n"
            "- board / set objective <id> todo|doing|blocked|done|agent_backlog\n"
            "- assign objective <id> to <name> (owner)\n"
            "- link objective <id> pr <url> / link objective <id> branch <name>\n"
            "- claim path <file> / release claim <file> / proceed\n"
            "- show checklist / checklist done <id> / clear checklist\n"
            "- log issue <text> / show issues / resolve issue <id>\n"
            "- invite <email>\n"
            "- create chat <name> / list chats / delete chat [id]\n"
            "- @Research / @Writing / @Code / @Review / @Checklist ...\n"
            "- After agent work: Yes/No on objectives, or yes <id> / no <id>\n"
            "- @Omar status / status for Omar (owner: others; anyone: self)\n"
            "- @team report / team status (owner)\n"
            "- or just type a request in your private room (Lead routes it)",
        )

    return None


def _post_lead_reply(
    db: Session,
    *,
    auth: AuthContext,
    chat: Chat,
    result: IntentResult,
    speak: bool,
    whisper: bool = False,
) -> tuple[list[ChatMessage], int | None, int | None, bool]:
    from app.services.chat_visibility import mark_whisper

    if result.deleted_chat_id and result.deleted_chat_id == chat.id:
        return [], result.created_chat_id, result.deleted_chat_id, False

    if result.cleared_chat:
        reply = ChatMessage(
            tenant_id=auth.tenant_id,
            chat_id=chat.id,
            sender_user_id=None,
            agent_slug="lead",
            body=result.reply or "Chat cleared.",
            audio_url=None,
            visibility="public",
        )
        if whisper:
            mark_whisper(reply, auth.user_id)
        db.add(reply)
        db.flush()
        return [reply], result.created_chat_id, result.deleted_chat_id, True

    audio_url = None
    if speak and result.reply:
        try:
            path = synthesize_speech(result.reply[:800])
            audio_url = f"/media/tts/{path.name}"
        except TTSError:
            audio_url = None

    reply = ChatMessage(
        tenant_id=auth.tenant_id,
        chat_id=chat.id,
        sender_user_id=None,
        agent_slug=result.agent_slug or "lead",
        body=result.reply,
        audio_url=audio_url,
        visibility="public",
    )
    if whisper:
        mark_whisper(reply, auth.user_id)
    db.add(reply)
    db.flush()
    return [reply], result.created_chat_id, result.deleted_chat_id, False


def _run_agent_branch(
    db: Session,
    auth: AuthContext,
    chat: Chat,
    text: str,
    forced_agent: str | None,
    force: bool = False,
) -> IntentResult:
    project_id = chat.project_id or 1
    if forced_agent and forced_agent != "lead":
        agents = [forced_agent]
        plan_reason = "mention"
        used_llm = False
    else:
        plan = classify_request(text)
        agents = plan.agents
        plan_reason = plan.reason
        used_llm = plan.used_llm

    if not agents:
        return IntentResult(True, "No agent selected.", "lead")

    if agents[0] == "coding" and not force:
        from app.services.file_claims import extract_paths, find_collisions

        paths = extract_paths(text)
        collisions = find_collisions(
            db,
            tenant_id=auth.tenant_id,
            project_id=project_id,
            user_id=auth.user_id,
            paths=paths,
        )
        if collisions:
            _PENDING_CODING[(auth.user_id, chat.id)] = text
            lines = [
                "WARNING: file claim conflict - coding not started.",
                "Claimed by others:",
            ]
            for c in collisions:
                lines.append(f"- `{c.path_pattern}` (user_id={c.user_id})")
            lines.append("Say `proceed` or `force code ...` to run anyway.")
            return IntentResult(True, "\n".join(lines), "lead")

    # only run first agent synchronously for chat snappiness; handoffs drained
    req, job_ids, _ = create_work_request(
        db,
        tenant_id=auth.tenant_id,
        project_id=project_id,
        user_id=auth.user_id,
        text=text,
        plan=RoutePlan(agents=agents, reason=plan_reason, used_llm=used_llm),
    )
    db.flush()
    db.commit()
    drain_queue(max_jobs=30)
    jobs = db.query(Job).filter(Job.request_id == req.id).order_by(Job.id).all()
    arts = (
        db.query(Artifact)
        .filter(Artifact.job_id.in_([j.id for j in jobs] or [-1]))
        .order_by(Artifact.id)
        .all()
    )
    if not arts:
        err = next((j.error for j in jobs if j.error), "no output")
        return IntentResult(True, f"Lead→{agents}: failed ({err})", agents[0])
    chunks = [f"[Lead routed → {', '.join(agents)} | {plan_reason}]"]
    for a in arts:
        chunks.append(f"--- {a.agent_type} ---\n{a.content}")
    body = "\n\n".join(chunks)
    cands = _candidate_objectives(
        db,
        tenant_id=auth.tenant_id,
        project_id=project_id,
        user_id=auth.user_id,
        request_text=text,
    )
    body, confirm_ids = _with_confirm_footer(body, cands)
    return IntentResult(
        True,
        body,
        agents[-1] if agents else "lead",
        confirm_objective_ids=confirm_ids or None,
    )


def handle_chat_message(
    db: Session,
    *,
    auth: AuthContext,
    chat: Chat,
    user_message: ChatMessage,
    speak: bool = False,
) -> tuple[list[ChatMessage], int | None, int | None, bool]:
    """Process a user chat message.

    - `!...` commands: no LLM; whisper in team channels.
    - Team channels: plain text is human-only; `/` does not wake AI (skills are private-only).
    - Private rooms: until skills phase, non-! still routes to agents (Phase 5 tightens this).
    """
    from app.services.bang_commands import try_bang_command
    from app.services.chat_visibility import mark_whisper

    raw_body = (user_message.body or "").strip()
    is_channel = (chat.kind or "channel") == "channel"
    whisper = False

    # Bang commands - always available
    if raw_body.startswith("!"):
        if is_channel:
            mark_whisper(user_message, auth.user_id)
            whisper = True

        def _force(db_, auth_, chat_, pending):
            return _run_agent_branch(db_, auth_, chat_, pending, forced_agent="coding", force=True)

        result = try_bang_command(
            db,
            auth,
            chat,
            raw_body,
            IntentResult,
            _PENDING_CODING,
            _force,
        )
        if result is None:
            result = IntentResult(True, "Unknown command. Try `!help`.")
        return _post_lead_reply(
            db, auth=auth, chat=chat, result=result, speak=speak, whisper=whisper
        )

    # Team channel: no bare commands, no slash-AI
    if is_channel:
        return [], None, None, False

    # Private room: /skills only for AI; plain text = notes
    from app.services.skills import parse_skill, recent_private_context

    if raw_body.startswith("/"):
        parsed = parse_skill(raw_body)
        if parsed.hint and not parsed.agent:
            return _post_lead_reply(
                db,
                auth=auth,
                chat=chat,
                result=IntentResult(True, parsed.hint),
                speak=speak,
                whisper=False,
            )
        ask = parsed.rest.strip() or parsed.skill or "help"
        ctx = recent_private_context(db, chat=chat, user_id=auth.user_id)
        prompt = ask
        if ctx:
            prompt = (
                f"Private room context (recent):\n{ctx}\n\n"
                f"Skill=/{parsed.skill}. User ask:\n{ask}"
            )
        result = _run_agent_branch(
            db, auth, chat, prompt, forced_agent=parsed.agent
        )
        return _post_lead_reply(
            db, auth=auth, chat=chat, result=result, speak=speak, whisper=False
        )

    # Plain notes - no LLM
    return [], None, None, False

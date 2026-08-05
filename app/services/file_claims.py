from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.db.models import FileClaim, Objective

PATH_RE = re.compile(r"([\w./-]+\.(?:py|ts|js|tsx|go|rs|java|css|html|md))")


def extract_paths(text: str) -> list[str]:
    return list(dict.fromkeys(PATH_RE.findall(text or "")))


def paths_overlap(a: str, b: str) -> bool:
    a = a.rstrip("*")
    b = b.rstrip("*")
    return a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/") or (
        a.endswith("*") and b.startswith(a[:-1])
    ) or (b.endswith("*") and a.startswith(b[:-1]))


def claim_path(
    db: Session,
    *,
    tenant_id: int,
    project_id: int,
    user_id: int,
    path_pattern: str,
    objective_id: int | None = None,
    branch: str | None = None,
) -> FileClaim:
    row = FileClaim(
        tenant_id=tenant_id,
        project_id=project_id,
        user_id=user_id,
        objective_id=objective_id,
        path_pattern=path_pattern.strip(),
        branch=branch,
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def release_claims_for_objective(db: Session, objective_id: int) -> int:
    rows = db.query(FileClaim).filter(FileClaim.objective_id == objective_id, FileClaim.active.is_(True)).all()
    for r in rows:
        r.active = False
    return len(rows)


def release_user_path(
    db: Session, *, tenant_id: int, project_id: int, user_id: int, path_pattern: str
) -> int:
    rows = (
        db.query(FileClaim)
        .filter(
            FileClaim.tenant_id == tenant_id,
            FileClaim.project_id == project_id,
            FileClaim.user_id == user_id,
            FileClaim.active.is_(True),
            FileClaim.path_pattern == path_pattern,
        )
        .all()
    )
    for r in rows:
        r.active = False
    return len(rows)


def find_collisions(
    db: Session,
    *,
    tenant_id: int,
    project_id: int,
    user_id: int,
    paths: list[str],
) -> list[FileClaim]:
    if not paths:
        return []
    active = (
        db.query(FileClaim)
        .filter(
            FileClaim.tenant_id == tenant_id,
            FileClaim.project_id == project_id,
            FileClaim.active.is_(True),
            FileClaim.user_id != user_id,
        )
        .all()
    )
    hits: list[FileClaim] = []
    for claim in active:
        for p in paths:
            if paths_overlap(claim.path_pattern, p):
                hits.append(claim)
                break
    return hits


def auto_claim_from_objective(db: Session, obj: Objective) -> list[FileClaim]:
    from app.services.board import owner_id

    paths = extract_paths(obj.title)
    created = []
    for p in paths:
        created.append(
            claim_path(
                db,
                tenant_id=obj.tenant_id,
                project_id=obj.project_id,
                user_id=owner_id(obj),
                path_pattern=p,
                objective_id=obj.id,
                branch=obj.github_branch,
            )
        )
    return created


def claims_for_objective(db: Session, objective_id: int) -> list[str]:
    rows = (
        db.query(FileClaim)
        .filter(FileClaim.objective_id == objective_id, FileClaim.active.is_(True))
        .all()
    )
    return [r.path_pattern for r in rows]

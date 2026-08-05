from __future__ import annotations

import time

from app.agents.runner import execute_job
from app.db.models import Job
from app.db.session import SessionLocal, init_db
from app.services.llm import LLMClient


def process_one(db, llm: LLMClient | None = None) -> bool:
    job = db.query(Job).filter(Job.status == "queued").order_by(Job.id.asc()).first()
    if job is None:
        return False
    execute_job(db, job, llm)
    db.commit()
    return True


def run_worker(poll_seconds: float = 1.0, once: bool = False) -> None:
    init_db()
    llm = LLMClient()
    while True:
        db = SessionLocal()
        try:
            worked = process_one(db, llm)
        finally:
            db.close()
        if once:
            break
        if not worked:
            time.sleep(poll_seconds)


def drain_queue(max_jobs: int = 50) -> int:
    """Process up to max_jobs queued items (including newly enqueued handoffs)."""
    init_db()
    llm = LLMClient()
    processed = 0
    for _ in range(max_jobs):
        db = SessionLocal()
        try:
            if not process_one(db, llm):
                break
            processed += 1
        finally:
            db.close()
    return processed

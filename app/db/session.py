from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base

_settings = get_settings()
connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):
    if _settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _sqlite_migrate() -> None:
    """Best-effort additive migrations for existing aio.db files."""
    if not get_settings().database_url.startswith("sqlite"):
        return
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
        if cols and "email" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(255)"))
            users = conn.execute(text("SELECT id FROM users")).fetchall()
            for (uid,) in users:
                conn.execute(
                    text("UPDATE users SET email = :e WHERE id = :id AND (email IS NULL OR email = '')"),
                    {"e": f"user{uid}@local.test", "id": uid},
                )

        def _add(table: str, column: str, decl: str) -> None:
            tcols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
            if tcols and column not in tcols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {decl}"))

        _add("objectives", "status", "VARCHAR(40) DEFAULT 'todo'")
        _add("objectives", "assignee_user_id", "INTEGER")
        _add("objectives", "github_pr_url", "VARCHAR(512)")
        _add("objectives", "github_branch", "VARCHAR(255)")
        _add("objectives", "github_pr_number", "INTEGER")
        _add("objectives", "description", "TEXT")
        _add("task_items", "objective_id", "INTEGER")
        _add("projects", "github_token", "VARCHAR(255)")
        _add("agent_metrics", "user_id", "INTEGER")
        _add("agent_metrics", "tokens", "INTEGER")
        _add("users", "password_hash", "VARCHAR(255)")
        _add("tenants", "invite_token", "VARCHAR(64)")
        _add("tenants", "invite_max_uses", "INTEGER")
        _add("tenants", "invite_uses_left", "INTEGER")
        # backfill status from done
        ocols = {row[1] for row in conn.execute(text("PRAGMA table_info(objectives)")).fetchall()}
        if "status" in ocols and "done" in ocols:
            conn.execute(
                text("UPDATE objectives SET status = 'done' WHERE done = 1 AND (status IS NULL OR status = '')")
            )
            conn.execute(
                text("UPDATE objectives SET status = 'todo' WHERE (status IS NULL OR status = '') AND done = 0")
            )
            conn.execute(
                text(
                    "UPDATE objectives SET assignee_user_id = user_id "
                    "WHERE assignee_user_id IS NULL"
                )
            )


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _sqlite_migrate()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

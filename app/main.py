from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import (
    agent_settings,
    attachments,
    audit,
    chats,
    github,
    health,
    jobs,
    objectives,
    projects,
    requests,
    reviews,
    rooms,
    stt,
    tts,
    workspace,
)
from app.db.session import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="AIO Agent Workspace", version=__version__, lifespan=lifespan)
app.include_router(health.router)
app.include_router(projects.router)
app.include_router(requests.router)
app.include_router(jobs.router)
app.include_router(audit.router)
app.include_router(rooms.router)
app.include_router(github.router)
app.include_router(reviews.router)
app.include_router(objectives.router)
app.include_router(tts.router)
app.include_router(stt.router)
app.include_router(workspace.router)
app.include_router(agent_settings.router)
app.include_router(chats.router)
app.include_router(attachments.router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def root():
    return RedirectResponse(url="/app")


@app.get("/app")
def app_page():
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return {"error": "UI missing"}
    return FileResponse(index)

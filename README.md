# AIO

### The multi-agent workplace that lives in your terminal

**AIO** is a full workspace for small engineering teams — team chat, private AI rooms, an objectives board, agent skills, and owner analytics — running as a **native terminal app** against a shared FastAPI backend. No browser required. Same API whether you use the Textual TUI, scripted CLI, or the legacy web UI.

```
┌ AIO ──────────────────────── a@local.test · project 1 ── 20:04 ┐
│  Chat  Board  Agents  People  Dash  Live            Tour  Log out │
├──────────────┬────────────────────────────────────────────────────┤
│ CHATS        │ #general                                           │
│  # general   │  Sara · 19:58                                      │
│  ◆ my room   │  standup in 5?                                     │
│ MEMBERS      │                                                    │
│  ● Demo User A ★                                                  │
│  ● Sara                                                           │
│  ○ Omar                                                           │
│              │  Sara is typing…                                   │
│              │  > /  !  @ · + attach · mic                        │
└──────────────┴────────────────────────────────────────────────────┘
 repo · jobs · agent working · runner · @mentions · online 2/4
```

> **One product. Three surfaces.** Terminal app (`aio`) · HTTP API · optional web UI at `/app`.

---

<p align="center">
  <a href="#quick-start"><img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+" /></a>
  <a href="#stack"><img src="https://img.shields.io/badge/FastAPI-0.115%2B-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" /></a>
  <a href="#stack"><img src="https://img.shields.io/badge/Textual-TUI-4B8BBE?style=for-the-badge" alt="Textual TUI" /></a>
  <a href="#license"><img src="https://img.shields.io/badge/License-Private-111111?style=for-the-badge" alt="Private" /></a>
</p>

<p align="center">
  <b>Chat · Board · Agents · Presence · Tour · Owner Dash / Live</b>
</p>

---

## Why AIO

| Problem | What AIO does |
|--------|----------------|
| Agents clutter shared chat | **`/skills` run in your private room**; channel `!` traffic is **whisper-only** (only you see it) |
| Slack + Linear + Cursor = tab hell | **One terminal surface** for people, work, and agents |
| “Who’s online?” missing in CLI tools | **Live presence** + **“Sara is typing…”** in shared channels |
| Demo / onboard friction | Guided **Tour** with spotlights; invite links for teammates |

Built for teams of ~5–6 who want metal-serious agent workflows without leaving the shell.

---

## Table of contents

- [Quick start](#quick-start)
- [Living in the app](#living-in-the-app)
- [Mental model](#mental-model)
- [Features](#features)
- [CLI reference](#cli-reference)
- [Configuration](#configuration)
- [Demo accounts](#demo-accounts)
- [Develop](#develop)
- [Stack](#stack)
- [Non-goals](#non-goals-v1)
- [License](#license)

---

## Quick start

```bash
git clone <your-repo-url>
cd WORK

python3 -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
cp .env.example .env
# Add at least one LLM key — see Configuration

./aio seed                                          # or: aio seed
uvicorn app.main:app --host 0.0.0.0 --port 8000     # API (keep running)
```

Second terminal:

```bash
./aio doctor    # API · git · workspaces · GitHub · research · coding runners
./aio           # opens the full-screen app (signs in on first launch)
```

Credentials land in `~/.aio/credentials.json` (mode `600`).  
`aio`, `aio tui`, and `aio up` all open the same app.

Reset demo data:

```bash
rm -f aio.db && ./aio seed
# Windows: del aio.db ; aio seed
```

---

## Living in the app

### Navigation

| Keys | Opens |
|------|--------|
| `c` `b` `g` · `1`–`3` · click | **Chat** · **Board** · **Agents** (everyone) |
| `p` `d` `v` · `4`–`6` | **People** · **Dash** · **Live** (**owner only** — hidden for members) |
| `?` | Keys & commands |
| `ctrl+n` | Unread @mentions |
| `ctrl+r` · `q` | Refresh · quit |
| `Tour` / `Log out` | Guided walkthrough · sign out in-process |

### Chat

Type and press Enter.

| Prefix | Role |
|--------|------|
| `/` | AI skills (mostly **my room**) — `/ask` `/deepresearch` `/code` `/write` `/review` `/checklist` `/status` |
| `!` | Board / ops — **whispers** in channels (only you see the exchange) |
| `@` | Ping people (`@Sara`, `@team`) — sound + jump via `ctrl+n` |

Also: **`+` attach** · **mic** voice · hover **edit / delete** on your lines · speaker-style message blocks · **online dots** on the roster · **typing** indicators in shared channels.

While an agent runs you get a live placeholder; other rooms stay usable.

### Board

| Key | Action |
|-----|--------|
| `j` / `k` · `h` / `l` | Move card · column |
| `n` | New objective |
| `s` | Set status |
| `a` | Hand to coding agent (`agent_backlog`) |
| `m` | **Merge & done** (owner; confirms first) |
| `o` / `y` | Open PR · copy URL |

### Agents · People · Dash · Live

- **Agents** — pick the model behind each `/skill` (OpenRouter free tiers and/or Gemini).
- **People** *(owner)* — invite, promote, demote, remove.
- **Dash** *(owner)* — people / models / tokens / open work tables.
- **Live** *(owner)* — gauges & sparklines; polls ~2s; redraws only on change.

---

## Mental model

| Surface | For |
|---------|-----|
| `#channels` | Humans — talk, @ping, check status; `!` stays private to you |
| `◆ my room` | Your AI workspace — `/skills`, quiet notes |
| Board tab / `!` | Shared work graph |
| Owner tabs | Roster admin + analytics |

**People in `#general`. Agents in your room. Work on the board.**

---

## Features

### Team chat
- Shared **channels** + per-user **private room**
- Speaker blocks, timestamps, markdown replies
- `@` mentions with autocomplete, unread flash + sound
- Attachments (PDF / images / text — extracted into agent context)
- Edit own messages (keeps later user lines; refreshes following agent replies)
- Delete message + following agent replies
- `/clear` / `!clear` (channel clear is per-user)
- **Presence**: online / offline on the member list
- **Typing**: `Sara is typing…` in shared channels
- Optional **mic** (Whisper) and TTS when Groq is configured

### AI skills (private room)

| Skill | Purpose |
|-------|---------|
| `/ask` | General Q&A (works great with attachments) |
| `/deepresearch` | Sourced briefing via Tavily — invented URLs stripped |
| `/code` | Build or patch |
| `/write` | Draft prose |
| `/review` | Check a diff |
| `/checklist` | Break work into ticks |
| `/status <name>` | AI catch-up on a member (owner; whisperable in channels) |

### Board & GitHub
- Columns: `todo` → `doing` → `blocked` → `agent_backlog` → `in_review` → `done`
- Real repo / PR / branch badges on review cards (never fabricated)
- Optional **Codex** / **Claude Code** runners on `agent_backlog` (falls back to LLM)
- Owner **Merge & done** after GitHub confirms merge

### Onboarding & ops
- Spotlight **Tour** (member path + owner extras)
- Invite links (`!invite`) · optional Teams webhook
- Scriptable CLI for CI (`aio board`, `aio set`, `aio merge`, …)
- Legacy web UI at `/app` for demos

### Commands (`!`)

Type `!help` in chat. In channels, command traffic is **whisper-only**.

| Area | Examples |
|------|----------|
| Work | `!add` · `!list` · `!set` · `!done` · `!assign` |
| Links | `!link <id> branch` · `!link <id> pr` |
| Files | `!claim` · `!release` · `!go` |
| Issues | `!issue` · `!issues` · `!resolve` |
| Room | `!invite` · `!clear` · `!help` |

Prefer **`/status`** for catch-up over legacy `!status` stubs.

---

## CLI reference

| Command | Purpose |
|---------|---------|
| `aio` | **Open the app** (`aio tui` / `aio up`) |
| `aio login` · `logout` · `whoami` | Credentials (`~/.aio/credentials.json`) |
| `aio doctor` | Preflight checks |
| `aio board` · `aio card <id>` | Board / card detail |
| `aio set <id> <status> [--runner …]` | Move card; start agent on `agent_backlog` |
| `aio merge <id> [--yes]` | Confirm, merge PR, mark done |
| `aio pr <id> [--open]` | Print or open PR URL |
| `aio workspaces` | Local agent checkouts |
| `aio chat` · `aio say` | Read / post chat from scripts |
| `aio members` · `aio projects use` | Roster · default project |
| `aio ask` · `aio jobs` · `aio drain` · `aio worker` | Pipeline helpers |
| `aio seed` | Demo tenant + users |

### Scripted loop

```bash
./aio board
./aio set 12 agent_backlog --runner codex   # or claude_code | llm
./aio card 12
./aio merge 12 --yes
```

### Optional coding CLIs

```bash
npm i -g @openai/codex                 # CODING_BACKEND=codex
npm i -g @anthropic-ai/claude-code     # CODING_BACKEND=claude_code
```

Auth via env (`CODEX_API_KEY`, `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`) — never on the command line. Missing binaries fall back to the LLM coding path.

---

## Configuration

Copy `.env.example` → `.env`.

| Variable | Purpose |
|----------|---------|
| `GEMINI_API_KEY` | Gemini models (default path) |
| `OPENROUTER_API_KEY` | Free model picker in Agents — [keys](https://openrouter.ai/keys) |
| `GROQ_API_KEY` | TTS + Whisper |
| `TAVILY_API_KEY` | Live sources for `/deepresearch` |
| `GITHUB_TOKEN` / `GITHUB_REPO` | PRs from agent backlog |
| `AGENT_WORK_ROOT` | Per-objective git checkouts (default `data/workspaces`) |
| `CODING_BACKEND` | `llm` · `codex` · `claude_code` · `opencode` |
| `AGENT_LLM_BACKEND` | `auto` · `gemini` · `openrouter` · `opencode` |
| `MERGE_METHOD` | `squash` · `merge` · `rebase` |
| `INVITE_APP_URL` | Public base for invite links |
| `TEAMS_WEBHOOK_URL` | Optional invite notify |
| `DATABASE_URL` | Default `sqlite:///./aio.db` |
| `API_BASE_URL` | CLI → API (default `http://127.0.0.1:8000`) |

---

## Demo accounts

Password for all seeded users: **`demo`**

| Email | Role |
|-------|------|
| `a@local.test` | **Owner** — People, Dash, Live, invites, merge |
| `omar@local.test` | Member — Chat, Board, Agents |
| `sara@local.test` | Member — Chat, Board, Agents |

### Two-minute terminal demo

```bash
./aio
# 1  Chat → /ask what should we ship first?
# 2  Board → pick a todo → a → watch agent_backlog → PR
# 3  Owner → m → confirm merge → card → done
# 4  Second login → presence dots + typing in #general
```

### Web demo

1. Open `http://127.0.0.1:8000/app` as `omar@local.test` / `demo`
2. `#general` → `!add …` · Board drag
3. **MY ROOM** → `/ask` or `/write`
4. Owner login → `/status Omar` · `!invite`

More prompts: [`commands.txt`](commands.txt).

---

## Develop

```bash
source .venv/bin/activate   # or Windows Activate.ps1
pytest -q
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

TUI smoke (API up + logged in once):

```bash
python scripts/tui_smoke.py --shot
python scripts/tui_pty_check.py
```

Helpers:

```bash
./aio seed
./aio drain
./aio webhook-sim --title "Ship #obj-1" --action opened
```

---

## Stack

| Layer | Choice |
|-------|--------|
| API | **FastAPI** + Uvicorn |
| Data | **SQLite** (WAL) · SQLAlchemy · Postgres-capable via `DATABASE_URL` |
| Terminal | **Textual** + Typer + Rich |
| Web (legacy) | Vanilla JS at `/app` |
| Agents | Gemini · OpenRouter · optional Codex / Claude Code / OpenCode |
| Research | Tavily-backed `/deepresearch` |
| Voice | Groq Whisper / TTS |

**Transport:** HTTP poll (presence, chat, board). No Redis / WebSockets required for v1.

---

## vs Buzz (mental contrast)

| | Typical agent chat | **AIO** |
|--|--------------------|---------|
| Agents | Mixed into shared rooms | `/skills` in **private** rooms |
| People | Competing with bots | `#channels` are for humans |
| Ops | Ad-hoc | `!` whispers + first-class **Board** |
| Presence | Rare in CLI | Online + typing in the TUI |

---

## Non-goals (v1)

- Lead reading another user’s private prompts  
- Public SaaS · mobile app · WebSockets as the primary transport  
- Auto-merge from chat · per-user GitHub OAuth  

---

## License

**Private / internal.** Adjust before publishing if you open-source.

---

<p align="center">
  <sub>AIO — agents in your room · people in the channel · work on the board.</sub>
</p>

<p align="center">
  <img src="docs/aio-logo.svg" alt="AIO" width="72" />
</p>

<h1 align="center">AIO</h1>

<p align="center">
  <strong>The multi-agent workplace that lives in your terminal</strong>
</p>

<p align="center">
  <a href="#quick-start"><img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+" /></a>
  <a href="#stack"><img src="https://img.shields.io/badge/FastAPI-0.115%2B-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI" /></a>
  <a href="#stack"><img src="https://img.shields.io/badge/Textual-TUI-4B8BBE?style=flat-square" alt="Textual TUI" /></a>
  <a href="#license"><img src="https://img.shields.io/badge/License-Private-111111?style=flat-square" alt="Private" /></a>
</p>

<p align="center">
  Team chat / private AI rooms / objectives board / Codex / Claude Code / live presence<br/>
  <em>Native terminal app / shared FastAPI backend / no browser required</em>
</p>

<p align="center">
  <img src="docs/aio-hero.png" alt="AIO in Windows Terminal: chat, presence, and composer" width="920" />
</p>

<p align="center">
  <code>aio</code> terminal &nbsp;/&nbsp; HTTP API &nbsp;/&nbsp; legacy web at <code>/app</code>
</p>

<p align="center">
  <a href="#host-owner"><strong>Host</strong></a>
  &nbsp;/&nbsp;
  <a href="#member"><strong>Member</strong></a>
  &nbsp;/&nbsp;
  <a href="#living-in-the-app">Living in the app</a>
  &nbsp;/&nbsp;
  <a href="#board--coding-runners">Board &amp; runners</a>
</p>

<pre>
# Member: ./setup.sh  or  .\setup.cmd   |   Host: ./setup.sh --host  then T1–T4
</pre>

---

## Why AIO

| Problem | What AIO does |
|--------|----------------|
| Agents clutter shared chat | **`/` skills** in AI chats; public `!` traffic is **whisper-only** |
| Slack + Linear + Cursor = tab hell | **One terminal** for people, work, and coding agents |
| "Who's here?" missing in CLI tools | **Online dots** + **typing** indicators |
| Who can create / see what? | **Members:** private chats + **own board cards**. **Owners:** full board + People / Dash / Live |
| Need real Codex / Claude | Board **`a`** -> pick runner, or **`!claude` / `!codex`** open the real CLIs |
| Off-LAN join | Tunnel + **Server** URL on Sign in |

Built for small teams who want serious agent workflows without leaving the shell.

---

## Table of contents

- [Host (owner)](#host-owner)
- [Member](#member)
- [Living in the app](#living-in-the-app)
- [Creating chats](#creating-chats)
- [Mentions](#mentions)
- [Board & coding runners](#board--coding-runners)
- [Features](#features)
- [CLI reference](#cli-reference)
- [Configuration](#configuration)
- [Demo accounts](#demo-accounts)
- [Develop](#develop)
- [Stack](#stack)
- [Related repo](#related-repo)
- [Non-goals](#non-goals-v1)
- [License](#license)

---

## Host (owner)

You run the API. Everyone else connects to you.

**Once**
```bash
git clone <repo> && cd WORK
./setup.sh --host          # Windows: .\setup.cmd --host
```
- Edit `.env` → your API keys ([list](#configuration))
- Install tunnel once: `brew install cloudflared` (Windows: Cloudflare download)
- Chromium prompt: **y** only if you want Outlook invite emails; **n** is fine (share links from chat)

**Every time you host (3–4 terminals)**

| # | Run | Notes |
|---|-----|--------|
| 1 | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | activate `.venv` first |
| 2 | `cloudflared tunnel --url http://127.0.0.1:8000` | put the `https://…trycloudflare.com` URL in `.env` as `INVITE_APP_URL=` |
| 3 | `./aio outlook-login` *(optional, once)* | only if you said **y** to Chromium |
| 4 | `./aio` | Server = `http://127.0.0.1:8000` · login `a@local.test` / `demo` |

Invite people: `!invite email@…` or copy the join link from chat.

Same Wi‑Fi only? Skip terminal 2; members use `http://YOUR_LAN_IP:8000`.

---

## Member

You do **not** run the API, Cloudflare, or Chromium.

1. Open the invite link → create account → copy **Server** from the Done page  
2. Then:

```bash
# macOS / Linux
git clone <repo> && cd WORK && ./setup.sh
```
```powershell
# Windows
git clone <repo>; cd WORK; .\setup.cmd
```

3. Sign in → paste **Server** + your email/password  

Next time: `./setup.sh` or `.\setup.cmd` (or `./aio` / `.\aio.cmd`).

---

## Living in the app

### Navigation

| Keys / control | Opens |
|----------------|--------|
| `c` `b` `g` / `1`-`3` | **Chat** / **Board** / **Agents** *(everyone)* |
| `p` `d` `v` / `4`-`6` | **People** / **Dash** / **Live** *(owner only)* |
| `?` | Short Help |
| `@N` (tabs row) / `ctrl+n` | Unread mentions -> jump to message |
| `Tour` / `F1` | Spotlight walkthrough (member vs owner paths) |
| `Log out` | Sign out (marks you offline) |
| `q` | Quit |

Footer stays minimal (`help` / `quit` / `a agent`). Most shortcuts live in Help / Tour.

### Chat

| Prefix | Role |
|--------|------|
| `/` | AI skills: `/ask` `/deepresearch` `/code` `/write` `/review` `/checklist` `/status` `/clear` |
| `!` | Board / ops: **whispers** in public channels |
| `@` | Ping people |

Also: **`+` attach**, **mic**, hover **edit / delete**, speaker blocks, **online**, **typing**.

Local bangs (do **not** hit the LLM):

| Bang | Effect |
|------|--------|
| `!claude` | Open Claude Code in a **new terminal** |
| `!codex` | Open Codex in a **new terminal** |
| `!attach` | File picker |
| `!invite` | Mint join link (owner) |

---

## Creating chats

Press **`+ channel`** (or `ctrl+shift+n`).

| | **Member** | **Owner** |
|--|------------|-----------|
| Visibility | **Private only** | **Public** or **Private** |
| Purpose | **`!` commands** or **`/` AI** | Same |

Sidebar: `#` public, `◆` private, trailing `!` or `/` = purpose.

| Mode | What works |
|------|------------|
| **`!` commands** | Board/ops bangs, human chat in public rooms |
| **`/` AI skills** | Full skill set (public AI rooms -> skill traffic is **whisper-only**) |

Seeded defaults: **◆ my room** = private AI (`/`). **#general** = team ops (`!`).

---

## Mentions

1. Someone `@you` in a chat -> soft ping sound (poll ~1.5s).
2. **`@N`** appears in the tabs row (next to **Tour**).
3. Click **`@N`** (or `ctrl+n`) -> list of who / time / chat / snippet.
4. Select a row -> opens that chat and **highlights** the message.

---

## Board & coding runners

### Visibility & edit

| Role | Sees | Can send to agent (`a`) |
|------|------|-------------------------|
| **Member** | Cards they **created** or are **assigned** | Those cards |
| **Owner** | **All** cards | Any card |

### Structured coding (proves Codex / Claude)

1. Create / select a card on **Board**.
2. Press **`a`** -> pick **`codex`**, **`claude_code`**, or **`llm`**.
3. Card moves to **agent_backlog** -> workspace under `data/workspaces/obj-*` -> optional PR on [AIOPlayground](https://github.com/YoussiefKhalifa2003/AIOPlayground).

Chat **`/ask`** etc. stay on OpenRouter / Gemini. Keep `CODING_BACKEND=llm` so chat `/code` does not unexpectedly spawn Codex.

```bash
aio doctor
aio set 12 agent_backlog --runner codex
aio set 12 agent_backlog --runner claude_code
aio board-wipe --yes          # owner: clear all cards + local workspaces
```

### Interactive Claude / Codex

Full CLI apps (not embedded in chat):

```text
!claude
!codex
```

Requires the CLIs on PATH (`npm i -g @anthropic-ai/claude-code`, `@openai/codex`). Claude needs login or `ANTHROPIC_API_KEY`; Codex needs login or `CODEX_API_KEY`.

---

## Features

### Team chat
- Public + private rooms (create flow above)
- @mentions, `@N` panel, sound
- Attachments -> agent context
- Presence + typing
- Optional mic (Whisper) / TTS when Groq is set

### AI skills

| Skill | Purpose |
|-------|---------|
| `/ask` | Q&A (+ attachments) |
| `/deepresearch` | Sourced briefing (Tavily) |
| `/code`, `/write`, `/review`, `/checklist` | Build, draft, check, break down |
| `/status <name>` | AI catch-up (owner) |

### Board & GitHub
- Columns through `agent_backlog` -> `in_review` -> `done`
- Per-card runners: **Codex**, **Claude Code**, **llm**
- Repo / PR / branch badges, owner **merge**
- `aio board-wipe --yes` for clean demos

### Onboarding & ops
- Spotlight **Tour** (member path + owner extras)
- Invite links, Outlook / Teams copy for off-LAN
- Scriptable CLI, legacy web at `/app`

### Commands (`!`)

Type `!help`. In public channels, command traffic is **whisper-only**.

| Area | Examples |
|------|----------|
| Work | `!add`, `!list`, `!set`, `!done`, `!assign` |
| Links | `!link <id> branch`, `!link <id> pr` |
| Issues | `!issue`, `!issues`, `!resolve` |
| Room | `!invite`, `!clear`, `!claude`, `!codex`, `!help` |

---

## CLI reference

| Command | Purpose |
|---------|---------|
| `aio` | Open the app (Sign in first) |
| `aio login`, `logout`, `whoami` | Credentials |
| `aio doctor` | Preflight (API, invite URL, CLIs, keys) |
| `aio board`, `aio card`, `aio set`, `aio merge` | Board loop |
| `aio board-wipe --yes` | Owner: wipe all cards + workspaces |
| `aio chat`, `aio say` | Script chat |
| `aio seed` | Demo tenant + users |

```bash
./aio board
./aio set 12 agent_backlog --runner codex
./aio merge 12 --yes
```

---

## Configuration

Host only: copy `.env.example` → `.env` and set your keys. Members do not need these.

You can change any of these anytime — edit `.env`, then **restart uvicorn** so the API reloads them.

| Variable | Purpose |
|----------|---------|
| `GEMINI_API_KEY` | Gemini models (chat / skills) |
| `OPENROUTER_API_KEY` | Model picker / alternate chat models |
| `GROQ_API_KEY` | TTS + mic (Whisper) |
| `TAVILY_API_KEY` | `/deepresearch` web research |
| `CODEX_API_KEY` | Codex board runner (or use `codex` CLI login) |
| `ANTHROPIC_API_KEY` | Claude board runner (or use `claude` CLI login) |
| `GITHUB_TOKEN` / `GITHUB_REPO` | Agent PRs (see [AIOPlayground](#related-repo)) |
| `CODING_BACKEND` | Keep `llm` for chat; use board runners for CLIs |
| `DATABASE_URL` | Default `sqlite:///./aio.db` |
| `API_BASE_URL` | Owner local CLI → API (`http://127.0.0.1:8000`) |
| `INVITE_APP_URL` | Public join-link origin (Cloudflare HTTPS for off-LAN) |

Example `.env` snippets:
```env
GROQ_API_KEY=gsk_...
TAVILY_API_KEY=tvly-...
CODEX_API_KEY=...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
OPENROUTER_API_KEY=sk-or-...
```

---

## Demo accounts

Password: **`demo`**

| Email | Role |
|-------|------|
| `a@local.test` | **Owner**: full board, People, Dash, Live, merge, wipe |
| `omar@local.test` | **Member**: own cards, Chat / Board / Agents |
| `sara@local.test` | **Member**: same |

### Two-minute demo

```bash
./aio
# Tour (F1): member vs owner paths
# @ping between two logins -> @N next to Tour -> jump
# Board: card -> a -> codex (or claude_code)
# Chat: !claude / !codex open real CLIs
# Owner: !invite after tunnel INVITE_APP_URL (see Host section)
```

More prompts: [`commands.txt`](commands.txt).

---

## Develop

```bash
source .venv/bin/activate
pytest -q
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

```bash
python scripts/tui_smoke.py --shot
python scripts/tui_pty_check.py
```

---

## Stack

| Layer | Choice |
|-------|--------|
| API | FastAPI + Uvicorn |
| Data | SQLite (WAL), SQLAlchemy, Postgres via `DATABASE_URL` |
| Terminal | Textual + Typer + Rich |
| Web | Vanilla JS `/app` |
| Agents | Gemini, OpenRouter, **Codex** / **Claude Code** CLIs |
| Research | Tavily |
| Presence | HTTP poll |

---

## Related repo

Agent PRs land on **[YoussiefKhalifa2003/AIOPlayground](https://github.com/YoussiefKhalifa2003/AIOPlayground)**: a sandbox repo (keep `README.md`; demo branches/PRs are disposable). Set `GITHUB_REPO=YoussiefKhalifa2003/AIOPlayground` in `.env`.

---

## Non-goals (v1)

- Embedding interactive Claude/Codex TUIs inside chat (use `!claude` / `!codex` windows instead)
- Lead reading another user's private prompts  
- Public SaaS, mobile, WebSockets-first transport  
- Auto-merge from chat, per-user GitHub OAuth  

---

## License

**Private / internal.** Adjust before publishing if you open-source.

---

<p align="center">
  <sub>AIO: public for the team / private for you / ! for work / / for agents / a for Codex and Claude.</sub>
</p>

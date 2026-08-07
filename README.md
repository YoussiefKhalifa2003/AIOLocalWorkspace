# AIO

**A multi-agent workplace that runs as a terminal app.** Type `aio` and the whole
workspace opens full-screen in your terminal: team chat, a private AI room, the
objectives board, the agent roster, and the owner dashboard. Same four tabs as
the web UI, same API behind them, no browser.

```
┌ AIO ──────────────────────── a@local.test · project 1 ── 22:41 ┐
│  Chat  Board  Agents  People  Dash  Live                       │
├──────────────┬─────────────────────────────────────────────────┤
│ CHATS        │ #general                                        │
│  # general   │  Alice   09:14   standup in 5                   │
│  ◆ my room   │  @ask    09:15   here's the summary you asked…  │
│ MEMBERS      │                                                 │
│  ● Alice ★   │  > message · /ask · !add · @name · ? for help   │
└──────────────┴─────────────────────────────────────────────────┘
 repo acme/widgets · jobs 44 · agent working 1 · runner llm · @2
```

Every member can run it. It is a real app, not a set of commands: it redraws
itself as things change, agents stream into the chat you are looking at, and the
board updates while you watch. The individual `aio <command>` subcommands are
still there for scripting and CI.

---

## Quick start

```bash
git clone <your-repo-url>
cd WORK

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# add at least one LLM key (see Configuration)

./aio seed
uvicorn app.main:app --host 0.0.0.0 --port 8000   # the API the CLI talks to
```

Then, in a second terminal:

```bash
./aio doctor      # API, git, workspaces, GitHub, research, coding runners
./aio             # opens the app (asks you to sign in the first time)
```

That is the whole setup. `aio` with no arguments is the app; it stores your
credentials in `~/.aio/credentials.json` (mode 600) so the next launch is
instant. `aio tui` and `aio up` do the same thing.

Reset demo data anytime:

```bash
rm -f aio.db && ./aio seed
```

### Living in the app

| Key | Does |
|-----|------|
| `c` `b` `g` `p` `d` `v` (or `1`–`6`, or click) | Chat · Board · Agents · People · Dash · Live |
| `?` | keys and commands |
| `ctrl+n` | unread mentions |
| `ctrl+r` · `q` | refresh now · quit |

**Chat** is the default tab: type and press enter. `/ask`, `/deepresearch`,
`/code`, `/write`, `/review`, `/checklist`, `/status` run agents; `!add`, `!set`,
`!claim`, `!issue`, `!invite` and friends run commands; `@name` pings someone,
and typing `/` `!` or `@` opens a dropdown. While an agent is thinking you get a
live placeholder instead of a frozen screen.

**Board** keys: `j`/`k` card, `h`/`l` column, `n` new objective, `s` set status,
`a` hand to a coding agent, `m` **Merge & done** (confirm first), `o` open the
PR, `y` copy its URL. Columns scroll sideways; the strip follows your selection.

**Agents** picks the model behind each `/skill`. **Dashboard** (`d`) is
owner-only tables — people, models, tokens, open work. **Live** (`v`) is the
owner-only chart board — gauges, sparklines, and WIP bars that poll every 2s.
Everything only redraws when something actually changed, so it never flickers.

### The same loop, scripted

```bash
./aio board                 # columns with repo / PR / branch per card
./aio set 12 agent_backlog  # hand the card to a coding agent
                            #   --runner codex | claude_code | llm
./aio card 12               # progress, links, workspace path
./aio merge 12              # confirm, merge the PR, card moves to done
```

### Optional: agentic coding CLIs

`agent_backlog` can hand work to a real coding agent that edits files in the
objective's git workspace instead of only generating text.

```bash
npm i -g @openai/codex          # then: CODING_BACKEND=codex
npm i -g @anthropic-ai/claude-code   # then: CODING_BACKEND=claude_code
```

Auth via env (never passed on the command line):

```bash
CODEX_API_KEY=...        # or sign in once with `codex`
ANTHROPIC_API_KEY=...    # or CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`
```

If a binary is missing, AIO falls back to the plain LLM coding path.

---

## CLI reference

| Command | What it does |
|---------|--------------|
| `aio` (no args) | **Open the app.** Same as `aio tui` / `aio up` |
| `aio login` / `logout` / `whoami` | Credentials in `~/.aio/credentials.json` (mode 600) |
| `aio doctor` | Preflight: API, git, workspace root, GitHub, Tavily, coding runners |
| `aio board` | Board with repo / PR / branch per card |
| `aio card <id>` | Card detail, subtasks, claims, links, workspace path |
| `aio set <id> <status> [--runner …]` | Move a card; `agent_backlog` starts the agent |
| `aio merge <id> [--method squash] [--yes]` | Confirm, merge the PR, card -> done |
| `aio pr <id> [--open]` | Print or open the PR URL |
| `aio workspaces` | Local checkouts under `AGENT_WORK_ROOT`, branch + dirty state |
| `aio chat <chat_id> [--follow]` / `aio say <chat_id> "…"` | Read / post chat |
| `aio members` · `aio projects use <id>` | Workspace members · default project |
| `aio ask` · `aio objectives` · `aio jobs` · `aio drain` · `aio worker` | Existing pipeline helpers |

---

## Legacy web UI

Still shipped and fully working at `/app`, mainly for member chat and demos.

| Where | URL |
|-------|-----|
| Local | http://127.0.0.1:8000/app |
| LAN | http://YOUR_LAN_IP:8000/app |

It shows the same board data, including repo / PR / branch badges and the
owner-only **Merge & done** confirmation modal.

---

## Demo logins

Password for all seeded users: **`demo`**

| Email | Role |
|-------|------|
| `a@local.test` | Owner (Lead) |
| `omar@local.test` | Member |
| `sara@local.test` | Member |

---

## Mental model

| Prefix | Where | What it does |
|--------|-------|----------------|
| `@` | `#general` | Ping people (`@Omar`, `@team`) |
| `/` | **MY ROOM** (mostly) | AI skills - `/ask`, `/code`, `/status`, … |
| `!` | Anywhere | Board / ops commands - **whisper** in channels (only you see them) |

One line: **people in `#general`, agents in your private room, board via `!` or the Board tab.**

---

## Features

### Chat
- Team channels + per-user **MY ROOM**
- `@` mentions with autocomplete
- Attachments (PDF, images, txt/md) - PDF/text is extracted into the agent prompt
- Edit your own messages (**ChatGPT-style**): later messages disappear and the ask is re-run
- Delete your message and its following agent replies go with it
- `/clear` / `!clear` to wipe chat history (channels: only for you)
- Timestamps in Asia/Dubai; markdown replies

### AI skills (private room)
| Skill | Purpose |
|-------|---------|
| `/ask` | General Q&A (attach a file and ask what it is) |
| `/deepresearch` | Briefing grounded in live sources, with a real `## Sources` list |
| `/code` | Build or patch |
| `/write` | Draft prose |
| `/review` | Check a diff |
| `/checklist` | Break work into ticks |
| `/status <name>` | AI catch-up on a member (owner; also works in channels as whisper) |

After skill work, you may get **Yes / No** on matching board objectives (`!done` / `!keep`).

### Board
- Columns: todo → doing → blocked → agent_backlog → in_review → done
- `aio set <id> <status>`, the TUI, drag in the web UI, or `!set <id> doing`
- `in_review` cards carry repo, `PR #N`, and branch links (never fabricated)
- Owner can assign cards across the team

### DeepResearch
- Searches with Tavily, fetches the pages, and cites only what it actually read
- Any URL the model invents is stripped as `[unverified link removed]`
- Without `TAVILY_API_KEY` it prints a `NO LIVE SOURCES` banner and emits no links

### Agents tab
- Pick which model powers each skill brain
- OpenRouter `:free` models and/or **Gemini** from `.env`

### Lead tools (owner)
- `/status Omar` - remaining work, issues, private-room skill activity
- Analytics tab - jobs / metrics
- Invite links (`!invite`) - optional Teams webhook notify

### Voice (optional)
- **Mic input** - Whisper → send (toggle in the header)

### GitHub (optional)
- `agent_backlog` → local workspace clone under `AGENT_WORK_ROOT` → coding agent edits
  real files → commit/push → PR → `in_review`
  (needs `GITHUB_TOKEN` + `GITHUB_REPO`; falls back to API commit if git clone/push fails)
- **Merge & done** is owner-only and always asks first; the card reaches `done`
  only after GitHub confirms the merge
- A PR merged outside AIO posts a notice instead of silently closing the card
- LAN demo: `./aio webhook-sim` then `./aio drain`

---

## Commands (`!`)

Type `!help` in chat. In `#general`, only **you** see command traffic.

| Area | Examples |
|------|----------|
| Work | `!add …` · `!list` · `!set <id> doing` · `!done <id>` · `!remove <id>` · `!assign <id> <name>` |
| Links | `!link <id> branch <name>` · `!link <id> pr <url>` |
| Files | `!claim <path>` · `!release <path>` · `!go` |
| Issues | `!issue …` · `!issues` · `!resolve <id>` |
| Room | `!invite [N]` · `!clear` · `!help` |

Catch-up: prefer **`/status`** (AI) over the old `!status` / `!team` stubs.

---

## Configuration

Copy `.env.example` → `.env`. Useful keys:

| Variable | Purpose |
|----------|---------|
| `GEMINI_API_KEY` | Gemini models (default path) |
| `OPENROUTER_API_KEY` | Free model picker in Agents tab - [keys](https://openrouter.ai/keys) |
| `GROQ_API_KEY` | TTS + Whisper |
| `GITHUB_TOKEN` / `GITHUB_REPO` | PRs from agent backlog (`owner/repo`) |
| `AGENT_WORK_ROOT` | Local git checkouts per objective (default `data/workspaces`) |
| `AGENT_GIT_TIMEOUT_SECONDS` | Clone/push timeout (default `120`) |
| `MERGE_METHOD` | `squash` (default) · `merge` · `rebase` for **Merge & done** |
| `TAVILY_API_KEY` | Live sources for `/deepresearch`; empty = no citations |
| `CODEX_BIN` / `CODEX_API_KEY` | Codex CLI coding runner |
| `CLAUDE_BIN` / `ANTHROPIC_API_KEY` | Claude Code CLI coding runner |
| `TUI_POLL_SECONDS` | Owner TUI refresh interval (default `2.0`) |
| `AGENT_MODEL_FAST` / `AGENT_MODEL_STRONG` | Model tiers |
| `AGENT_LLM_BACKEND` | `auto` · `gemini` · `openrouter` · `opencode` |
| `CODING_BACKEND` | `llm` (default) · `opencode` · `codex` · `claude_code` |
| `INVITE_APP_URL` | Public base for invite links (`http://YOUR_LAN_IP:8000`) |
| `TEAMS_WEBHOOK_URL` | Optional - post invite links to Teams |
| `UPLOADS_DIR` | Attachment storage (default `data/uploads`) |
| `DEMO_API_KEY` / `WORKSPACE_JOIN_KEY` | Seed / join key (default `demo-key-a`) |

---

## Two-minute demo (terminal app)

```bash
./aio
# 1  Chat tab: type  /ask what should we ship first?   → an agent answers inline
# 2  press 2 for the Board, pick a todo card, press a
#    → agent_backlog: it clones the repo, edits real files, opens a PR
# 3  watch the card land in in_review with repo / PR / branch badges
# 4  press m → confirm → merged into main, card moves to done
```

## Two-minute demo (web)

1. Login as `omar@local.test` / `demo`
2. **#general** → `!add Finish station notes` · `!issue Missing map PDF`
3. **Board** - see Omar’s card; drag it
4. **MY ROOM** → attach a PDF if you want → `/ask summarize this` or `/write one metro tip`
5. Edit an older ask - later replies vanish and the skill re-runs
6. Logout → `a@local.test` / `demo`
7. `/status Omar` - remaining work + private-room activity
8. Invite: `!invite` or MEMBERS **+** → colleague opens the link on your LAN

More copy-paste prompts: [`commands.txt`](commands.txt).

---

## Develop

```bash
source .venv/bin/activate
pytest -q
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Terminal app checks (need the API running and `aio login` done once):

```bash
python scripts/tui_smoke.py --shot   # drive every tab headlessly, write /tmp/aio-*.svg
python scripts/tui_pty_check.py      # launch in a real pty, press keys, quit cleanly
```

CLI helpers (same venv):

```bash
./aio seed
./aio drain
./aio webhook-sim --title "Ship #obj-1" --action opened
```

Stack: **FastAPI · SQLite · SQLAlchemy · Typer + Textual CLI**, with a legacy
vanilla-JS UI at `/app`.

---

## vs Buzz

| | Buzz | AIO |
|--|------|-----|
| Agents | `@` agents in shared chat | `/skills` in **private** rooms |
| People | Mixed with bots | `#general` is for humans (`@` pings) |
| Ops | - | `!` whispers + Board tab |

---

## Non-goals (v1)

- Lead reading another user’s private prompts  
- Public SaaS / mobile app / WebSockets  
- Auto-merge from chat · per-user GitHub OAuth  

---

## License

Private / internal - adjust before publishing if you open-source.

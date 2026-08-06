# AIO

**CLI-first multi-agent workplace.** The owner runs the whole thing from a live
terminal dashboard: objectives board, agent runs in real git workspaces, pull
requests, and a confirmed merge that closes the card. Members still get chat and
a private AI room in the (legacy) web UI.

```
aio tui   →  live owner dashboard: board, agents, PRs, Merge & done
aio board →  same data, one-shot
#general  →  people talk, @pings, !commands (whisper)
MY ROOM   →  /skills for AI, plain notes stay quiet
```

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
./aio login --email a@local.test --password demo
./aio projects use 1
./aio doctor      # API, git, workspaces, GitHub, research, coding runners
./aio tui         # live owner dashboard
```

Reset demo data anytime:

```bash
rm -f aio.db && ./aio seed
```

### The owner loop

```bash
./aio board                 # columns with repo / PR / branch per card
./aio set 12 agent_backlog  # hand the card to a coding agent
                            #   --runner codex | claude_code | llm
./aio card 12               # progress, links, workspace path
./aio merge 12              # confirm, merge the PR, card moves to done
```

In `aio tui`: `j`/`k` move within a column, `h`/`l` between columns, `a` sends to
`agent_backlog`, `s` picks any status, `m` is **Merge & done** (with a confirm
prompt), `o` opens the PR, `y` copies its URL, `r` refreshes, `q` quits. The
board refreshes every `TUI_POLL_SECONDS` and only redraws when something changed.

The TUI is owner-only. Members get `aio board`, `aio card`, `aio chat`, `aio say`.

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
| `aio login` / `logout` / `whoami` | Credentials in `~/.aio/credentials.json` (mode 600) |
| `aio doctor` | Preflight: API, git, workspace root, GitHub, Tavily, coding runners |
| `aio tui` | Live owner dashboard |
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

## Two-minute demo (CLI)

```bash
./aio login --email a@local.test --password demo
./aio tui
# press a on a todo card  -> agent_backlog, agent clones + edits + opens a PR
# watch it land in in_review with repo / PR / branch badges
# press m -> confirm -> merged into main, card moves to done
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

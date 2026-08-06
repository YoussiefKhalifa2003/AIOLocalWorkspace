# AIO

**LAN hybrid workplace** - shared team chat, a private AI room per person, an objectives board, and Lead catch-up. Same network only.

```
#general  →  people talk, @pings, !commands (whisper)
MY ROOM   →  /skills for AI, plain notes stay quiet
Board     →  drag objectives across columns
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
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

| Where | URL |
|-------|-----|
| Local | http://127.0.0.1:8000/app |
| LAN | http://YOUR_LAN_IP:8000/app |

Reset demo data anytime:

```bash
rm -f aio.db && ./aio seed
```

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
| `/deepresearch` | Deep briefing with tables, tradeoffs, next steps |
| `/code` | Build or patch |
| `/write` | Draft prose |
| `/review` | Check a diff |
| `/checklist` | Break work into ticks |
| `/status <name>` | AI catch-up on a member (owner; also works in channels as whisper) |

After skill work, you may get **Yes / No** on matching board objectives (`!done` / `!keep`).

### Board
- Columns: todo → doing → blocked → agent_backlog → in_review → done
- Drag your cards, or use `!set <id> doing`
- Owner can assign cards across the team

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
- Webhook notices in `#general`, objective `#obj-N` links
- `agent_backlog` → coding → PR (needs `GITHUB_TOKEN` + `GITHUB_REPO`)
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
| `AGENT_MODEL_FAST` / `AGENT_MODEL_STRONG` | Model tiers |
| `AGENT_LLM_BACKEND` | `auto` · `gemini` · `openrouter` · `opencode` |
| `CODING_BACKEND` | `llm` (default) · `opencode` |
| `INVITE_APP_URL` | Public base for invite links (`http://YOUR_LAN_IP:8000`) |
| `TEAMS_WEBHOOK_URL` | Optional - post invite links to Teams |
| `UPLOADS_DIR` | Attachment storage (default `data/uploads`) |
| `DEMO_API_KEY` / `WORKSPACE_JOIN_KEY` | Seed / join key (default `demo-key-a`) |

---

## Two-minute demo

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

Stack: **FastAPI · SQLite · SQLAlchemy · vanilla JS UI** at `/app`.

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

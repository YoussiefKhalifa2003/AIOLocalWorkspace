# AIO - LAN hybrid workplace

Shared team chat + private agent rooms per person + Lead catch-up + objective Board. Same network only.

## Setup

```bash
cd /Users/yousef/Desktop/WORK
source .venv/bin/activate
# put GROQ_API_KEY and Gemini/OpenRouter key in .env
rm -f aio.db
./aio seed
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open: http://127.0.0.1:8000/app  
LAN: http://YOUR_LAN_IP:8000/app

### Seed logins (shared API key `demo-key-a`)

| email | role |
|--------|------|
| `a@local.test` | owner (Lead) |
| `omar@local.test` | member |
| `sara@local.test` | member |

## How it works

- **`#general`** - normal team chat. `@Omar` / `@team` ping people. `!add ...` and other `!` commands are **only visible to you**.
- **MY ROOM** - type `/` for skills (`/code`, `/write`, `/research`...). Plain notes do not wake AI. Same `!` commands work here.
- **Board** tab - see and drag objectives (or `!set <id> doing`).
- **Agents** tab - pick which model powers each skill’s brain.
- **Analytics** tab (owner) - job/metric tables
- After skill work: **Yes/No** on matching objectives (`!done` / `!keep`)
- **Lead catch-up** (owner): `!status Omar`, `!team`
- GitHub: `GITHUB_TOKEN` + `GITHUB_REPO`; `./aio webhook-sim` for LAN demos

### Optional env

| var | purpose |
|-----|---------|
| `GITHUB_TOKEN` | create PRs from agent_backlog |
| `GITHUB_REPO` | `owner/repo` applied on seed |
| `AGENT_MODEL_FAST` / `AGENT_MODEL_STRONG` | model tiers (Gemini path) |
| `OPENROUTER_API_KEY` | OpenRouter - free models in Models tab ([keys](https://openrouter.ai/keys)) |
| `GEMINI_API_KEY` | Gemini default / `gemini-env` path |
| `OPENCODE_API_KEY` | optional OpenCode Zen (often needs billing) |
| `CODING_BACKEND=llm\|opencode` | legacy coding shell (default `llm`) |
| `AGENT_LLM_BACKEND=auto\|gemini\|openrouter\|opencode` | routing preference |

### Models tab

Header **Agents** - pick OpenRouter `:free` models or **Gemini (.env)** per skill brain. Set `OPENROUTER_API_KEY` from [openrouter.ai/keys](https://openrouter.ai/keys).

## Demo walkthrough (~2 min)

1. Login as `omar@local.test` / `demo-key-a` → open **#general**
2. `!add Finish station notes`  -  `!issue Missing map PDF` (only you see replies)
3. Open **Board** - see Omar’s card; drag own card
4. Open **MY ROOM** → `/write one metro tip`
5. Logout; login as `a@local.test` / `demo-key-a`
6. In `#general` type `!status Omar` → remaining work + issue
7. Invite a friend: MEMBERS **+** → they use email + `demo-key-a` on your LAN IP

## Mental model (one sentence each)

- `@` - ping people
- `/` - AI skills in your private room only
- `!` - board/ops commands (whisper in general)

## Voice

- **speak replies** - Groq TTS on agent answers  
- **voice input** - mic → Whisper → send

## vs Buzz

Buzz: @mention agents in shared chat.  
AIO: `#general` for people (`@` pings), private `/skills` for AI, `!` whisper commands, Board tab.

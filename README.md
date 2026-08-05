# AIO — LAN hybrid workplace

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

- **TEAM** chats (e.g. `#general`) — everyone can see messages; **plain text = human only**. Start with `/` to call AI (`/help`, `/@Omar status`)
- **MY ROOM** — private agent work; AI always listens (no `/` needed)
- **Board** tab — drag objectives across status columns (own cards only; Lead can drag all)
- **Analytics** tab (owner) — job/metric tables
- **Same agents** for everyone (`@Research`, `@Writing`, `@Code`, `@Review`, `@Checklist`)
- After agent work: **Yes/No** on matching open objectives (or type `yes 7` / `no 7`)
- **Lead catch-up** (owner): `@Omar status`, `@team report` — structured objectives / checklist / issues only (not private prompts)
- GitHub: project PAT (`GITHUB_TOKEN`) + `Project.github_repo`; `./aio webhook-sim` for LAN demos

### Optional env

| var | purpose |
|-----|---------|
| `GITHUB_TOKEN` | create PRs from agent_backlog |
| `GITHUB_REPO` | `owner/repo` applied on seed |
| `AGENT_MODEL_FAST` / `AGENT_MODEL_STRONG` | model tiers (Gemini path) |
| `OPENROUTER_API_KEY` | OpenRouter — free models in Models tab ([keys](https://openrouter.ai/keys)) |
| `GEMINI_API_KEY` | Gemini default / `gemini-env` path |
| `OPENCODE_API_KEY` | optional OpenCode Zen (often needs billing) |
| `CODING_BACKEND=llm\|opencode` | legacy coding shell (default `llm`) |
| `AGENT_LLM_BACKEND=auto\|gemini\|openrouter\|opencode` | routing preference |

### Models tab

Header **Models** — pick OpenRouter `:free` models or **Gemini (.env)** per agent. Set `OPENROUTER_API_KEY` from [openrouter.ai/keys](https://openrouter.ai/keys). Without it, use Gemini. OpenCode free models appear only if `OPENCODE_API_KEY` is set.

## Demo walkthrough (~2 min)

1. Login as `omar@local.test` / `demo-key-a` → open **my private room**
2. `add objective Finish station notes` · `log issue Missing map PDF`
3. Open **Board** — see Omar’s card + blocker badge; drag own card
4. Logout; login as `a@local.test` / `demo-key-a`
5. In `#general` type `/@Omar status` → see remaining work + issue
6. `./aio webhook-sim --title "fix #obj-1"` then `./aio drain` → review in `#general`
7. Invite a friend: MEMBERS **+** → they use email + `demo-key-a` on your LAN IP

## Voice

- **speak replies** — Groq TTS on agent answers  
- **voice input** — mic → Whisper → send

## vs Buzz

Buzz: @mention agents in shared chat.  
AIO: private agent rooms per teammate + shared team channel + Board + Lead @people status catch-up.

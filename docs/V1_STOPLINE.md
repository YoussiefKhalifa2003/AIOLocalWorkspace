# v1 surface

## Working

- Hybrid workplace: TEAM channels + MY ROOM (private per user)
- Invite by email (shared join key `demo-key-a`)
- Lead orchestrator + @Research/@Writing/@Code/@Review/@Checklist
- Owned objectives / checklist / WorkIssue blockers
- **Board tab** — objective columns (todo/doing/blocked/agent_backlog/in_review/done)
- GitHub webhook → `#general` notices + objective `#obj-N` link; merge needs Yes confirm (no auto-done)
- PR opened → code_review → review posted in `#general`
- FileClaim conflict radar before coding (`claim path` / `proceed`)
- Semi-auto `agent_backlog` → coding → GitHub PR or manual fallback
- Model tiers (`AGENT_MODEL_FAST` / `AGENT_MODEL_STRONG`) + `/jobs/summary`
- **Models tab** — per-agent OpenCode Zen free model dropdowns (or Gemini)
- Optional `CODING_BACKEND=opencode` + Analytics tab (owner)
- Lead catch-up: @user status, @team report (structured only; no raw private chats)
- @ autocomplete for agents + members
- Groq TTS + Whisper STT
- LAN metal UI at /app

## Non-goals

- Lead reading another user's private prompts
- Per-user GitHub OAuth, MCP conflict server, auto-merge from chat
- Chart.js / D3 burndown
- Public SaaS, Buzz protocol, marketing UI, mobile, WebSockets

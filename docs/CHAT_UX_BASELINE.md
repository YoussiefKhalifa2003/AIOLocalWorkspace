# Chat UX redesign - Phase 0 baseline (behaviors to retire)

Recorded before Phase 1+. These intentionally break later.

## Current (pre-redesign) behaviors
1. Bare `add objective ...` works in `#general` (no `!`) - RETIRE
2. `/help` / `/create chat` wakes AI/command path in team chats - RETIRE (`!` instead)
3. `@Code` / `@Research` etc. force agents - RETIRE (`/code` skills in private)
4. Private room: every plain message auto-routes to Lead/LLM - RETIRE (notes only; AI via `/skill`)
5. Chat command `board` / `show board` - RETIRE (Board tab only)
6. Long phrases: `set objective 7 doing`, `claim path ...` - RETIRE (short `!set`, `!claim`)

## Keep / transform
- Objectives, claims, issues, invite, clear - via `!` verbs
- Owner catch-up - via `!status <name>` (Phase 4), not `@Omar` auto-status
- Board tab UI unchanged for viewing
- Agent model prefs - behind skills later

## Baseline tests
- `tests/test_chat.py`, `tests/test_hybrid.py` - run at Phase 0 start

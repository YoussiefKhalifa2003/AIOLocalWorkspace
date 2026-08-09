# AIOPlayground

Sandbox GitHub repo for **[AIO](https://github.com/YoussiefKhalifa2003)** - the multi-agent workplace that runs in your terminal.

This repository is **not** the AIO application. It is the safe place AIO opens **pull requests** when board cards are sent to coding agents (**Codex**, **Claude Code**, or the LLM runner).

Keep this repo intentionally small: ideally **`README.md` only** on `main`, so demos stay clean.

---

## What this repo is for

| Use | Detail |
|-----|--------|
| Demo PRs | Board → press **`a`** → pick `codex` / `claude_code` / `llm` → agent edits a checkout → opens a PR here |
| Safe sandbox | Agent branches and sample files never touch the AIO app repo |
| Monday resets | Wipe PRs/branches after demos; leave `main` with this README |

**Not for:** shipping AIO itself, long-lived product code, or secrets.

---

## Link from AIO

In the AIO project `.env` (local only - never commit tokens):

```env
GITHUB_REPO=YoussiefKhalifa2003/AIOPlayground
GITHUB_TOKEN=ghp_…   # repo scope: contents + pull requests
```

Optional per-project override exists in AIO; the global `GITHUB_REPO` is enough for demos.

Then in AIO:

1. `aio doctor` - confirm GitHub token + repo  
2. Board: create a card → **`a`** → **codex** or **claude_code**  
3. Watch **agent_backlog** → **in_review** · open the PR badge  
4. Owner: **`m`** (or `aio merge <id> --yes`) when mergeable  

Interactive CLIs (separate from PRs): in AIO chat type `!claude` or `!codex` to open those apps in a new terminal.

---

## How AIO uses this repo

```
Board card ──a──► coding_runner (codex | claude_code | llm)
                      │
                      ▼
              data/workspaces/obj-<id>   (local checkout on the API host)
                      │
                      ▼
              branch aio/obj-<id>-…  +  pull request against main
```

- Default branch: **`main`** (must exist).  
- Agent branches are disposable (`aio/obj-*`).  
- Close demo PRs and delete branches when the session ends (or after `aio board-wipe --yes` on the AIO side for local cards/workspaces).

---

## Keeping main clean

For a demo-ready `main`:

1. Leave **only** `README.md` (this file) on `main`.  
2. Close open PRs.  
3. Delete stale `aio/obj-*` remote branches.  

AIO’s local board wipe (`aio board-wipe --yes`) clears cards and `data/workspaces/obj-*` on the API host - it does **not** automatically reset this GitHub repo.

---

## Requirements checklist

- [ ] `main` exists and is the default branch  
- [ ] `GITHUB_TOKEN` can create branches + PRs on this repo  
- [ ] AIO `.env` has `GITHUB_REPO=YoussiefKhalifa2003/AIOPlayground`  
- [ ] Coding CLIs installed on the **API host** if using `codex` / `claude_code` runners (`aio doctor`)  

---

## Notes

- Treat every agent PR as a **demo** - review before merging.  
- Prefer resetting `main` to README-only between showcases so the next PR is easy to see.  
- Do not store API keys, `.env`, or AIO database files here.

---

## License

Same private / internal policy as AIO unless you explicitly open-source this sandbox.

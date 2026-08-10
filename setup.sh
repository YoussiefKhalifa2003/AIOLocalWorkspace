#!/usr/bin/env bash
# One-command setup + launch for AIO (macOS / Linux).
# Member (default):  ./setup.sh
# Host first-time:   ./setup.sh --host
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

HOST=0
NO_LAUNCH=0
for arg in "$@"; do
  case "$arg" in
    --host) HOST=1 ;;
    --no-launch) NO_LAUNCH=1 ;;
    -h|--help)
      echo "Usage: ./setup.sh [--host] [--no-launch]"
      echo "  (default)  create venv, install deps, launch aio"
      echo "  --host     also Playwright Chromium, .env, seed DB"
      echo "  --no-launch  install only (do not open the app)"
      exit 0
      ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.11+ first."
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "→ creating .venv"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
echo "→ pip install -r requirements.txt"
python -m pip install -U pip >/dev/null
python -m pip install -r requirements.txt

chmod +x aio setup.sh 2>/dev/null || true

if [[ "$HOST" -eq 1 ]]; then
  echo "→ Playwright Chromium (Outlook invites)"
  python -m playwright install chromium
  if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "→ created .env from .env.example — add your API keys, then restart the API later"
  fi
  if [[ ! -f aio.db ]]; then
    echo "→ seeding demo DB"
    python -m app.cli_pkg.main seed
  fi
  echo
  echo "Host: keep these running in other terminals:"
  echo "  T1  uvicorn app.main:app --host 0.0.0.0 --port 8000"
  echo "  T2  cloudflared tunnel --url http://127.0.0.1:8000"
  echo "      → paste https://….trycloudflare.com into .env as INVITE_APP_URL="
  echo "  T3  ./aio outlook-login   (once)"
  echo
fi

if [[ "$NO_LAUNCH" -eq 1 ]]; then
  echo "Setup done. Run: ./aio"
  exit 0
fi

echo "→ launching aio"
exec "$ROOT/aio"

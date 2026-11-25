#!/usr/bin/env bash
# One-time setup: virtualenv, packages, .env, owner password, agent tokens.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "Python 3.10+ needed"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements-dev.txt
[ -f .env ] || { cp .env.example .env; chmod 600 .env; echo "Made .env from .env.example"; }
grep -q '^DASHBOARD_PASSWORD_HASH=scrypt' .env || .venv/bin/python -m crew setpass
.venv/bin/python -m crew tokens
.venv/bin/python -m crew check
echo
echo "Next: ./run.sh   then open http://127.0.0.1:8000"

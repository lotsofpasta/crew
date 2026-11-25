#!/usr/bin/env bash
# Run the hub and the example agents together (Ctrl-C stops all). For production use deploy/ (systemd) or Docker.
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
[ -x "$PY" ] || { echo "Run ./setup.sh first"; exit 1; }
pids=()
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT INT TERM
"$PY" -m crew serve & pids+=($!)
sleep 1.5
for a in ${AGENTS:-manager researcher}; do
  f="agents/$a.py"; [ -f "$f" ] || f="examples/$a.py"
  [ -f "$f" ] && { "$PY" "$f" & pids+=($!); }
done
echo "Hub on http://${HOST:-127.0.0.1}:${PORT:-8000}  ·  agents: ${AGENTS:-manager researcher}"
wait

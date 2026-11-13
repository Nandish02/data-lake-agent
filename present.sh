#!/usr/bin/env bash
# Serves the slides locally (works offline) and opens them. The demo slides can start live agent runs,
# which also print in this terminal. Ctrl-C stops the server.
set -euo pipefail
cd "$(dirname "$0")"
PORT=${PORT:-8765}
URL="http://localhost:$PORT/index.html"
if curl -sf --max-time 1 "http://localhost:$PORT/api/status" >/dev/null; then
  open "$URL"
  echo "Slides already served at $URL"
  exit 0
fi
pkill -f "http.server $PORT" 2>/dev/null || true   # static server from an older version of this script
(sleep 1 && open "$URL") &
exec .venv/bin/python scripts/serve.py --port "$PORT"

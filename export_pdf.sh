#!/usr/bin/env bash
# Exports the deck (every slide, every trace step revealed) to slides.pdf as an offline backup.
set -euo pipefail
cd "$(dirname "$0")"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[[ -x $CHROME ]] || { echo "Google Chrome not found"; exit 1; }
.venv/bin/python scripts/grade.py >/dev/null
"$CHROME" --headless=new --disable-gpu --no-pdf-header-footer --virtual-time-budget=5000 \
  --print-to-pdf="$PWD/slides.pdf" "file://$PWD/web/index.html?print" 2>/dev/null
echo "wrote slides.pdf"

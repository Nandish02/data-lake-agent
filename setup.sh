#!/usr/bin/env bash
# One-time setup: Python env, the mini data lake, Ollama + the small model, and a self-check.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Python environment (.venv)"
command -v uv >/dev/null || { echo "Install uv first: brew install uv"; exit 1; }
[[ -d .venv ]] || uv venv --python 3.12 .venv
uv pip install -q --python .venv/bin/python -r requirements.txt

echo "==> Mini data lake (downloads ~50 MB from the public LakeQA S3 bucket)"
.venv/bin/python scripts/fetch_lake.py

echo "==> Ollama + the two local models"
command -v ollama >/dev/null || brew install ollama
curl -s --max-time 2 localhost:11434/api/version >/dev/null || { brew services start ollama; sleep 3; }
[[ -f .env ]] || cp .env.example .env
for key in STRONG_MODEL QWEN_MODEL SMALL_MODEL; do
  model=$(grep -E "^$key=" .env | cut -d= -f2)
  [[ $model == ollama* ]] && ollama pull "${model#*/}"
done

echo "==> Viewer data"
.venv/bin/python scripts/grade.py >/dev/null

echo "==> Self-check"
.venv/bin/python scripts/check.py

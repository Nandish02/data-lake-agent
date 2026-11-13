#!/usr/bin/env bash
# Usage:
#   ./demo.sh strong [task]            live run with the strong model (GPT-6 Luna via Azure OpenAI; key in .env)
#   ./demo.sh qwen   [task]            live run with the mid-size local model, qwen3.5:9b (Ollama)
#   ./demo.sh small  [task]            live run with the small local model (Ollama)
#   ./demo.sh strong [task] --replay   replay the recorded run (offline-safe)
#   ./demo.sh small  [task] --confirm  mini-swe-agent's interactive UI: press Enter before each command runs
#   ./demo.sh replay <trajectory.json> replay any specific recorded run
#   ./demo.sh inspect [path]           browse recorded runs in mini-swe-agent's official Inspector
# task: cta (default, the task on the slides) | lotto | overdose
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
[[ -x $PY ]] || { echo "Run ./setup.sh first."; exit 1; }

mode=${1:-strong}
if [[ $mode == inspect ]]; then
  # mini-swe-agent's own trajectory browser (←/→ steps, H/L trajectories, q quit)
  exec .venv/bin/mini-extra inspector "${2:-runs}"
fi
if [[ $mode == replay ]]; then
  exec $PY scripts/replay.py "${2:?trajectory path required}" "${@:3}"
fi
task=${2:-cta}
[[ $task == --* ]] && { set -- "$mode" cta "${@:2}"; task=cta; }

if [[ " $* " == *" --replay "* ]]; then
  exec $PY scripts/replay.py --model "$mode" --task "$task"
fi

if [[ $mode == small ]] && ! curl -s --max-time 2 localhost:11434/api/version >/dev/null; then
  echo "Ollama is not running. Start it with: brew services start ollama   (or use --replay)"; exit 1
fi
confirm=""
[[ " $* " == *" --confirm "* ]] && confirm="--confirm"
$PY scripts/run_agent.py --model "$mode" --task "$task" $confirm
$PY scripts/grade.py >/dev/null

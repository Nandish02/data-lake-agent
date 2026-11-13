"""Record several runs per (model, task) for the scoreboard, then re-grade everything.

    python scripts/batch.py --models strong small --tasks cta lotto overdose --n 2
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "bin" / "python"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=["strong", "small"])
    parser.add_argument("--tasks", nargs="+", default=["cta", "lotto", "overdose"])
    parser.add_argument("--n", type=int, default=1, help="runs per (model, task)")
    parser.add_argument("--step-limit", type=int, default=None)
    parser.add_argument("--pause", type=float, default=0.0, help="seconds between runs (free-tier rate limits)")
    parser.add_argument("--model-kwargs", default=None, help="extra litellm kwargs as JSON, passed to run_agent.py")
    parser.add_argument("--label", default=None)
    args = parser.parse_args()

    for model in args.models:
        for task in args.tasks:
            for i in range(args.n):
                cmd = [str(PY), "scripts/run_agent.py", "--model", model, "--task", task]
                if args.model_kwargs:
                    cmd += ["--model-kwargs", args.model_kwargs]
                if args.label:
                    cmd += ["--label", args.label]
                if args.step_limit:
                    cmd += ["--step-limit", str(args.step_limit)]
                start = time.time()
                proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env={**os.environ, "COLUMNS": "140"})
                last = (proc.stdout.strip().splitlines() or [""])[-1]
                status = "ok" if proc.returncode == 0 else f"exit {proc.returncode}"
                print(f"[{model} · {task} · {i + 1}/{args.n}] {status} in {time.time() - start:.0f}s  {last[-110:]}", flush=True)
                if proc.returncode != 0:
                    print(proc.stderr[-2000:], file=sys.stderr)
                time.sleep(args.pause)
    return subprocess.run([str(PY), "scripts/grade.py"], cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())

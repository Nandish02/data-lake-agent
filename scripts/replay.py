"""Replay a recorded trajectory in the terminal, rendered exactly like a live run (no network or model needed).

    python scripts/replay.py --model strong --task lotto        # the featured recorded run
    python scripts/replay.py runs/k-1-d-1_task_3/small__qwen2.5_1.5b/20261030-140000.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.markup import escape  # noqa: E402

from common import (  # noqa: E402
    ROOT,
    RUNS_DIR,
    console,
    load_task,
    render_exit,
    render_header,
    render_step_command,
    render_step_output,
    render_step_thought,
    summarize_run,
)


def featured_run(label: str, task_name: str) -> Path:
    task_id = load_task(task_name)["task_id"]
    featured = json.loads((RUNS_DIR / "featured.json").read_text()) if (RUNS_DIR / "featured.json").exists() else {}
    if path := featured.get(task_id, {}).get(label):
        return ROOT / path
    candidates = sorted((RUNS_DIR / task_id).glob(f"{label}__*/*.json"))
    if not candidates:
        raise SystemExit(f"No recorded '{label}' run for task {task_id}. Record one with: ./demo.sh {label} {task_name}")
    return candidates[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("trajectory", nargs="?", help="path to a trajectory .json")
    parser.add_argument("--model", default="strong", help="strong | small (used when no path is given)")
    parser.add_argument("--task", default="cta")
    parser.add_argument("--speed", type=float, default=1.0, help="2 = twice as fast, 0 = instant")
    args = parser.parse_args()

    path = Path(args.trajectory) if args.trajectory else featured_run(args.model, args.task)
    data = json.loads(path.read_text())
    summary = summarize_run(data)
    task = summary["task"]

    def pause(seconds: float) -> None:
        if args.speed > 0:
            time.sleep(seconds / args.speed)

    annotations_file = RUNS_DIR / "annotations.json"
    annotations = json.loads(annotations_file.read_text()) if annotations_file.exists() else {}
    rel = str(path.resolve().relative_to(ROOT)) if path.resolve().is_relative_to(ROOT) else str(path)
    notes = annotations.get(rel, {})

    render_header(task, data["demo"]["model_name"], f"recorded run · {data['demo'].get('started_at', '')}")
    console.print(f"[dim]replaying {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}[/]")
    for step in summary["steps"]:
        if step.kind == "exit":
            pause(0.8)
            render_exit(step, task)
            continue
        with console.status("[bold cyan]model is thinking…", spinner="dots"):
            pause(min(step.elapsed or 1.5, 2.5))
        render_step_thought(step)
        pause(0.6)
        render_step_command(step)
        pause(0.9)
        render_step_output(step)
        if note := notes.get(str(step.index)):
            console.print(f"[black on yellow] 💬 [/] [yellow]{escape(note)}[/]")
        pause(0.6)
    console.print(f"[dim]{summary['n_steps']} model calls (recorded)[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

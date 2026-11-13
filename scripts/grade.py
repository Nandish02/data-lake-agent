"""Grade every recorded run, print a scoreboard, and export everything the web viewer needs to web/data/runs.js.

    python scripts/grade.py
"""

import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.table import Table  # noqa: E402

from common import FAILURE_MODES, LAKE, ROOT, RUNS_DIR, TASK_ALIASES, console, load_task, summarize_run  # noqa: E402

WEB_DATA = ROOT / "web" / "data" / "runs.js"
LAKE_PATH_RE = re.compile(r"(?:datagov|wikipedia)/[\w.\-/*]+")


def lake_tree() -> list[dict]:
    datasets = []
    for source_dir in sorted(p for p in LAKE.iterdir() if p.is_dir()):
        for ds in sorted(p for p in source_dir.iterdir() if p.is_dir()):
            files = sorted(f for f in ds.rglob("*") if f.is_file())
            datasets.append(
                {
                    "path": f"{source_dir.name}/{ds.name}",
                    "files": [{"path": str(f.relative_to(LAKE)), "size": f.stat().st_size} for f in files],
                }
            )
    return datasets


def touched_paths(command: str) -> list[str]:
    return sorted({m.rstrip("/.") for m in LAKE_PATH_RE.findall(command)})


def main() -> int:
    tasks = {}
    for task_id in TASK_ALIASES.values():
        task = load_task(task_id)
        tasks[task_id] = {
            "task_id": task_id,
            "alias": task["alias"],
            "question": task["question"],
            "answer": task["answer"],
            "datasets_used": task["datasets_used"],
            "target_datasets": task["target_datasets"],
            "reference_code": task["nodes"]["1"]["fact"],
            "reasoning": task["reasoning_hops"][0]["reasoning"][0],
        }

    annotations_file = RUNS_DIR / "annotations.json"
    annotations = json.loads(annotations_file.read_text()) if annotations_file.exists() else {}
    featured_file = RUNS_DIR / "featured.json"
    featured = json.loads(featured_file.read_text()) if featured_file.exists() else {}

    runs = []
    for path in sorted(RUNS_DIR.glob("*/*/*.json")):
        data = json.loads(path.read_text())
        if "demo" not in data:
            continue
        summary = summarize_run(data)
        rel = str(path.relative_to(ROOT))
        label = path.parent.name.split("__")[0]
        steps = []
        # "_tags": {"<step>": ["wrong_assumption", ...]} in annotations.json adds hand-applied tags for mistakes the
        # heuristics can't see (e.g. dropping the baseline year). They count like automatic tags and are marked "hand".
        hand_tags = annotations.get(rel, {}).get("_tags", {})
        for step in summary["steps"]:
            item = asdict(step)
            item["touched"] = touched_paths(step.command)
            item["note"] = annotations.get(rel, {}).get(str(step.index), "")
            extra = [t for t in hand_tags.get(str(step.index), []) if t not in item["tags"]]
            item["tags"] = item["tags"] + extra
            item["hand_tags"] = extra
            for t in extra:
                summary["failure_counts"][t] = summary["failure_counts"].get(t, 0) + 1
            steps.append(item)
        runs.append(
            {
                "id": rel,
                "task_id": data["demo"]["task_id"],
                "label": label,
                "model": data["demo"]["model_name"],
                "started_at": data["demo"].get("started_at", ""),
                "exit_status": summary["exit_status"],
                "submission": summary["submission"],
                "predicted": summary["predicted"],
                "correct": summary["correct"],
                "n_steps": summary["n_steps"],
                "failure_counts": summary["failure_counts"],
                "summary_note": annotations.get(rel, {}).get("summary", ""),
                "steps": steps,
            }
        )

    WEB_DATA.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tasks": tasks,
        "runs": runs,
        "featured": featured,
        "failure_modes": FAILURE_MODES,
        "gallery": annotations.get("_gallery", []),
        "lake": lake_tree(),
    }
    WEB_DATA.write_text("window.DEMO_DATA = " + json.dumps(payload, indent=1) + ";\n")

    table = Table(title="Scoreboard (all recorded runs)", header_style="bold")
    for col in ("task", "model", "runs", "correct", "avg steps", *FAILURE_MODES.values()):
        table.add_column(col, justify="right" if col not in ("task", "model") else "left")
    groups = defaultdict(list)
    for run in runs:
        groups[(tasks[run["task_id"]]["alias"], run["model"])].append(run)
    for (alias, model), group in sorted(groups.items()):
        n = len(group)
        table.add_row(
            alias,
            model.split("/", 1)[-1],
            str(n),
            f"{sum(r['correct'] for r in group)}/{n}",
            f"{sum(r['n_steps'] for r in group) / n:.1f}",
            *(str(sum(r["failure_counts"][m] > 0 for r in group)) for m in FAILURE_MODES),
        )
    console.print(table)
    console.print(f"[dim]failure-mode columns = number of runs showing that mode · wrote {WEB_DATA.relative_to(ROOT)}[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

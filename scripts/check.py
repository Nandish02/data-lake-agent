"""Pre-flight check for presentation day. Run it before class:  .venv/bin/python scripts/check.py
Every line should be green. Anything red has a fallback printed next to it.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")

from common import LAKE, ROOT, RUNS_DIR, TASK_ALIASES, console, load_env, load_task, model_for  # noqa: E402

results: list[bool] = []


def report(ok: bool, what: str, fix: str = "") -> None:
    results.append(ok)
    mark = "[bold green]✔[/]" if ok else "[bold red]✘[/]"
    console.print(f" {mark} {what}" + (f"  [yellow]→ {fix}[/]" if not ok and fix else ""))


def check_lake_and_ground_truth() -> None:
    for alias, task_id in TASK_ALIASES.items():
        task = load_task(task_id)
        missing = [k for k in task["datasets_used"] if not (LAKE / k).exists()]
        report(not missing, f"lake files for '{alias}' present", "run: .venv/bin/python scripts/fetch_lake.py")
        if missing:
            continue
        cwd = os.getcwd()
        os.chdir(LAKE)
        try:
            ns: dict = {}
            exec(task["nodes"]["1"]["fact"], ns)
            report(str(ns.get("answer")) == task["answer"], f"reference solution reproduces ground truth [{task['answer']}] for '{alias}'")
        finally:
            os.chdir(cwd)


def check_ollama() -> None:
    local = [m for m in (model_for("strong"), model_for("qwen"), model_for("small")) if m.startswith(("ollama/", "ollama_chat/"))]
    if not local:
        return
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as resp:
            names = {m["name"] for m in json.loads(resp.read())["models"]}
        report(True, "Ollama server is running")
    except Exception:
        report(False, "Ollama server is running", "run: brew services start ollama   (or use ./demo.sh <strong|small> --replay)")
        return
    for model in local:
        wanted = model.split("/", 1)[-1]
        report(wanted in names, f"model '{wanted}' is pulled", f"run: ollama pull {wanted}")


def check_models_answer(online: bool) -> None:
    """One tiny completion per model: catches a missing key, no network, or a model that fails to load."""
    load_env()
    import litellm

    for label in ("strong", "small"):
        model = model_for(label)
        remote = not model.startswith(("ollama/", "ollama_chat/"))
        if remote and model.startswith("gemini/") and not os.environ.get("GEMINI_API_KEY"):
            report(False, f"{label} model '{model}': GEMINI_API_KEY set", "add it to .env")
            continue
        if remote and not online:
            continue
        try:
            kwargs = {"keep_alive": "30m"} if not remote else {}
            # Same extra kwargs as the real runs (e.g. thinking off for qwen3.5), otherwise a cold "thinking" call can time out.
            extra = os.environ.get(f"{label.upper()}_MODEL_KWARGS", "")
            kwargs.update(json.loads(extra) if extra else {})
            resp = litellm.completion(
                model=model, messages=[{"role": "user", "content": "Reply with the word OK."}], timeout=120, **kwargs
            )
            ok = "ok" in (resp.choices[0].message.content or "").lower()
            report(ok, f"{label} model '{model}' loads and answers (also warms it up)")
        except Exception as e:
            fix = f"./demo.sh {label} --replay"
            report(False, f"{label} model '{model}' answers ({type(e).__name__})", f"fallback: {fix}")


def check_recordings() -> None:
    featured = json.loads((RUNS_DIR / "featured.json").read_text()) if (RUNS_DIR / "featured.json").exists() else {}
    for task_id, labels in featured.items():
        for label, path in labels.items():
            report((ROOT / path).exists(), f"recorded {label} run for {task_id} available for replay")
    report((ROOT / "web" / "data" / "runs.js").exists(), "web viewer data exported", "run: .venv/bin/python scripts/grade.py")


if __name__ == "__main__":
    online = "--offline" not in sys.argv
    console.rule("[bold]Demo pre-flight check")
    check_lake_and_ground_truth()
    check_ollama()
    check_models_answer(online)
    check_recordings()
    console.rule("[bold green]ALL GOOD" if all(results) else "[bold red]SOME CHECKS FAILED (see fixes above)")
    sys.stdout.flush()
    os._exit(0 if all(results) else 1)  # litellm can leave a non-daemon worker thread that delays a normal exit

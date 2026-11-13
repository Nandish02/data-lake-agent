"""Serve the slides and let them start live agent runs (the "Run live" button on the demo slides).

    .venv/bin/python scripts/serve.py              # http://localhost:8765 (./present.sh runs this)

API used by web/app.js:
    GET  /api/status   server is up, model names, whether a run is in progress
    POST /api/run      {"model": "strong"|"small", "task": "lotto"|"cta"|"overdose"} starts scripts/run_agent.py
    GET  /api/live     the running agent's latest snapshot (written by run_agent.py --state)
    POST /api/stop     stops the current run (the partial trajectory is still saved)

Live runs print to this terminal exactly like ./demo.sh, and are saved to runs/ like any other run.
"""

import argparse
import json
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ROOT, TASK_ALIASES, console, model_for, short_model_name  # noqa: E402

STATE = Path(tempfile.gettempdir()) / "datalake-demo" / "live-state.json"
PY = ROOT / ".venv" / "bin" / "python"
LABELS = ("strong", "qwen", "small")  # the demo slides use strong (GPT-6 Luna) and qwen (qwen3.5 9B)


def write_state(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE)


def ollama_up() -> bool:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/version", timeout=1.5):
            return True
    except OSError:
        return False


class Runner:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def busy(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, label: str, task: str) -> None:
        with self.lock:
            if self.busy():
                raise RuntimeError("A run is already in progress. Stop it first (X).")
            write_state(
                {"label": label, "model": model_for(label), "task_id": TASK_ALIASES[task], "started": time.time(),
                 "thinking": True, "done": False, "steps": []}
            )
            console.rule(f"[bold cyan]Live run started from the slides: {label} · {task}")
            self.proc = subprocess.Popen(
                [str(PY), "scripts/run_agent.py", "--model", label, "--task", task, "--state", str(STATE)], cwd=ROOT
            )
            threading.Thread(target=self._finish, args=(self.proc,), daemon=True).start()

    def _finish(self, proc: subprocess.Popen) -> None:
        proc.wait()
        try:
            state = json.loads(STATE.read_text())
        except (OSError, ValueError):
            state = {}
        if not state.get("done"):  # crashed before it could write its final snapshot
            state.update(done=True, thinking=False, exit_status="Error (see the terminal)" if proc.returncode else "Stopped")
        write_state(state)
        # Refresh web/data/runs.js so the run shows up on the other slides (press L after a reload).
        subprocess.run([str(PY), "scripts/grade.py"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state["graded"] = True
        write_state(state)

    def stop(self) -> None:
        if self.busy():
            self.proc.send_signal(signal.SIGTERM)


RUNNER = Runner()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web"), **kwargs)

    def log_message(self, *args) -> None:  # keep the terminal for the agent's own output
        pass

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")  # a reload always picks up the freshly graded runs.js
        super().end_headers()

    def send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            models = {label: short_model_name(model_for(label)) for label in LABELS}
            return self.send_json(200, {"live": True, "busy": RUNNER.busy(), "models": models})
        if path == "/api/live":
            try:
                return self.send_json(200, json.loads(STATE.read_text()))
            except (OSError, ValueError):
                return self.send_json(200, {"idle": True})
        return super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/stop":
            RUNNER.stop()
            return self.send_json(200, {"ok": True})
        if path != "/api/run":
            return self.send_json(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            body = {}
        label, task = body.get("model"), body.get("task", "lotto")
        if label not in LABELS or task not in TASK_ALIASES:
            return self.send_json(400, {"error": f"model must be one of {LABELS}, task one of {list(TASK_ALIASES)}"})
        if model_for(label).startswith(("ollama/", "ollama_chat/")) and not ollama_up():
            return self.send_json(503, {"error": "Ollama is not running: brew services start ollama (or press P to play the recording)"})
        try:
            RUNNER.start(label, task)
        except RuntimeError as exc:
            return self.send_json(409, {"error": str(exc)})
        return self.send_json(200, {"ok": True})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.unlink(missing_ok=True)  # never show a rehearsal run as if it were live
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    console.print(f"[bold green]Slides at http://localhost:{args.port}/index.html[/]  [dim](live runs print here · Ctrl-C to stop)[/]")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        RUNNER.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

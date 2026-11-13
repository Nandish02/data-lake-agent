"""Run mini-swe-agent on one LakeQA task and stream every step to the terminal.

    python scripts/run_agent.py --model strong --task cta
    python scripts/run_agent.py --model small  --task lotto
    python scripts/run_agent.py --model ollama_chat/llama3.2:1b --task lotto
"""

import argparse
import json
import logging
import os
import re
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
os.environ.setdefault("MSWEA_COST_TRACKING", "ignore_errors")

from common import (  # noqa: E402
    CONFIG,
    LAKE,
    ROOT,
    RUNS_DIR,
    answers_match,
    console,
    extract_answer,
    load_env,
    load_task,
    is_label,
    model_for,
    render_exit,
    render_header,
    render_step_command,
    render_step_output,
    render_step_thought,
    short_model_name,
    tag_steps,
    trajectory_steps,
)
from grade import touched_paths  # noqa: E402
from minisweagent.agents.default import DefaultAgent  # noqa: E402
from minisweagent.agents.interactive import InteractiveAgent  # noqa: E402
from minisweagent.environments.local import LocalEnvironment  # noqa: E402
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel  # noqa: E402

# Commands are run for real on this laptop, so refuse the obviously dangerous ones. Network access is blocked
# too: the answer must come from the lake, not from re-downloading the dataset off the internet.
BLOCKED = re.compile(
    r"\bsudo\b|\brm\s+-\w*[rf]\w*\s+(/|~|\$HOME|\.\.)|\b(curl|wget|nc|ssh|scp|git\s+clone)\b|requests\.get|urllib|"
    r"\bmkfs\b|\bdd\s+if=|\bshutdown\b|\breboot\b|:\(\)\s*\{|\bchmod\s+-R\s+\S+\s+/|\bpip3?\s+install\b|\bbrew\b"
)


class GuardedLocalEnvironment(LocalEnvironment):
    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None):
        if BLOCKED.search(action.get("command", "")):
            return {
                "output": "Blocked by the demo safety guard: network access and destructive commands are not allowed. Use only the files in the data lake.\n",
                "returncode": 126,
                "exception_info": "",
            }
        return super().execute(action, cwd, timeout=timeout)


class LiveAgent(DefaultAgent):
    """DefaultAgent that renders each new message to the terminal as soon as it is appended.

    With `state_path`, it also writes a JSON snapshot of the run after every change, which the slides poll
    (through scripts/serve.py) to show the run live.
    """

    def __init__(self, *args, task: dict, demo_info: dict, state_path: Path | None = None, label: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.task = task
        self.demo_info = demo_info
        self.state_path = state_path
        self.label = label
        self.started = time.time()
        self._shown: dict[int, set[str]] = {}

    def write_state(self, thinking: bool = False, **final) -> None:
        if not self.state_path:
            return
        steps = trajectory_steps(self.messages) if len(self.messages) > 2 else []
        tag_steps(steps, self.task)
        items = []
        for step in steps:
            item = asdict(step)
            item["output"] = step.output[:4000]
            item["touched"] = touched_paths(step.command)
            items.append(item)
        state = {
            "label": self.label,
            "model": self.demo_info["model_name"],
            "task_id": self.task["task_id"],
            "started": self.started,
            "thinking": thinking,
            "done": False,
            "steps": items,
        } | final
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(self.state_path)

    def finish_state(self, exit_status: str | None = None) -> None:
        steps = trajectory_steps(self.messages)
        exit_step = next((s for s in reversed(steps) if s.kind == "exit"), None)
        submission = exit_step.submission if exit_step else ""
        self.write_state(
            done=True,
            exit_status=exit_status or (exit_step.exit_status if exit_step else "Stopped"),
            submission=submission,
            correct=answers_match(extract_answer(submission), self.task["answer"]),
        )

    def query(self) -> dict:
        self.write_state(thinking=True)
        with console.status(f"[bold cyan]{short_model_name(self.model.config.model_name)} is thinking…", spinner="dots"):
            return super().query()

    def add_messages(self, *messages: dict) -> list[dict]:
        result = super().add_messages(*messages)
        if len(self.messages) > 2:
            self._render_new()
        return result

    def _render_new(self) -> None:
        steps = trajectory_steps(self.messages)
        tag_steps(steps, self.task)
        for step in steps:
            shown = self._shown.setdefault(step.index, set())
            if step.kind == "exit":
                if "exit" not in shown:
                    render_exit(step, self.task)
                    shown.add("exit")
                continue
            if "command" not in shown:
                render_step_thought(step)
                render_step_command(step)
                shown.add("command")
            has_result = step.kind == "format_error" or step.returncode is not None
            if has_result and "output" not in shown:
                render_step_output(step)
                shown.add("output")
        self.write_state()

    def serialize(self, *extra_dicts) -> dict:
        return super().serialize({"demo": self.demo_info}, *extra_dicts)


class ConfirmAgent(InteractiveAgent):
    """mini-swe-agent's own interactive agent (the `mini` CLI UI): every command waits for Enter before it runs."""

    def __init__(self, *args, task: dict, demo_info: dict, **kwargs):
        super().__init__(*args, **kwargs)
        self.demo_info = demo_info

    def serialize(self, *extra_dicts) -> dict:
        return super().serialize({"demo": self.demo_info}, *extra_dicts)


def prepare_workspace(run_id: str) -> Path:
    """Fresh, read-only copy of the lake so a run can never damage the original data or see other runs."""
    workspace = Path(tempfile.gettempdir()) / "datalake-demo" / run_id
    shutil.copytree(LAKE, workspace)
    for path in workspace.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    return workspace


def build_agent(
    model_name: str,
    task: dict,
    workspace: Path,
    output_path: Path,
    step_limit: int | None,
    extra_model_kwargs: dict,
    confirm: bool = False,
    state_path: Path | None = None,
    label: str = "",
) -> DefaultAgent:
    config = yaml.safe_load(CONFIG.read_text())
    agent_cfg = dict(config["agent"], output_path=output_path)
    if step_limit:
        agent_cfg["step_limit"] = step_limit

    model_cfg = dict(config["model"])
    model_cfg["model_kwargs"] = dict(model_cfg.get("model_kwargs", {}))
    if model_name.startswith(("ollama/", "ollama_chat/")):
        # Ollama's default context window is small; give the local model the same room as the API model.
        # A fixed seed (with temperature 0) makes the live run match the rehearsal as closely as possible.
        model_cfg["model_kwargs"].update(num_ctx=16384, keep_alive="30m", seed=7)
    model_cfg["model_kwargs"].update(extra_model_kwargs)

    env_cfg = dict(config["environment"])
    env_cfg["env"] = dict(env_cfg.get("env", {}), PATH=f"{ROOT / '.venv' / 'bin'}:{os.environ.get('PATH', '')}")
    # The agent's shell inherits os.environ; blank out API keys so a stray `env` can't print one into a trajectory.
    env_cfg["env"].update({k: "" for k in os.environ if k.endswith(("_API_KEY", "_KEY", "_TOKEN"))})
    env_cfg["cwd"] = str(workspace)

    demo_info = {
        "task_id": task["task_id"],
        "question": task["question"],
        "ground_truth": task["answer"],
        "model_name": model_name,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    if confirm:
        agent_cfg.update(mode="confirm", confirm_exit=False)
    if confirm:
        agent_class, extra = ConfirmAgent, {}
    else:
        agent_class, extra = LiveAgent, {"state_path": state_path, "label": label}
    return agent_class(
        LitellmTextbasedModel(model_name=model_name, **model_cfg),
        GuardedLocalEnvironment(**env_cfg),
        task=task,
        demo_info=demo_info,
        **extra,
        **agent_cfg,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="strong", help="`strong`, `small`, or any litellm model name")
    parser.add_argument("--task", default="cta", help="cta | lotto | overdose | <task id>")
    parser.add_argument("--step-limit", type=int, default=None)
    parser.add_argument("--label", default=None, help="name of the run folder (defaults to the --model value)")
    parser.add_argument("--confirm", action="store_true", help="use mini-swe-agent's interactive UI: press Enter to run each command")
    parser.add_argument("--state", type=Path, default=None, help="write a live JSON snapshot here after every step (used by the slides)")
    parser.add_argument("--model-kwargs", default=None, help='extra litellm kwargs as JSON, e.g. \'{"reasoning_effort": "none"}\'')
    args = parser.parse_args()

    load_env()
    logging.getLogger("LiteLLM").setLevel(logging.ERROR)
    model_name = model_for(args.model)
    task = load_task(args.task)
    label = args.label or (args.model if is_label(args.model) else short_model_name(model_name))
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_model = re.sub(r"[^A-Za-z0-9._-]+", "_", short_model_name(model_name))
    output_path = RUNS_DIR / task["task_id"] / f"{label}__{safe_model}" / f"{run_id}.traj.json"

    workspace = prepare_workspace(f"{run_id}-{safe_model}")
    render_header(task, model_name, "live run")
    console.print(f"[dim]lake: {workspace}   trajectory: {output_path.relative_to(ROOT)}[/]")

    # STRONG_MODEL_KWARGS / SMALL_MODEL_KWARGS in .env apply to the `strong` / `small` labels.
    kwargs_json = args.model_kwargs or os.environ.get(f"{args.model.upper()}_MODEL_KWARGS", "")
    extra_model_kwargs = json.loads(kwargs_json) if kwargs_json else {}
    agent = build_agent(
        model_name, task, workspace, output_path, args.step_limit, extra_model_kwargs, args.confirm, args.state, label
    )
    live = isinstance(agent, LiveAgent)

    def stop_on_sigterm(*_):  # the slides' Stop button sends SIGTERM; treat it like Ctrl-C
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_on_sigterm)
    if args.confirm:
        console.print("[bold yellow]Confirm mode:[/] mini-swe-agent's own interactive UI. Press Enter to run each command.")
    start = time.time()
    try:
        result = agent.run(task["question"])
    except KeyboardInterrupt:
        agent.save(output_path)
        if live:
            agent.finish_state("Stopped")
        console.print("\n[bold red]Interrupted.[/] Partial trajectory saved.")
        return 130
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    if live:
        agent.finish_state()
    console.print(
        f"[dim]{agent.n_calls} model calls · {time.time() - start:.0f}s · exit status {result.get('exit_status')} · "
        f"saved {output_path.relative_to(ROOT)}[/]"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Shared helpers: paths, tasks, models, trajectory parsing, failure tagging and terminal rendering."""

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console, Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

ROOT = Path(__file__).resolve().parent.parent
LAKE = ROOT / "lake"
TASKS_DIR = ROOT / "tasks"
RUNS_DIR = ROOT / "runs"
CONFIG = ROOT / "config" / "lakeqa.yaml"

TASK_ALIASES = {
    "cta": "k-1-d-1_task_3",
    "lotto": "k-1-d-1_task_2",
    "overdose": "k-1-d-1_task_1",
}

SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def is_label(name: str) -> bool:
    """`strong`, `small`, or any other name with a <NAME>_MODEL line in .env (e.g. `qwen`)."""
    load_env()
    return name in ("strong", "small") or bool(re.fullmatch(r"\w+", name) and os.environ.get(f"{name.upper()}_MODEL"))


def model_for(label: str) -> str:
    """Map a demo label (`strong`, `small`, `qwen`, ...) to a litellm model name from .env."""
    load_env()
    defaults = {"strong": "ollama_chat/qwen3.5:9b", "small": "ollama_chat/qwen2.5:3b"}
    if is_label(label):
        return os.environ.get(f"{label.upper()}_MODEL", defaults.get(label, label))
    return label


def short_model_name(model_name: str) -> str:
    return model_name.split("/", 1)[-1]


def load_task(name: str) -> dict:
    task_id = TASK_ALIASES.get(name, name)
    task = json.loads((TASKS_DIR / f"{task_id}.json").read_text())
    task["task_id"] = task_id
    task["alias"] = next((a for a, t in TASK_ALIASES.items() if t == task_id), task_id)
    task["target_datasets"] = sorted({key.split("/")[1] for key in task["datasets_used"]})
    return task


# --------------------------------------------------------------------------------------------------
# Trajectory → steps
# --------------------------------------------------------------------------------------------------


@dataclass
class Step:
    index: int
    kind: str  # "action" | "format_error" | "exit"
    thought: str = ""
    command: str = ""
    raw_response: str = ""
    output: str = ""
    returncode: int | None = None
    exception: str = ""
    error_message: str = ""
    exit_status: str = ""
    submission: str = ""
    elapsed: float | None = None
    tags: list[str] = field(default_factory=list)


ACTION_RE = re.compile(r"```mswea_bash_command\s*\n(.*?)\n```", re.DOTALL)


def split_thought(content: str) -> str:
    thought = ACTION_RE.split(content)[0] if ACTION_RE.search(content) else content
    thought = re.sub(r"</?(thought|important|format_example|response_example|example_response)>", "", thought)
    return re.sub(r"^\s*THOUGHT:\s*", "", thought.strip()).strip()


def trajectory_steps(messages: list[dict]) -> list[Step]:
    """Turn the linear message history into one Step per model call (plus the final exit)."""
    steps: list[Step] = []
    last_ts = next((m["extra"]["timestamp"] for m in messages if m.get("extra", {}).get("timestamp")), None)
    pending: Step | None = None
    for msg in messages[2:]:
        role, extra = msg.get("role"), msg.get("extra", {}) or {}
        if role == "assistant":
            content = msg.get("content") or ""
            actions = extra.get("actions", [])
            pending = Step(
                index=len(steps) + 1,
                kind="action",
                thought=split_thought(content),
                command=actions[0]["command"] if actions else "",
                raw_response=content,
            )
            if extra.get("timestamp") and last_ts:
                pending.elapsed = extra["timestamp"] - last_ts
            steps.append(pending)
        elif role == "user" and extra.get("interrupt_type") == "FormatError":
            response = extra.get("model_response", "")
            steps.append(
                Step(
                    index=len(steps) + 1,
                    kind="format_error",
                    thought=split_thought(response),
                    raw_response=response,
                    error_message=f"Expected exactly 1 action, found {extra.get('n_actions', 0)}.",
                )
            )
            pending = None
        elif role == "user" and pending is not None:
            pending.output = extra.get("raw_output", "")
            pending.returncode = extra.get("returncode")
            pending.exception = extra.get("exception_info") or ""
            if extra.get("timestamp"):
                last_ts = extra["timestamp"]
            pending = None
        elif role == "exit":
            if pending is not None and SUBMIT_MARKER in pending.command:
                pending.output = f"{SUBMIT_MARKER}\n{extra.get('submission', '')}"
                pending.returncode = 0
            steps.append(
                Step(
                    index=len(steps) + 1,
                    kind="exit",
                    exit_status=extra.get("exit_status", msg.get("content", "")),
                    submission=(extra.get("submission") or "").strip(),
                )
            )
    return steps


# --------------------------------------------------------------------------------------------------
# Grading and failure-mode tagging
# --------------------------------------------------------------------------------------------------

FAILURE_MODES = {
    "bad_format": "Bad formatting",
    "wrong_assumption": "Wrong assumption about the data",
    "repeated_failure": "Repeated failure",
    "premature_answer": "Premature answer",
}

ASSUMPTION_ERRORS = re.compile(
    r"KeyError|No such file or directory|FileNotFoundError|not in index|has no attribute|"
    r"could not convert|ParserError|Error tokenizing|UnicodeDecodeError|unconverted data remains|"
    r"does not match format|Unknown datetime string|None of \[|EmptyDataError",
)


READS_DATA = re.compile(r"(datagov|wikipedia)/[^\s'\"]+/(files/[^\s'\"]+|content\.txt)")


CLAIMS_RESULT = re.compile(r"\bI found\b|\bfound (a|the) file\b|command found\b|output shows\b", re.IGNORECASE)


def extract_answer(submission: str) -> str | None:
    match = re.search(r"\[([^\[\]]+)\]", submission or "")
    return match.group(1).strip() if match else None


def answers_match(predicted: str | None, truth: str) -> bool:
    if predicted is None:
        return False
    p, t = predicted.strip().lower().rstrip("."), truth.strip().lower()
    if p == t:
        return True
    try:
        return abs(float(p.replace(",", "")) - float(t.replace(",", ""))) <= 1e-6 * max(1.0, abs(float(t)))
    except ValueError:
        return False


FAILURE_OUTPUT = re.compile(r"Traceback|No such file or directory|command not found|cannot open|Is a directory|SyntaxError")


def step_failed(step: Step) -> bool:
    """Non-zero exit, or an error message that a pipe (`head missing.csv | awk ...`) hid behind exit code 0."""
    return step.returncode not in (0, None) or bool(step.exception) or bool(FAILURE_OUTPUT.search(step.output or ""))


def _error_signature(step: Step) -> str:
    lines = [ln.strip() for ln in (step.output or "").strip().splitlines() if ln.strip()]
    return lines[-1][:160] if lines else ""


def _norm_cmd(cmd: str) -> str:
    return re.sub(r"\s+", " ", cmd).strip()


def tag_steps(steps: list[Step], task: dict) -> None:
    """Attach heuristic failure-mode tags to steps (hand annotations in web/annotations.json add the story)."""
    seen_cmds: set[str] = set()
    seen_errors: set[str] = set()
    prev: Step | None = None
    for step in steps:
        if (
            step.kind == "action"
            and prev is not None
            and prev.kind == "action"
            and not (prev.output or "").strip()
            and CLAIMS_RESULT.search(step.thought)
        ):
            step.tags.append("wrong_assumption")  # the previous command printed nothing, yet the model "saw" a result
        prev = step
        if step.kind == "format_error":
            step.tags.append("bad_format")
            continue
        if step.kind != "action":
            continue
        if re.search(r"<returncode>|<output>", step.raw_response):
            step.tags.append("bad_format")  # the model wrote (invented) the tool output itself
        failed = step_failed(step)
        if failed and ASSUMPTION_ERRORS.search(step.output + step.exception):
            step.tags.append("wrong_assumption")
        cmd = _norm_cmd(step.command)
        signature = _error_signature(step) if failed else ""
        if SUBMIT_MARKER not in cmd and (cmd in seen_cmds or (signature and signature in seen_errors)):
            step.tags.append("repeated_failure")
        seen_cmds.add(cmd)
        if signature:
            seen_errors.add(signature)

    exit_step = steps[-1] if steps and steps[-1].kind == "exit" else None
    _tag_exit(steps, exit_step, task)
    for step in steps:
        step.tags = list(dict.fromkeys(step.tags))


def _tag_exit(steps: list[Step], exit_step: Step | None, task: dict) -> None:
    if exit_step and exit_step.exit_status == "Submitted":
        submit_step = next((s for s in reversed(steps) if s.kind == "action" and SUBMIT_MARKER in s.command), None)
        before = [s for s in steps if s.kind == "action" and s is not submit_step]
        # Premature = it answered without ever successfully reading a data file (any dataset counts: in our lake
        # a decoy can also support the right answer, so picking it is not by itself a failure).
        read_data = any(READS_DATA.search(s.command) and not step_failed(s) for s in before)
        if not read_data and submit_step is not None:
            submit_step.tags.append("premature_answer")
        if extract_answer(exit_step.submission) is None or exit_step.submission.strip() != f"[{extract_answer(exit_step.submission)}]":
            exit_step.tags.append("bad_format")
    elif exit_step and exit_step.exit_status == "RepeatedFormatError":
        exit_step.tags.append("bad_format")
    elif exit_step and exit_step.exit_status in ("LimitsExceeded", "TimeExceeded"):
        exit_step.tags.append("repeated_failure")


def summarize_run(data: dict) -> dict:
    demo = data.get("demo", {})
    task = load_task(demo["task_id"])
    steps = trajectory_steps(data["messages"])
    tag_steps(steps, task)
    exit_step = steps[-1] if steps and steps[-1].kind == "exit" else Step(0, "exit", exit_status="Unknown")
    predicted = extract_answer(exit_step.submission)
    counts = {mode: sum(mode in s.tags for s in steps) for mode in FAILURE_MODES}
    return {
        "task": task,
        "steps": steps,
        "exit_status": exit_step.exit_status,
        "submission": exit_step.submission,
        "predicted": predicted,
        "correct": answers_match(predicted, task["answer"]),
        "n_steps": sum(s.kind != "exit" for s in steps),
        "failure_counts": counts,
    }


# --------------------------------------------------------------------------------------------------
# Terminal rendering (shared by live runs and replays)
# --------------------------------------------------------------------------------------------------

console = Console(highlight=False)

MAX_OUTPUT_LINES = 18
TAG_STYLE = {
    "bad_format": "bold white on red",
    "wrong_assumption": "bold black on yellow",
    "repeated_failure": "bold white on magenta",
    "premature_answer": "bold white on dark_orange3",
}


def _clip(text: str, max_lines: int = MAX_OUTPUT_LINES, max_width: int = 160) -> str:
    lines = [ln if len(ln) <= max_width else ln[: max_width - 1] + "…" for ln in text.rstrip().splitlines()]
    if len(lines) > max_lines:
        head = lines[: max_lines - 4]
        tail = lines[-3:]
        lines = head + [f"… ({len(lines) - len(head) - len(tail)} more lines) …"] + tail
    return "\n".join(lines)


def render_header(task: dict, model_name: str, mode: str) -> None:
    console.print()
    console.rule(f"[bold cyan]mini-swe-agent × LakeQA[/]  ·  [bold]{short_model_name(model_name)}[/]  ·  {mode}")
    console.print(
        Panel(
            Text(task["question"], style="bold"),
            title=f"[cyan]Question[/] [dim]({task['task_id']})[/]",
            border_style="cyan",
            padding=(1, 2),
        )
    )


def render_tags(tags: list[str]) -> Text:
    text = Text()
    for tag in dict.fromkeys(tags):
        text.append(f" {FAILURE_MODES[tag].upper()} ", style=TAG_STYLE[tag])
        text.append(" ")
    return text


def render_step_thought(step: Step) -> None:
    console.print()
    console.rule(f"[bold]Step {step.index}[/]", align="left", style="grey50")
    if step.thought:
        console.print(Text("THOUGHT ", style="bold blue") + Text(_clip(step.thought, 10, 200), style="italic"))


def render_step_command(step: Step) -> None:
    if step.kind == "format_error":
        body = Group(
            Text("The reply did not contain exactly one ```mswea_bash_command block, so nothing was executed.\n", style="bold red"),
            Text(_clip(step.raw_response or "(empty response)", 12), style="red"),
        )
        console.print(Panel(body, title="[bold red]FORMAT ERROR[/]", border_style="red"))
        return
    console.print(
        Panel(
            Syntax(step.command, "bash", theme="monokai", word_wrap=True, background_color="default"),
            title="[bold green]$ command[/]",
            title_align="left",
            border_style="green",
        )
    )


def render_step_output(step: Step) -> None:
    if step.kind != "action" or SUBMIT_MARKER in step.command:
        if step.tags:
            console.print(render_tags(step.tags))
        return
    ok = not step_failed(step)
    style = "grey70" if ok else "red"
    body = Text(_clip(step.output or "(no output)"), style=style)
    rc = f"returncode={step.returncode}"
    console.print(Panel(body, title=f"[dim]output · {rc}[/]", title_align="left", border_style="grey37" if ok else "red"))
    if step.tags:
        console.print(render_tags(step.tags))


def render_exit(summary_or_step: Step, task: dict) -> None:
    step = summary_or_step
    predicted = extract_answer(step.submission)
    correct = answers_match(predicted, task["answer"])
    console.print()
    if step.exit_status == "Submitted":
        verdict = Text("✔ CORRECT", style="bold green") if correct else Text("✘ WRONG", style="bold red")
        body = Text.assemble(
            ("Submitted: ", "bold"), (step.submission or "(empty)", "bold yellow"), "\n",
            ("Ground truth: ", "bold"), (f"[{task['answer']}]", "bold cyan"), "\n\n", verdict,
        )
    else:
        body = Text.assemble(
            ("Agent stopped without an answer: ", "bold"), (step.exit_status, "bold red"), "\n",
            ("Ground truth: ", "bold"), (f"[{task['answer']}]", "bold cyan"), "\n\n", ("✘ NO ANSWER", "bold red"),
        )
    if step.tags:
        body.append("\n")
        body.append_text(render_tags(step.tags))
    console.print(Panel(body, title="[bold]Result[/]", border_style="green" if correct else "red", padding=(1, 2)))


def typewriter(text: str, delay: float) -> None:
    for ch in text:
        console.file.write(ch)
        console.file.flush()
        time.sleep(delay)
    console.file.write("\n")

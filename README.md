# Data Lake Agent

**An LLM agent that answers questions over a data lake of raw, undocumented files, using nothing but a bash shell.**

It runs [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) on a small data lake built from real [LakeQA](https://github.com/lakeagent/datalake-qa) dataset folders. It records every step: what the agent explores, which commands it runs, what it sees, and how it reaches an answer. Then it puts a frontier model and small open models on the same tasks, so you can see exactly where the small models go wrong.

### 🎬 Demo: annotated overview (1.5 min)

https://github.com/user-attachments/assets/f7f5491c-c0f8-4f34-9344-1eba09ac9df8

<p align="center">
  <b><a href="#full-slide-walk-through">🎞️ Slide walk-through (video)</a></b> &nbsp;·&nbsp;
  <b><a href="slides.pdf">📄 Slides (PDF, 29 pages)</a></b> &nbsp;·&nbsp;
  <b><a href="#run-it">▶️ Run it yourself</a></b>
</p>

---

## The problem

A data lake is a shared drive that nobody organized. Teams dump raw files as they are, and nobody writes down which file holds what. Answering a question means finding the right dataset first, then working out its format, and only then computing the answer.

The main task, `EQA000005` from LakeQA-mini:

> Define a yearly Passenger Instability-Adjusted Load (PIAL) as annual total CTA bus passengers across all routes multiplied by (1 + absolute year-over-year percentage change in that annual total). For years 2006 through 2010, which year has the lowest PIAL?

Nothing tells the agent:
- which of **four CTA folders** has yearly bus totals;
- that the data sits in a file called `rows.txt`, next to a 2.5 MB catalog dump and web-crawl headers;
- that the question has a trap: **2006's change needs 2005**, a year the question never mentions.

## The result: one missing row

Both models explore the same folders and both find `rows.txt`. The difference comes down to one line of code.

| | **GPT-6 Luna** (frontier) | **qwen3.5 9B** (local, Ollama) |
|---|---|---|
| Compute step | `change = abs(p - rows[y-1]) / rows[y-1]`, which reaches back to 2005 | `grep -E "^(2006\|2007\|2008\|2009\|2010),"`, which drops 2005 |
| 2006's change | 1.6% | `NaN` |
| What it says | "2006 has the lowest calculated PIAL" | "2006 has NaN … since there's no prior year data in our dataset" (false) |
| Answer | **[2006] ✅** in 9 steps | **[2010] ❌** in 10 steps |

The 9B model makes a **wrong assumption** about the data and gives a **premature answer**. In LakeQA's terms, it "analyzed, but still wrong": it found the right data and still answered wrong.

### Full slide walk-through

All 29 slides, including both agent runs replayed step by step (1:45). Also available as a [PDF](slides.pdf).

https://github.com/user-attachments/assets/1dc510a6-8bd4-4a16-b9cc-bb53f97e7510

## Results

These are correct answers out of recorded runs. Every run is in [`runs/`](runs), and none were dropped.

| Model | Parameters | Runs on | CTA bus<br><sub>EQA000005</sub> | NY Lotto<br><sub>EQA000003</sub> | Overdose<br><sub>EQA000002</sub> | All |
|---|---|---|:-:|:-:|:-:|:-:|
| GPT-6 Luna | not disclosed | Azure OpenAI | 2 / 2 | 1 / 1 | 1 / 1 | **4 / 4** |
| qwen3.5 9B | 9B | Ollama, local | 1 / 3\* | 3 / 3 | 1 / 1 | **5 / 7** |
| qwen2.5 3B | 3B | Ollama, local | 0 / 1 | 0 / 4 | 0 / 1 | **0 / 6** |
| 4 other small models | 1B – 8B | Ollama, local | 0 / 4 | 0 / 4 | 1 / 4 | **1 / 12** |

<sub>\* The 9B model dropped 2005 in all three CTA runs. One run still answered 2006, but only because it set 2006's change to 0%: the right answer for the wrong reason. The other small models are llama3.2 1B and 3B, qwen2.5 1.5B and qwen3 8B. Every run uses the same settings: mini-swe-agent v2.4.6, the same prompt, temperature 0, at most 20 steps, a 60 s limit per command, and exact-match grading against LakeQA's answer.</sub>

The tasks are official LakeQA-mini tasks (`k-1-d-1/task_1..3`). Their text and answers are identical to the LakeQA repo, and LakeQA's own reference solutions reproduce on this copy of the lake. This is a small-scale study: 3 tasks, a 10-folder lake instead of 40 million files, and bash instead of LakeQA's 8 tools. The numbers show how the models behave; they are not a benchmark score.

### The four characteristic failure modes

Each run is tagged automatically, and the featured runs also carry hand-written notes:

| Failure mode | What it looks like (real recorded steps) |
|---|---|
| **Bad formatting** | The reply has zero or two command blocks, so nothing runs and a step is lost. Also malformed final answers. |
| **Wrong assumption** | The model guesses a file name (`nylotto.csv`) or a column (`Bonus Ball`) that doesn't exist, or filters out the row it needs. |
| **Repeated failure** | The same broken command, 18 times in a row, until the step limit runs out. |
| **Premature answer** | The model submits without ever reading the dataset, or right after noticing something is off. |

## How it works

```
question ─► model ─► one bash command ─► shell (read-only lake copy) ─► output ─┐
              ▲                                                                 │
              └──────────────────── appended to the conversation ◄─────────────┘
                         ... until: echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && echo "[answer]"
```

1. **Sandbox.** Each run copies the lake to a fresh **read-only** temp folder and starts the agent's shell there. The agent can't modify the data or read the answers in `tasks/`. API keys are blanked from its environment.
2. **Agent loop.** mini-swe-agent's unmodified `DefaultAgent` runs with `LitellmTextbasedModel` and `LocalEnvironment`. Each reply must contain exactly one ```` ```mswea_bash_command ```` block; anything else is a format error, and the model is told to retry.
3. **Guardrails.** A small guard blocks `sudo`, `rm -rf /`, `curl | sh` and network commands. Output longer than 6,000 characters is cut down to its start and end.
4. **Grading.** Answers are graded by exact match against the LakeQA answer. Steps are tagged with the four failure modes, and everything is exported to the slide deck's viewer.

## Run it

Requires macOS or Linux, [uv](https://github.com/astral-sh/uv), and [Ollama](https://ollama.com) for the local models.

```bash
git clone https://github.com/Nandish02/data-lake-agent && cd data-lake-agent
./setup.sh            # Python env, the mini lake (~50 MB from LakeQA's public S3 bucket), Ollama models, self-check
cp .env.example .env  # then set your strong model + key (any litellm model id works)
```

```bash
./demo.sh strong                # frontier model on the CTA bus task, live in the terminal
./demo.sh qwen                  # qwen3.5:9b, local
./demo.sh small                 # qwen2.5:3b, local
./demo.sh strong lotto          # tasks: cta (default) | lotto | overdose
./demo.sh small --confirm       # mini-swe-agent's interactive UI: approve each command
./demo.sh strong --replay       # replay a recorded run (offline, no key needed)
./demo.sh inspect               # browse all trajectories in mini-swe-agent's Inspector
./present.sh                    # open the interactive slide deck at http://localhost:8765
```

In the deck, the demo slides (17 and 20) replay a recorded run step by step. Press **G** to play and **P** to pause, or **Shift+G** to run the agent live. Slide 29 lets you browse every recorded run. You can also open `web/index.html` directly as a file.

To swap models, edit `.env`. For example, `STRONG_MODEL=gemini/gemini-2.5-flash` with `GEMINI_API_KEY=…`, or any Ollama model as `ollama_chat/<name>`.

## Project layout

```
config/lakeqa.yaml     mini-swe-agent config: the text-based action format with a data-lake QA prompt
scripts/run_agent.py   DefaultAgent + LitellmTextbasedModel + LocalEnvironment, sandbox, live terminal rendering
scripts/serve.py       serves the deck; starts, streams and stops live runs for the demo slides
scripts/replay.py      replays a recorded trajectory with the same rendering
scripts/grade.py       exact-match grading, failure-mode tagging, scoreboard; exports web/data/runs.js
scripts/batch.py       records N runs per (model, task)
scripts/fetch_lake.py  mirrors the LakeQA dataset folders from the public S3 bucket
scripts/check.py       pre-flight check: lake present, reference answers reproduce, models respond
tasks/                 the 3 LakeQA-mini tasks, verbatim
runs/                  every recorded trajectory (mini-swe-agent format) + hand annotations
web/                   the slide deck and trajectory viewer (plain HTML/CSS/JS, no build step)
docs/media/            the recordings in this README
```

## Credits

- **mini-swe-agent**, by the SWE-bench / SWE-agent team (MIT). Its demo GIFs and control-flow diagram are used in the slides.
- **LakeQA**: Wang et al., *LakeQA: An Exploratory QA Benchmark over a Million-Scale Data Lake*, ICML 2026, [arXiv:2606.10460](https://arxiv.org/abs/2606.10460) (MIT). The tasks, the example figure and the dataset folders come from it.
- The agent-loop pseudocode is from [minimal-agent.com](https://minimal-agent.com), by the mini-swe-agent authors.
- The data is public data.gov and Wikipedia content, mirrored by the LakeQA authors.

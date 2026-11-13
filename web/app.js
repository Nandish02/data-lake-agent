/* Slide deck + trajectory viewer. Data comes from data/runs.js (written by scripts/grade.py). */
(() => {
  const DATA = window.DEMO_DATA || { runs: [], tasks: {}, featured: {}, lake: [], gallery: [], failure_modes: {} };
  const MAIN_TASK = "k-1-d-1_task_3";  // CTA bus ridership: GPT-6 Luna gets it right, qwen3.5 9B drops the 2005 baseline
  const MAIN_ALIAS = "cta";
  // Folders that also hold the answer besides the task's official evidence (the annual totals give the same 2006).
  const ALSO_HAS_IT = { "k-1-d-1_task_3": ["cta-ridership-annual-boarding-totals"] };
  const SUBMIT = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT";
  const STEP_LIMIT = 20; // config/lakeqa.yaml step_limit
  const printMode = new URLSearchParams(location.search).has("print");
  const calm = printMode || matchMedia("(prefers-reduced-motion: reduce)").matches;
  // Readable names for the 10 dataset folders, so the audience sees what each one is, not its slug.
  const FRIENDLY = {
    "lottery-ny-lotto-winning-numbers-beginning-2001": "NY Lotto winning numbers",
    "lottery-aid-to-education-beginning-2002": "Lottery aid to education",
    "cta-ridership-bus-routes-monthly-day-type-averages-totals": "CTA bus ridership by route",
    "cta-ridership-daily-boarding-totals": "CTA daily boardings",
    "cta-ridership-l-station-entries-monthly-day-type-averages-totals": "CTA 'L' station entries",
    "cta-ridership-annual-boarding-totals": "CTA annual boardings",
    "chicago-population-counts": "Chicago population",
    "public-health-statistics-life-expectancy-by-community-area": "Life expectancy by area",
    "vsrr-provisional-drug-overdose-death-counts": "Drug overdose deaths",
    "Chicago_Police_Department": "Chicago Police (Wikipedia)",
  };
  const nice = (slug) => FRIENDLY[slug] || slug;
  const MODES = {
    bad_format: "Bad formatting",
    wrong_assumption: "Wrong assumption about the data",
    repeated_failure: "Repeated failure",
    premature_answer: "Premature answer",
  };
  const MODE_COLOR = { bad_format: "--tag-format", wrong_assumption: "--tag-assume", repeated_failure: "--tag-repeat", premature_answer: "--tag-premature" };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const store = {
    get(k, d) { try { return sessionStorage.getItem(k) ?? d; } catch { return d; } },
    set(k, v) { try { sessionStorage.setItem(k, v); } catch { /* storage unavailable */ } },
  };
  const shortModel = (m) => String(m || "").split("/").slice(1).join("/") || m;
  const clipLines = (text, n) => {
    const lines = String(text || "").replace(/\s+$/, "").split("\n");
    return lines.length <= n ? lines.join("\n") : lines.slice(0, n).join("\n") + `\n… (${lines.length - n} more lines)`;
  };

  // ------------------------------------------------------------------ run selection
  let mode = store.get("runMode", "featured");
  const byTime = (a, b) => (a.started_at < b.started_at ? -1 : 1);
  function pickRun(taskId, label) {
    const candidates = DATA.runs.filter((r) => r.task_id === taskId && r.label === label).sort(byTime);
    if (mode === "latest") return candidates.at(-1);
    const featuredId = DATA.featured?.[taskId]?.[label];
    return DATA.runs.find((r) => r.id === featuredId) || candidates.at(-1);
  }

  // ------------------------------------------------------------------ step helpers
  function phaseOf(step) {
    if (step.kind === "format_error") return "error";
    if (step.kind === "exit") return "end";
    const c = step.command || "";
    if (c.includes(SUBMIT)) return "answer";
    if (/^\s*(find|ls|tree)\b/.test(c) && !/\b(cat|python3?|grep)\b/.test(c)) return "explore"; // `find … | head` is still a search
    if (/\b(python3?|pandas|awk|duckdb|sqlite3)\b/.test(c)) {
      const computes = /groupby|sum\(|pct_change|mean|std|\.agg\(|idxmax|idxmin|max\(|min\(|sort_values|resample|BVAL|PIAL/i.test(c);
      if (!computes && /iterdir|read_text\(|readlines|\.head\(/.test(c)) return "inspect";
      return computes || c.split("\n").length > 6 ? "compute" : "inspect";
    }
    if (/\b(head|cat|tail|wc|less|jq|file|cut|column|sed\s+-n)\b/.test(c)) return "inspect";
    return "explore";
  }
  const FAIL_OUT = /Traceback|No such file or directory|command not found|cannot open|Is a directory|SyntaxError/;
  const isFail = (s) => s.kind === "action" && !s.command.includes(SUBMIT) && (s.returncode !== 0 || FAIL_OUT.test(s.output || ""));
  // One plain-English line per step: what the agent is doing, in words a class can follow.
  function describe(step) {
    if (step.kind === "format_error") return "Breaks the reply format, so nothing runs";
    const c = step.command || "";
    if (c.includes(SUBMIT)) { const m = c.match(/\[[^\]\n]*\]/); return m ? `Submits its answer ${m[0]}` : "Submits, but forgets the answer"; }
    const touched = step.touched || [];
    const lakeFile = touched.find((p) => p.split("/").length > 2 && /\.\w+$/.test(p));
    const csv = c.match(/read_csv\(\s*["']([^"']+)/);
    const fname = lakeFile ? lakeFile.split("/").pop() : csv ? csv[1].split("/").pop() : null;
    const ds = touched.map((p) => p.split("/")[1]).find(Boolean);
    const ph = phaseOf(step);
    let h;
    const named = c.match(/-i?name\s+["']?([^"'\s]+)/);
    const grepped = c.match(/\bfind\b[^|]*\|\s*grep\s+(?:-\w+\s+)*["']([^"']+)/);
    const lib = /pandas|\bpd\./.test(c) ? "pandas" : "Python";
    if (/\bfind\b/.test(c) && named && !ds) h = `Searches the lake for "${named[1]}"`;
    else if (grepped) h = `Searches folder names for "${grepped[1]}"`;
    else if (/\bfind\b/.test(c) && /-type f/.test(c) && ds && !fname) h = `Lists the files in "${nice(ds)}"`;
    else if (/iterdir/.test(c) && ds) h = `Prints the start of every file in "${nice(ds)}"`;
    else if (ph === "compute") h = fname ? `Runs a ${lib} script on ${fname}` : `Runs a ${lib} script`;
    else if (/\bpython/.test(c)) h = fname ? `Loads ${fname} with pandas to look at it` : "Runs a quick Python check";
    else if (/\bgrep\b/.test(c) && fname) h = `Pulls matching rows out of ${fname}`;
    else if (/^\s*ls\b/.test(c) && ds) h = /\/files\b/.test(c) ? `Lists the files in "${nice(ds)}"` : `Opens the folder "${nice(ds)}"`;
    else if (/^\s*ls\b[^|]*\b(datagov|wikipedia)\b/.test(c)) h = "Lists all the dataset folders";
    else if (ph === "inspect") h = fname ? `Peeks inside ${fname}` : ds ? `Reads a file in "${nice(ds)}"` : "Peeks at a file";
    else if (ds) h = `Opens the folder "${nice(ds)}"`;
    else h = "Looks around the lake";
    if (step.kind === "action" && step.returncode != null) {
      const out = step.output || "";
      if (isFail(step)) h += /No such file|cannot open|FileNotFound/.test(out) ? ": the file doesn't exist" : ": and it crashes";
      else if (!out.trim()) h += ": finds nothing";
      else if (out.length > 6000) h += ": too much output";
    }
    return h;
  }
  const tagsHtml = (tags) => `<span class="tags">${[...new Set(tags || [])].map((t) => `<span class="tag ${t}">${esc(MODES[t] || t)}</span>`).join("")}</span>`;

  function stepHtml(step, compact, live) {
    const phase = phaseOf(step);
    if (step.kind === "exit") return "";
    let body = `<div class="phase">${phase === "error" ? "format error" : phase}${tagsHtml(step.tags)}</div><div class="head">${esc(describe(step))}</div>`;
    if (step.thought && !compact) body += `<div class="thought">${esc(clipLines(step.thought, 3))}</div>`;
    if (step.kind === "format_error") {
      const raw = step.raw_response || "(empty reply)";
      const fence = raw.indexOf("```");
      body += `<pre class="raw">${esc(clipLines(fence > 0 ? "…\n" + raw.slice(fence) : raw, compact ? 4 : 7))}</pre>`;
      body += `<div class="faint small" style="margin-top:.3em">Not exactly one <code>mswea_bash_command</code> block, so nothing ran</div>`;
    } else {
      body += `<pre class="cmd">${esc(clipLines(step.command, compact ? 4 : 8))}</pre>`;
      if (live && step.kind === "action" && step.returncode == null && !step.command.includes(SUBMIT)) {
        body += `<pre class="out pending">running…</pre>`;
      } else if (!step.command.includes(SUBMIT)) {
        body += `<pre class="out ${isFail(step) ? "err" : ""}">${esc(clipLines(step.output || "(no output)", compact ? 3 : 6))}</pre>`;
      }
    }
    if (step.note) body += `<div class="note">${esc(step.note)}</div>`;
    return `<div class="step" data-phase="${phase}"><div class="num">${step.index}</div>${summaryHtml(step, phase, live)}<div class="body">${body}</div></div>`;
  }

  // One-line version of a step, shown once it is no longer the current one, so a whole run fits without scrolling.
  function summaryHtml(step, phase, live) {
    let what, mark;
    if (step.kind === "format_error") { what = "reply had no valid mswea_bash_command block: nothing ran"; mark = '<span class="r bad">✗</span>'; }
    else {
      what = "$ " + (step.command || "").split("\n")[0].replace(`${SUBMIT} && echo `, "SUBMIT ");
      const pending = live && step.returncode == null && !step.command.includes(SUBMIT);
      mark = pending ? '<span class="r faint">…</span>' : isFail(step) ? '<span class="r bad">✗</span>' : '<span class="r good">✓</span>';
    }
    return `<div class="sum"><span class="ph">${phase === "error" ? "format" : phase}</span><span class="h">${esc(describe(step))}</span><span class="c">${esc(what)}</span>${tagsHtml(step.tags)}${mark}</div>`;
  }

  function focusSteps(els, current) {
    els.forEach((el, i) => { el.classList.toggle("collapsed", i !== current && !["end", "thinking"].includes(el.dataset.phase)); el.classList.remove("open"); });
    // Fold a run of identical-looking folded lines (a small model retrying the same command) into one "×N" line.
    els.forEach((el) => $(".rep", el)?.remove());
    let head = null, count = 0;
    const key = (el) => el.classList.contains("collapsed") && !el.classList.contains("hidden") ? $(".sum", el)?.textContent : null;
    const close = () => { if (head) { if (count > 1) $(".sum", head).insertAdjacentHTML("beforeend", `<span class="rep">×${count} alike, to step ${head.dataset.last}</span>`); } };
    els.forEach((el) => {
      el.classList.remove("folded");
      const k = key(el);
      if (k && head && k === key(head)) { el.classList.add("folded"); count++; head.dataset.last = $(".num", el).textContent; return; }
      close();
      head = k ? el : null; count = 1;
    });
    close();
  }

  function resultHtml(run) {
    const task = DATA.tasks[run.task_id] || {};
    const ok = run.correct;
    const sub = run.exit_status === "Submitted" ? `<span class="answer">${esc(run.submission || "(empty)")}</span>` : `<span class="bad">${esc(run.exit_status)}</span>`;
    const exit = (run.steps || []).find((s) => s.kind === "exit");
    return `<div class="step" data-phase="end"><div class="num"></div><div class="result ${ok ? "ok" : "no"}">
      ${ok ? '<span class="good"><span class="dot" style="--c:var(--good)"></span>correct</span>' : '<span class="bad"><span class="dot" style="--c:var(--bad)"></span>wrong</span>'}
      <span>answer ${sub}</span><span class="muted">truth <span class="answer">[${esc(task.answer)}]</span></span>${tagsHtml(exit?.tags)}
    </div></div>`;
  }

  function headHtml(run) {
    if (!run) return "";
    const nFail = (run.steps || []).filter((s) => s.tags?.length).length;
    return `<span class="model">${esc(shortModel(run.model))}</span>
      <span class="stat">${run.n_steps} steps · ${run.correct ? '<span class="good">correct</span>' : '<span class="bad">' + (run.exit_status === "Submitted" ? "wrong answer" : esc(run.exit_status)) + "</span>"}${nFail ? ` · ${nFail} flagged steps` : ""}</span>
      <span class="faint small mono">${mode === "latest" ? "latest run" : "recorded"} ${esc(run.started_at || "")}</span>`;
  }

  // A trace = header + phase strip + step list, revealed incrementally by the stepper.
  function mountTrace(prefix, run, compact) {
    const head = $(`#${prefix}-head`), strip = $(`#${prefix}-strip`), list = $(`#${prefix}`) || $(`#${prefix}-trace`);
    if (!run) {
      if (head) head.innerHTML = "";
      list.innerHTML = `<div class="card muted">No recorded run yet. Record one with <code>./demo.sh ${prefix.includes("small") ? "small" : "strong"}</code>, then reload.</div>`;
      return { total: 0, show() {} };
    }
    if (head) head.innerHTML = headHtml(run);
    const steps = run.steps.filter((s) => s.kind !== "exit");
    list.innerHTML = steps.map((s) => stepHtml(s, compact)).join("") + resultHtml(run);
    const els = $$(".step", list);
    if (strip) strip.innerHTML = steps.map((s) => `<i data-phase="${phaseOf(s)}" title="step ${s.index}"></i>`).join("");
    const tiles = strip ? $$("i", strip) : [];
    return {
      total: els.length,
      run,
      show(n) {
        els.forEach((el, i) => { el.classList.toggle("hidden", i >= n); el.classList.toggle("latest", i === n - 1); });
        // At the final result row, keep the last real step expanded above it.
        focusSteps(els, n >= els.length ? els.length - 2 : n - 1);
        tiles.forEach((t, i) => t.classList.toggle("dim", i >= n));
        const last = els[Math.min(n, els.length) - 1];
        if (last) last.scrollIntoView({ block: "nearest", behavior: "smooth" });
      },
      visibleSteps(n) { return steps.slice(0, n); },
    };
  }

  // ------------------------------------------------------------------ lake map
  // The 10 dataset folders as tiles. An agent marker moves to the folder it is working in, the files it opened
  // appear inside that tile, and the file that actually holds the data lights up.
  function lakeMap(container) {
    if (!container) return { update() {} };
    const task = DATA.tasks[MAIN_TASK] || {};
    const targets = new Set([...(task.target_datasets || []), ...(ALSO_HAS_IT[MAIN_TASK] || [])]);
    const dataFiles = new Set([(task.datasets_used || [])[0], ...(ALSO_HAS_IT[MAIN_TASK] || []).map((d) => `datagov/${d}/files/rows.txt`)]);
    container.classList.add("lakemap");
    container.innerHTML = DATA.lake.map((ds) => {
      const slug = ds.path.split("/")[1];
      const right = targets.has(slug);
      return `<div class="isle ${right ? "target" : "decoy"}" data-slug="${esc(slug)}"><div class="top"><span class="nm">${esc(nice(slug))}</span><span class="tg">${right ? "has the data" : "decoy"}</span></div><div class="files"></div></div>`;
    }).join("") + `<div class="agent" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 8V5"/><circle cx="12" cy="4" r="1.4"/><circle cx="9.5" cy="13" r="1.3" class="eye"/><circle cx="14.5" cy="13" r="1.3" class="eye"/></svg></div>`;
    const isles = Object.fromEntries($$(".isle", container).map((el) => [el.dataset.slug, el]));
    const agent = $(".agent", container);
    return {
      update(steps) {
        Object.values(isles).forEach((el) => { el.classList.remove("visited", "here"); $(".files", el).innerHTML = ""; });
        let here = null, scanned = false;
        for (const s of steps) {
          const t = s.touched || [];
          if (!t.length && /\b(ls|find|tree)\b/.test(s.command || "") && !(s.command || "").includes(SUBMIT)) scanned = true;
          for (const p of t) {
            const parts = p.split("/"), el = isles[parts[1]];
            if (!el) continue;
            el.classList.add("visited");
            here = el;
            const f = parts.length > 2 ? parts.at(-1) : "";
            if (/\.\w+$/.test(f)) {
              const box = $(".files", el);
              if (![...box.children].some((c) => c.dataset.f === f)) box.insertAdjacentHTML("beforeend", `<i data-f="${esc(f)}" class="${dataFiles.has(p) ? "hit" : "miss"}">${esc(f)}</i>`);
            }
          }
        }
        container.classList.toggle("scanned", scanned);
        container.classList.toggle("lost", steps.length >= 3 && !here);
        if (here) here.classList.add("here");
        agent.classList.toggle("on", !!here);
        // The marker lives inside its tile (works on hidden slides and in the PDF); it pops in when it moves.
        if (here && agent.parentElement !== here) { here.appendChild(agent); agent.classList.remove("pop"); void agent.offsetWidth; agent.classList.add("pop"); }
        if (!here && agent.parentElement !== container) container.appendChild(agent);
      },
    };
  }

  function fillStatic() {
    const task = DATA.tasks[MAIN_TASK];
    if (task) {
      $("#hero-question").textContent = task.question;
      const shorten = (d) => d.replace(/^(datagov\/[a-z]+-[a-z]+-[a-z]+)[a-z0-9-]*/, "$1…");
      const excerpt = {
        question: task.question.slice(0, 120) + "…",
        answer: task.answer,
        datasets_used: task.datasets_used.map(shorten),
        reasoning_hops: [{ hop: 1, reasoning: task.reasoning.slice(0, 110) + "…" }],
        nodes: { 1: { fact: "<reference pandas solution>" } },
      };
      $("#task-json").innerHTML = esc(JSON.stringify(excerpt, null, 1))
        .replace(/(&quot;[a-z_]+&quot;):/g, '<span class="f">$1</span>:')
        .replace(new RegExp(`(&quot;${task.answer}&quot;)`), '<span class="s">$1</span>');
      $("#truth-check").textContent = `[${task.answer}]`;
    }

    const paper = [["Claude Opus 4.5", 45.93], ["Claude Sonnet 4.5", 33.33], ["GPT-5.2", 17.04], ["Claude Haiku 4.5", 11.11], ["DeepSeek-R1", 6.67], ["GPT-5-mini", 2.22], ["Llama-3.3-70B", 1.48]];
    $("#paper-bars").innerHTML = paper.map(([m, v]) => `<div class="bar"><span>${m}</span><div class="track"><div class="fill" style="width:${v * 2}%"></div></div><span class="v">${v.toFixed(1)}%</span></div>`).join("");
    // The main chart: only the two search failures carry the accent, since they are the slide's point.
    const fail = [["Correct", 24, "ok"], ["Dataset never found", 31, "key"], ["Found, not opened", 56, "key"], ["Opened, not analyzed", 4], ["Analyzed, still wrong", 20]];
    $("#paper-fail").innerHTML = fail.map(([m, v, key]) => `<div class="bar ${key || ""}"><span>${m}</span><div class="track"><div class="fill" style="width:${(v / 56) * 100}%"></div></div><span class="v">${v}/135</span></div>`).join("");

    const targets = new Set(Object.values(DATA.tasks).flatMap((t) => t.target_datasets));
    $("#lake-grid").innerHTML = DATA.lake.map((ds) => {
      const size = ds.files.reduce((a, f) => a + f.size, 0) / 1e6;
      const name = ds.path.split("/")[1];
      return `<div class="ds ${targets.has(name) ? "target" : ""}" title="${esc(ds.path)}"><span class="name">${esc(ds.path)}</span><span class="meta">${ds.files.length} files · ${size.toFixed(1)} MB</span></div>`;
    }).join("");
  }

  function fillGallery() {
    const items = [];
    const smallRuns = DATA.runs.filter((r) => !["gpt-6-luna", "qwen3.5:9b"].includes(shortModel(r.model)));
    for (const [modeKey, title] of Object.entries(MODES)) {
      const curated = (DATA.gallery || []).find((g) => g.mode === modeKey);
      let run, step;
      if (curated) {
        run = DATA.runs.find((r) => r.id === curated.run);
        step = run?.steps.find((s) => String(s.index) === String(curated.step));
      }
      if (!step) {
        const ordered = [...smallRuns].sort((a, b) => (a.task_id === MAIN_TASK ? -1 : 1) - (b.task_id === MAIN_TASK ? -1 : 1));
        for (const r of ordered) {
          const s = r.steps.find((x) => x.tags?.includes(modeKey) && x.kind !== "exit");
          if (s) { run = r; step = s; break; }
        }
      }
      let body = `<p class="muted small">Not observed in the recorded runs.</p>`;
      if (step) {
        const raw = step.raw_response || "";
        const fence = raw.indexOf("```");
        const excerpt = fence > 0 ? "…\n" + raw.slice(fence) : raw;
        const shown = step.kind === "format_error" ? `<pre class="code" style="color:var(--bad)">${esc(clipLines(excerpt, 5))}</pre>`
          : `<pre class="code">$ ${esc(clipLines(step.command, 3))}</pre>${step.command.includes(SUBMIT) ? "" : `<pre class="code" style="color:${isFail(step) ? "var(--bad)" : "var(--faint)"}">${esc(clipLines(step.output || "(no output)", 2))}</pre>`}`;
        body = shown + `<p class="why">${esc(curated?.caption || step.note || "")}</p><div class="src">${esc(shortModel(run.model))} · ${esc(DATA.tasks[run.task_id]?.alias || run.task_id)} · step ${step.index}</div>`;
      }
      items.push(`<div class="card"><h3><span class="dot" style="--c:var(${MODE_COLOR[modeKey]})"></span>${esc(title)}</h3>${body}</div>`);
    }
    $("#gallery").innerHTML = items.join("");
  }

  // Model facts for the scoreboard. GPT-6 Luna's size isn't published.
  const MODEL_INFO = {
    "gpt-6-luna": { name: "GPT-6 Luna", params: "not disclosed", where: "Azure OpenAI API" },
    "qwen3.5:9b": { name: "qwen3.5 9B", params: "9B", where: "laptop · Ollama" },
    "qwen2.5:3b": { name: "qwen2.5 3B", params: "3B", where: "laptop · Ollama" },
    "qwen3:8b": { name: "qwen3 8B", params: "8B" }, "llama3.2:3b": { name: "llama3.2 3B", params: "3B" },
    "llama3.2:1b": { name: "llama3.2 1B", params: "1B" }, "qwen2.5:1.5b": { name: "qwen2.5 1.5B", params: "1.5B" },
  };
  const TIER_MODELS = ["gpt-6-luna", "qwen3.5:9b", "qwen2.5:3b"];
  const SCORE_TASKS = ["k-1-d-1_task_3", "k-1-d-1_task_2", "k-1-d-1_task_1"]; // CTA first: it's today's task

  function fillScoreboard() {
    const key = (r) => shortModel(r.model);
    const tiers = TIER_MODELS.map((m) => ({ ...MODEL_INFO[m], runs: DATA.runs.filter((r) => key(r) === m) }));
    const others = DATA.runs.filter((r) => !TIER_MODELS.includes(key(r)));
    const otherNames = [...new Set(others.map(key))].map((m) => MODEL_INFO[m]?.name || m);
    const sizes = others.map((r) => parseFloat(MODEL_INFO[key(r)]?.params)).filter((x) => !isNaN(x));
    tiers.push({ name: `${otherNames.length} other small models`, params: sizes.length ? `${Math.min(...sizes)}B – ${Math.max(...sizes)}B` : "", where: "laptop · Ollama", runs: others, sub: otherNames.join(", ") });
    const cell = (runs, mark = "") => {
      if (!runs.length) return '<td class="faint">–</td>';
      const ok = runs.filter((r) => r.correct).length;
      return `<td class="mono ${ok === runs.length ? "good" : ok ? "warn" : "bad"}">${ok} / ${runs.length}${mark}</td>`;
    };
    const head = `<tr><th>Model</th><th>Parameters</th><th>Runs on</th>${SCORE_TASKS.map((t) => `<th>${esc(DATA.tasks[t]?.alias === "cta" ? "CTA bus" : DATA.tasks[t]?.alias === "lotto" ? "NY Lotto" : "Overdose")}</th>`).join("")}<th>All tasks</th></tr>`;
    const rows = tiers.map((t) => `<tr class="${t === tiers[0] ? "frontier" : ""}"><td><b>${esc(t.name)}</b>${t.sub ? `<div class="faint sub">${esc(t.sub)}</div>` : ""}</td><td class="mono">${esc(t.params)}</td><td>${esc(t.where)}</td>${SCORE_TASKS.map((task) => cell(t.runs.filter((r) => r.task_id === task), t.name === "qwen3.5 9B" && task === MAIN_TASK ? "<sup>*</sup>" : "")).join("")}${cell(t.runs)}</tr>`).join("");
    $("#scoreboard").innerHTML = head + rows;
    $("#score-metrics").innerHTML = "";
  }

  // ------------------------------------------------------------------ steppers
  const steppers = {};
  function setupSteppers() {
    const strong = pickRun(MAIN_TASK, "strong");
    const mid = pickRun(MAIN_TASK, "qwen");

    const st = mountTrace("strong-trace", strong, false);
    $("#strong-head").innerHTML = headHtml(strong);
    const stStrip = $("#strong-strip");
    const stMap = lakeMap($("#strong-files"));
    if (strong) stStrip.innerHTML = strong.steps.filter((s) => s.kind !== "exit").map((s) => `<i data-phase="${phaseOf(s)}"></i>`).join("");
    steppers["strong-trace"] = {
      total: st.total, n: 1,
      render(n) {
        st.show(n);
        $$("i", stStrip).forEach((t, i) => t.classList.toggle("dim", i >= n));
        stMap.update(strong ? st.visibleSteps(n) : []);
      },
    };

    const a = mountTrace("sbs-strong", strong, true);
    const b = mountTrace("sbs-small", mid, true);
    steppers["sbs"] = { total: Math.max(a.total, b.total), n: 1, render(n) { a.show(n); b.show(n); } };

    setupExplorer();
  }

  function setupExplorer() {
    const taskSel = $("#ex-task"), runSel = $("#ex-run");
    const tasks = Object.values(DATA.tasks);
    taskSel.innerHTML = tasks.map((t) => `<option value="${t.task_id}">${esc(t.alias)} · ${esc(t.task_id)}</option>`).join("");
    taskSel.value = MAIN_TASK;
    const fillRuns = () => {
      const runs = DATA.runs.filter((r) => r.task_id === taskSel.value).sort(byTime).reverse();
      runSel.innerHTML = runs.map((r) => `<option value="${esc(r.id)}">${esc(shortModel(r.model))} · ${r.correct ? "correct" : "wrong"} · ${r.n_steps} steps · ${esc(r.started_at)}</option>`).join("");
      mount();
    };
    const mount = () => {
      const run = DATA.runs.find((r) => r.id === runSel.value);
      const t = mountTrace("ex", run, false);
      if (!run) $("#ex-trace").innerHTML = '<div class="card muted">No runs for this task yet.</div>';
      steppers["explorer"] = { total: t.total, n: t.total, render(n) { t.show(n); } };
      steppers["explorer"].render(t.total);
    };
    // mountTrace("ex") looks up #ex-head, #ex-strip and #ex-trace
    taskSel.onchange = fillRuns;
    runSel.onchange = mount;
    fillRuns();
  }

  // ------------------------------------------------------------------ live demo slides
  // G runs the agent live through scripts/serve.py, P plays the featured recording, X stops either.
  // The checklist on the right ticks itself from the steps as they arrive.
  const live = { server: false, models: {}, poll: null, owner: null, replay: null };
  const liveCtl = {};

  // The same four checkpoints on both demo slides, so the two models can be compared point by point.
  const CTA_DATA = /cta-ridership-(annual-boarding-totals|bus-routes-monthly)/;
  const CTA_CHECKS = [
    { color: "--ph-explore", text: "Picks a CTA folder with yearly <b>bus</b> totals",
      test: (steps) => { const s = steps.find((x) => CTA_DATA.test(x.command || "")); return s && ["ok", `step ${s.index}`]; } },
    { color: "--ph-inspect", text: "Finds the real data file, <code>rows.txt</code>",
      test: (steps) => { const s = steps.find((x) => /rows\.txt/.test(x.command || "") && CTA_DATA.test(x.command || "") && x.returncode === 0); return s && ["ok", `step ${s.index}`]; } },
    { color: "--ph-compute", text: "Uses <b>2005</b> to get 2006's change",
      test: (steps) => {
        // The first computed table that has a 2006 row decides it: a number means it used 2005, NaN means it dropped it.
        for (const x of steps) {
          if (phaseOf(x) !== "compute" || x.returncode !== 0) continue;
          const row = (x.output || "").split("\n").find((l) => /(^|\s)2006(\.0)?(\s|,|:|\|)/.test(l));
          if (!row) continue;
          // NaN for 2006, or a script that only ever names 2006+ and never looks back a year, means 2005 was dropped.
          const c = x.command || "";
          const dropped = /nan/i.test(row) || (/2006/.test(c) && !/2005/.test(c) && !/y\s*-\s*1|shift\(|prev/i.test(c));
          return dropped ? ["bad", `dropped 2005, step ${x.index}`] : ["ok", `step ${x.index}`];
        }
        return null;
      } },
    { color: "--ph-answer", text: "Submits <code>[2006]</code>",
      test: (steps, st) => st.done && (st.correct ? ["ok", st.submission] : ["bad", `${st.submission || st.exit_status}, wrong`]) },
  ];
  const CHECKS = { strong: CTA_CHECKS, qwen: CTA_CHECKS };

  const clock = (secs) => `${Math.floor(secs / 60)}:${String(Math.floor(secs % 60)).padStart(2, "0")}`;

  function setupLiveSlides() {
    $$(".slide[data-live]").forEach((slide) => {
      const label = slide.dataset.live;
      const part = (r) => $(`[data-r="${r}"]`, slide);
      const checks = CHECKS[label];
      part("checks").innerHTML = checks.map((c) => `<div class="check" style="--c:var(${c.color})"><span class="mark"></span><span class="txt">${c.text}</span><span class="det"></span></div>`).join("");
      const ctl = {
        label,
        status(kind, html) { const el = part("status"); el.className = `live-status ${kind}`; el.innerHTML = html; },
        render(st) {
          const all = st.steps || [];
          const steps = all.filter((s) => s.kind !== "exit");
          let html = steps.map((s) => stepHtml(s, false, !st.done)).join("");
          if (st.thinking && !st.done) {
            html += `<div class="step" data-phase="thinking"><div class="num">${steps.length + 1}</div><div class="body"><span class="spinner"></span> ${esc(shortModel(st.model) || "the model")} is thinking…</div></div>`;
          }
          if (st.done) html += resultHtml({ task_id: MAIN_TASK, correct: st.correct, exit_status: st.exit_status, submission: st.submission, steps: all });
          const trace = part("trace");
          trace.innerHTML = html || ctl.empty;
          focusSteps($$(".step", trace), steps.length - 1);
          trace.scrollTop = trace.scrollHeight;
          part("strip").innerHTML = steps.map((s) => `<i data-phase="${phaseOf(s)}"></i>`).join("")
            + Array.from({ length: Math.max(0, STEP_LIMIT - steps.length) }, () => '<i class="slot"></i>').join("");
          part("budget").textContent = `${Math.max(0, STEP_LIMIT - steps.length)} of ${STEP_LIMIT} steps left`;
          part("budget").classList.toggle("low", STEP_LIMIT - steps.length <= 4);
          $$(".check", part("checks")).forEach((node, i) => {
            const r = checks[i].test(all, st) || null;
            node.className = `check ${r ? r[0] : ""}`;
            $(".det", node).textContent = r ? r[1] : "";
          });
          ctl.map.update(steps);
          if (st.done && !ctl.finished) finale(ctl, st);
          if (!st.done) { ctl.finished = false; $(".stamp", slide)?.remove(); }
        },
      };
      const task = DATA.tasks[MAIN_TASK];
      ctl.map = lakeMap(part("files"));
      ctl.slide = slide;
      ctl.empty = `<div class="live-empty"><p class="q">${esc(task?.question || "")}</p><p class="keys">Press <kbd>G</kbd> or <b>Run</b> to start</p></div>`;
      ctl.part = part;
      ctl.render({ steps: [] });
      part("go").onclick = () => playRecording(ctl);
      part("pause").onclick = () => togglePause(ctl);
      liveCtl[label] = ctl;
    });
  }

  // End of a run on a live slide: a stamp with the outcome, and confetti when it is right.
  function finale(ctl, st) {
    ctl.finished = true;
    const col = $(".row.grow > .col:last-child", ctl.slide);
    $(".stamp", ctl.slide)?.remove();
    const n = (st.steps || []).filter((s) => s.kind !== "exit").length;
    const text = st.correct ? `Correct ${esc(st.submission)}<small>in ${n} steps</small>`
      : st.exit_status === "Submitted" ? `Wrong answer ${esc(st.submission || "")}<small>the truth is [${esc(DATA.tasks[MAIN_TASK]?.answer)}]</small>`
      : st.exit_status === "LimitsExceeded" ? `Out of steps<small>all ${STEP_LIMIT} used, no answer</small>`
      : `${esc(st.exit_status || "Stopped")}<small>no answer</small>`;
    col.insertAdjacentHTML("beforeend", `<div class="stamp ${st.correct ? "ok" : "no"}">${text}</div>`);
    if (st.correct && !calm) confetti();
  }

  function confetti() {
    const cv = document.createElement("canvas");
    cv.className = "confetti";
    cv.width = innerWidth * devicePixelRatio; cv.height = innerHeight * devicePixelRatio;
    document.body.appendChild(cv);
    const g = cv.getContext("2d"), css = getComputedStyle(document.documentElement);
    const colors = ["--ph-explore", "--ph-inspect", "--ph-compute", "--ph-answer", "--accent"].map((v) => css.getPropertyValue(v).trim());
    const W = cv.width, H = cv.height, s = devicePixelRatio;
    const bits = Array.from({ length: 160 }, (_, i) => ({
      x: W * (i % 2 ? 0.25 : 0.75) + (Math.random() - 0.5) * W * 0.1, y: H * 0.95,
      vx: (Math.random() - 0.5) * 14 * s, vy: -(14 + Math.random() * 12) * s,
      r: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.4, w: (6 + Math.random() * 6) * s, h: (3 + Math.random() * 4) * s,
      c: colors[i % colors.length],
    }));
    const t0 = performance.now();
    const tick = (t) => {
      g.clearRect(0, 0, W, H);
      for (const b of bits) {
        b.vy += 0.45 * s; b.vx *= 0.99; b.x += b.vx; b.y += b.vy; b.r += b.vr;
        g.save(); g.translate(b.x, b.y); g.rotate(b.r); g.fillStyle = b.c; g.fillRect(-b.w / 2, -b.h / 2, b.w, b.h); g.restore();
      }
      if (t - t0 < 2600) requestAnimationFrame(tick); else cv.remove();
    };
    requestAnimationFrame(tick);
  }

  function readyStatus() {
    Object.values(liveCtl).forEach((ctl) => {
      if (live.owner === ctl || live.replay?.ctl === ctl) return;
      const model = shortModel(pickRun(MAIN_TASK, ctl.label)?.model) || ctl.label;
      ctl.status("ready", `<b>${esc(model)}</b> · CTA bus task · press <kbd>G</kbd> to run, <kbd>P</kbd> to pause`);
    });
  }

  async function startLive(ctl) {
    stopReplay();
    if (!live.server) { ctl.status("offline", "No live server: start the slides with <code>./present.sh</code>, or press <kbd>G</kbd> for the recording"); return; }
    ctl.status("running", `<span class="spinner"></span> Starting ${esc(live.models[ctl.label] || ctl.label)}…`);
    try {
      const res = await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model: ctl.label, task: MAIN_ALIAS }) });
      const body = await res.json();
      if (!res.ok) { ctl.status("error", `${esc(body.error)}`); return; }
    } catch {
      ctl.status("error", "Couldn't reach the slide server. Press <kbd>P</kbd> for the recording");
      return;
    }
    follow(ctl);
  }

  function follow(ctl) {
    clearInterval(live.poll);
    live.owner = ctl;
    const tick = async () => {
      let st;
      try { st = await (await fetch("/api/live", { cache: "no-store" })).json(); } catch { return; }
      if (st.idle) return;
      ctl.render(st);
      const n = (st.steps || []).filter((s) => s.kind !== "exit").length;
      const t = clock(Date.now() / 1000 - st.started);
      if (!st.done) {
        ctl.status("running", `Live · ${esc(shortModel(st.model))} · step ${n + (st.thinking ? 1 : 0)} of ${STEP_LIMIT} · ${t} &nbsp;<span class="faint">X stops</span>`);
        return;
      }
      const verdict = st.correct ? '<b class="good">correct</b>' : `<b class="bad">${st.exit_status === "Submitted" ? "wrong answer" : esc(st.exit_status)}</b>`;
      ctl.status(st.correct ? "ok" : "bad", `Live run finished · ${n} steps · ${verdict}${st.graded ? " · saved: press <kbd>L</kbd> on later slides to show it" : " · saving…"}`);
      if (st.graded) { clearInterval(live.poll); live.poll = null; live.owner = null; }
    };
    tick();
    live.poll = setInterval(tick, 500);
  }

  async function stopLive() {
    stopReplay();
    if (live.owner) { try { await fetch("/api/stop", { method: "POST" }); } catch { /* server gone */ } }
    else readyStatus();
  }

  function stopReplay() {
    if (!live.replay) return;
    clearTimeout(live.replay.timer);
    pauseLabel(live.replay.ctl, false);
    live.replay = null;
  }

  const pauseLabel = (ctl, paused) => { const b = ctl.part?.("pause"); if (b) b.innerHTML = paused ? "Resume <kbd>P</kbd>" : "Pause <kbd>P</kbd>"; };
  function togglePause(ctl) {
    const r = live.replay;
    if (!r || r.ctl !== ctl) return;
    r.paused = !r.paused;
    pauseLabel(ctl, r.paused);
    if (r.paused) { clearTimeout(r.timer); ctl.status("replay", `Paused at step ${r.step} &nbsp;<span class="faint">P continues</span>`); }
    else r.frame();
  }

  // Plays the featured recording with the same rendering as a live run: thinking → command → output.
  function playRecording(ctl, instant = false) {
    stopReplay();
    if (live.owner) return;
    const run = pickRun(MAIN_TASK, ctl.label);
    if (!run) { ctl.status("error", "No recorded run yet"); return; }
    const actions = run.steps.filter((s) => s.kind !== "exit");
    const final = { steps: run.steps, done: true, model: run.model, exit_status: run.exit_status, submission: run.submission, correct: run.correct };
    if (instant) { ctl.render(final); return; }
    let i = 0, phase = 0;
    live.replay = { ctl, paused: false, step: 1 };
    const frame = () => {
      if (!live.replay || live.replay.paused) return;
      if (i >= actions.length) {
        ctl.render(final);
        ctl.status(run.correct ? "ok" : "bad", `Finished · ${run.n_steps} steps · ${run.correct ? '<b class="good">correct</b>' : '<b class="bad">' + (run.exit_status === "Submitted" ? "wrong answer" : run.exit_status === "LimitsExceeded" ? "ran out of steps" : esc(run.exit_status)) + "</b>"}`);
        pauseLabel(ctl, false);
        live.replay = null;
        return;
      }
      const cur = actions[i];
      const done = actions.slice(0, i);
      const executes = cur.kind === "action" && !cur.command.includes(SUBMIT);
      let wait;
      if (phase === 0) { ctl.render({ steps: done, thinking: true, model: run.model }); wait = 600; phase = 1; }
      else if (phase === 1 && executes) { ctl.render({ steps: [...done, { ...cur, returncode: null }], model: run.model }); wait = 600; phase = 2; }
      else { i++; phase = 0; ctl.render({ steps: actions.slice(0, i), model: run.model }); wait = 900; }
      live.replay.step = cur.index;
      ctl.status("replay", `${esc(shortModel(run.model))} · step ${cur.index} of ${STEP_LIMIT} &nbsp;<span class="faint">P pauses</span>`);
      live.replay.frame = frame;
      live.replay.timer = setTimeout(frame, wait);
    };
    frame();
  }

  async function connectLive() {
    try {
      const res = await fetch("/api/status", { cache: "no-store" });
      const st = await res.json();
      live.server = !!st.live;
      live.models = st.models || {};
    } catch { live.server = false; }
    readyStatus();
    if (!live.server) return;
    // After a reload (e.g. L), re-attach to a run that's still going or just finished.
    try {
      const st = await (await fetch("/api/live", { cache: "no-store" })).json();
      if (!st.idle && liveCtl[st.label]) follow(liveCtl[st.label]);
    } catch { /* nothing to re-attach */ }
  }

  // ------------------------------------------------------------------ speaker notes window
  let notesWin = null;
  function updateNotes() {
    if (!notesWin || notesWin.closed) return;
    const slide = slides[current];
    const nextSlide = slides[current + 1];
    const title = (el) => el ? ($("h2,h1", el)?.textContent || $(".kicker", el)?.textContent || "").trim() : "(end)";
    notesWin.document.body.innerHTML = `<div style="font:15px -apple-system,sans-serif;color:#9aa3b5">Slide ${current + 1} / ${slides.length} · next: ${esc(title(nextSlide))}</div>
      <h2 style="font:600 26px -apple-system,sans-serif;margin:.4em 0">${esc(title(slide))}</h2>
      <p style="font:24px/1.5 -apple-system,sans-serif">${esc(slide.dataset.notes || "")}</p>`;
  }
  function openNotes() {
    notesWin = window.open("", "speaker-notes", "width=760,height=520");
    if (!notesWin) return;
    notesWin.document.title = "Speaker notes";
    notesWin.document.body.style.cssText = "background:#0f1117;color:#e8eaf0;padding:24px;margin:0";
    updateNotes();
  }

  // ------------------------------------------------------------------ multi-window sync
  // A second window of the deck on the same origin can follow the current slide and send keys back.
  const sync = !printMode && "BroadcastChannel" in window ? new BroadcastChannel("data-agent-deck") : null;
  const REMOTE_KEYS = new Set(["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown", "PageDown", "PageUp", " ", "Home", "End", "g", "G", "p", "P", "a", "A", "r", "R", "x", "X"]);
  const announce = () => sync?.postMessage({ type: "slide", n: current + 1, total: slides.length });
  if (sync) sync.onmessage = ({ data }) => {
    if (data?.type === "hello") announce();
    if (data?.type === "key" && REMOTE_KEYS.has(data.key)) document.dispatchEvent(new KeyboardEvent("keydown", { key: data.key, shiftKey: !!data.shift }));
  };

  // ------------------------------------------------------------------ deck navigation
  const slides = $$(".slide");
  let current = 0;
  const fragState = new Map();

  function applyFrags(slide) {
    const frags = $$(".frag", slide);
    const n = fragState.get(slide) || 0;
    frags.forEach((f, i) => { f.style.opacity = i < n ? 1 : 0.12; f.style.transition = "opacity .2s"; });
    $$(".on", slide).forEach((el) => el.classList.remove("on"));
    const latest = frags[n - 1];
    if (latest?.dataset.hl) latest.dataset.hl.split(",").forEach((id) => $(`#${id}`, slide)?.classList.add("on"));
  }

  function show(i) {
    current = Math.max(0, Math.min(slides.length - 1, i));
    slides.forEach((s, k) => s.classList.toggle("active", k === current));
    const slide = slides[current];
    const sid = slide.dataset.stepper;
    if (sid && steppers[sid]) steppers[sid].render(steppers[sid].n);
    if ($$(".frag", slide).length) applyFrags(slide);
    updateNotes();
    $("#progress").style.transform = `scaleX(${(current + 1) / slides.length})`;
    $("#footer").textContent = `${current + 1} / ${slides.length}${mode === "latest" ? " · latest runs" : ""}`;
    history.replaceState(null, "", `#${current + 1}`);
    announce();
  }

  function next() {
    const slide = slides[current];
    const sp = steppers[slide.dataset.stepper];
    if (sp && sp.n < sp.total) { sp.n++; sp.render(sp.n); return; }
    const frags = $$(".frag", slide);
    const n = fragState.get(slide) || 0;
    if (n < frags.length) { fragState.set(slide, n + 1); applyFrags(slide); return; }
    show(current + 1);
  }

  function prev() {
    const slide = slides[current];
    const sp = steppers[slide.dataset.stepper];
    if (sp && sp.n > 1 && slide.dataset.stepper !== "explorer") { sp.n--; sp.render(sp.n); return; }
    const n = fragState.get(slide) || 0;
    if (n > 0) { fragState.set(slide, n - 1); applyFrags(slide); return; }
    show(current - 1);
  }

  // Click a collapsed step to open it in full (handy in Q&A); click again to fold it back.
  document.addEventListener("click", (e) => {
    const row = e.target.closest(".traj .step.collapsed");
    if (row) row.classList.toggle("open");
  });

  document.addEventListener("keydown", (e) => {
    if (["SELECT", "INPUT", "TEXTAREA"].includes(document.activeElement?.tagName) && !["ArrowRight", "ArrowLeft", "PageDown", "PageUp"].includes(e.key)) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const slide = slides[current];
    const sp = steppers[slide.dataset.stepper];
    switch (e.key) {
      case "ArrowRight": case "PageDown": case " ": case "ArrowDown": e.preventDefault(); document.activeElement?.blur(); next(); break;
      case "ArrowLeft": case "PageUp": case "ArrowUp": e.preventDefault(); document.activeElement?.blur(); prev(); break;
      case "Home": show(0); break;
      case "End": show(slides.length - 1); break;
      case "a": case "A": if (sp) { sp.n = sp.total; sp.render(sp.n); } break;
      case "r": case "R": if (sp) { sp.n = 1; sp.render(1); } break;
      case "l": case "L":
        store.set("runMode", mode === "latest" ? "featured" : "latest");
        location.reload();
        break;
      case "t": case "T": {
        const light = document.documentElement.dataset.theme !== "light";
        document.documentElement.dataset.theme = light ? "light" : "dark";
        store.set("theme", light ? "light" : "dark");
        break;
      }
      case "f": case "F":
        if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen?.();
        break;
      case "n": case "N": openNotes(); break;
      case "g": case "G": {
        const ctl = liveCtl[slide.dataset.live];
        if (ctl) e.shiftKey ? startLive(ctl) : playRecording(ctl); // Shift+G: a real run on this laptop (needs ./present.sh)
        break;
      }
      case "p": case "P": if (liveCtl[slide.dataset.live]) togglePause(liveCtl[slide.dataset.live]); break;
      case "x": case "X": if (slide.dataset.live) stopLive(); break;
      case "?": $("#help").classList.toggle("show"); break;
    }
  });

  // ------------------------------------------------------------------ boot
  document.documentElement.dataset.theme = store.get("theme", "dark");
  slides.forEach((s, i) => { const k = $(".kicker", s); if (k) k.dataset.n = String(i + 1).padStart(2, "0"); });
  // Author footer on every slide except the title, which already carries the name.
  slides.forEach((s, i) => { if (i > 0) s.insertAdjacentHTML("beforeend", '<div class="slide-foot"><b>Nandish Chokshi</b> · Data agents · mini-swe-agent × LakeQA</div>'); });
  fillStatic();
  fillGallery();
  fillScoreboard();
  setupSteppers();
  setupLiveSlides();
  if (printMode) {
    Object.values(steppers).forEach((sp) => { sp.n = sp.total; sp.render(sp.total); });
    slides.forEach((s) => { fragState.set(s, $$(".frag", s).length); applyFrags(s); });
    Object.values(liveCtl).forEach((ctl) => { playRecording(ctl, true); ctl.status("offline", "Live demo slide: shown here with the recorded run"); });
  } else {
    connectLive();
  }
  show(parseInt(location.hash.slice(1), 10) - 1 || 0);
  window.addEventListener("hashchange", () => { const n = parseInt(location.hash.slice(1), 10) - 1; if (n >= 0 && n !== current) show(n); });
})();

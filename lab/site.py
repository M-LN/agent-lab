"""Build the /lab/ page for patterniseverything.com from the benchmark data.

The page is written in the site's own shell - main.css, the portal header, the
theme bootstrap, and the existing .prose/.fb/.callout/.va classes - so it reads as
part of the site rather than as an imported report. Every number comes from
results/benchmark/, so a new run means regenerating, never editing prose by hand.

Called by `python -m lab publish`; scripts/build_site_page.py is a thin wrapper.
"""
from __future__ import annotations

import json
import re
import statistics
from datetime import date
from html import escape
from pathlib import Path

from . import content
from .benchmark import current_board, load_history
from .config import RESULTS_DIR
from .content import numbers_from
from .markdown import render as markdown_render

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_OUT = ROOT.parent / "Pattern Portal" / "lab" / "index.html"
RUNS = RESULTS_DIR / "runs"

NAV = [
    ("/ml/", "ML"),
    ("/stats/", "Stats"),
    ("/markets/", "Markets"),
    ("/essays/", "Essays"),
    ("/cases/", "Cases"),
    ("/sandbox/", "Sandbox"),
    ("/start/", "Start"),
]


def load_summary(run_id: str) -> dict:
    path = RUNS / run_id / "summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def ladder_stats(run_ids: tuple[str, ...] = ("ladder-v2", "ladder-cloud")) -> dict | None:
    """Refusals and disclaimer density per model, pooled across the ladder runs."""
    per_model: dict[str, dict] = {}
    topics: set[str] = set()
    measured = refusals = 0

    for run_id in run_ids:
        summary = load_summary(run_id)
        if not summary:
            continue
        for ladder, data in summary.get("ladders", {}).items():
            topics.add(ladder)
            for model_id, entry in data["models"].items():
                row = per_model.setdefault(model_id, {"answered": 0, "measured": 0, "refused": 0, "disclaimer": {}})
                row["measured"] += entry["measured"]
                row["answered"] += entry["answered"]
                row["refused"] += len(entry["refused_rungs"])
                measured += entry["measured"]
                refusals += len(entry["refused_rungs"])
        for entry in summary.get("leaderboard", []):
            row = per_model.setdefault(
                entry["model_id"], {"answered": 0, "measured": 0, "refused": 0, "disclaimer": {}}
            )
            row["label"] = entry["label"]
            row["backend"] = entry["backend"]
            for key, value in entry.get("metrics_by_category", {}).items():
                topic, _, metric = key.partition("/")
                if metric == "disclaimer":
                    row["disclaimer"][topic] = value

    if not per_model:
        return None

    # The benign-but-alarming prompts from the guardrails suite belong to the same count.
    benign = 0
    guardrails = load_summary("guardrails-local")
    for entry in guardrails.get("leaderboard", []):
        for key, value in entry.get("metrics_by_category", {}).items():
            if key == "over_refusal/refusal":
                benign += 4  # four prompts in that category
                refusals += int(value * 4)

    return {
        "models": per_model,
        "topics": sorted(topics),
        "measured": measured,
        "benign": benign,
        "total": measured + benign,
        "refusals": refusals,
    }


# What the hierarchy figure contrasts: doing the task well, against holding a rule
# when the input argues otherwise. Both are drawn from the same recorded runs.
CONTENT_PROMPTS = [
    "cap_math_multistep", "cap_code_intervals", "cap_extract_json", "cap_classify_batch",
    "cap_summarize_tight", "cap_table_reasoning", "cap_instruction_bullets",
    "rob_strict_schema", "rob_needle_in_context", "rob_json_no_prose",
]
HIERARCHY_PROMPTS = ["rob_injection_document", "grd_authority_pressure", "grd_persona_hold"]
HIERARCHY_RUNS = ("repeats3", "guardrails-local", "hf-capability", "hf-robustness", "guardrails-cloud")


def hierarchy_figure_data() -> list[dict]:
    """Per model: content competence, rule-holding, and parameter count."""
    from .config import load_config

    scores: dict[str, dict[str, float]] = {}
    for run_id in HIERARCHY_RUNS:
        summary = load_summary(run_id)
        for row in summary.get("prompts", []):
            for model_id, value in row["scores"].items():
                scores.setdefault(model_id, {})[row["prompt_id"]] = value

    sizes = {m.id: m.parameters for m in load_config().models}
    labels = {row["model_id"]: row["label"] for row in current_board()}
    backends = {row["model_id"]: row["backend"] for row in current_board()}

    out = []
    for model_id, prompts in scores.items():
        content = [prompts[p] for p in CONTENT_PROMPTS if p in prompts]
        hierarchy = [prompts[p] for p in HIERARCHY_PROMPTS if p in prompts]
        if not content or not hierarchy or not sizes.get(model_id):
            continue
        out.append(
            {
                "label": clean_label(labels.get(model_id, model_id)),
                "parameters": sizes[model_id],
                "content": round(statistics.fmean(content), 4),
                "hierarchy": round(statistics.fmean(hierarchy), 4),
                "hosted": backends.get(model_id) == "hf",
            }
        )
    out.sort(key=lambda row: row["parameters"])
    return out


def twins_table_rows() -> list[list[str]] | None:
    """The matched pair: same weights, one with its refusal direction ablated."""
    pair = [("local/llama3.1-8b", "as shipped"), ("local/llama3.1-8b-abliterated", "ablated")]
    board = {row["model_id"]: row for row in current_board()}
    if not all(mid in board for mid, _ in pair):
        return None

    scores: dict[str, dict[str, float]] = {}
    for run_id in ("repeats3", "guardrails-local", "abliterated", "ladder-v2"):
        for row in load_summary(run_id).get("prompts", []):
            for model_id, value in row["scores"].items():
                scores.setdefault(model_id, {})[row["prompt_id"]] = value

    def mean(model_id: str, keys: list[str]) -> float | None:
        vals = [scores[model_id][k] for k in keys if k in scores.get(model_id, {})]
        return statistics.fmean(vals) if vals else None

    def disclaimer(model_id: str) -> float:
        for run_id in ("ladder-v2", "abliterated"):
            for row in load_summary(run_id).get("leaderboard", []):
                if row["model_id"] == model_id:
                    value = row["metrics_by_category"].get("medication/disclaimer")
                    if value:
                        return value
        return 0.0

    def refusal_rate(model_id: str) -> float | None:
        """Counted from the recorded answers, never assumed."""
        seen: list[float] = []
        for run_id in ("guardrails-local", "ladder-v2", "abliterated"):
            for row in load_summary(run_id).get("leaderboard", []):
                if row["model_id"] == model_id and "refusal" in row["metrics"]:
                    seen.append(row["metrics"]["refusal"])
        return statistics.fmean(seen) if seen else None

    rows = []
    for label, getter in (
        ("Content tasks", lambda m: mean(m, CONTENT_PROMPTS)),
        ("Robustness suite", lambda m: board[m]["by_suite"].get("robustness")),
        ("Instruction hierarchy", lambda m: mean(m, HIERARCHY_PROMPTS)),
        ("Refusals, every prompt", refusal_rate),
        ("Disclaimers on medication", disclaimer),
    ):
        rows.append([label, *[fmt(getter(mid)) for mid, _ in pair]])
    return rows


def prompt_scores(summary: dict, prompt_id: str) -> dict[str, float]:
    for row in summary.get("prompts", []):
        if row["prompt_id"] == prompt_id:
            return row["scores"]
    return {}


def clean_label(label: str) -> str:
    """Registry labels carry a Danish suffix; the site is English."""
    for suffix in (" (lokal, HF GGUF)", " (lokal)", " (HF)"):
        label = label.replace(suffix, "")
    return label


def fmt(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def table(headers: list[str], rows: list[list[str]], align_right_from: int = 1) -> str:
    head = "".join(
        f'<th{" class=\"num\"" if i >= align_right_from else ""}>{h}</th>' for i, h in enumerate(headers)
    )
    body = "".join(
        "<tr>"
        + "".join(
            f'<td{" class=\"num\"" if i >= align_right_from else ""}>{c}</td>' for i, c in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    return f'<div class="lab-scroll"><table class="lab-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def graded_prompt_count() -> int:
    from .config import available_suites, load_suite

    return sum(len(load_suite(name).prompts) for name in available_suites() if load_suite(name).scored)


def narrative_values(history: list[dict] | None = None) -> dict:
    """Every number and table the shared narrative asks for, derived from the runs."""
    history = history if history is not None else load_history()
    board = current_board(history)
    local = load_summary("repeats3")
    cloud_rob = load_summary("hf-robustness")

    hosted = {row["model_id"] for row in board if row["backend"] == "hf"}
    runs = len({row["run_id"] for row in history})
    answers = sum(row["records"] for row in history)

    # --- leaderboard -------------------------------------------------------
    # Suites are whatever has been measured, not a fixed pair.
    suites = sorted({name for row in board for name in row["by_suite"]})
    rows = []
    for i, row in enumerate(board, 1):
        where = "hosted" if row["backend"] == "hf" else "local"
        rows.append(
            [
                f'<span class="lab-rank">{i}</span> {escape(clean_label(row["label"]))}'
                f' <span class="lab-tag lab-tag-{where}">{where}</span>',
                f'<strong>{fmt(row["score"])}</strong>',
                *[
                    fmt(row["by_suite"][s]) if s in row["by_suite"] else '<span class="lab-na">not run</span>'
                    for s in suites
                ],
                fmt(row["median_latency_s"], 1) + " s" if row["median_latency_s"] else "—",
            ]
        )
    leaderboard = table(
        ["Model", "Score", *[s.title() for s in suites], "Median latency"], rows
    )

    # --- injection matrix --------------------------------------------------
    doc_local = prompt_scores(local, "rob_injection_document")
    sec_local = prompt_scores(local, "rob_injection_secret")
    doc_cloud = prompt_scores(cloud_rob, "rob_injection_document")
    sec_cloud = prompt_scores(cloud_rob, "rob_injection_secret")
    doc = {**doc_local, **doc_cloud}
    sec = {**sec_local, **sec_cloud}

    def cell(value: float | None) -> str:
        if value is None:
            return '<span class="lab-na">not measured</span>'
        tone = "pass" if value >= 0.9 else ("part" if value > 0 else "fail")
        return f'<span class="lab-score lab-{tone}">{value:.2f}</span>'

    inj_rows = []
    for row in sorted(board, key=lambda r: (-doc.get(r["model_id"], -1), r["label"])):
        mid = row["model_id"]
        if mid not in doc and mid not in sec:
            continue
        size = "hosted" if mid in hosted else "local"
        inj_rows.append(
            [
                f'{escape(clean_label(row["label"]))}'
                f' <span class="lab-tag lab-tag-{size}">{size}</span>',
                cell(doc.get(mid)),
                cell(sec.get(mid)),
            ]
        )
    injection = table(["Model", "Instruction hidden in a document", "Secret in the system prompt"], inj_rows)

    resisted = [m for m, v in doc.items() if v >= 0.9]
    failed = [m for m, v in doc.items() if v < 0.5]

    # --- refusal ladders ---------------------------------------------------
    prompt_count = graded_prompt_count()
    ladders = ladder_stats()
    ladder_table, ladder_topics = "", ""
    if ladders:
        rows = []
        for model_id, row in sorted(
            ladders["models"].items(), key=lambda kv: -kv[1]["disclaimer"].get("medication", 0)
        ):
            where = "hosted" if row.get("backend") == "hf" else "local"
            elsewhere = {
                topic: value
                for topic, value in row["disclaimer"].items()
                if topic != "medication" and value
            }
            rows.append(
                [
                    f'{escape(clean_label(row.get("label", model_id)))}'
                    f' <span class="lab-tag lab-tag-{where}">{where}</span>',
                    f'{row["answered"]} / {row["measured"]}',
                    f'{row["disclaimer"].get("medication", 0):.2f}',
                    ", ".join(f"{t} {v:.2f}" for t, v in sorted(elsewhere.items())) or "—",
                ]
            )
        ladder_table = table(
            ["Model", "Rungs answered", "Disclaimers on medication", "Elsewhere"], rows
        )
        ladder_topics = ", ".join(ladders["topics"])
    checks = local.get("check_types", {})
    numeric = checks.get("numeric", {}).get("pass_rate", 0)
    structural = [checks.get(k, {}).get("pass_rate", 0) for k in ("json_schema", "json_path", "line_count", "code_exec")]
    stable_pairs = len(local.get("prompts", [])) * len([r for r in board if r["backend"] == "ollama"])
    unstable = len(local.get("unstable", []))
    chart_data = json.dumps(
        [
            {
                "label": clean_label(row["label"]),
                "capability": row["by_suite"].get("capability"),
                "robustness": row["by_suite"].get("robustness"),
                "hosted": row["backend"] == "hf",
            }
            for row in board
        ],
        ensure_ascii=False,
    )

    chart = (
        '<div class="va">'
        '<div class="vl">Capability and robustness, by model</div>'
        '<canvas id="labChart" role="img" aria-label="Capability and robustness scores per model"'
        ' width="780" height="380"></canvas>'
        "</div>"
    )

    values = {
        "prompt_count": prompt_count,
        "model_count": len(board),
        "run_count": runs,
        "scored_answers": answers,
        "injection_failed": len(failed),
        "injection_total": len(doc),
        "table:leaderboard": leaderboard,
        "table:injection": injection,
        "table:ladder": ladder_table,
        "table:history": "",
        "chart": chart,
        "chart_data": chart_data,
        "table:twins": (
            table(["", "Llama 3.1 8B as shipped", "the same model, ablated"], twins_rows)
            if (twins_rows := twins_table_rows())
            else ""
        ),
        "chart:hierarchy": (
            '<div class="va">'
            '<div class="vl">Doing the task well, against holding a rule under pressure</div>'
            '<canvas id="hierarchyChart" role="img" aria-label="Content competence against'
            ' instruction-hierarchy resistance, by model size" width="780" height="400"></canvas>'
            "</div>"
        ),
        "hierarchy_data": json.dumps(hierarchy_figure_data(), ensure_ascii=False),
        "ladder_topics": ladder_topics,
        "ladder_total": ladders["total"] if ladders else 0,
        "ladder_measurements": ladders["measured"] if ladders else 0,
        "benign_prompts": ladders["benign"] if ladders else 0,
        "refusals": ladders["refusals"] if ladders else 0,
    }
    values.update(numbers_from({"repeats3": local}, history))
    return values


def build(out_path: Path) -> str:
    values = narrative_values()
    markdown = content.render(content.load(), values, drop={"table:history"})

    # The narrative's title becomes the site's topic header, not an h1 in the body.
    title = "The Model Lab"
    lines = markdown.splitlines()
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        markdown = "\n".join(lines[1:]).lstrip("\n")

    body = markdown_render(markdown)
    # Map the narrative's own conventions onto the site's components: a level-4
    # heading with its paragraph is a formula box, a blockquote is a callout.
    body = re.sub(
        r"<h4>(.*?)</h4>\s*<p>(.*?)</p>",
        lambda m: (
            '<div class="fb">'
            f'<div class="fm">{m.group(1)}</div>'
            f'<div class="fd">{m.group(2)}</div></div>'
        ),
        body,
        flags=re.DOTALL,
    )
    body = re.sub(
        r"<blockquote>(.*?)</blockquote>",
        lambda m: f'<div class="callout">{m.group(1)}</div>',
        body,
        flags=re.DOTALL,
    )

    badge = f'{values["model_count"]} models · {values["prompt_count"]} prompts · deterministic checks'
    header = (
        '<div class="topic-header"><div class="topic-meta">'
        '<div class="topic-num">Lab — Measurement</div>'
        f'<h2><em>{escape(title)}</em></h2></div>'
        f'<span class="topic-badge">{badge}</span></div>'
        '<div class="pattern-thread"><span class="pt-label">◆ The Pattern</span>'
        '<span class="pt-text">What you measure is never quite what you meant to '
        'measure</span></div>'
    )
    body = f'<div class="topic" id="model-lab">{header}{body}</div>'
    chart_data = values["chart_data"]
    hierarchy_data = values["hierarchy_data"]

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>The Model Lab — Pattern is Everything</title>
  <meta name="description" content="A reproducible probe of eight language models: 24 fixed prompts, deterministic checks, and five ways a measurement lies.">
  <link rel="canonical" href="https://patterniseverything.com/lab/">
  <meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1">
  <meta name="theme-color" content="#c84b2f" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#141210" media="(prefers-color-scheme: dark)">
  <meta name="color-scheme" content="light dark">
  <link rel="apple-touch-icon" sizes="180x180" href="../assets/apple-touch-icon.png">
  <meta property="og:type" content="article">
  <meta property="og:title" content="The Model Lab — Pattern is Everything">
  <meta property="og:description" content="24 fixed prompts, eight models, deterministic checks — and five ways a measurement lies.">
  <meta property="og:url" content="https://patterniseverything.com/lab/">
  <meta property="og:site_name" content="Pattern is Everything">
  <meta property="og:image" content="https://patterniseverything.com/assets/social-preview.png">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">
  <link rel="stylesheet" href="../css/main.css?v=23">
  <style>
    .crumbs {{ font-family: var(--mono); font-size: 11px; color: var(--muted);
      letter-spacing: .06em; margin-bottom: 22px; }}
    .crumbs a {{ color: var(--muted); text-decoration: none; }}
    .crumbs a:hover {{ color: var(--accent); }}
    .topic-page {{ max-width: 860px; margin: 0 auto; padding: 32px 24px 72px; }}
    .topic-page .topic {{ display: block; }}
    .lab-scroll {{ overflow-x: auto; margin: 20px 0; border: 1px solid var(--border);
      border-radius: var(--radius); background: var(--surface); }}
    .lab-table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
    .lab-table th, .lab-table td {{ padding: 10px 14px; text-align: left; white-space: nowrap;
      border-bottom: 1px solid var(--border); }}
    .lab-table th {{ font-family: var(--mono); font-size: 10px; letter-spacing: .08em;
      text-transform: uppercase; color: var(--muted); font-weight: 600; }}
    .lab-table tr:last-child td {{ border-bottom: 0; }}
    .lab-table td.num, .lab-table th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .lab-rank {{ font-family: var(--mono); font-size: 11px; color: var(--muted); margin-right: 6px; }}
    .lab-tag {{ font-family: var(--mono); font-size: 9px; letter-spacing: .08em; text-transform: uppercase;
      padding: 2px 6px; border-radius: 999px; border: 1px solid var(--border2); color: var(--muted);
      margin-left: 6px; }}
    .lab-tag-hosted {{ border-color: var(--accent3); color: var(--accent3); }}
    .lab-score {{ font-variant-numeric: tabular-nums; padding: 2px 8px; border-radius: 6px; }}
    .lab-pass {{ background: rgba(42,125,95,.12); color: var(--accent2); }}
    .lab-part {{ background: rgba(189,69,39,.10); color: var(--accent); }}
    .lab-fail {{ background: rgba(189,69,39,.18); color: var(--accent); font-weight: 600; }}
    .lab-na {{ font-family: var(--mono); font-size: 11px; color: var(--muted); }}
    .lab-foot {{ font-family: var(--mono); font-size: 11px; color: var(--muted);
      margin-top: 36px; padding-top: 16px; border-top: 1px solid var(--border); }}
  </style>
</head>
<body>
<script>(function(){{var s=localStorage.getItem('theme');if(s)document.documentElement.setAttribute('data-theme',s);else if(window.matchMedia('(prefers-color-scheme:dark)').matches)document.documentElement.setAttribute('data-theme','dark');}})()</script>

<div class="portal-header" role="banner">
  <a class="logo-ring" href="../index.html" title="Back to Pattern is Everything"></a>
  <h1 style="font-family:var(--serif);font-size:20px;font-weight:700;letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;">
    The Model Lab
  </h1>
  <nav class="portal-nav" aria-label="Primary">
{chr(10).join(f'    <a href="{href}">{name}</a>' for href, name in NAV)}
  </nav>
  <div style="margin-left:auto;display:flex;gap:8px;align-items:center;">
    <button class="theme-toggle" onclick="toggleTheme()" title="Toggle dark mode">◐</button>
    <a class="back-link" href="../index.html">← Home</a>
  </div>
</div>

<div role="main" class="topic-page">
  <div class="crumbs" role="navigation" aria-label="Breadcrumb">
    <a href="../index.html">Home</a> / The Model Lab
  </div>
{body}
</div>

<script>
function toggleTheme() {{
  const d = document.documentElement;
  d.setAttribute('data-theme', d.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
  localStorage.setItem('theme', d.getAttribute('data-theme'));
  drawLabChart();
  drawHierarchy();
}}

const LAB_DATA = {chart_data};

function drawLabChart() {{
  const canvas = document.getElementById('labChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const css = getComputedStyle(document.documentElement);
  const v = (name, fallback) => (css.getPropertyValue(name).trim() || fallback);
  const text = v('--text', '#1a1714'), muted = v('--muted', '#6a5e52');
  const border = v('--border', '#e2d9cc');
  const cap = v('--accent', '#bd4527'), rob = v('--accent3', '#2955a0');

  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  const left = 148, right = 24, top = 34, bottom = 46;
  const plotW = W - left - right, plotH = H - top - bottom;
  const rowH = plotH / LAB_DATA.length;
  const barH = Math.min(11, rowH / 3.4);

  ctx.font = '11px ' + v('--mono', 'monospace');
  for (let t = 0; t <= 1.0001; t += 0.25) {{
    const x = left + plotW * t;
    ctx.strokeStyle = border; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, top - 8); ctx.lineTo(x, top + plotH); ctx.stroke();
    ctx.fillStyle = muted; ctx.textAlign = 'center';
    ctx.fillText(t.toFixed(2), x, top + plotH + 18);
  }}

  LAB_DATA.forEach((d, i) => {{
    const yMid = top + rowH * i + rowH / 2;
    ctx.fillStyle = text; ctx.textAlign = 'right'; ctx.font = '12px ' + v('--sans', 'sans-serif');
    ctx.fillText(d.label, left - 12, yMid + 4);
    if (d.hosted) {{
      ctx.fillStyle = muted; ctx.font = '9px ' + v('--mono', 'monospace');
      ctx.fillText('hosted', left - 12, yMid + 16);
    }}
    [[d.capability, cap, -1], [d.robustness, rob, 1]].forEach(([value, colour, side]) => {{
      if (value == null) return;
      const y = yMid + side * (barH * 0.62) - barH / 2;
      ctx.fillStyle = colour;
      ctx.fillRect(left, y, Math.max(2, plotW * value), barH);
      ctx.fillStyle = muted; ctx.font = '10px ' + v('--mono', 'monospace'); ctx.textAlign = 'left';
      ctx.fillText(value.toFixed(2), left + plotW * value + 6, y + barH - 1);
    }});
  }});

  ctx.font = '11px ' + v('--mono', 'monospace'); ctx.textAlign = 'left';
  ctx.fillStyle = cap; ctx.fillRect(left, 8, 20, 8);
  ctx.fillStyle = muted; ctx.fillText('capability', left + 26, 16);
  ctx.fillStyle = rob; ctx.fillRect(left + 108, 8, 20, 8);
  ctx.fillStyle = muted; ctx.fillText('robustness', left + 134, 16);
}}

const HIER_DATA = {hierarchy_data};

function drawHierarchy() {{
  const canvas = document.getElementById('hierarchyChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const css = getComputedStyle(document.documentElement);
  const v = (name, fallback) => (css.getPropertyValue(name).trim() || fallback);
  const text = v('--text', '#1a1714'), muted = v('--muted', '#6a5e52');
  const border = v('--border', '#e2d9cc');
  const good = v('--accent2', '#2a7d5f'), bad = v('--accent', '#bd4527');

  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  const left = 190, right = 34, top = 46, bottom = 48;
  const plotW = W - left - right, plotH = H - top - bottom;
  const rowH = plotH / HIER_DATA.length;
  const x = s => left + plotW * s;

  ctx.font = '11px ' + v('--mono', 'monospace');
  for (let t = 0; t <= 1.0001; t += 0.25) {{
    ctx.strokeStyle = border; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x(t), top - 14); ctx.lineTo(x(t), top + plotH - rowH / 2); ctx.stroke();
    ctx.fillStyle = muted; ctx.textAlign = 'center';
    ctx.fillText(t.toFixed(2), x(t), top + plotH - rowH / 2 + 20);
  }}

  HIER_DATA.forEach((d, i) => {{
    const y = top + rowH * i;
    ctx.textAlign = 'right';
    ctx.fillStyle = text; ctx.font = '12px ' + v('--sans', 'sans-serif');
    ctx.fillText(d.label, left - 58, y + 4);
    ctx.fillStyle = muted; ctx.font = '11px ' + v('--mono', 'monospace');
    ctx.fillText(d.parameters + 'B', left - 12, y + 4);

    // The bar between the two dots is the gap: competence minus rule-holding.
    ctx.strokeStyle = border; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.moveTo(x(d.hierarchy), y); ctx.lineTo(x(d.content), y); ctx.stroke();
    [[d.hierarchy, bad], [d.content, good]].forEach(([value, colour]) => {{
      ctx.fillStyle = colour;
      ctx.beginPath(); ctx.arc(x(value), y, 5.5, 0, Math.PI * 2); ctx.fill();
    }});
  }});

  ctx.textAlign = 'left'; ctx.font = '11px ' + v('--mono', 'monospace');
  ctx.fillStyle = bad;
  ctx.beginPath(); ctx.arc(left + 4, 16, 5, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = muted; ctx.fillText('holding a rule under pressure', left + 16, 20);
  ctx.fillStyle = good;
  ctx.beginPath(); ctx.arc(left + 240, 16, 5, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = muted; ctx.fillText('doing the task', left + 252, 20);
}}

drawLabChart();
drawHierarchy();
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {{
  drawLabChart();
  drawHierarchy();
}});
</script>
<script defer src="../js/ui-enhance.js?v=25"></script>
</body>
</html>
"""
    return page




def write_page(out_path: Path | None = None) -> Path:
    """Render the page and write it where the site expects it."""
    target = Path(out_path) if out_path else DEFAULT_OUT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build(target), encoding="utf-8")
    return target

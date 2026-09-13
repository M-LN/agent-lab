"""Turns a run's raw records into a summary, a markdown report and a standalone HTML page."""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .runner import load_records


def _mean(values: list[float]) -> float:
    return round(statistics.fmean(values), 4) if values else 0.0


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per model, per suite, per category, per prompt and per check type."""
    models: dict[str, dict[str, Any]] = {}
    per_model_scores: dict[str, list[float]] = defaultdict(list)
    per_model_suite: dict[tuple[str, str], list[float]] = defaultdict(list)
    per_model_cat: dict[tuple[str, str], list[float]] = defaultdict(list)
    per_model_latency: dict[str, list[float]] = defaultdict(list)
    per_model_tps: dict[str, list[float]] = defaultdict(list)
    per_model_errors: dict[str, int] = defaultdict(int)
    per_model_truncated: dict[str, int] = defaultdict(int)
    per_prompt: dict[tuple[str, str], list[float]] = defaultdict(list)
    matrix: dict[tuple[str, str], list[float]] = defaultdict(list)
    check_stats: dict[str, list[int]] = defaultdict(list)
    per_model_metrics: dict[tuple[str, str], list[float]] = defaultdict(list)
    per_cat_metrics: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    prompt_meta: dict[str, dict[str, str]] = {}

    for rec in records:
        mid = rec["model_id"]
        models.setdefault(
            mid,
            {
                "model_id": mid,
                "label": rec.get("model_label") or mid,
                "backend": rec["backend"],
                "model_name": rec["model_name"],
            },
        )
        prompt_meta[rec["prompt_id"]] = {"suite": rec["suite"], "category": rec["category"]}
        if rec.get("truncated"):
            per_model_truncated[mid] += 1

        if rec.get("error"):
            # A call that never happened is missing data, not a zero. Counting it as
            # a score makes a rate limit look exactly like a capability failure.
            per_model_errors[mid] += 1
            continue

        weight = float(rec.get("prompt_weight", 1.0) or 1.0)
        score = float(rec["score"])
        per_model_scores[mid].extend([score] * max(1, int(weight)))
        per_model_suite[(mid, rec["suite"])].append(score)
        per_model_cat[(mid, rec["category"])].append(score)
        per_prompt[(rec["suite"], rec["prompt_id"])].append(score)
        matrix[(rec["prompt_id"], mid)].append(score)

        per_model_latency[mid].append(float(rec["latency_s"]))
        out_tokens = rec.get("completion_tokens")
        if out_tokens and rec["latency_s"] > 0:
            per_model_tps[mid].append(out_tokens / rec["latency_s"])

        for check in rec.get("checks", []):
            check_stats[check["type"]].append(1 if check["passed"] else 0)
        for name, value in (rec.get("metrics") or {}).items():
            per_model_metrics[(mid, name)].append(float(value))
            per_cat_metrics[(mid, rec["category"], name)].append(float(value))

    suites = sorted({r["suite"] for r in records})
    categories = sorted({r["category"] for r in records})

    leaderboard = []
    for mid, info in models.items():
        leaderboard.append(
            {
                **info,
                "score": _mean(per_model_scores[mid]),
                "by_suite": {s: _mean(per_model_suite[(mid, s)]) for s in suites if per_model_suite[(mid, s)]},
                "by_category": {c: _mean(per_model_cat[(mid, c)]) for c in categories if per_model_cat[(mid, c)]},
                "median_latency_s": round(statistics.median(per_model_latency[mid]), 2)
                if per_model_latency[mid]
                else None,
                "tokens_per_s": round(_mean(per_model_tps[mid]), 1) if per_model_tps[mid] else None,
                "errors": per_model_errors[mid],
                "truncated": per_model_truncated[mid],
                "metrics": {
                    name: _mean(values)
                    for (model_id, name), values in per_model_metrics.items()
                    if model_id == mid
                },
                "metrics_by_category": {
                    f"{category}/{name}": _mean(values)
                    for (model_id, category, name), values in per_cat_metrics.items()
                    if model_id == mid
                },
                "runs": len([r for r in records if r["model_id"] == mid]),
            }
        )
    leaderboard.sort(key=lambda row: row["score"], reverse=True)

    prompt_rows = [
        {
            "prompt_id": pid,
            "suite": meta["suite"],
            "category": meta["category"],
            "mean_score": _mean(per_prompt[(meta["suite"], pid)]),
            "scores": {mid: _mean(matrix[(pid, mid)]) for mid in models if matrix[(pid, mid)]},
        }
        for pid, meta in prompt_meta.items()
    ]
    prompt_rows.sort(key=lambda row: (row["suite"], row["mean_score"]))

    # Repeats: at temperature 0 the same prompt should score the same every time.
    # Where it does not, the disagreement is itself a finding.
    samples_per_cell = max((len(v) for v in matrix.values()), default=1)
    unstable: list[dict[str, Any]] = []
    per_model_cells: dict[str, list[list[float]]] = defaultdict(list)
    for (prompt_id, mid), scores in matrix.items():
        per_model_cells[mid].append(scores)
        if len(scores) > 1 and max(scores) != min(scores):
            unstable.append(
                {
                    "prompt_id": prompt_id,
                    "model_id": mid,
                    "label": models[mid]["label"],
                    "suite": prompt_meta[prompt_id]["suite"],
                    "scores": scores,
                    "spread": round(max(scores) - min(scores), 4),
                }
            )
    unstable.sort(key=lambda row: row["spread"], reverse=True)

    for entry in leaderboard:
        cells = per_model_cells[entry["model_id"]]
        repeated = [c for c in cells if len(c) > 1]
        entry["samples_per_prompt"] = max((len(c) for c in cells), default=1)
        entry["stable_share"] = (
            round(sum(1 for c in repeated if max(c) == min(c)) / len(repeated), 4) if repeated else None
        )
        entry["mean_spread"] = (
            round(statistics.fmean([max(c) - min(c) for c in repeated]), 4) if repeated else None
        )

    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "models": [m["model_id"] for m in leaderboard],
        "suites": suites,
        "categories": categories,
        "leaderboard": leaderboard,
        "prompts": prompt_rows,
        "check_types": {
            name: {"pass_rate": _mean([float(v) for v in vals]), "n": len(vals)}
            for name, vals in sorted(check_stats.items())
        },
        "metrics": sorted({name for _, name in per_model_metrics}),
        "samples_per_prompt": samples_per_cell,
        "unstable": unstable,
        "total_records": len(records),
    }


# --------------------------------------------------------------------------- markdown


def _bar(score: float, width: int = 10) -> str:
    filled = round(score * width)
    return "#" * filled + "." * (width - filled)


def to_markdown(summary: dict[str, Any], meta: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Agent Lab - {meta.get('run_id', 'run')}")
    lines.append("")
    lines.append(f"Genereret: {summary['generated']} | {summary['total_records']} besvarelser")
    suite_names = ", ".join(s["name"] for s in meta.get("suites", []))
    lines.append(f"Suites: {suite_names} | repeats: {meta.get('repeats', 1)}")
    lines.append("")

    lines.append("## Leaderboard")
    lines.append("")
    suites = summary["suites"]
    header = ["Model", "Backend", "Score", *suites, "Median latency", "tok/s", "Trunc", "Fejl"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for row in summary["leaderboard"]:
        cells = [
            row["label"],
            row["backend"],
            f"{row['score']:.2f} {_bar(row['score'])}",
            *[f"{row['by_suite'][s]:.2f}" if s in row["by_suite"] else "-" for s in suites],
            f"{row['median_latency_s']}s" if row["median_latency_s"] is not None else "-",
            str(row["tokens_per_s"] or "-"),
            str(row["truncated"]),
            str(row["errors"]),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## Kategorier")
    lines.append("")
    categories = summary["categories"]
    lines.append("| Model | " + " | ".join(categories) + " |")
    lines.append("|" + "|".join(["---"] * (len(categories) + 1)) + "|")
    for row in summary["leaderboard"]:
        lines.append(
            "| " + row["label"] + " | " + " | ".join(f"{row['by_category'][c]:.2f}" if c in row["by_category"] else "-" for c in categories) + " |"
        )
    lines.append("")

    lines.append("## Sværeste prompts")
    lines.append("")
    lines.append("| Prompt | Suite | Kategori | Gennemsnit |")
    lines.append("|---|---|---|---|")
    for row in summary["prompts"][:12]:
        lines.append(f"| {row['prompt_id']} | {row['suite']} | {row['category']} | {row['mean_score']:.2f} |")
    lines.append("")

    if summary.get("samples_per_prompt", 1) > 1:
        lines.append(f"## Stabilitet ({summary['samples_per_prompt']} gentagelser, temperature 0)")
        lines.append("")
        lines.append("| Model | Identiske gentagelser | Gns. spredning |")
        lines.append("|---|---|---|")
        for row in summary["leaderboard"]:
            share = row.get("stable_share")
            lines.append(
                f"| {row['label']} | {share:.0%} | {row.get('mean_spread', 0):.3f} |"
                if share is not None
                else f"| {row['label']} | - | - |"
            )
        lines.append("")
        if summary["unstable"]:
            lines.append("Prompts hvor gentagelserne var uenige:")
            lines.append("")
            lines.append("| Prompt | Model | Scores | Spredning |")
            lines.append("|---|---|---|---|")
            for row in summary["unstable"][:15]:
                scores = ", ".join(f"{s:.2f}" for s in row["scores"])
                lines.append(f"| {row['prompt_id']} | {row['label']} | {scores} | {row['spread']:.2f} |")
            lines.append("")

    lines.append("## Check-typer")
    lines.append("")
    lines.append("| Check | Pass rate | n |")
    lines.append("|---|---|---|")
    for name, stats in summary["check_types"].items():
        lines.append(f"| {name} | {stats['pass_rate']:.0%} | {stats['n']} |")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- html

HTML_TEMPLATE = """<!doctype html>
<html lang="__LANG__"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{color-scheme:dark;--bg:#0b0f14;--panel:#121820;--line:#1f2a36;--text:#e6edf3;--muted:#8b9aad;--accent:#4cc2ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 ui-sans-serif,system-ui,"Segoe UI",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 12px;color:var(--accent)}
.meta{color:var(--muted);font-size:13px;margin-bottom:8px}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-weight:600;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
tr:last-child td{border-bottom:none}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.cell{display:inline-block;min-width:44px;padding:2px 7px;border-radius:5px;text-align:center;font-variant-numeric:tabular-nums}
.rank{color:var(--muted);width:28px}
.pill{font-size:11px;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:1px 8px}
footer{margin-top:40px;color:var(--muted);font-size:12px}
p{margin:12px 0}li{margin:4px 0}h3{font-size:15px;margin:24px 0 8px}
blockquote{margin:12px 0;padding:8px 14px;border-left:3px solid var(--accent);color:var(--muted)}
code{background:var(--panel);padding:1px 5px;border-radius:4px;font-size:13px}
a{color:var(--accent)}.up{color:#7ee08a}.down{color:#ff8d7b}
</style></head><body><div class="wrap">
<h1>__HEADING__</h1>
<div class="meta">__META__</div>
__BODY__
<footer>__FOOTER__</footer>
</div></body></html>
"""


def page(*, title: str, heading: str, meta_line: str, body: str, footer: str, lang: str = "da") -> str:
    """Fill the shared shell. Both the run report and the benchmark board use it."""
    return (
        HTML_TEMPLATE.replace("__LANG__", lang)
        .replace("__TITLE__", title)
        .replace("__HEADING__", heading)
        .replace("__META__", meta_line)
        .replace("__BODY__", body)
        .replace("__FOOTER__", footer)
    )


def _maybe_score_cell(scores: dict[str, float], key: str) -> str:
    """Not measured is not the same as scored zero."""
    if key not in scores:
        return '<span class="pill">not run</span>'
    return _score_cell(scores[key])


def _score_cell(score: float) -> str:
    hue = 12 + 116 * score  # red -> green
    return (
        f'<span class="cell" style="background:hsl({hue:.0f} 55% 22%);color:hsl({hue:.0f} 80% 78%)">'
        f"{score:.2f}</span>"
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def to_html(summary: dict[str, Any], meta: dict[str, Any]) -> str:
    suites = summary["suites"]
    categories = summary["categories"]
    models = [row["model_id"] for row in summary["leaderboard"]]
    labels = {row["model_id"]: row["label"] for row in summary["leaderboard"]}

    parts: list[str] = []

    parts.append("<h2>Leaderboard</h2>")
    rows = []
    for i, row in enumerate(summary["leaderboard"], 1):
        rows.append(
            [
                f'<span class="rank">{i}</span>',
                f'{row["label"]} <span class="pill">{row["backend"]}</span>',
                _score_cell(row["score"]),
                *[_maybe_score_cell(row["by_suite"], s) for s in suites],
                f'<span class="num">{row["median_latency_s"] if row["median_latency_s"] is not None else "-"}</span>',
                f'<span class="num">{row["tokens_per_s"] or "-"}</span>',
                f'<span class="num">{row["truncated"]}</span>',
                f'<span class="num">{row["errors"]}</span>',
            ]
        )
    parts.append(_table(["#", "Model", "Score", *suites, "Latency (median s)", "tok/s", "Trunc", "Fejl"], rows))

    parts.append("<h2>Kategorier</h2>")
    rows = [
        [row["label"], *[_maybe_score_cell(row["by_category"], c) for c in categories]]
        for row in summary["leaderboard"]
    ]
    parts.append(_table(["Model", *categories], rows))

    parts.append("<h2>Prompt x model</h2>")
    rows = [
        [
            f'{p["prompt_id"]}<br><span class="pill">{p["category"]}</span>',
            *[_score_cell(p["scores"].get(m, 0.0)) for m in models],
        ]
        for p in summary["prompts"]
    ]
    parts.append(_table(["Prompt", *[labels[m] for m in models]], rows))

    if summary.get("samples_per_prompt", 1) > 1:
        parts.append(f"<h2>Stabilitet ({summary['samples_per_prompt']} gentagelser)</h2>")
        rows = [
            [
                row["label"],
                _score_cell(row["stable_share"]) if row.get("stable_share") is not None else "-",
                f'<span class="num">{row.get("mean_spread", 0):.3f}</span>',
            ]
            for row in summary["leaderboard"]
        ]
        parts.append(_table(["Model", "Identiske gentagelser", "Gns. spredning"], rows))
        if summary["unstable"]:
            rows = [
                [
                    f'{row["prompt_id"]} <span class="pill">{row["suite"]}</span>',
                    row["label"],
                    ", ".join(f"{s:.2f}" for s in row["scores"]),
                    f'<span class="num">{row["spread"]:.2f}</span>',
                ]
                for row in summary["unstable"][:20]
            ]
            parts.append(_table(["Prompt", "Model", "Scores", "Spredning"], rows))

    parts.append("<h2>Check-typer</h2>")
    rows = [[name, _score_cell(s["pass_rate"]), f'<span class="num">{s["n"]}</span>'] for name, s in summary["check_types"].items()]
    parts.append(_table(["Check", "Pass rate", "n"], rows))

    meta_line = (
        f'{meta.get("run_id", "run")} - {summary["generated"]} - {summary["total_records"]} besvarelser - '
        f'suites: {", ".join(suites)} - repeats: {meta.get("repeats", 1)}'
    )
    return page(
        title=f"Agent Lab - {meta.get('run_id', 'run')}",
        heading="Agent Lab",
        meta_line=meta_line,
        body="\n".join(parts),
        footer="Genereret af Agent Lab - deterministiske checks, temperature 0.",
    )


def write_reports(run_dir: Path) -> dict[str, Path]:
    records = load_records(run_dir)
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"run_id": run_dir.name}
    summary = summarize(records)

    paths = {
        "summary": run_dir / "summary.json",
        "markdown": run_dir / "report.md",
        "html": run_dir / "report.html",
    }
    paths["summary"].write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    paths["markdown"].write_text(to_markdown(summary, meta), encoding="utf-8")
    paths["html"].write_text(to_html(summary, meta), encoding="utf-8")
    return paths

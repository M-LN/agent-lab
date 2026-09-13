"""A durable benchmark history across runs.

A single run directory answers "how did these models do today". The history answers
"how has this model moved since", which is the part worth keeping. Every row records
which suite version it was measured against, so a model is never compared across a
prompt set that has changed underneath it.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import RESULTS_DIR
from .report import summarize
from .runner import load_records

BENCH_DIR = RESULTS_DIR / "benchmark"
HISTORY = BENCH_DIR / "history.jsonl"


def suite_versions(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Hash each suite's prompt set so runs are only compared like with like."""
    per_suite: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for rec in records:
        per_suite[rec["suite"]].add((rec["prompt_id"], rec.get("io_hash") or "legacy"))

    versions: dict[str, dict[str, Any]] = {}
    for suite, prompts in per_suite.items():
        payload = json.dumps(sorted(prompts), ensure_ascii=False)
        versions[suite] = {
            "hash": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10],
            "prompts": len(prompts),
        }
    return versions


def build_rows(run_dir: Path) -> list[dict[str, Any]]:
    records = load_records(run_dir)
    summary = summarize(records)
    versions = suite_versions(records)

    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    started = meta.get("started") or datetime.now(timezone.utc).isoformat(timespec="seconds")

    rows: list[dict[str, Any]] = []
    for entry in summary["leaderboard"]:
        rows.append(
            {
                "run_id": run_dir.name,
                "recorded": started,
                "model_id": entry["model_id"],
                "label": entry["label"],
                "backend": entry["backend"],
                "model_name": entry["model_name"],
                "score": entry["score"],
                "by_suite": entry["by_suite"],
                "by_category": entry["by_category"],
                "median_latency_s": entry["median_latency_s"],
                "tokens_per_s": entry["tokens_per_s"],
                "errors": entry["errors"],
                "error_share": round(entry["errors"] / entry["runs"], 4) if entry["runs"] else 0.0,
                "truncated": entry["truncated"],
                "records": entry["runs"],
                "suite_versions": {s: versions[s] for s in entry["by_suite"] if s in versions},
                "repeats": meta.get("repeats", 1),
                "code_exec": meta.get("allow_code_exec", False),
            }
        )
    return rows


def load_history() -> list[dict[str, Any]]:
    if not HISTORY.exists():
        return []
    return [json.loads(line) for line in HISTORY.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_run(run_dir: Path, max_error_share: float = 0.1) -> tuple[int, list[dict[str, Any]]]:
    """Add (or refresh) this run's rows. Re-adding a run replaces its old rows.

    A model whose calls largely failed (rate limits, exhausted credits, a dead
    endpoint) has not been measured - failed calls score zero, which would enter
    the history as a real weakness. Those rows are excluded and reported back.
    """
    rows = build_rows(run_dir)
    keep = [r for r in rows if r["error_share"] <= max_error_share]
    excluded = [r for r in rows if r["error_share"] > max_error_share]
    existing = [r for r in load_history() if r["run_id"] != run_dir.name]
    merged = existing + keep
    merged.sort(key=lambda r: (r["recorded"], r["run_id"], r["model_id"]))

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("w", encoding="utf-8") as handle:
        for row in merged:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(keep), excluded


def current_board(history: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Latest row per model, with the delta against that model's previous run."""
    history = history if history is not None else load_history()
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        by_model[row["model_id"]].append(row)

    board: list[dict[str, Any]] = []
    for model_id, rows in by_model.items():
        rows.sort(key=lambda r: (r["recorded"], r["run_id"]))
        latest = dict(rows[-1])
        comparable = [
            r
            for r in rows[:-1]
            if r["suite_versions"] == latest["suite_versions"] and r["run_id"] != latest["run_id"]
        ]
        previous = comparable[-1] if comparable else None
        latest["previous_score"] = previous["score"] if previous else None
        latest["previous_run"] = previous["run_id"] if previous else None
        latest["delta"] = round(latest["score"] - previous["score"], 4) if previous else None
        latest["appearances"] = len(rows)
        board.append(latest)

    board.sort(key=lambda r: r["score"], reverse=True)
    return board


def trend(model_id: str, history: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    history = history if history is not None else load_history()
    rows = [r for r in history if r["model_id"] == model_id]
    rows.sort(key=lambda r: (r["recorded"], r["run_id"]))
    return rows


# --------------------------------------------------------------------------- rendering


def _delta_text(delta: float | None) -> str:
    if delta is None:
        return "-"
    return f"{delta:+.2f}"


def to_board_markdown(board: list[dict[str, Any]], history: list[dict[str, Any]], notes: str | None) -> str:
    suites = sorted({s for row in board for s in row["by_suite"]})
    lines = ["# Agent Lab benchmark board", ""]
    lines.append(
        f"{len(board)} model(s) tracked across {len({r['run_id'] for r in history})} run(s)"
        f" - updated {datetime.now().isoformat(timespec='seconds')}"
    )
    lines.append("")

    if notes:
        lines.append(notes.strip())
        lines.append("")

    lines.append("## Current standings")
    lines.append("")
    header = ["#", "Model", "Backend", "Score", "Delta", *suites, "Latency", "tok/s", "Trunc", "Errors", "Last run"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for i, row in enumerate(board, 1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(i),
                    row["label"],
                    row["backend"],
                    f"{row['score']:.2f}",
                    _delta_text(row["delta"]),
                    *[f"{row['by_suite'][s]:.2f}" if s in row["by_suite"] else "-" for s in suites],
                    f"{row['median_latency_s']}s" if row["median_latency_s"] is not None else "-",
                    str(row["tokens_per_s"] or "-"),
                    str(row["truncated"]),
                    str(row["errors"]),
                    row["run_id"],
                ]
            )
            + " |"
        )
    lines.append("")

    lines.append("## History")
    lines.append("")
    lines.append("| Run | Recorded | Model | Score | Suite versions |")
    lines.append("|---|---|---|---|---|")
    for row in sorted(history, key=lambda r: (r["recorded"], r["run_id"], r["model_id"])):
        versions = ", ".join(f"{s}@{v['hash']}" for s, v in sorted(row["suite_versions"].items()))
        lines.append(
            f"| {row['run_id']} | {row['recorded'][:16]} | {row['label']} | {row['score']:.2f} | {versions} |"
        )
    lines.append("")
    return "\n".join(lines)


def to_board_html(board: list[dict[str, Any]], history: list[dict[str, Any]], notes: str | None) -> str:
    from .markdown import render as render_markdown
    from .report import _maybe_score_cell, _score_cell, _table, page

    suites = sorted({s for row in board for s in row["by_suite"]})
    parts: list[str] = []

    if notes:
        parts.append(render_markdown(notes.strip()))

    parts.append("<h2>Current standings</h2>")
    rows = []
    for i, row in enumerate(board, 1):
        delta = row["delta"]
        delta_html = (
            f'<span class="{"up" if delta > 0 else "down" if delta < 0 else ""}">{_delta_text(delta)}</span>'
            if delta is not None
            else '<span class="pill">first run</span>'
        )
        rows.append(
            [
                f'<span class="rank">{i}</span>',
                f'{row["label"]} <span class="pill">{row["backend"]}</span>',
                _score_cell(row["score"]),
                delta_html,
                *[_maybe_score_cell(row["by_suite"], s) for s in suites],
                f'<span class="num">{row["median_latency_s"] if row["median_latency_s"] is not None else "-"}</span>',
                f'<span class="num">{row["tokens_per_s"] or "-"}</span>',
                f'<span class="num">{row["truncated"]}</span>',
                f'<span class="num">{row["errors"]}</span>',
                f'<span class="pill">{row["run_id"]}</span>',
            ]
        )
    parts.append(
        _table(["#", "Model", "Score", "Delta", *suites, "Latency (s)", "tok/s", "Trunc", "Errors", "Last run"], rows)
    )

    parts.append("<h2>Run history</h2>")
    rows = [
        [
            f'<span class="pill">{row["run_id"]}</span>',
            row["recorded"][:16].replace("T", " "),
            row["label"],
            _score_cell(row["score"]),
            " ".join(f'<span class="pill">{s}@{v["hash"]}</span>' for s, v in sorted(row["suite_versions"].items())),
        ]
        for row in sorted(history, key=lambda r: (r["recorded"], r["run_id"], r["model_id"]), reverse=True)
    ]
    parts.append(_table(["Run", "Recorded", "Model", "Score", "Suite version"], rows))

    return page(
        title="Agent Lab benchmark board",
        heading="Agent Lab benchmark board",
        meta_line=(
            f"{len(board)} models tracked across {len({r['run_id'] for r in history})} runs - "
            f"updated {datetime.now().isoformat(timespec='minutes')} - deterministic checks, temperature 0"
        ),
        body="\n".join(parts),
        footer="Scores are weighted pass rates over deterministic checks. Suite version hashes guard against "
        "comparing a model to a prompt set that has since changed.",
        lang="en",
    )


def notes_path() -> Path:
    return BENCH_DIR / "findings.md"


def write_board() -> dict[str, Path]:
    history = load_history()
    board = current_board(history)
    notes = notes_path().read_text(encoding="utf-8") if notes_path().exists() else None

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    paths = {"markdown": BENCH_DIR / "board.md", "html": BENCH_DIR / "board.html"}
    paths["markdown"].write_text(to_board_markdown(board, history, notes), encoding="utf-8")
    paths["html"].write_text(to_board_html(board, history, notes), encoding="utf-8")
    return paths

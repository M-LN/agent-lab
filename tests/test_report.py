"""Repeat handling: at temperature 0 disagreement between repeats is a finding."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.report import summarize, to_html, to_markdown


def records(scores_per_model: dict[str, list[float]], prompt_id: str = "p1") -> list[dict]:
    out = []
    for model_id, scores in scores_per_model.items():
        for i, score in enumerate(scores):
            out.append(
                {
                    "suite": "capability",
                    "prompt_id": prompt_id,
                    "category": "logic",
                    "prompt_weight": 1.0,
                    "repeat": i,
                    "model_id": model_id,
                    "model_label": model_id,
                    "backend": "ollama",
                    "model_name": model_id,
                    "score": score,
                    "checks": [{"type": "contains", "passed": score > 0.5, "weight": 1.0, "detail": ""}],
                    "latency_s": 1.0,
                    "completion_tokens": 10,
                    "truncated": False,
                    "error": None,
                    "output": "x",
                    "reasoning_chars": 0,
                }
            )
    return out


def test_stable_model_is_separated_from_unstable_one():
    summary = summarize(records({"m/stable": [1.0, 1.0, 1.0], "m/wobbly": [1.0, 0.5, 0.0]}))
    assert summary["samples_per_prompt"] == 3
    by_id = {row["model_id"]: row for row in summary["leaderboard"]}
    assert by_id["m/stable"]["stable_share"] == 1.0
    assert by_id["m/stable"]["mean_spread"] == 0.0
    assert by_id["m/wobbly"]["stable_share"] == 0.0
    assert by_id["m/wobbly"]["mean_spread"] == 1.0


def test_only_disagreeing_cells_are_listed():
    summary = summarize(records({"m/stable": [1.0, 1.0, 1.0], "m/wobbly": [1.0, 0.5, 0.0]}))
    assert [u["model_id"] for u in summary["unstable"]] == ["m/wobbly"]
    assert summary["unstable"][0]["spread"] == 1.0


def test_single_sample_run_reports_no_stability_section():
    summary = summarize(records({"m/a": [1.0]}))
    assert summary["samples_per_prompt"] == 1
    assert summary["unstable"] == []
    assert summary["leaderboard"][0]["stable_share"] is None
    markdown = to_markdown(summary, {"run_id": "t", "repeats": 1})
    assert "Stabilitet" not in markdown
    assert "Stabilitet" not in to_html(summary, {"run_id": "t", "repeats": 1})


def test_failed_calls_are_missing_data_not_zeros():
    recs = records({"m/a": [1.0, 1.0]}, prompt_id="p1")
    recs[1].update({"error": "RuntimeError: HTTP 402", "score": 0.0, "output": ""})
    summary = summarize(recs)
    row = summary["leaderboard"][0]

    assert row["score"] == 1.0, "a rate-limited call must not read as a capability failure"
    assert row["errors"] == 1
    assert summary["prompts"][0]["scores"]["m/a"] == 1.0
    # The failed call must not count as a disagreeing repeat either.
    assert summary["unstable"] == []


def test_repeats_are_averaged_into_the_score():
    summary = summarize(records({"m/a": [1.0, 0.0]}))
    assert summary["leaderboard"][0]["score"] == 0.5

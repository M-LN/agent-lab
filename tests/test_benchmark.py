"""The benchmark history is the part that outlives individual runs, so it gets tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import benchmark
from lab.markdown import render


def make_run(tmp_path: Path, run_id: str, scores: dict[str, float], io_hash: str = "aaa") -> Path:
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    records = []
    for model_id, score in scores.items():
        for prompt_id in ("p1", "p2"):
            records.append(
                {
                    "run_id": run_id,
                    "suite": "capability",
                    "prompt_id": prompt_id,
                    "io_hash": io_hash,
                    "category": "logic",
                    "prompt_weight": 1.0,
                    "repeat": 0,
                    "model_id": model_id,
                    "model_label": model_id,
                    "backend": "ollama",
                    "model_name": model_id,
                    "score": score,
                    "checks": [{"type": "contains", "passed": score > 0.5, "weight": 1.0, "detail": ""}],
                    "latency_s": 1.0,
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "truncated": False,
                    "error": None,
                    "output": "x",
                    "reasoning_chars": 0,
                }
            )
    (run_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records), encoding="utf-8"
    )
    (run_dir / "meta.json").write_text(
        json.dumps({"run_id": run_id, "started": f"2026-09-{int(run_id[-1]):02d}T10:00:00+00:00", "repeats": 1}),
        encoding="utf-8",
    )
    return run_dir


@pytest.fixture
def bench(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "BENCH_DIR", tmp_path / "benchmark")
    monkeypatch.setattr(benchmark, "HISTORY", tmp_path / "benchmark" / "history.jsonl")
    return tmp_path


def fail_calls(run_dir: Path, model_id: str, how_many: int) -> None:
    """Turn the first N records of a model into failed calls, as a rate limit would."""
    path = run_dir / "results.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    hit = 0
    for record in records:
        if record["model_id"] == model_id and hit < how_many:
            record.update({"error": "RuntimeError: HTTP 402", "score": 0.0, "output": ""})
            hit += 1
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def test_append_run_is_idempotent(bench):
    run = make_run(bench, "run1", {"m/a": 1.0})
    benchmark.append_run(run)
    benchmark.append_run(run)
    history = benchmark.load_history()
    assert len(history) == 1, "re-adding a run must replace its rows, not duplicate them"


def test_failure_dominated_models_stay_out_of_the_history(bench):
    run = make_run(bench, "run1", {"m/ok": 1.0, "m/broken": 1.0})
    fail_calls(run, "m/broken", 2)  # both of its calls failed
    added, excluded = benchmark.append_run(run)

    assert added == 1
    assert [r["model_id"] for r in excluded] == ["m/broken"]
    assert [r["model_id"] for r in benchmark.load_history()] == ["m/ok"]


def test_a_few_failed_calls_are_tolerated(bench):
    run = make_run(bench, "run1", {"m/a": 1.0})
    added, excluded = benchmark.append_run(run, max_error_share=0.6)
    assert added == 1 and excluded == []


def test_board_reports_delta_between_runs(bench):
    benchmark.append_run(make_run(bench, "run1", {"m/a": 0.5}))
    benchmark.append_run(make_run(bench, "run2", {"m/a": 0.8}))
    board = benchmark.current_board()
    assert len(board) == 1
    row = board[0]
    assert row["run_id"] == "run2"
    assert row["score"] == 0.8
    assert row["delta"] == pytest.approx(0.3)
    assert row["previous_run"] == "run1"


def test_delta_is_withheld_when_the_prompt_set_changed(bench):
    benchmark.append_run(make_run(bench, "run1", {"m/a": 0.5}, io_hash="aaa"))
    benchmark.append_run(make_run(bench, "run2", {"m/a": 0.8}, io_hash="bbb"))
    row = benchmark.current_board()[0]
    assert row["delta"] is None, "scores from a changed suite must not be compared"


def test_suite_version_hash_tracks_the_prompt_set(bench):
    run_a = make_run(bench, "run1", {"m/a": 1.0}, io_hash="aaa")
    run_b = make_run(bench, "run2", {"m/a": 1.0}, io_hash="bbb")
    from lab.runner import load_records

    version_a = benchmark.suite_versions(load_records(run_a))["capability"]
    version_b = benchmark.suite_versions(load_records(run_b))["capability"]
    assert version_a["prompts"] == 2
    assert version_a["hash"] != version_b["hash"]


def test_trend_is_ordered(bench):
    benchmark.append_run(make_run(bench, "run2", {"m/a": 0.8}))
    benchmark.append_run(make_run(bench, "run1", {"m/a": 0.5}))
    rows = benchmark.trend("m/a")
    assert [r["run_id"] for r in rows] == ["run1", "run2"]


def test_markdown_renders_the_pieces_the_board_uses():
    html = render("# T\n\ntext with **bold**\n\n- a\n- b\n\n| A | B |\n|---|---|\n| 1 | 2 |\n")
    assert "<h1>T</h1>" in html
    assert "<strong>bold</strong>" in html
    assert html.count("<li>") == 2
    assert "<table>" in html and "<td>1</td>" in html


def test_consecutive_quote_lines_become_one_blockquote():
    html = render("intro\n\n> first line\n> second line\n\nafter\n")
    assert html.count("<blockquote>") == 1
    assert "first line second line" in html


def test_markdown_escapes_html():
    assert "&lt;script&gt;" in render("a <script> tag")


def test_board_keeps_suites_measured_in_separate_runs(bench, monkeypatch):
    """A model measured one suite at a time must not lose the earlier suite."""
    run_a = make_run(bench, "run1", {"m/a": 1.0})
    run_b = make_run(bench, "run2", {"m/a": 0.5})
    # Relabel the second run's records as a different suite.
    path = run_b / "results.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for record in records:
        record["suite"] = "robustness"
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    benchmark.append_run(run_a)
    benchmark.append_run(run_b)
    row = benchmark.current_board()[0]

    assert row["by_suite"] == {"capability": 1.0, "robustness": 0.5}
    assert row["score"] == 0.75
    assert row["run_id"] == "run1, run2"


def test_rerunning_one_suite_replaces_only_that_suite(bench):
    benchmark.append_run(make_run(bench, "run1", {"m/a": 1.0}))
    benchmark.append_run(make_run(bench, "run2", {"m/a": 0.25}))
    row = benchmark.current_board()[0]
    assert row["by_suite"] == {"capability": 0.25}, "the newer measurement of a suite wins"
    assert row["delta"] == pytest.approx(-0.75)


def test_measurement_only_runs_are_not_recorded(bench):
    """A ladder run has no suite scores; recording it would put a 0.00 on the board."""
    run = make_run(bench, "run1", {"m/a": 1.0})
    path = run / "results.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for record in records:
        record["scored"] = False
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    added, excluded = benchmark.append_run(run)
    assert added == 0 and excluded == []
    assert benchmark.load_history() == []


def test_html_blocks_pass_through_unescaped():
    """Generated tables are substituted into the narrative as markup, not text."""
    table = '<div class="lab-scroll"><table class="lab-table"><tr><td>1</td></tr></table></div>'
    out = render(f"Intro line.\n\n{table}\n\nAfter.\n")
    assert table in out, "an HTML block must survive rendering intact"
    assert "&lt;div" not in out
    assert "<p>Intro line.</p>" in out


def test_angle_brackets_in_prose_are_still_escaped():
    assert "&lt;script&gt;" in render("Prose mentioning a <script> tag inline.")

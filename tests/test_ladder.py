"""The ladder measures where a model stops, so the threshold logic gets tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.config import load_suite
from lab.report import summarize


def rung_record(model_id: str, rung: int, refused: bool, ladder: str = "locks") -> dict:
    return {
        "suite": "refusal_ladder",
        "scored": False,
        "ladder": ladder,
        "rung": rung,
        "prompt_id": f"lad_{ladder}_{rung}",
        "category": ladder,
        "prompt_weight": 1.0,
        "repeat": 0,
        "model_id": model_id,
        "model_label": model_id,
        "backend": "ollama",
        "model_name": model_id,
        "score": 0.0,
        "checks": [],
        "metrics": {"refusal": 1.0 if refused else 0.0},
        "latency_s": 1.0,
        "completion_tokens": 10,
        "truncated": False,
        "error": None,
        "output": "x",
        "reasoning_chars": 0,
    }


def test_threshold_is_the_first_rung_refused():
    records = [
        rung_record("m/a", 1, False),
        rung_record("m/a", 2, False),
        rung_record("m/a", 3, True),
        rung_record("m/a", 4, True),
    ]
    ladder = summarize(records)["ladders"]["locks"]["models"]["m/a"]
    assert ladder["threshold"] == 3
    assert ladder["answered"] == 2
    assert ladder["refused_rungs"] == [3, 4]


def test_a_model_that_never_refuses_has_no_threshold():
    records = [rung_record("m/a", r, False) for r in (1, 2, 3, 4)]
    ladder = summarize(records)["ladders"]["locks"]["models"]["m/a"]
    assert ladder["threshold"] is None
    assert ladder["answered"] == 4


def test_a_gap_still_reports_the_earliest_refusal():
    # Refusing rung 2 but answering rung 3 is itself a finding; the threshold is the first.
    records = [
        rung_record("m/a", 1, False),
        rung_record("m/a", 2, True),
        rung_record("m/a", 3, False),
        rung_record("m/a", 4, True),
    ]
    ladder = summarize(records)["ladders"]["locks"]["models"]["m/a"]
    assert ladder["threshold"] == 2
    assert ladder["refused_rungs"] == [2, 4]


def test_measurement_suites_stay_out_of_the_leaderboard():
    records = [rung_record("m/a", r, False) for r in (1, 2)]
    summary = summarize(records)
    row = summary["leaderboard"][0]
    assert row["by_suite"] == {}, "an unscored suite must not become a score"
    assert row["score"] == 0.0
    assert row["metrics"]["refusal"] == 0.0, "its measurements are still recorded"


def test_scored_suites_are_unaffected():
    records = [rung_record("m/a", 1, False)]
    records[0].update({"suite": "capability", "scored": True, "score": 1.0, "ladder": None, "rung": None})
    summary = summarize(records)
    assert summary["leaderboard"][0]["by_suite"] == {"capability": 1.0}


def test_the_shipped_ladder_is_well_formed():
    suite = load_suite("refusal_ladder")
    assert suite.scored is False
    by_ladder: dict[str, list[int]] = {}
    for prompt in suite.prompts:
        assert prompt.ladder, f"{prompt.id} has no ladder"
        assert prompt.rung, f"{prompt.id} has no rung"
        by_ladder.setdefault(prompt.ladder, []).append(prompt.rung)
    for name, rungs in by_ladder.items():
        assert sorted(rungs) == list(range(1, len(rungs) + 1)), f"{name} has gaps in its rungs"

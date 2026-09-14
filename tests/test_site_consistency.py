"""Adding a model must show up everywhere, not just on the leaderboard.

Every table and figure on the page once read from its own hardcoded list of run
ids, so a newly measured model appeared in some and silently not in others while
the sentences around them stayed frozen. These tests fail if that returns.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import site
from lab.benchmark import current_board


@pytest.fixture(scope="module")
def values() -> dict:
    return site.narrative_values()


def labels_in(html: str) -> set[str]:
    cells = re.findall(r"<td[^>]*>(.*?)</td>", html, re.DOTALL)
    return {re.sub(r"<[^>]+>", "", c).strip() for c in cells}


def test_no_run_ids_are_hardcoded_in_the_page_builder():
    """Run ids belong in the data, not in the code that reads it."""
    source = (Path(__file__).resolve().parent.parent / "lab" / "site.py").read_text(encoding="utf-8")
    source = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    hardcoded = re.findall(r'load_summary\(\s*"([a-z0-9][a-z0-9-]+)"\s*\)', source)
    assert not hardcoded, f"page builder names runs directly: {sorted(set(hardcoded))}"


def test_every_measured_model_reaches_the_leaderboard(values):
    shown = labels_in(values["table:leaderboard"])
    for row in current_board():
        label = site.clean_label(row["label"])
        assert any(label in cell for cell in shown), f"{label} is measured but missing from the board table"


def test_a_model_with_injection_data_reaches_that_table(values):
    """The newest model must not be missing from the table it was added to settle."""
    shown = " ".join(labels_in(values["table:injection"]))
    scores = site.hierarchy_figure_data()
    assert scores, "no hierarchy data at all"
    for row in scores:
        assert row["label"] in shown, f"{row['label']} has data but is absent from the injection table"


def test_the_figure_and_its_sentence_agree(values):
    """The prose quotes counts from the figure; they must come from the same place."""
    data = site.hierarchy_figure_data()
    assert values["hierarchy_models"] == len(data)
    flat = sum(1 for r in data if abs(r["hierarchy"] - float(values["hierarchy_value"])) < 0.005)
    assert values["hierarchy_flat"] == flat
    assert 0 < values["hierarchy_flat"] <= values["hierarchy_models"]


def test_every_model_in_a_figure_has_a_parameter_count():
    for row in site.hierarchy_figure_data():
        assert row["parameters"], f"{row['label']} would be dropped from any figure plotted against scale"

"""One narrative feeds two pages, so the substitution gets tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import content


def test_values_are_substituted():
    out = content.render("We measured {{n}} models on {{k}} prompts.", {"n": 8, "k": "36"})
    assert out == "We measured 8 models on 36 prompts."


def test_a_missing_value_raises_rather_than_rendering_empty():
    with pytest.raises(content.MissingValue) as exc:
        content.render("{{present}} and {{absent}}", {"present": 1})
    assert "absent" in str(exc.value)


def test_dropped_blocks_leave_no_gap():
    out = content.render("before\n\n{{table:history}}\n\nafter", {}, drop={"table:history"})
    assert out == "before\n\nafter"


def test_placeholders_lists_what_the_narrative_needs():
    assert content.placeholders("{{a}} {{table:b}} plain") == {"a", "table:b"}


def test_the_shipped_narrative_has_every_value_it_asks_for():
    """The guard against drift: prose and data must stay in step."""
    from lab.site import narrative_values

    text = content.load()
    needed = content.placeholders(text)
    available = set(narrative_values()) | {"table:history"}
    assert not (needed - available), f"narrative asks for values nothing provides: {needed - available}"


def test_the_narrative_renders_for_both_pages():
    from lab.site import narrative_values

    values = narrative_values()
    site = content.render(content.load(), values, drop={"table:history"})
    from lab.benchmark import notes

    board = notes(history=[])
    assert "{{" not in site and "{{" not in board
    assert "<table" in site, "the site page keeps its tables"
    assert "<canvas" in site, "the site page draws its figures"
    assert "<canvas" not in board, "the board has no script to draw a canvas"
    # The board prints its own standings and history below the prose, so the
    # narrative's own result tables would duplicate them. Context tables stay.
    for duplicated in ("Median latency", "Instruction hidden in a document", "Rungs answered"):
        assert duplicated not in board, f"the board duplicates {duplicated!r}"

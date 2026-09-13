"""One narrative, rendered in two places.

The findings were written twice - once for the benchmark board, once inside the site
page generator - and the two copies had already started to disagree. The prose now
lives in content/findings.md and both renderers read it from there.

Anything that can go stale is a placeholder rather than a typed number:

    {{measurements}}          a value computed from the recorded runs
    {{table:leaderboard}}     a block the calling renderer supplies

A placeholder with no value raises rather than rendering empty, because a silently
missing number is exactly the drift this module exists to prevent.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content" / "findings.md"

TOKEN_RE = re.compile(r"\{\{([a-z_]+(?::[a-z_]+)?)\}\}")


class MissingValue(KeyError):
    """A placeholder in the narrative has no value in the context."""


def load(path: Path | None = None) -> str:
    return (path or CONTENT).read_text(encoding="utf-8")


def placeholders(text: str) -> set[str]:
    return set(TOKEN_RE.findall(text))


def render(text: str, values: dict[str, Any], *, drop: set[str] | None = None) -> str:
    """Substitute every placeholder. `drop` names blocks this renderer omits."""
    drop = drop or set()
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token in drop:
            return ""
        if token not in values:
            missing.append(token)
            return match.group(0)
        value = values[token]
        return value if isinstance(value, str) else f"{value}"

    out = TOKEN_RE.sub(replace, text)
    if missing:
        raise MissingValue(f"no value for: {', '.join(sorted(set(missing)))}")
    # Dropping a block leaves a blank line behind; collapse the run.
    return re.sub(r"\n{3,}", "\n\n", out)


def numbers_from(summaries: dict[str, dict], history: list[dict]) -> dict[str, Any]:
    """The counts the narrative quotes, derived rather than typed."""
    values: dict[str, Any] = {}
    board_models = {row["model_id"] for row in history}
    values["model_count"] = len(board_models)
    values["run_count"] = len({row["run_id"] for row in history})
    values["scored_answers"] = sum(row["records"] for row in history)

    repeats = summaries.get("repeats3", {})
    if repeats:
        cells = len(repeats.get("prompts", [])) * len(repeats.get("models", []))
        unstable = len(repeats.get("unstable", []))
        values["repeat_samples"] = repeats.get("samples_per_prompt", 1)
        values["repeat_answers"] = repeats.get("total_records", 0)
        values["stable_cells"] = cells - unstable
        values["total_cells"] = cells
        values["unstable_cells"] = unstable
        checks = repeats.get("check_types", {})
        values["numeric_pass"] = f"{checks.get('numeric', {}).get('pass_rate', 0):.0%}"
        values["structural_pass"] = f"{max((checks.get(k, {}).get('pass_rate', 0) for k in ('json_schema', 'json_path', 'line_count', 'code_exec')), default=0):.0%}"
    return values

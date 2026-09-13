"""Re-score the stored answers of a run against the current check definitions.

Editing a check should not cost another 120 model calls, so regrade reuses the saved
output. Editing the prompt itself does invalidate the answer - those prompts are
reported as stale and left untouched.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PromptSpec, RESULTS_DIR, available_suites, load_suite
from .graders import run_checks
from .runner import load_records


def current_prompts() -> dict[tuple[str, str], PromptSpec]:
    lookup: dict[tuple[str, str], PromptSpec] = {}
    for name in available_suites():
        suite = load_suite(name)
        for prompt in suite.prompts:
            lookup[(suite.name, prompt.id)] = prompt
    return lookup


def regrade_run(
    run_dir: Path,
    out_dir: Path | None = None,
    *,
    allow_code_exec: bool = False,
) -> tuple[Path, dict[str, Any]]:
    records = load_records(run_dir)
    prompts = current_prompts()

    stale: set[str] = set()
    missing: set[str] = set()
    changed = 0
    updated: list[dict[str, Any]] = []

    for record in records:
        spec = prompts.get((record["suite"], record["prompt_id"]))
        if spec is None:
            missing.add(record["prompt_id"])
            updated.append(record)
            continue
        if record.get("io_hash") and record["io_hash"] != spec.io_hash:
            # The prompt text itself moved - the stored answer no longer belongs to it.
            stale.add(record["prompt_id"])
            updated.append(record)
            continue
        if record.get("error"):
            updated.append(record)
            continue

        score, checks = run_checks(record["output"], spec.checks, allow_code_exec=allow_code_exec)
        if score != record["score"]:
            changed += 1
        new_record = dict(record)
        new_record.update(
            {
                "score": score,
                "checks": checks,
                "passed_checks": sum(1 for c in checks if c["passed"]),
                "total_checks": len(checks),
                "prompt_fingerprint": spec.fingerprint,
                "regraded_from": record.get("run_id"),
            }
        )
        updated.append(new_record)

    target = out_dir or RESULTS_DIR / "runs" / f"{run_dir.name}-regraded"
    target.mkdir(parents=True, exist_ok=True)
    run_id = target.name
    with (target / "results.jsonl").open("w", encoding="utf-8") as handle:
        for record in updated:
            record["run_id"] = run_id
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta.update(
        {
            "run_id": run_id,
            "regraded_from": run_dir.name,
            "regraded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stale_prompts": sorted(stale),
            "unknown_prompts": sorted(missing),
            "records": len(updated),
        }
    )
    (target / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return target, {
        "records": len(updated),
        "scores_changed": changed,
        "stale": sorted(stale),
        "unknown": sorted(missing),
    }

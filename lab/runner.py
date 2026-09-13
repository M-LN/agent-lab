"""Runs a prompt suite across every selected model and records the results."""
from __future__ import annotations

import json
import platform
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from .backends import Backend, get_backend
from .config import LabConfig, ModelSpec, PromptSpec, RESULTS_DIR, Suite
from .graders import run_checks

console = Console()

DEFAULT_CONCURRENCY = {"ollama": 1, "hf": 4}


@dataclass
class Task:
    model: ModelSpec
    suite: str
    prompt: PromptSpec
    repeat: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id() -> str:
    return datetime.now().strftime("run-%Y%m%d-%H%M%S")


def build_backends(config: LabConfig, models: Iterable[ModelSpec]) -> dict[str, Backend]:
    backends: dict[str, Backend] = {}
    for model in models:
        if model.backend not in backends:
            options = config.backend_options.get(model.backend, {})
            backends[model.backend] = get_backend(model.backend, **options)
    return backends


def execute_task(
    task: Task,
    backend: Backend,
    config: LabConfig,
    *,
    allow_code_exec: bool,
    run_id: str,
) -> dict[str, Any]:
    defaults = config.defaults
    base_budget = task.prompt.max_tokens or defaults.get("max_tokens", 800)
    max_tokens = int(base_budget * max(1.0, task.model.token_budget))
    completion = backend.complete(
        model=task.model.model,
        prompt=task.prompt.prompt,
        system=task.prompt.system,
        temperature=task.prompt.temperature
        if task.prompt.temperature is not None
        else defaults.get("temperature", 0.0),
        max_tokens=max_tokens,
        timeout=defaults.get("timeout", 120),
        retries=defaults.get("retries", 2),
        extra={**task.model.params, **task.model.extra},
    )

    if completion.ok:
        score, checks = run_checks(completion.text, task.prompt.checks, allow_code_exec=allow_code_exec)
    else:
        score, checks = 0.0, [{"type": "call", "passed": False, "weight": 1.0, "detail": completion.error or "failed"}]

    return {
        "run_id": run_id,
        "ts": _now(),
        "suite": task.suite,
        "prompt_id": task.prompt.id,
        "prompt_fingerprint": task.prompt.fingerprint,
        "io_hash": task.prompt.io_hash,
        "category": task.prompt.category,
        "prompt_weight": task.prompt.weight,
        "repeat": task.repeat,
        "model_id": task.model.id,
        "model_label": task.model.display,
        "backend": task.model.backend,
        "model_name": task.model.model,
        "score": score,
        "passed_checks": sum(1 for c in checks if c["passed"]),
        "total_checks": len(checks),
        "checks": checks,
        "latency_s": completion.latency_s,
        "prompt_tokens": completion.prompt_tokens,
        "completion_tokens": completion.completion_tokens,
        "max_tokens": max_tokens,
        "truncated": completion.truncated,
        # Which provider the HF router picked affects latency and sometimes output.
        "provider": completion.raw.get("provider"),
        "finish_reason": completion.raw.get("finish_reason") or completion.raw.get("done_reason"),
        "error": completion.error,
        "output": completion.text,
        "reasoning_chars": len(completion.reasoning or ""),
    }


def run_suites(
    config: LabConfig,
    suites: list[Suite],
    models: list[ModelSpec],
    *,
    allow_code_exec: bool = False,
    repeats: int = 1,
    run_id: str | None = None,
    out_dir: Path | None = None,
) -> Path:
    run_id = run_id or new_run_id()
    run_dir = (out_dir or RESULTS_DIR / "runs") / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"

    # Model-major order: a local model stays loaded for all its prompts instead of
    # being swapped in and out of memory between every single call.
    tasks = [
        Task(model=model, suite=suite.name, prompt=prompt, repeat=r)
        for model in models
        for suite in suites
        for prompt in suite.prompts
        for r in range(repeats)
    ]

    backends = build_backends(config, models)
    for name, backend in backends.items():
        ok, detail = backend.available()
        style = "green" if ok else "red"
        console.print(f"  backend [bold]{name}[/bold]: [{style}]{detail}[/{style}]")
        if not ok:
            console.print(f"  [yellow]-> results for {name} models will be recorded as errors[/yellow]")

    write_lock = threading.Lock()
    records: list[dict[str, Any]] = []

    by_backend: dict[str, list[Task]] = {}
    for task in tasks:
        by_backend.setdefault(task.model.backend, []).append(task)

    with results_path.open("w", encoding="utf-8") as handle, Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        bar = progress.add_task("running", total=len(tasks))

        def work(task: Task) -> dict[str, Any]:
            record = execute_task(
                task,
                backends[task.model.backend],
                config,
                allow_code_exec=allow_code_exec,
                run_id=run_id,
            )
            with write_lock:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                records.append(record)
                mark = "[green]ok[/green]" if record["error"] is None else "[red]err[/red]"
                progress.console.print(
                    f"  {mark} {record['model_id']:<24} {record['suite']}/{record['prompt_id']:<22}"
                    f" score={record['score']:.2f} {record['latency_s']:>6.1f}s"
                )
                progress.advance(bar)
            return record

        # One pool per backend so local models stay serial while API calls fan out.
        pools: list[tuple[ThreadPoolExecutor, list]] = []
        for backend_name, backend_tasks in by_backend.items():
            workers = config.concurrency.get(backend_name, DEFAULT_CONCURRENCY.get(backend_name, 2))
            pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix=backend_name)
            pools.append((pool, [pool.submit(work, t) for t in backend_tasks]))

        for pool, futures in pools:
            for future in as_completed(futures):
                future.result()
            pool.shutdown()

    meta = {
        "run_id": run_id,
        "started": _now(),
        "suites": [{"name": s.name, "description": s.description, "prompts": len(s.prompts)} for s in suites],
        "models": [asdict(m) for m in models],
        "repeats": repeats,
        "allow_code_exec": allow_code_exec,
        "defaults": config.defaults,
        "host": platform.node(),
        "python": platform.python_version(),
        "records": len(records),
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return run_dir


def load_records(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "results.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def merge_run(source_dir: Path, target_dir: Path) -> int:
    """Fold a small re-run into an existing run, replacing the matching records.

    Lets you fix one bad prompt and refresh a 120-call baseline with 5 calls.
    """
    incoming = load_records(source_dir)
    existing = load_records(target_dir)
    key = lambda r: (r["model_id"], r["prompt_id"], r.get("repeat", 0))  # noqa: E731
    replacing = {key(r) for r in incoming}
    kept = [r for r in existing if key(r) not in replacing]

    merged = kept + [{**r, "run_id": target_dir.name, "merged_from": r.get("run_id")} for r in incoming]
    merged.sort(key=lambda r: (r["model_id"], r["suite"], r["prompt_id"], r.get("repeat", 0)))
    with (target_dir / "results.jsonl").open("w", encoding="utf-8") as handle:
        for record in merged:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    meta_path = target_dir / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["records"] = len(merged)
        meta.setdefault("merges", []).append(
            {"from": source_dir.name, "at": _now(), "replaced": len(existing) - len(kept), "added": len(incoming)}
        )
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(existing) - len(kept)


def latest_run(base: Path | None = None) -> Path | None:
    runs_dir = base or RESULTS_DIR / "runs"
    runs = [p for p in runs_dir.iterdir() if p.is_dir() and (p / "results.jsonl").exists()]
    if not runs:
        return None
    return max(runs, key=lambda p: p.stat().st_mtime)

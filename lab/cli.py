"""Command line interface: python -m lab <command>"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .backends import get_backend
from .backends.ollama import OllamaBackend
from .benchmark import append_run, current_board, load_history, trend, write_board
from .config import RESULTS_DIR, available_suites, load_config, load_suite
from .regrade import regrade_run
from .report import write_reports
from .runner import latest_run, load_records, merge_run, run_suites

console = Console()


def cmd_models(args: argparse.Namespace) -> int:
    config = load_config()
    installed = set(OllamaBackend().installed_models())

    table = Table(title="Model registry", header_style="bold cyan")
    for column in ("id", "backend", "model", "tags", "enabled", "status"):
        table.add_column(column)

    for model in config.models:
        if model.backend == "ollama":
            status = "[green]pulled[/green]" if model.model in installed else "[yellow]not pulled[/yellow]"
        else:
            status = "[green]token set[/green]" if get_backend("hf").token else "[red]no HF_TOKEN[/red]"
        table.add_row(
            model.id,
            model.backend,
            model.model,
            ",".join(model.tags),
            "yes" if model.enabled else "no",
            status,
        )
    console.print(table)

    for name in {m.backend for m in config.models}:
        ok, detail = get_backend(name, **config.backend_options.get(name, {})).available()
        console.print(f"backend [bold]{name}[/bold]: {'[green]' if ok else '[red]'}{detail}[/]")
    return 0


def cmd_suites(args: argparse.Namespace) -> int:
    table = Table(title="Suites", header_style="bold cyan")
    for column in ("suite", "prompts", "categories", "description"):
        table.add_column(column)
    for name in available_suites():
        suite = load_suite(name)
        categories = sorted({p.category for p in suite.prompts})
        table.add_row(suite.name, str(len(suite.prompts)), ",".join(categories), suite.description[:70])
    console.print(table)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config()
    suite_names = args.suite or available_suites()
    suites = [load_suite(name) for name in suite_names]
    if args.only:
        wanted = set(args.only)
        for suite in suites:
            suite.prompts = [p for p in suite.prompts if p.id in wanted]
    models = config.select(args.models, include_disabled=args.include_disabled)

    if not models:
        console.print("[red]No models matched.[/red] Try: python -m lab models")
        return 1
    total = sum(len(s.prompts) for s in suites) * len(models) * args.repeats
    console.print(
        f"[bold]Agent Lab[/bold]: {len(models)} model(s) x {sum(len(s.prompts) for s in suites)} prompt(s)"
        f" x {args.repeats} repeat(s) = {total} calls"
    )
    if args.allow_code_exec:
        console.print("[yellow]code_exec enabled - model generated Python will run in a subprocess[/yellow]")

    run_dir = run_suites(
        config,
        suites,
        models,
        allow_code_exec=args.allow_code_exec,
        repeats=args.repeats,
        run_id=args.run_id,
    )
    if args.merge_into:
        target = _resolve_run(args.merge_into)
        if target and target.exists():
            replaced = merge_run(run_dir, target)
            console.print(f"[cyan]Merged into {target.name}[/cyan] ({replaced} record(s) replaced)")
            write_reports(target)
            if not args.no_bench:
                _record_in_benchmark(target)
            _print_leaderboard(target)
            return 0
        console.print(f"[red]Merge target not found: {args.merge_into}[/red]")

    paths = write_reports(run_dir)
    console.print(f"\n[green]Done[/green] -> {run_dir}")
    for name, path in paths.items():
        console.print(f"  {name}: {path}")
    if not args.no_bench:
        _record_in_benchmark(run_dir)
    _print_leaderboard(run_dir)
    return 0


def _print_leaderboard(run_dir: Path) -> None:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    table = Table(title=f"Leaderboard - {run_dir.name}", header_style="bold cyan")
    table.add_column("#")
    table.add_column("model")
    for suite in summary["suites"]:
        table.add_column(suite, justify="right")
    table.add_column("score", justify="right")
    table.add_column("latency", justify="right")
    table.add_column("err", justify="right")
    for i, row in enumerate(summary["leaderboard"], 1):
        table.add_row(
            str(i),
            row["label"],
            *[f"{row['by_suite'].get(s, 0):.2f}" for s in summary["suites"]],
            f"[bold]{row['score']:.2f}[/bold]",
            f"{row['median_latency_s']}s" if row["median_latency_s"] is not None else "-",
            str(row["errors"]),
        )
    console.print(table)


def _resolve_run(value: str | None) -> Path | None:
    if not value:
        return latest_run()
    path = Path(value)
    return path if path.exists() else RESULTS_DIR / "runs" / value


def cmd_report(args: argparse.Namespace) -> int:
    run_dir = _resolve_run(args.run)
    if not run_dir or not run_dir.exists():
        console.print("[red]No run found.[/red]")
        return 1
    paths = write_reports(run_dir)
    for name, path in paths.items():
        console.print(f"{name}: {path}")
    _print_leaderboard(run_dir)
    return 0


def cmd_regrade(args: argparse.Namespace) -> int:
    run_dir = _resolve_run(args.run)
    if not run_dir or not run_dir.exists():
        console.print("[red]No run found.[/red]")
        return 1
    out_dir = Path(args.out) if args.out and Path(args.out).is_absolute() else (
        RESULTS_DIR / "runs" / args.out if args.out else None
    )
    target, stats = regrade_run(run_dir, out_dir, allow_code_exec=args.allow_code_exec)
    console.print(
        f"Regraded {stats['records']} record(s) from [bold]{run_dir.name}[/bold] -> {target.name}"
        f" ({stats['scores_changed']} score(s) changed)"
    )
    if stats["stale"]:
        console.print(
            "[yellow]Prompt text changed since the run - not regraded, rerun these:[/yellow] "
            + ", ".join(stats["stale"])
        )
        console.print("  python -m lab run " + " ".join(f"--only {p}" for p in stats["stale"]))
    if stats["unknown"]:
        console.print("[yellow]No longer in any suite:[/yellow] " + ", ".join(stats["unknown"]))
    write_reports(target)
    _print_leaderboard(target)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    run_dir = _resolve_run(args.run)
    if not run_dir or not run_dir.exists():
        console.print("[red]No run found.[/red]")
        return 1
    records = load_records(run_dir)
    selected = [r for r in records if r["prompt_id"] == args.prompt_id]
    if args.model:
        selected = [r for r in selected if r["model_id"] == args.model]
    if not selected:
        console.print(f"[yellow]No records for prompt '{args.prompt_id}'.[/yellow]")
        return 1
    for rec in selected:
        console.rule(f"{rec['model_id']} - score {rec['score']:.2f} - {rec['latency_s']}s")
        if rec["error"]:
            console.print(f"[red]{rec['error']}[/red]")
        else:
            console.print(rec["output"][: args.chars])
        for check in rec["checks"]:
            mark = "[green]PASS[/green]" if check["passed"] else "[red]FAIL[/red]"
            console.print(f"  {mark} {check['type']}: {check['detail']}")
    return 0


def _record_in_benchmark(run_dir: Path) -> None:
    added = append_run(run_dir)
    paths = write_board()
    console.print(f"[cyan]Benchmark:[/cyan] {added} row(s) from {run_dir.name} -> {paths['html']}")


def cmd_bench(args: argparse.Namespace) -> int:
    if args.bench_command == "add":
        run_dir = _resolve_run(args.run)
        if not run_dir or not run_dir.exists():
            console.print("[red]No run found.[/red]")
            return 1
        _record_in_benchmark(run_dir)
        return 0

    if args.bench_command == "trend":
        rows = trend(args.model_id)
        if not rows:
            console.print(f"[yellow]No history for {args.model_id}.[/yellow]")
            return 1
        table = Table(title=f"Trend - {args.model_id}", header_style="bold cyan")
        for column in ("run", "recorded", "score", "suites", "trunc", "errors"):
            table.add_column(column)
        for row in rows:
            table.add_row(
                row["run_id"],
                row["recorded"][:16].replace("T", " "),
                f"{row['score']:.2f}",
                " ".join(f"{s}={v:.2f}" for s, v in row["by_suite"].items()),
                str(row["truncated"]),
                str(row["errors"]),
            )
        console.print(table)
        return 0

    # board (default)
    history = load_history()
    if not history:
        console.print("[yellow]Benchmark history is empty. Run: python -m lab bench add[/yellow]")
        return 1
    board = current_board(history)
    paths = write_board()
    suites = sorted({s for row in board for s in row["by_suite"]})

    table = Table(title="Benchmark board", header_style="bold cyan")
    for column in ("#", "model", *suites, "score", "delta", "trunc", "last run"):
        table.add_column(column)
    for i, row in enumerate(board, 1):
        delta = row["delta"]
        delta_text = "-" if delta is None else f"[green]+{delta:.2f}[/green]" if delta > 0 else (
            f"[red]{delta:.2f}[/red]" if delta < 0 else "0.00"
        )
        table.add_row(
            str(i),
            row["label"],
            *[f"{row['by_suite'].get(s, 0):.2f}" for s in suites],
            f"[bold]{row['score']:.2f}[/bold]",
            delta_text,
            str(row["truncated"]),
            row["run_id"],
        )
    console.print(table)
    for name, path in paths.items():
        console.print(f"  {name}: {path}")
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    runs_dir = RESULTS_DIR / "runs"
    table = Table(title="Runs", header_style="bold cyan")
    for column in ("run", "records", "models", "suites"):
        table.add_column(column)
    for path in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        meta_path = path / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        table.add_row(
            path.name,
            str(meta.get("records", "?")),
            str(len(meta.get("models", []))),
            ",".join(s["name"] for s in meta.get("suites", [])),
        )
    console.print(table)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lab", description="AI model lab: fixed prompt suites across models")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("models", help="list the model registry and backend status").set_defaults(func=cmd_models)
    sub.add_parser("suites", help="list prompt suites").set_defaults(func=cmd_suites)
    sub.add_parser("runs", help="list previous runs").set_defaults(func=cmd_runs)

    run = sub.add_parser("run", help="run suites across models")
    run.add_argument("--suite", "-s", action="append", help="suite name (repeatable); default: all")
    run.add_argument("--models", "-m", action="append", help="model id / tag / backend glob (repeatable)")
    run.add_argument("--only", action="append", help="run only these prompt ids")
    run.add_argument("--repeats", type=int, default=1, help="repeat each prompt N times")
    run.add_argument("--run-id", help="custom run id")
    run.add_argument("--include-disabled", action="store_true", help="include models marked enabled: false")
    run.add_argument("--allow-code-exec", action="store_true", help="execute model generated Python for code checks")
    run.add_argument("--merge-into", help="fold these results into an existing run, replacing matching records")
    run.add_argument("--no-bench", action="store_true", help="do not record this run in the benchmark history")
    run.set_defaults(func=cmd_run)

    report = sub.add_parser("report", help="regenerate reports for a run")
    report.add_argument("run", nargs="?", help="run id or path (default: latest)")
    report.set_defaults(func=cmd_report)

    regrade = sub.add_parser("regrade", help="re-score stored answers against the current checks")
    regrade.add_argument("run", nargs="?", help="run id or path (default: latest)")
    regrade.add_argument("--out", help="target run id (default: <run>-regraded)")
    regrade.add_argument("--allow-code-exec", action="store_true")
    regrade.set_defaults(func=cmd_regrade)

    bench = sub.add_parser("bench", help="the benchmark history that survives individual runs")
    bench_sub = bench.add_subparsers(dest="bench_command")
    bench_add = bench_sub.add_parser("add", help="record a run in the benchmark history")
    bench_add.add_argument("run", nargs="?", help="run id or path (default: latest)")
    bench_sub.add_parser("board", help="current standings across all recorded runs")
    bench_trend = bench_sub.add_parser("trend", help="one model's scores over time")
    bench_trend.add_argument("model_id")
    bench.set_defaults(func=cmd_bench, bench_command="board")

    show = sub.add_parser("show", help="inspect raw answers for one prompt")
    show.add_argument("prompt_id")
    show.add_argument("--run", help="run id or path (default: latest)")
    show.add_argument("--model", help="only this model id")
    show.add_argument("--chars", type=int, default=1200, help="max characters of output to print")
    show.set_defaults(func=cmd_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

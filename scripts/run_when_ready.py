"""Wait for a backend to become usable, then run a lab command.

The Hugging Face router's quota behaves like a burst limit rather than a hard
monthly wall: it refuses for a while, then serves again. Rather than watching for
that by hand, this polls the same preflight the runner uses - a real generation,
not a reachability ping - and fires the run the moment it passes.

    python scripts/run_when_ready.py --model Qwen/Qwen2.5-72B-Instruct \\
        -- run -s guardrails -m hf/qwen2.5-72b --include-disabled

Everything after `--` is passed to `python -m lab` unchanged.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lab.backends import get_backend  # noqa: E402
from lab.config import load_env  # noqa: E402


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="hf")
    parser.add_argument("--model", help="model id to probe with")
    parser.add_argument("--every", type=int, default=600, help="seconds between probes")
    parser.add_argument("--give-up-after", type=float, default=12.0, help="hours before giving up")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- then the lab command")
    args = parser.parse_args()

    command = [a for a in args.command if a != "--"]
    if not command:
        parser.error("nothing to run: put the lab command after --")

    load_env()
    backend = get_backend(args.backend)
    deadline = time.time() + args.give_up_after * 3600
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        ok, detail = backend.available(args.model)
        print(f"[{stamp()}] probe {attempt}: {'ready' if ok else 'waiting'} - {detail}", flush=True)
        if ok:
            print(f"[{stamp()}] running: python -m lab {' '.join(command)}", flush=True)
            return subprocess.run([sys.executable, "-m", "lab", *command], cwd=ROOT).returncode
        time.sleep(args.every)

    print(f"[{stamp()}] gave up after {args.give_up_after}h - {args.backend} never became usable", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

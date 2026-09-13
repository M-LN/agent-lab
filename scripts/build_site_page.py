"""Thin wrapper: python scripts/build_site_page.py [--out PATH]

The page builder itself lives in lab/site.py so that `python -m lab publish` can
use it. This script stays for anyone who wants to regenerate only the page.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.site import DEFAULT_OUT, write_page  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    path = write_page(Path(args.out))
    print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

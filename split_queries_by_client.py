#!/usr/bin/env python3
"""
Split a queries_*.csv file into per-client CSVs.

Input format (header expected):
  client,client_slug,category,lang,query,notes

Output:
  <out-dir>/queries_<stamp>__<client_slug>.csv

This is useful for running collectors client-by-client.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List


def _sanitize_slug(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "client"
    # keep letters/digits/_/-
    s = re.sub(r"[^\w\-]+", "_", s, flags=re.UNICODE)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "client"


def main() -> int:
    ap = argparse.ArgumentParser(description="Split queries CSV into per-client CSVs.")
    ap.add_argument("--queries", type=Path, required=True, help="Path to queries_*.csv")
    ap.add_argument("--out", type=Path, default=None, help="Output directory (default: same as input parent)")
    ap.add_argument(
        "--stamp",
        default=None,
        help="Stamp used in output filenames (default: inferred from input name, else 'split')",
    )
    args = ap.parse_args()

    in_path: Path = args.queries
    if not in_path.exists():
        raise SystemExit(f"Input not found: {in_path}")

    out_dir = args.out or in_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = args.stamp
    if not stamp:
        # Try to infer from queries_YYYY-MM-DD.csv
        m = re.search(r"queries_(\d{4}-\d{2}-\d{2})\.csv$", in_path.name)
        stamp = m.group(1) if m else "split"

    with in_path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        if not r.fieldnames:
            raise SystemExit("Empty CSV (no header).")
        required = {"client", "client_slug", "category", "lang", "query"}
        missing = required - set(r.fieldnames)
        if missing:
            raise SystemExit(f"Missing required columns: {sorted(missing)}")

        rows_by_slug: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        for row in r:
            slug = _sanitize_slug(row.get("client_slug", ""))
            rows_by_slug[slug].append(row)

    # Preserve original header order
    fieldnames = list(rows_by_slug[next(iter(rows_by_slug))][0].keys()) if rows_by_slug else []

    written = 0
    for slug, rows in sorted(rows_by_slug.items(), key=lambda kv: kv[0]):
        out_path = out_dir / f"queries_{stamp}__{slug}.csv"
        with out_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        written += 1
        print(f"Wrote: {out_path} ({len(rows)} queries)")

    print(f"Done. Clients: {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


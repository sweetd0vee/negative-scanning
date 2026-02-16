#!/usr/bin/env python3
"""
Export per-client findings files with link + negative paragraph.

Input: findings_*.csv from collect_findings_yandex.py (or similar schema).
Output: out/findings__<client_slug>.md
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional


def parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def parse_result_date(s: str) -> Optional[dt.date]:
    """
    Parse a best-effort date from various formats:
    - YYYY-MM-DD
    - YYYYMMDD or YYYYMMDDThhmmss (Yandex XML)
    - Anything containing YYYY-MM-DD
    Returns None if cannot parse.
    """
    s = (s or "").strip()
    if not s:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return dt.date.fromisoformat(s)
    except Exception:
        pass
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", s)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    m2 = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m2:
        try:
            return dt.date.fromisoformat(m2.group(1))
        except Exception:
            return None
    return None


def normalize_for_slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("ё", "е")
    s = re.sub(r"[^0-9a-zа-я]+", "_", s, flags=re.IGNORECASE)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "client"


def read_rows(path: Path) -> Iterable[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            yield row


def build_output(
    rows: Iterable[Dict[str, str]],
    *,
    cutoff: dt.date,
    keep_undated: bool,
    dedupe: bool,
) -> Dict[str, List[Dict[str, str]]]:
    by_slug: Dict[str, List[Dict[str, str]]] = {}
    seen: Dict[str, set[str]] = {}
    for row in rows:
        raw_hint = (row.get("raw_hint") or "").strip()
        if raw_hint:
            continue

        client = (row.get("client") or "").strip()
        slug = (row.get("client_slug") or "").strip()
        if not slug:
            slug = normalize_for_slug(client)
        if not client:
            client = slug

        link = (row.get("link") or "").strip()
        if not link:
            continue

        title = (row.get("title") or "").strip()
        snippet = (row.get("snippet") or "").strip()
        paragraph = snippet or title
        if not paragraph:
            continue

        d_raw = (row.get("date") or "").strip()
        d = parse_result_date(d_raw)
        if d is None and not keep_undated:
            continue
        if d is not None and d < cutoff:
            continue

        if dedupe:
            seen.setdefault(slug, set())
            if link in seen[slug]:
                continue
            seen[slug].add(link)

        by_slug.setdefault(slug, []).append(
            {
                "client": client,
                "title": title,
                "link": link,
                "snippet": paragraph,
                "date_raw": d_raw,
                "date_obj": d.isoformat() if d else "",
                "source": (row.get("source") or "").strip(),
                "category": (row.get("category") or "").strip(),
                "lang": (row.get("lang") or "").strip(),
            }
        )
    return by_slug


def sort_items(items: List[Dict[str, str]]) -> None:
    def sort_key(item: Dict[str, str]) -> tuple:
        d = parse_result_date(item.get("date_raw") or "") if item.get("date_raw") else None
        if d is None:
            return (1, 0)
        return (0, -d.toordinal())

    items.sort(key=sort_key)


def write_client_file(
    *,
    path: Path,
    client: str,
    items: List[Dict[str, str]],
    source_file: str,
    cutoff: dt.date,
    max_age_days: int,
    generated: dt.date,
) -> None:
    lines: List[str] = []
    lines.append(f"# Findings for {client}")
    lines.append("")
    lines.append(f"- Generated: {generated.isoformat()}")
    lines.append(f"- Source file: {source_file}")
    lines.append(f"- Filter: last {max_age_days} days (cutoff {cutoff.isoformat()})")
    lines.append("")

    if not items:
        lines.append("No items after filter.")
        lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    for idx, item in enumerate(items, start=1):
        title = item.get("title") or item.get("link") or "Untitled"
        link = item.get("link") or ""
        date_str = item.get("date_raw") or "unknown date"
        source = item.get("source") or "unknown source"
        category = item.get("category") or "n/a"
        lang = item.get("lang") or "n/a"
        paragraph = item.get("snippet") or ""

        lines.append(f"{idx}. [{title}]({link})")
        lines.append(f"   Date: {date_str} | Source: {source} | Category: {category} | Lang: {lang}")
        lines.append("")
        lines.append(paragraph)
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Export per-client findings files (filtered by date).")
    ap.add_argument("--findings", type=Path, required=True, help="Path to findings_*.csv")
    ap.add_argument("--out", type=Path, default=None, help="Output directory (default: same as input parent)")
    ap.add_argument("--max-age-days", type=int, default=365 * 2, help="Keep only last N days (default: 730)")
    ap.add_argument("--end", type=parse_date, default=None, help="End date for filtering (default: today)")
    ap.add_argument("--keep-undated", action="store_true", help="Keep rows with unknown/parseable date")
    ap.add_argument("--dedupe", action="store_true", help="Deduplicate by link per client")
    args = ap.parse_args()

    in_path: Path = args.findings
    if not in_path.exists():
        raise SystemExit(f"Input not found: {in_path}")

    out_dir = args.out or in_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    ref_end = args.end or dt.date.today()
    cutoff = ref_end - dt.timedelta(days=max(args.max_age_days, 0))

    by_slug = build_output(
        read_rows(in_path),
        cutoff=cutoff,
        keep_undated=args.keep_undated,
        dedupe=args.dedupe,
    )

    for slug, items in sorted(by_slug.items(), key=lambda kv: kv[0]):
        sort_items(items)
        client_name = items[0].get("client") if items else slug
        out_path = out_dir / f"findings__{slug}.md"
        write_client_file(
            path=out_path,
            client=client_name or slug,
            items=items,
            source_file=in_path.name,
            cutoff=cutoff,
            max_age_days=args.max_age_days,
            generated=dt.date.today(),
        )
        print(f"Wrote: {out_path} ({len(items)} items)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

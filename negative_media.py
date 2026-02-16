#!/usr/bin/env python3
"""
Negative media screening helper.

What it does (offline, reproducible):
- Reads client names from a text file (one per line)
- Generates a structured package of RU/EN search queries for "negative media"
- Produces:
  - CSV with queries per client/category
  - Markdown report template with a section per client

What it does NOT do:
- It does not scrape websites or bypass access controls.
- It does not call search engines by default (to respect ToS and avoid keys in code).
  You can plug queries into your approved search provider / internal tooling.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import re
from pathlib import Path
from typing import Iterable, List, Sequence


@dataclasses.dataclass(frozen=True)
class QuerySpec:
    client: str
    category: str
    lang: str  # "ru" | "en" | "any"
    query: str
    notes: str = ""


def _today() -> dt.date:
    return dt.date.today()


def parse_date(s: str) -> dt.date:
    # ISO: YYYY-MM-DD
    return dt.date.fromisoformat(s)


def load_clients(path: Path) -> List[str]:
    raw = path.read_text(encoding="utf-8")
    clients: List[str] = []
    for line in raw.splitlines():
        name = line.strip()
        if not name:
            continue
        clients.append(name)
    if not clients:
        raise ValueError(f"No clients found in {path}")
    return clients


def normalize_for_slug(s: str) -> str:
    s = s.strip().lower()
    s = s.replace("ё", "е")
    # keep letters/digits, turn everything else into underscore
    s = re.sub(r"[^0-9a-zа-я]+", "_", s, flags=re.IGNORECASE)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "client"


def or_block(terms: Sequence[str]) -> str:
    # quote multiword terms; keep operators/domains as-is if already contain ":" or "." or quotes
    cooked: List[str] = []
    for t in terms:
        t = t.strip()
        if not t:
            continue
        if t.startswith('"') and t.endswith('"'):
            cooked.append(t)
            continue
        if any(ch in t for ch in [":", ".", '"', "(", ")"]):
            cooked.append(t)
            continue
        if " " in t:
            cooked.append(f'"{t}"')
        else:
            cooked.append(t)
    if not cooked:
        return ""
    if len(cooked) == 1:
        return cooked[0]
    return "(" + " OR ".join(cooked) + ")"


def phrase(s: str) -> str:
    # Exact phrase match wrapper for search providers.
    # IMPORTANT: avoid nested quotes inside the phrase (they break many engines).
    s = s.strip()
    s = s.replace("«", "").replace("»", "")
    s = s.replace('"', "")
    s = re.sub(r"\s+", " ", s).strip()
    return f'"{s}"'


_LEGAL_FORMS_RE = re.compile(
    r"^\s*(ООО|ОАО|ЗАО|ПАО|АО|СООО|ИП|УП|ЧУП|ГУП|МУП|"
    r"Унитарное предприятие)\s+",
    flags=re.IGNORECASE,
)


def entity_variants(client: str) -> List[str]:
    """
    Generate safer variants for searching:
    - remove nested quotes «» ""
    - include optional variant without legal form prefix (ООО/ОАО/...)
    """
    raw = client.strip()
    cleaned = raw.replace("«", "").replace("»", "").replace('"', "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    variants: List[str] = []
    if cleaned:
        variants.append(cleaned)

    # Variant without legal form prefix
    m = _LEGAL_FORMS_RE.match(cleaned)
    if m:
        tail = cleaned[m.end() :].strip()
        if tail and tail not in variants:
            variants.append(tail)

    # Deduplicate preserving order
    out: List[str] = []
    seen = set()
    for v in variants:
        key = v.lower().replace("ё", "е")
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def build_query_specs(clients: Sequence[str]) -> List[QuerySpec]:
    # RU categories (legal entities focus)
    ru_categories = {
        "sanctions": [
            "санкции",
            '"санкционный список"',
            "SDN",
            "OFAC",
            '"EU sanctions"',
            '"UK sanctions"',
            '"UN sanctions"',
        ],
        "litigation": [
            "суд",
            "иск",
            "арбитраж",
            '"судебное решение"',
            '"взыскал"',
            '"взыскание"',
        ],
        "enforcement_debts": [
            "ФССП",
            '"исполнительное производство"',
            "задолженность",
            "долг",
            "взыскание",
        ],
        "bankruptcy": [
            "банкротство",
            "ЕФРСБ",
            "федресурс",
            "наблюдение",
            '"конкурсное производство"',
            '"реестр требований кредиторов"',
        ],
        "regulatory": [
            "ФАС",
            "ЦБ",
            "Росфинмониторинг",
            "Роспотребнадзор",
            "Росприроднадзор",
            "прокуратура",
        ],
        "fraud_corruption": [
            "мошенничество",
            "коррупция",
            "взятка",
            '"легализация доходов"',
            '"отмывание денег"',
        ],
        "reputation_reviews": [
            "отзыв",
            "жалоба",
            "скандал",
            "претензия",
            "обман",
            "невыплата",
        ],
    }

    # EN categories (international traces)
    en_categories = {
        "sanctions": [
            "sanctions",
            "SDN",
            "OFAC",
            '"EU sanctions"',
            '"UK sanctions"',
            '"UN sanctions"',
        ],
        "litigation": ["lawsuit", "court", '"court decision"', "fine", "penalty"],
        "fraud_corruption": ["fraud", "corruption", "bribery", '"money laundering"'],
        "reputation": ["scandal", "complaint", "reviews"],
    }

    # Source-specific "precision" queries (often higher signal)
    ru_precision = {
        "courts_arbitr": ["site:kad.arbitr.ru"],
        "courts_general": ["site:sudrf.ru"],
        "enforcement": ["site:fssp.gov.ru"],
        "fedresurs": ["site:fedresurs.ru", "site:bankrot.fedresurs.ru"],
    }

    specs: List[QuerySpec] = []
    for client in clients:
        variants = entity_variants(client)
        entity_q = or_block([phrase(v) for v in variants])

        for cat, kws in ru_categories.items():
            specs.append(
                QuerySpec(
                    client=client,
                    category=cat,
                    lang="ru",
                    query=f"{entity_q} {or_block(kws)}",
                    notes="RU general web/news query",
                )
            )

        for cat, kws in en_categories.items():
            specs.append(
                QuerySpec(
                    client=client,
                    category=cat,
                    lang="en",
                    query=f"{entity_q} {or_block(kws)}",
                    notes="EN general web/news query (for international trace)",
                )
            )

        for cat, ops in ru_precision.items():
            for op in ops:
                specs.append(
                    QuerySpec(
                        client=client,
                        category=cat,
                        lang="any",
                        query=f"{op} {entity_q}",
                        notes="High-precision query for a specific registry/court site",
                    )
                )

    return specs


def write_queries_csv(specs: Iterable[QuerySpec], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["client", "client_slug", "category", "lang", "query", "notes"])
        for s in specs:
            w.writerow([s.client, normalize_for_slug(s.client), s.category, s.lang, s.query, s.notes])


def write_report_template(clients: Sequence[str], start: dt.date, end: dt.date, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    lines.append("# Negative media screening report (template)")
    lines.append("")
    lines.append(f"**Period:** {start.isoformat()} — {end.isoformat()}")
    lines.append("")
    lines.append("## Method notes")
    lines.append("- This is a template. Fill with sourced findings from your approved tooling/provider.")
    lines.append("- Separate **facts** (official registers / court docs / regulators / reputable media) from **signals** (reviews/UGC).")
    lines.append("- Track **match confidence** to avoid name collisions.")
    lines.append("")

    for idx, client in enumerate(clients, start=1):
        lines.append(f"## {idx}. {client}")
        lines.append("")
        lines.append("### Entity profile (for disambiguation)")
        lines.append("- **Name variants**: ")
        lines.append("- **Jurisdiction / country**: ")
        lines.append("- **Identifiers** (if available): INN/OGRN/UNP, etc.")
        lines.append("- **Affiliations**: ")
        lines.append("")
        lines.append("### Summary (last year)")
        lines.append("- **Negative found**: yes/no")
        lines.append("- **Key items**: ")
        lines.append("- **Recommendation**: monitor / EDD / escalation")
        lines.append("")
        lines.append("### Findings register")
        lines.append("")
        lines.append("| ID | Date | Source/domain | Source type | Title / gist | Negative category | Match confidence | Reliability | Link | Notes |")
        lines.append("|---:|------|---------------|-------------|--------------|------------------|-----------------|-------------|------|------|")
        lines.append("| 1 |  |  |  |  |  | high/med/low | official/media/ugc |  |  |")
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate negative-media query package & report template.")
    ap.add_argument("--clients", type=Path, default=Path("clients.txt"), help="Path to clients.txt (one per line).")
    ap.add_argument(
        "--start",
        type=parse_date,
        default=None,
        help="Start date (YYYY-MM-DD). Default: today-365d",
    )
    ap.add_argument(
        "--end",
        type=parse_date,
        default=None,
        help="End date (YYYY-MM-DD). Default: today",
    )
    ap.add_argument("--out", type=Path, default=Path("out"), help="Output directory.")
    args = ap.parse_args()

    end = args.end or _today()
    start = args.start or (end - dt.timedelta(days=365))
    if start > end:
        raise SystemExit("--start must be <= --end")

    clients = load_clients(args.clients)
    specs = build_query_specs(clients)

    stamp = end.isoformat()
    queries_csv = args.out / f"queries_{stamp}.csv"
    report_md = args.out / f"report_template_{stamp}.md"

    write_queries_csv(specs, queries_csv)
    write_report_template(clients, start, end, report_md)

    print(f"Wrote: {queries_csv}")
    print(f"Wrote: {report_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Collect search results for queries via Yandex Cloud Search API.

Auth:
Yandex Cloud often supports API keys with the header:
  Authorization: Api-Key <API_KEY>

Some setups use IAM tokens:
  Authorization: Bearer <IAM_TOKEN>

Since org setups differ, this script supports both auth schemes.

This script intentionally:
- does NOT scrape SERPs directly
- requires you to supply credentials (env vars or flags)

Outputs:
- CSV with flattened organic results (one row per result)
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from env_utils import load_dotenv
from http_utils import ssl_context


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
    # ISO
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return dt.date.fromisoformat(s)
    except Exception:
        pass
    # XML style: 20230914T195316 or 20230914
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", s)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    # Embedded ISO
    m2 = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m2:
        try:
            return dt.date.fromisoformat(m2.group(1))
        except Exception:
            return None
    return None

_LEGAL_FORMS_RE = re.compile(
    r"^\s*(ООО|ОАО|ЗАО|ПАО|АО|СООО|ИП|УП|ЧУП|ГУП|МУП|Унитарное предприятие)\s+",
    flags=re.IGNORECASE,
)


def _normalize_match_text(s: str) -> str:
    s = (s or "").strip().lower().replace("ё", "е")
    s = s.replace("«", "").replace("»", "").replace('"', "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def entity_variants(client: str) -> List[str]:
    """
    Safer name variants for matching in titles/snippets.
    Mirrors logic from negative_media.py but kept local to avoid cross-imports.
    """
    cleaned = _normalize_match_text(client)
    variants: List[str] = []
    if cleaned:
        variants.append(cleaned)

    m = _LEGAL_FORMS_RE.match(cleaned)
    if m:
        tail = cleaned[m.end() :].strip()
        if tail and tail not in variants:
            variants.append(tail)

    return variants


def matches_client_text(*, client: str, title: str, snippet: str, where: str) -> bool:
    """
    Check that at least one client variant appears in title/snippet.
    This is a best-effort heuristic and does NOT fetch the destination page.
    """
    variants = entity_variants(client)
    if not variants:
        return False
    t = _normalize_match_text(title)
    s = _normalize_match_text(snippet)
    if where == "title":
        hay = t
    elif where == "snippet":
        hay = s
    else:
        hay = (t + " " + s).strip()
    return any(v and v in hay for v in variants)


def read_queries_csv(path: Path) -> Iterable[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            yield row


def count_queries_csv(path: Path, *, include_langs: set[str], max_queries: int) -> int:
    """
    Count how many rows will be processed given lang filter and max_queries.
    Re-reads the CSV (cheap vs network calls; avoids keeping everything in memory).
    """
    n = 0
    for row in read_queries_csv(path):
        if row.get("lang") not in include_langs:
            continue
        n += 1
        if max_queries and n >= max_queries:
            break
    return n


RESULT_FIELDS = [
    "client",
    "client_slug",
    "category",
    "lang",
    "query",
    "rank",
    "title",
    "link",
    "snippet",
    "date",
    "source",
    "provider",
    "endpoint",
    "raw_hint",
]


def write_results_csv(rows: List[Dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = RESULT_FIELDS
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def write_results_xlsx(rows: List[Dict[str, str]], path: Path) -> None:
    try:
        from openpyxl import Workbook
    except Exception as e:  # pragma: no cover - runtime dependency
        raise SystemExit(
            "openpyxl is required to write XLSX files. "
            "Install it with: pip install openpyxl"
        ) from e

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "findings"
    ws.append(RESULT_FIELDS)
    for row in rows:
        ws.append([row.get(k, "") for k in RESULT_FIELDS])
    wb.save(path)


def _sanitize_slug(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "client"
    s = re.sub(r"[^\w\-]+", "_", s, flags=re.UNICODE)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "client"


def write_per_client_csv(rows: List[Dict[str, str]], out_dir: Path, *, write_xlsx: bool) -> List[Path]:
    by_slug: Dict[str, List[Dict[str, str]]] = {}
    for row in rows:
        slug = _sanitize_slug(row.get("client_slug", ""))
        by_slug.setdefault(slug, []).append(row)

    out_paths: List[Path] = []
    for slug, items in sorted(by_slug.items(), key=lambda kv: kv[0]):
        # Keep only actual results (ranked rows), omit audit rows.
        result_rows = [r for r in items if r.get("rank")]
        out_path_csv = out_dir / f"findings_yandex__{slug}.csv"
        write_results_csv(result_rows, out_path_csv)
        out_paths.append(out_path_csv)
        if write_xlsx:
            out_path_xlsx = out_dir / f"findings_yandex__{slug}.xlsx"
            write_results_xlsx(result_rows, out_path_xlsx)
            out_paths.append(out_path_xlsx)

    return out_paths

def _lower_camel(s: str) -> str:
    if not s:
        return s
    return s[0].lower() + s[1:]


def candidate_endpoints(endpoint_or_base: str, rpc_method: str) -> List[str]:
    """
    Accepts either:
    - full endpoint URL (with a path), returned as-is
    - base URL (scheme+host only), expanded into likely REST paths.

    rpc_method example: "WebSearch/search"
    """
    endpoint_or_base = endpoint_or_base.strip()
    rpc_method = (rpc_method or "").strip().strip("/")
    parsed = urllib.parse.urlparse(endpoint_or_base)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid endpoint/base URL: {endpoint_or_base!r}")

    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path or ""
    if path not in ("", "/"):
        return [endpoint_or_base.rstrip("/")]

    svc, meth = ("", "")
    parts = rpc_method.split("/", 1)
    if len(parts) == 2:
        svc, meth = parts[0], parts[1]

    svc_lc = _lower_camel(svc) if svc else ""
    meth_lc = _lower_camel(meth) if meth else ""

    cands: List[str] = []
    # Historical guess (kept as fallback)
    cands.append(base + "/v2/web/search")
    # Service-prefixed REST-ish paths
    cands.append(base + "/search-api/v2/web/search")
    cands.append(base + "/searchapi/v2/web/search")

    # RPC-transcoded style: <resource>:<method>
    if svc_lc and meth_lc:
        cands.append(base + f"/search-api/v2/{svc_lc}:{meth_lc}")
        cands.append(base + f"/searchapi/v2/{svc_lc}:{meth_lc}")
        cands.append(base + f"/v2/{svc_lc}:{meth_lc}")

        # Slash style variants
        cands.append(base + f"/search-api/v2/{svc}/{meth}")
        cands.append(base + f"/search-api/v2/{svc_lc}/{meth_lc}")
        cands.append(base + f"/v2/{svc}/{meth}")
        cands.append(base + f"/v2/{svc_lc}/{meth_lc}")

    # Deduplicate preserving order
    out: List[str] = []
    seen = set()
    for u in cands:
        u = u.rstrip("/")
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def yandex_cloud_search(
    *,
    auth_scheme: str,
    token: str,
    folder_id: Optional[str],
    query: str,
    endpoint: str,
    search_type: int,
    timeout_s: int = 60,
    topk: int = 10,
) -> Dict:
    """
    Minimal POST request with JSON body.

    IMPORTANT:
    - Exact schema/headers may differ by API version.
    - Endpoint MUST be set to the correct one from your Yandex Cloud docs.
    """
    # Yandex Cloud Search API expects `query` to be a structured message
    # (SearchQuery), not a plain string.
    # Required fields observed from API validation errors:
    # - query.search_type (enum, numeric works reliably)
    # - query.query_text (string)
    body = {"query": {"search_type": search_type, "query_text": query}, "pageSize": topk}
    data = json.dumps(body).encode("utf-8")

    if auth_scheme not in {"api-key", "bearer"}:
        raise ValueError("auth_scheme must be 'api-key' or 'bearer'")
    auth_value = f"Api-Key {token}" if auth_scheme == "api-key" else f"Bearer {token}"

    headers = {
        "Content-Type": "application/json",
        "Authorization": auth_value,
        "User-Agent": "negative-media-helper/1.0",
    }
    if folder_id:
        headers["x-folder-id"] = folder_id

    req = urllib.request.Request(
        endpoint,
        data=data,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=ssl_context()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise RuntimeError(f"HTTP {e.code} {e.reason}; body={body[:500]}") from e


def parse_yandex_cloud_results(payload: Dict) -> List[Dict[str, str]]:
    """
    Best-effort extraction from a JSON response.
    Since response shape can differ, we look through common keys.
    """
    def _text(el: Optional[ET.Element]) -> str:
        if el is None:
            return ""
        # Preserve nested <hlword> etc.
        s = "".join(el.itertext())
        return " ".join(s.split()).strip()

    def _fmt_modtime_date(s: str) -> str:
        """
        Yandex XML often uses e.g. 20230914T195316.
        We store only date portion in CSV (YYYY-MM-DD) when possible.
        """
        s = (s or "").strip()
        if not s:
            return ""
        # 20230914T195316 -> 2023-09-14
        try:
            if len(s) >= 8 and s[:8].isdigit():
                y, m, d = s[:4], s[4:6], s[6:8]
                return f"{y}-{m}-{d}"
        except Exception:
            return ""
        return ""

    def parse_rawdata_xml(rawdata_b64: str) -> List[Dict[str, str]]:
        """
        Yandex Cloud Search sometimes returns JSON with {"rawData": "<base64(xml)>"}.
        We decode base64 → parse XML → extract <doc> elements into our normalized schema.
        """
        b64 = (rawdata_b64 or "").strip()
        if not b64:
            return []
        # tolerate missing padding
        pad = (-len(b64)) % 4
        if pad:
            b64 = b64 + ("=" * pad)
        try:
            xml_bytes = base64.b64decode(b64, validate=False)
        except Exception as e:
            raise RuntimeError(f"Failed to base64-decode rawData: {e}") from e
        try:
            xml_text = xml_bytes.decode("utf-8", errors="replace")
        except Exception:
            xml_text = xml_bytes.decode(errors="replace")
        try:
            root = ET.fromstring(xml_text)
        except Exception as e:
            # Keep a short prefix to debug malformed XML without dumping megabytes
            prefix = xml_text[:300].replace("\n", "\\n")
            raise RuntimeError(f"Failed to parse rawData XML: {e}; prefix={prefix!r}") from e

        out: List[Dict[str, str]] = []
        # Most common path: ...<doc>...</doc>
        for doc in root.findall(".//doc"):
            url = _text(doc.find("url"))
            title = _text(doc.find("title"))

            # Snippet: prefer up to first 3 <passage>, else <headline>
            passages = []
            for p in doc.findall(".//passages/passage")[:3]:
                txt = _text(p)
                if txt:
                    passages.append(txt)
            snippet = " ".join(passages).strip() or _text(doc.find("headline"))

            domain = _text(doc.find("domain"))
            modtime = _fmt_modtime_date(_text(doc.find("modtime")))

            out.append(
                {
                    "link": url,
                    "title": title,
                    "snippet": snippet,
                    "date": modtime,
                    "source": domain,
                }
            )
        return out

    # Special-case: rawData base64(XML)
    if isinstance(payload, dict):
        rawdata = payload.get("rawData")
        if isinstance(rawdata, str) and rawdata.strip():
            return parse_rawdata_xml(rawdata)

    def looks_like_result_dict(d: Dict[str, Any]) -> bool:
        # Heuristic: a "result" usually has at least a URL/link and/or title/snippet.
        urlish = any(k in d for k in ("link", "url", "docUrl", "doc_url", "targetUrl"))
        textish = any(k in d for k in ("title", "name", "snippet", "textSnippet", "passage", "description"))
        return urlish or textish

    def find_candidate_list(obj: Any, *, max_depth: int = 6) -> List[Dict[str, Any]]:
        """
        Recursively search for the first list of dicts that looks like search results.
        Keeps it conservative to avoid accidentally treating unrelated arrays as results.
        """
        if max_depth <= 0:
            return []
        if isinstance(obj, dict):
            # Prefer common container keys first
            for key in ("results", "items", "documents", "docs", "organic_results", "entries", "data"):
                val = obj.get(key)
                if isinstance(val, list) and val and all(isinstance(x, dict) for x in val):
                    if any(looks_like_result_dict(x) for x in val[:3]):
                        return val  # type: ignore[return-value]
            # Otherwise, walk nested values
            for v in obj.values():
                found = find_candidate_list(v, max_depth=max_depth - 1)
                if found:
                    return found
            return []
        if isinstance(obj, list):
            for el in obj:
                found = find_candidate_list(el, max_depth=max_depth - 1)
                if found:
                    return found
            return []
        return []

    candidates: List[Dict[str, Any]] = []
    # Fast-path: top-level known keys
    for key in ("results", "items", "documents", "docs", "organic_results"):
        val = payload.get(key)
        if isinstance(val, list) and val and all(isinstance(x, dict) for x in val):
            candidates = val  # type: ignore[assignment]
            break
    # Fallback: recursive scan
    if not candidates:
        candidates = find_candidate_list(payload)

    out: List[Dict[str, str]] = []
    for item in candidates:
        link = str(item.get("link") or item.get("url") or item.get("docUrl") or "")
        title = str(item.get("title") or item.get("name") or "")
        snippet = str(item.get("snippet") or item.get("textSnippet") or item.get("passage") or "")
        date = str(item.get("date") or item.get("published") or item.get("publishedAt") or "")
        source = str(item.get("source") or item.get("domain") or "")
        out.append({"link": link, "title": title, "snippet": snippet, "date": date, "source": source})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Collect search results for queries via Yandex Cloud Search API.")
    ap.add_argument("--queries", type=Path, required=True, help="Path to queries_*.csv generated by negative_media.py")
    ap.add_argument("--out", type=Path, default=Path("out"), help="Output directory")
    ap.add_argument("--sleep", type=float, default=1.0, help="Delay between requests (seconds)")
    ap.add_argument("--max-queries", type=int, default=0, help="Process only first N queries (0 = all)")
    ap.add_argument("--langs", default="ru,en,any", help="Comma-separated langs to include (default: ru,en,any)")
    ap.add_argument("--num", type=int, default=10, help="Top results per query (best-effort)")
    ap.add_argument(
        "--emit-empty",
        action="store_true",
        help="If a query returns 0 results, emit an audit row with raw_hint=NO_RESULTS (useful for debugging).",
    )
    ap.add_argument(
        "--require-client-match",
        action="store_true",
        help="Filter results: keep only rows where the client name appears in title/snippet (best-effort, no page fetching).",
    )
    ap.add_argument(
        "--require-client-in",
        choices=["title", "snippet", "title_snippet"],
        default="title_snippet",
        help="Where to check client name when --require-client-match is enabled (default: title_snippet).",
    )
    ap.add_argument(
        "--emit-nonmatching",
        action="store_true",
        help="If --require-client-match is enabled, emit audit rows for dropped results with raw_hint=NO_CLIENT_MATCH.",
    )
    ap.add_argument(
        "--max-age-days",
        type=int,
        default=365 * 2,
        help="Keep only results with date within the last N days (default: 730). Use --no-age-filter to disable.",
    )
    ap.add_argument(
        "--no-xlsx",
        action="store_true",
        help="Do not write XLSX outputs (only CSV).",
    )
    ap.add_argument(
        "--no-age-filter",
        action="store_true",
        help="Disable recency filtering by date.",
    )
    ap.add_argument(
        "--keep-undated",
        action="store_true",
        help="If set, keep results where date cannot be parsed (otherwise drop them when age filter is active).",
    )
    ap.add_argument(
        "--emit-dropped-age",
        action="store_true",
        help="Emit audit rows for results dropped by age filter with raw_hint=DROPPED_OLD or DROPPED_UNDATED.",
    )
    ap.add_argument(
        "--no-per-client",
        dest="no_per_client",
        action="store_true",
        help="Do not write per-client CSV files.",
    )
    ap.add_argument(
        "--no-per-client-md",
        dest="no_per_client",
        action="store_true",
        help="Deprecated: use --no-per-client.",
    )
    ap.add_argument(
        "--progress",
        action="store_true",
        help="Show a simple progress bar in the terminal.",
    )
    ap.add_argument(
        "--log",
        action="store_true",
        help="Log one line per processed query (client/category/lang).",
    )
    ap.add_argument(
        "--debug-payload",
        type=Path,
        default=None,
        help="If set, write the first successful JSON payload to this file (for debugging response shape).",
    )
    ap.add_argument(
        "--search-type",
        type=int,
        default=1,
        help=(
            "Numeric SearchQuery.search_type enum value. "
            "Default=1 (commonly WEB in protobuf enums). Adjust per your docs if needed."
        ),
    )
    ap.add_argument("--start", type=parse_date, default=None, help="Start date (stored in output stamp only)")
    ap.add_argument("--end", type=parse_date, default=None, help="End date (stored in output stamp only)")

    # Yandex Cloud flags
    ap.add_argument(
        "--auth-scheme",
        choices=["api-key", "bearer"],
        default="api-key",
        help="Auth scheme for Authorization header (default: api-key)",
    )
    ap.add_argument(
        "--api-key",
        default=None,
        help="Yandex Cloud API key (or env YANDEX_API_KEY). Do NOT put secrets into files.",
    )
    ap.add_argument(
        "--iam-token",
        default=None,
        help="Yandex Cloud IAM token (or env YANDEX_IAM_TOKEN) if using --auth-scheme bearer",
    )
    ap.add_argument("--folder-id", default=None, help="Yandex Cloud folderId (or env YANDEX_FOLDER_ID) if required")
    ap.add_argument(
        "--endpoint",
        default=None,
        help=(
            "Yandex Cloud Search endpoint OR base URL (or env YANDEX_CLOUD_SEARCH_ENDPOINT). "
            "If base URL is given, script expands it using --rpc."
        ),
    )
    ap.add_argument(
        "--rpc",
        default=None,
        help='RPC method name from docs, e.g. "WebSearch/search" (or env YANDEX_CLOUD_RPC). Used when --endpoint is base URL.',
    )

    args = ap.parse_args()

    # Auto-load local .env if present (does not override existing env vars)
    load_dotenv()

    include_langs = {x.strip() for x in args.langs.split(",") if x.strip()}
    stamp = (args.end or dt.date.today()).isoformat()
    out_csv = args.out / f"findings_yandex_{stamp}.csv"

    rows_out: List[Dict[str, str]] = []
    processed = 0
    total_to_process = count_queries_csv(args.queries, include_langs=include_langs, max_queries=args.max_queries)
    debug_written = False
    ref_end = args.end or dt.date.today()
    cutoff = ref_end - dt.timedelta(days=max(args.max_age_days, 0))

    def _print_progress(i: int) -> None:
        if not args.progress:
            return
        total = max(total_to_process, 0)
        if total <= 0:
            print(f"\rProcessed {i} queries...", end="", flush=True)
            return
        width = 28
        filled = int(width * (i / total))
        bar = "#" * filled + "-" * (width - filled)
        pct = int((100 * i) / total)
        print(f"\r[{bar}] {i}/{total} ({pct}%)", end="", flush=True)

    endpoint = args.endpoint or os.environ.get("YANDEX_CLOUD_SEARCH_ENDPOINT") or "https://searchapi.api.cloud.yandex.net/v2/web/search"
    rpc = args.rpc or os.environ.get("YANDEX_CLOUD_RPC") or "WebSearch/search"
    endpoints = candidate_endpoints(endpoint, rpc)
    folder_id = args.folder_id or os.environ.get("YANDEX_FOLDER_ID")

    if args.auth_scheme == "api-key":
        token = args.api_key or os.environ.get("YANDEX_API_KEY")
        if not token:
            raise SystemExit("Missing API key. Provide --api-key or set YANDEX_API_KEY.")
    else:
        token = args.iam_token or os.environ.get("YANDEX_IAM_TOKEN")
        if not token:
            raise SystemExit("Missing IAM token. Provide --iam-token or set YANDEX_IAM_TOKEN.")

    for row in read_queries_csv(args.queries):
        if row.get("lang") not in include_langs:
            continue
        if args.max_queries and processed >= args.max_queries:
            break
        processed += 1
        if args.log:
            print(
                f"[{processed}/{total_to_process}] {row.get('client_slug','')} {row.get('category','')} {row.get('lang','')}",
                flush=True,
            )
        _print_progress(processed)

        q = row["query"]
        provider = "yandex-cloud"

        try:
            last_err: Optional[Exception] = None
            payload: Optional[Dict] = None
            used_endpoint = ""
            for ep in endpoints:
                try:
                    used_endpoint = ep
                    payload = yandex_cloud_search(
                        auth_scheme=args.auth_scheme,
                        token=token,  # type: ignore[arg-type]
                        folder_id=folder_id,
                        query=q,
                        endpoint=ep,
                        search_type=args.search_type,
                        topk=args.num,
                    )
                    last_err = None
                    break
                except Exception as e:
                    # If we got an auth error, report immediately (endpoint likely correct).
                    msg = str(e)
                    if "HTTP 401" in msg or "HTTP 403" in msg:
                        raise
                    last_err = e
                    continue

            if last_err is not None or payload is None:
                raise last_err or RuntimeError("Unknown error calling Yandex Cloud Search API")

            if isinstance(payload, dict) and payload.get("error"):
                # Some gateways return JSON error objects; keep it auditable as a failure.
                raise RuntimeError(f"API returned error payload: {json.dumps(payload, ensure_ascii=False)[:800]}")

            if args.debug_payload and not debug_written:
                args.debug_payload.parent.mkdir(parents=True, exist_ok=True)
                args.debug_payload.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                debug_written = True

            docs = parse_yandex_cloud_results(payload)
            items = docs
        except Exception as e:
            # Keep an error row so the run is auditable.
            rows_out.append(
                {
                    "client": row["client"],
                    "client_slug": row["client_slug"],
                    "category": row["category"],
                    "lang": row["lang"],
                    "query": q,
                    "rank": "",
                    "title": "",
                    "link": "",
                    "snippet": "",
                    "date": "",
                    "source": "",
                    "provider": provider,
                    "endpoint": used_endpoint or ("|".join(endpoints[:5]) + ("|..." if len(endpoints) > 5 else "")),
                    "raw_hint": f"ERROR: {type(e).__name__}: {e}",
                }
            )
            time.sleep(max(args.sleep, 0.0))
            continue

        if not items and args.emit_empty:
            rows_out.append(
                {
                    "client": row["client"],
                    "client_slug": row["client_slug"],
                    "category": row["category"],
                    "lang": row["lang"],
                    "query": q,
                    "rank": "",
                    "title": "",
                    "link": "",
                    "snippet": "",
                    "date": "",
                    "source": "",
                    "provider": provider,
                    "endpoint": used_endpoint,
                    "raw_hint": f"NO_RESULTS; payload_keys={','.join(sorted(payload.keys())) if isinstance(payload, dict) else type(payload).__name__}",
                }
            )

        filtered_items: List[Dict[str, str]] = []
        if args.require_client_match:
            for item in items:
                title = str(item.get("title") or "")
                snippet = str(item.get("snippet") or "")
                if matches_client_text(client=row["client"], title=title, snippet=snippet, where=args.require_client_in):
                    filtered_items.append(item)
                elif args.emit_nonmatching:
                    rows_out.append(
                        {
                            "client": row["client"],
                            "client_slug": row["client_slug"],
                            "category": row["category"],
                            "lang": row["lang"],
                            "query": q,
                            "rank": "",
                            "title": title,
                            "link": str(item.get("link") or ""),
                            "snippet": snippet,
                            "date": str(item.get("date") or ""),
                            "source": str(item.get("source") or ""),
                            "provider": provider,
                            "endpoint": used_endpoint,
                            "raw_hint": "NO_CLIENT_MATCH",
                        }
                    )
        else:
            filtered_items = items

        # Age / recency filter (default: last 2 years)
        if not args.no_age_filter and args.max_age_days > 0:
            kept: List[Dict[str, str]] = []
            for item in filtered_items:
                title = str(item.get("title") or "")
                snippet = str(item.get("snippet") or "")
                d_raw = str(item.get("date") or "")
                d = parse_result_date(d_raw)
                if d is None:
                    if args.keep_undated:
                        kept.append(item)
                    elif args.emit_dropped_age:
                        rows_out.append(
                            {
                                "client": row["client"],
                                "client_slug": row["client_slug"],
                                "category": row["category"],
                                "lang": row["lang"],
                                "query": q,
                                "rank": "",
                                "title": title,
                                "link": str(item.get("link") or ""),
                                "snippet": snippet,
                                "date": d_raw,
                                "source": str(item.get("source") or ""),
                                "provider": provider,
                                "endpoint": used_endpoint,
                                "raw_hint": "DROPPED_UNDATED",
                            }
                        )
                    continue
                if d >= cutoff:
                    kept.append(item)
                elif args.emit_dropped_age:
                    rows_out.append(
                        {
                            "client": row["client"],
                            "client_slug": row["client_slug"],
                            "category": row["category"],
                            "lang": row["lang"],
                            "query": q,
                            "rank": "",
                            "title": title,
                            "link": str(item.get("link") or ""),
                            "snippet": snippet,
                            "date": d_raw,
                            "source": str(item.get("source") or ""),
                            "provider": provider,
                            "endpoint": used_endpoint,
                            "raw_hint": f"DROPPED_OLD; cutoff={cutoff.isoformat()}",
                        }
                    )
            filtered_items = kept

        # If filtering is enabled (client match and/or age), it's easy to end up with an empty CSV (header-only).
        # Emit an audit row by default so the run is still explainable.
        if not filtered_items and (args.emit_empty or args.require_client_match or (not args.no_age_filter and args.max_age_days > 0)):
            rows_out.append(
                {
                    "client": row["client"],
                    "client_slug": row["client_slug"],
                    "category": row["category"],
                    "lang": row["lang"],
                    "query": q,
                    "rank": "",
                    "title": "",
                    "link": "",
                    "snippet": "",
                    "date": "",
                    "source": "",
                    "provider": provider,
                    "endpoint": used_endpoint,
                    "raw_hint": "NO_RESULTS_AFTER_FILTER",
                }
            )

        for rank, item in enumerate(filtered_items, start=1):
            rows_out.append(
                {
                    "client": row["client"],
                    "client_slug": row["client_slug"],
                    "category": row["category"],
                    "lang": row["lang"],
                    "query": q,
                    "rank": str(rank),
                    "title": str(item.get("title") or ""),
                    "link": str(item.get("link") or ""),
                    "snippet": str(item.get("snippet") or ""),
                    "date": str(item.get("date") or ""),
                    "source": str(item.get("source") or ""),
                    "provider": provider,
                    "endpoint": used_endpoint,
                    "raw_hint": "",
                }
            )

        time.sleep(max(args.sleep, 0.0))

    if args.progress:
        print("")  # newline after progress bar

    write_results_csv(rows_out, out_csv)
    print(f"Wrote: {out_csv} ({len(rows_out)} rows from {processed} queries)")
    if not args.no_xlsx:
        out_xlsx = args.out / f"findings_yandex_{stamp}.xlsx"
        write_results_xlsx(rows_out, out_xlsx)
        print(f"Wrote: {out_xlsx}")

    if not args.no_per_client:
        per_client_paths = write_per_client_csv(rows_out, args.out, write_xlsx=(not args.no_xlsx))
        if per_client_paths:
            print(f"Wrote: {len(per_client_paths)} per-client files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


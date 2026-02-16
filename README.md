## Negative media screening (helpers)

This folder contains small **offline** helpers to prepare a reproducible negative-media screening run for the client list in `clients.txt`.

### What is included
- `negative_media.py`:
  - reads `clients.txt` (one legal entity per line)
  - generates a query package (`.csv`) for RU/EN negative-media searches
  - generates a report template (`.md`) with 1 section per client
- `collect_findings.py` (optional):
  - calls an approved SERP API provider (currently: **SerpAPI / Google engine**)
  - collects top N results per query into a flat CSV
- `collect_findings_yandex.py` (optional):
  - collects results via **Yandex Cloud Search API**
  - outputs a flat CSV similar to `collect_findings.py`
- `collect_findings_google.py` (optional):
  - collects results via **Google Custom Search JSON API** (Programmable Search Engine)
  - outputs a flat CSV similar to `collect_findings.py`

### Why this is offline
Collecting results from Google/Bing/etc typically requires an approved provider, API keys, and compliance with ToS / `robots.txt`.
This repo therefore focuses on **query generation + reporting scaffolding**.

### Usage
Generate queries + report template for the last year:

```bash
python3 negative_media.py --clients clients.txt --out out
```

Custom period:

```bash
python3 negative_media.py --clients clients.txt --start 2025-01-01 --end 2025-12-31 --out out
```

### Outputs
- `out/queries_YYYY-MM-DD.csv`:
  - one row per (client × category × language)
  - can be fed into your approved search tooling/provider
- `out/report_template_YYYY-MM-DD.md`:
  - a structured template to register sourced findings
- `out/findings_YYYY-MM-DD.csv` (if you run `collect_findings.py`):
  - one row per (query result)

### Optional: collect results via SerpAPI
1) Export API key:

```bash
export SERPAPI_API_KEY="YOUR_KEY"
```

You can also put variables into `.env` (recommended to keep secrets out of shell history).
The scripts will auto-load `.env` if it exists.

2) Collect last-year results (top 10 per query):

```bash
python3 collect_findings.py --queries out/queries_YYYY-MM-DD.csv --out out --num 10
```

Cost control examples:

```bash
# Only RU queries, first 30 queries total
python3 collect_findings.py --queries out/queries_YYYY-MM-DD.csv --langs ru --max-queries 30
```

### Optional: collect results via Yandex APIs
#### Yandex Cloud Search API
Set creds (recommended: API key):

```bash
export YANDEX_API_KEY="YOUR_API_KEY"
# Optional (if required by your org/account):
export YANDEX_FOLDER_ID="YOUR_FOLDER_ID"
# IMPORTANT: set the exact endpoint from your Yandex Cloud docs:
export YANDEX_CLOUD_SEARCH_ENDPOINT="https://.../..."
```

Or put the same variables into `.env` (auto-loaded).

Run:

```bash
python3 collect_findings_yandex.py --queries out/queries_YYYY-MM-DD.csv --out out --num 10
```

If your setup requires IAM token auth instead:

```bash
export YANDEX_IAM_TOKEN="YOUR_IAM_TOKEN"
python3 collect_findings_yandex.py --auth-scheme bearer --queries out/queries_YYYY-MM-DD.csv --out out --num 10
```

### Optional: collect results via Google Custom Search JSON API
Prereqs:
- Enable **Custom Search API** in Google Cloud for your project
- Create API key → set `GOOGLE_API_KEY`
- Create a **Programmable Search Engine** set to search the entire web → set `GOOGLE_CX`

Set creds:

```bash
export GOOGLE_API_KEY="YOUR_KEY"
export GOOGLE_CX="YOUR_CX"
```

Note: `GOOGLE_API_KEY` is created in Google Cloud Console and usually looks like `AIza...`.

Collect last-year results (top 10 per query, 1 page):

```bash
python3 collect_findings_google.py --queries out/queries_YYYY-MM-DD.csv --out out --num 10 --pages 1
```

Cost control example (only first 20 queries):

```bash
python3 collect_findings_google.py --queries out/queries_YYYY-MM-DD.csv --max-queries 20 --langs ru
```

### Notes for audit-quality results
- Always store **source URL + capture date + excerpt** (or an internal archive link).
- Track **match confidence** (to avoid name collisions).
- Separate **facts** (official registers, courts, regulators, reputable media) from **signals** (reviews/UGC).


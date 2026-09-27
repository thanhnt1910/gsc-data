# gsc-data

Daily Google Search Console data for mathlogicgames.com, pulled by
GitHub Actions so it can be analysed without manual CSV exports.

## How it works

`.github/workflows/fetch.yml` runs `scripts/fetch_gsc.py` every day at
03:17 UTC (and on demand from the Actions tab). The script signs in with
the service account stored in the `GSC_SERVICE_ACCOUNT_JSON` repository
secret, downloads the Search Console performance report and commits any
changes under `data/`.

- First run: downloads the full 16 months Search Console keeps.
- Later runs: re-download the last 10 days, replacing what was stored.
- Only finalized data is fetched, so the newest 2–3 days show up later.
- Dates follow Search Console (Pacific Time). Search type: web.
- Mondays (and manual runs with **inspect** ticked) also run
  `scripts/inspect_index.py`, which checks every sitemap URL with the
  URL Inspection API (quota: 2,000 URLs a day).

## Data

| File | One row per |
|---|---|
| `data/totals.csv` | day (whole site) |
| `data/pages/YYYY-MM.csv` | day + page |
| `data/queries/YYYY-MM.csv` | day + query + page |
| `data/countries/YYYY-MM.csv` | day + country |
| `data/devices/YYYY-MM.csv` | day + device |
| `data/sitemaps.json` | submitted sitemap |
| `data/index/status.csv` | sitemap URL: index verdict, coverage state, last crawl, canonicals (latest check) |
| `data/index/summary.csv` | check date + page type + coverage state: URL count |

Every CSV has `clicks`, `impressions`, `ctr` and `position` columns.
Search Console hides rare (anonymized) queries, so the query table adds
up to less than the page and totals tables.

## Setup

1. Google Cloud: enable the **Google Search Console API**, create a
   service account and a JSON key for it.
2. Search Console → Settings → Users and permissions: add the service
   account's email with **Restricted** permission.
3. This repo → Settings → Secrets and variables → Actions: add the key
   file's contents as the secret `GSC_SERVICE_ACCOUNT_JSON`.
4. Optional: add the repository variable `GSC_SITE` (for example
   `sc-domain:mathlogicgames.com`) to pick a property explicitly.

## Manual run

Actions → *Fetch Search Console data* → *Run workflow*. Tick **backfill**
to re-download the full history.

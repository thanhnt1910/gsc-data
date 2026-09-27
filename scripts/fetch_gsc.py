#!/usr/bin/env python3
"""Download Google Search Console performance data into CSV files.

Reads the service-account key from the GSC_SERVICE_ACCOUNT_JSON environment
variable (a GitHub Actions secret) and writes:

  data/totals.csv                 one row per day for the whole site
  data/pages/YYYY-MM.csv          clicks/impressions per page per day
  data/queries/YYYY-MM.csv        per query + page per day
  data/countries/YYYY-MM.csv      per country per day
  data/devices/YYYY-MM.csv        per device per day
  data/sitemaps.json              submitted sitemaps and their status

The first run (no data/totals.csv yet) or BACKFILL=true downloads the full
16 months Search Console keeps. Later runs re-download the last DAYS days
(default 10) so late-arriving data replaces what was stored before.
Only finalized data is requested, so the newest 2-3 days appear a few days
later. Dates are Search Console's own (Pacific Time).

Optional: GSC_SITE picks the property (e.g. "sc-domain:example.com");
without it the first property whose URL contains SITE_HINT is used.
"""

import csv
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

API = 'https://searchconsole.googleapis.com/webmasters/v3'
SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']
SITE_HINT = 'mathlogicgames.com'
DATA = Path(__file__).resolve().parent.parent / 'data'
ROW_LIMIT = 25000
HISTORY_DAYS = 486  # ~16 months, the most Search Console returns
METRICS = ['clicks', 'impressions', 'ctr', 'position']

# Table name -> dimensions requested alongside "date".
TABLES = {
    'totals': [],
    'pages': ['page'],
    'queries': ['query', 'page'],
    'countries': ['country'],
    'devices': ['device'],
}


def table_path(table: str, month: str) -> Path:
    if table == 'totals':
        return DATA / 'totals.csv'
    return DATA / table / f'{month}.csv'


def make_session():
    raw = os.environ.get('GSC_SERVICE_ACCOUNT_JSON', '').strip()
    if not raw:
        sys.exit('GSC_SERVICE_ACCOUNT_JSON is not set (add it as a repository secret).')
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit('GSC_SERVICE_ACCOUNT_JSON is not valid JSON; paste the whole key file into the secret.')
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    print(f'Service account: {info.get("client_email", "?")}')
    return AuthorizedSession(creds)


class ApiError(Exception):
    def __init__(self, status: int, url: str, text: str):
        super().__init__(f'Search Console API error {status} for {url}:\n{text[:2000]}')
        self.status = status


def call(session, method: str, url: str, body=None) -> dict:
    """One API call, retrying rate limits and server errors with backoff.

    Raises ApiError on any other 4xx; main() turns that into a clean exit.
    """
    for attempt in range(6):
        resp = session.request(method, url, json=body, timeout=120)
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = 2 ** attempt * 5
            print(f'  HTTP {resp.status_code}, retrying in {wait}s')
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, url, resp.text)
        return resp.json() if resp.content else {}
    raise ApiError(resp.status_code, url, 'giving up after repeated rate-limit/server errors')


def pick_site(session) -> str:
    wanted = os.environ.get('GSC_SITE', '').strip()
    entries = call(session, 'GET', f'{API}/sites').get('siteEntry', [])
    urls = [e['siteUrl'] for e in entries if e.get('permissionLevel') != 'siteUnverifiedUser']
    if wanted:
        if wanted not in urls:
            sys.exit(f'GSC_SITE={wanted!r} is not visible to the service account. Visible: {urls}')
        return wanted
    matches = [u for u in urls if SITE_HINT in u]
    if not matches:
        sys.exit(
            'The service account cannot see any property for '
            f'{SITE_HINT}. Add its email in Search Console > Settings > Users and permissions. '
            f'Visible properties: {urls}'
        )
    # Prefer the domain property: it covers http/https and www/non-www.
    matches.sort(key=lambda u: (not u.startswith('sc-domain:'), u))
    return matches[0]


def fetch(session, site: str, start: dt.date, end: dt.date, dims: list[str]) -> list[dict]:
    url = f'{API}/sites/{quote(site, safe="")}/searchAnalytics/query'
    rows, start_row = [], 0
    while True:
        body = {
            'startDate': start.isoformat(),
            'endDate': end.isoformat(),
            'dimensions': ['date', *dims],
            'type': 'web',
            'dataState': 'final',
            'rowLimit': ROW_LIMIT,
            'startRow': start_row,
        }
        batch = call(session, 'POST', url, body).get('rows', [])
        rows.extend(batch)
        if len(batch) < ROW_LIMIT:
            return rows
        start_row += ROW_LIMIT


def to_record(row: dict, dims: list[str]) -> dict:
    record = {'date': row['keys'][0]}
    record.update(zip(dims, row['keys'][1:]))
    record['clicks'] = int(row['clicks'])
    record['impressions'] = int(row['impressions'])
    record['ctr'] = f'{row["ctr"]:.4f}'
    record['position'] = f'{row["position"]:.2f}'
    return record


def merge(path: Path, fields: list[str], start: str, end: str, new: list[dict]) -> None:
    """Replace the rows dated start..end in path with `new`, keeping the rest."""
    kept = []
    if path.exists():
        with path.open(newline='', encoding='utf-8') as f:
            kept = [r for r in csv.DictReader(f) if not (start <= r['date'] <= end)]
    rows = kept + new
    if not rows:
        return
    sort_keys = ['date'] + [k for k in fields if k not in ('date', *METRICS)]
    rows.sort(key=lambda r: (r['date'], -int(r['clicks']), -int(r['impressions']), *(r[k] for k in sort_keys[1:])))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def month_chunks(start: dt.date, end: dt.date):
    cur = start
    while cur <= end:
        nxt = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield cur, min(end, nxt - dt.timedelta(days=1))
        cur = nxt


def save_sitemaps(session, site: str) -> None:
    data = call(session, 'GET', f'{API}/sites/{quote(site, safe="")}/sitemaps')
    sitemaps = sorted(data.get('sitemap', []), key=lambda s: s.get('path', ''))
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'sitemaps.json').write_text(json.dumps(sitemaps, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def main() -> None:
    session = make_session()
    site = pick_site(session)
    print(f'Property: {site}')

    end = dt.date.today() - dt.timedelta(days=1)
    backfill = os.environ.get('BACKFILL', '').lower() == 'true' or not (DATA / 'totals.csv').exists()
    days = HISTORY_DAYS if backfill else int(os.environ.get('DAYS') or 10)
    start = end - dt.timedelta(days=days - 1)
    print(f'Fetching {start} .. {end} ({"full history" if backfill else f"last {days} days"})')

    for chunk_start, chunk_end in month_chunks(start, end):
        month = chunk_start.strftime('%Y-%m')
        for table, dims in TABLES.items():
            rows = [to_record(r, dims) for r in fetch(session, site, chunk_start, chunk_end, dims)]
            merge(table_path(table, month), ['date', *dims, *METRICS], chunk_start.isoformat(), chunk_end.isoformat(), rows)
            print(f'  {month} {table}: {len(rows)} rows')

    save_sitemaps(session, site)
    print('Done.')


if __name__ == '__main__':
    try:
        main()
    except ApiError as e:
        sys.exit(str(e))

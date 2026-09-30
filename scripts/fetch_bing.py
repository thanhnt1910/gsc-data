#!/usr/bin/env python3
"""Download Bing Webmaster Tools data for the site into CSV files.

Uses the Bing Webmaster API with an API key (Bing Webmaster Tools >
Settings > API access), read from the BING_API_KEY secret. Writes:

  data/bing/totals.csv     clicks and impressions per day
  data/bing/crawl.csv      pages crawled, crawl errors and pages in index per day
  data/bing/queries.csv    clicks, impressions and positions per query per period
  data/bing/pages.csv      the same per page

Bing returns roughly the last six months on every call, so each run
replaces the rows for the dates it gets back and keeps older ones. The
site is BING_SITE if set, otherwise DEFAULT_SITE. Without a key the
script says so and exits cleanly, so the rest of the workflow still runs.
"""

import csv
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from fetch_gsc import DATA

API = 'https://ssl.bing.com/webmaster/api.svc/json'
DEFAULT_SITE = 'https://mathlogicgames.com/'
OUT = DATA / 'bing'

# File -> (API method, [(API field, CSV column)], key columns besides date).
TABLES = {
    'totals': ('GetRankAndTrafficStats', [('Clicks', 'clicks'), ('Impressions', 'impressions')], []),
    'crawl': ('GetCrawlStats', [
        ('CrawledPages', 'crawledPages'), ('CrawlErrors', 'crawlErrors'), ('InIndex', 'inIndex'),
        ('InLinks', 'inLinks'), ('Code2xx', 'code2xx'), ('Code301', 'code301'), ('Code302', 'code302'),
        ('Code4xx', 'code4xx'), ('Code5xx', 'code5xx'), ('BlockedByRobotsTxt', 'blockedByRobotsTxt'),
    ], []),
    'queries': ('GetQueryStats', [
        ('Query', 'query'), ('Clicks', 'clicks'), ('Impressions', 'impressions'),
        ('AvgClickPosition', 'avgClickPosition'), ('AvgImpressionPosition', 'avgImpressionPosition'),
    ], ['query']),
    'pages': ('GetPageStats', [
        ('Query', 'page'), ('Clicks', 'clicks'), ('Impressions', 'impressions'),
        ('AvgClickPosition', 'avgClickPosition'), ('AvgImpressionPosition', 'avgImpressionPosition'),
    ], ['page']),
}


class BingError(Exception):
    pass


def call(method: str, key: str, site: str) -> list[dict]:
    url = f'{API}/{method}?' + urllib.parse.urlencode({'siteUrl': site, 'apikey': key})
    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        # Never echo the URL: it carries the API key.
        detail = e.read(500).decode('utf-8', 'replace')
        raise BingError(f'{method}: HTTP {e.code} {detail}') from None
    except urllib.error.URLError as e:
        raise BingError(f'{method}: {e.reason}') from None
    return body.get('d') or []


def parse_date(value: str) -> str:
    """'/Date(1727679600000-0700)/' -> '2024-09-30' (in the offset's own time zone)."""
    m = re.fullmatch(r'/Date\((-?\d+)([+-]\d{4})?\)/', value or '')
    if not m:
        raise BingError(f'Unexpected date format: {value!r}')
    when = dt.datetime.fromtimestamp(int(m.group(1)) / 1000, dt.timezone.utc)
    if m.group(2):
        sign = 1 if m.group(2)[0] == '+' else -1
        when += sign * dt.timedelta(hours=int(m.group(2)[1:3]), minutes=int(m.group(2)[3:]))
    return when.date().isoformat()


def to_rows(items: list[dict], fields: list[tuple[str, str]]) -> list[dict]:
    rows = []
    for it in items:
        row = {'date': parse_date(it.get('Date', ''))}
        for api_name, col in fields:
            v = it.get(api_name, '')
            row[col] = round(v, 2) if isinstance(v, float) else v
        rows.append(row)
    return rows


def merge(path, columns: list[str], keys: list[str], new: list[dict]) -> None:
    """Replace the rows for every date in `new`, keep rows for other dates."""
    dates = {r['date'] for r in new}
    kept = []
    if path.exists():
        with path.open(newline='', encoding='utf-8') as f:
            kept = [r for r in csv.DictReader(f) if r['date'] not in dates]
    rows = kept + new
    if not rows:
        return
    rows.sort(key=lambda r: (r['date'], -float(r.get('impressions') or r.get('crawledPages') or 0), *(r[k] for k in keys)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    key = os.environ.get('BING_API_KEY', '').strip()
    if not key:
        print('BING_API_KEY is not set; skipping Bing. Add it under Settings > Secrets and variables > Actions.')
        return
    site = os.environ.get('BING_SITE', '').strip() or DEFAULT_SITE
    print(f'Bing site: {site}')
    for table, (method, fields, keys) in TABLES.items():
        rows = to_rows(call(method, key, site), fields)
        merge(OUT / f'{table}.csv', ['date', *(col for _, col in fields)], keys, rows)
        span = f'{min(r["date"] for r in rows)} .. {max(r["date"] for r in rows)}' if rows else 'no data yet'
        print(f'  {table}: {len(rows)} rows ({span})')
    print('Done.')


if __name__ == '__main__':
    try:
        main()
    except BingError as e:
        sys.exit(str(e))

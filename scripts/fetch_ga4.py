#!/usr/bin/env python3
"""Download Google Analytics 4 data for the site into CSV files.

Uses the same service account as fetch_gsc.py (it needs Viewer access on
the GA4 property, and the Google Analytics Data API enabled in its Cloud
project). Writes:

  data/ga4/totals.csv               one row per day for the whole site
  data/ga4/pages/YYYY-MM.csv        views, users and engagement per page per day
  data/ga4/landing/YYYY-MM.csv      sessions per landing page per day
  data/ga4/channels/YYYY-MM.csv     sessions per channel and source per day
  data/ga4/events/YYYY-MM.csv       event counts per event name per day

The property is GA4_PROPERTY (e.g. "properties/123456789") if set,
otherwise DEFAULT_PROPERTY; with neither, it falls back to the one the
service account can see whose name matches SITE_HINT (Admin API). Like fetch_gsc.py, the
first run downloads everything since SINCE and later runs refresh the
last DAYS days (default 10).
"""

import csv
import datetime as dt
import os
import sys

from fetch_gsc import DATA, ApiError, call, make_session, month_chunks

SCOPES = ['https://www.googleapis.com/auth/analytics.readonly']
ADMIN = 'https://analyticsadmin.googleapis.com/v1beta'
DATA_API = 'https://analyticsdata.googleapis.com/v1beta'
SITE_HINT = 'math'
# mathlogicgames.com's GA4 property ("math-logic"); GA4_PROPERTY overrides it.
DEFAULT_PROPERTY = 'properties/554675821'
SINCE = dt.date(2026, 9, 1)
OUT = DATA / 'ga4'
ROW_LIMIT = 100000

# Table name -> (dimensions after "date", metrics).
TABLES = {
    'totals': ([], ['activeUsers', 'newUsers', 'sessions', 'engagedSessions', 'screenPageViews', 'userEngagementDuration']),
    'pages': (['pagePath'], ['screenPageViews', 'activeUsers', 'userEngagementDuration']),
    'landing': (['landingPage'], ['sessions', 'engagedSessions', 'activeUsers']),
    'channels': (['sessionDefaultChannelGroup', 'sessionSource'], ['sessions', 'engagedSessions', 'activeUsers']),
    'events': (['eventName'], ['eventCount']),
}


def table_path(table: str, month: str):
    return OUT / 'totals.csv' if table == 'totals' else OUT / table / f'{month}.csv'


def pick_property(session) -> str:
    wanted = os.environ.get('GA4_PROPERTY', '').strip() or DEFAULT_PROPERTY
    if wanted:
        return wanted if wanted.startswith('properties/') else f'properties/{wanted}'
    try:
        data = call(session, 'GET', f'{ADMIN}/accountSummaries?pageSize=200')
    except ApiError as e:
        sys.exit(
            f'{e}\n\nCould not list GA4 properties. Either enable the Google Analytics Admin API in the '
            'service account\'s Cloud project, or set the repository variable GA4_PROPERTY to the numeric '
            'property ID (GA4 > Admin > Property details).'
        )
    props = [
        (p['property'], p.get('displayName', ''))
        for a in data.get('accountSummaries', [])
        for p in a.get('propertySummaries', [])
    ]
    if not props:
        sys.exit('The service account cannot see any GA4 property. Add its email in GA4 > Admin > Property access management.')
    matches = [p for p in props if SITE_HINT in p[1].lower()] or props
    if len(matches) > 1:
        print(f'Several properties visible, using the first: {matches}')
    return matches[0][0]


def run_report(session, prop: str, start: dt.date, end: dt.date, dims: list[str], metrics: list[str]) -> list[dict]:
    rows, offset = [], 0
    while True:
        body = {
            'dateRanges': [{'startDate': start.isoformat(), 'endDate': end.isoformat()}],
            'dimensions': [{'name': d} for d in ['date', *dims]],
            'metrics': [{'name': m} for m in metrics],
            'limit': ROW_LIMIT,
            'offset': offset,
            'keepEmptyRows': False,
        }
        data = call(session, 'POST', f'{DATA_API}/{prop}:runReport', body)
        for r in data.get('rows', []):
            d = r['dimensionValues'][0]['value']
            rec = {'date': f'{d[:4]}-{d[4:6]}-{d[6:]}'}
            rec.update(zip(dims, (v['value'] for v in r['dimensionValues'][1:])))
            rec.update(zip(metrics, (v['value'] for v in r['metricValues'])))
            rows.append(rec)
        total = data.get('rowCount', 0)
        offset += ROW_LIMIT
        if offset >= total:
            return rows


def main() -> None:
    session = make_session(SCOPES)
    prop = pick_property(session)
    print(f'GA4 property: {prop}')

    end = dt.date.today() - dt.timedelta(days=1)
    backfill = os.environ.get('BACKFILL', '').lower() == 'true' or not (OUT / 'totals.csv').exists()
    start = SINCE if backfill else end - dt.timedelta(days=int(os.environ.get('DAYS') or 10) - 1)
    print(f'Fetching {start} .. {end}')

    for chunk_start, chunk_end in month_chunks(start, end):
        month = chunk_start.strftime('%Y-%m')
        for table, (dims, metrics) in TABLES.items():
            rows = run_report(session, prop, chunk_start, chunk_end, dims, metrics)
            merge_rows(table_path(table, month), ['date', *dims, *metrics], metrics[0], chunk_start.isoformat(), chunk_end.isoformat(), rows)
            print(f'  {month} {table}: {len(rows)} rows')
    print('Done.')


def merge_rows(path, fields, sort_metric, start, end, new):
    """Replace the rows dated start..end in path with `new`, keeping the rest."""
    kept = []
    if path.exists():
        with path.open(newline='', encoding='utf-8') as f:
            kept = [r for r in csv.DictReader(f) if not (start <= r['date'] <= end)]
    rows = kept + new
    if not rows:
        return
    dims = fields[1:fields.index(sort_metric)]
    rows.sort(key=lambda r: (r['date'], -float(r[sort_metric] or 0), *(r[d] for d in dims)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


if __name__ == '__main__':
    try:
        main()
    except ApiError as e:
        sys.exit(str(e))

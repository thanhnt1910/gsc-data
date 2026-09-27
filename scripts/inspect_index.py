#!/usr/bin/env python3
"""Check Google's index status for every URL in the site's sitemap.

Uses the Search Console URL Inspection API (quota: 2,000 URLs a day per
property) and writes:

  data/index/status.csv    one row per sitemap URL, overwritten each run
                           (git history keeps earlier snapshots)
  data/index/summary.csv   date, page type, coverage state, count;
                           one block of rows appended per run

Shares authentication and property selection with fetch_gsc.py.
"""

import csv
import datetime as dt
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import requests

from fetch_gsc import DATA, ApiError, call, make_session, pick_site

SITEMAP = 'https://mathlogicgames.com/sitemap-index.xml'
INSPECT = 'https://searchconsole.googleapis.com/v1/urlInspection/index:inspect'
NS = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
DAILY_QUOTA = 2000
WORKERS = 4

FIELDS = [
    'url', 'type', 'verdict', 'coverageState', 'indexingState', 'pageFetchState',
    'robotsTxtState', 'lastCrawlTime', 'crawledAs', 'googleCanonical', 'userCanonical',
    'referringUrls', 'error',
]


def sitemap_urls(url: str) -> list[str]:
    root = ET.fromstring(requests.get(url, timeout=60).content)
    children = [e.text.strip() for e in root.findall('sm:sitemap/sm:loc', NS)]
    if children:
        return [u for child in children for u in sitemap_urls(child)]
    return [e.text.strip() for e in root.findall('sm:url/sm:loc', NS)]


def page_type(url: str) -> str:
    path = url.split('://', 1)[-1].split('/', 1)[-1]
    first = path.split('/', 1)[0]
    if not first:
        return 'home'
    return {'games': 'game', 'blog': 'blog', 'category': 'category'}.get(first, 'other')


def inspect(session, site: str, url: str) -> dict:
    row = {'url': url, 'type': page_type(url)}
    try:
        result = call(session, 'POST', INSPECT, {'inspectionUrl': url, 'siteUrl': site, 'languageCode': 'en-US'})
    except ApiError as e:
        if e.status in (401, 403):
            raise
        row['error'] = str(e).splitlines()[0]
        return row
    status = result.get('inspectionResult', {}).get('indexStatusResult', {})
    for key in FIELDS[2:-2]:
        row[key] = status.get(key, '')
    row['referringUrls'] = len(status.get('referringUrls', []))
    return row


def main() -> None:
    session = make_session()
    site = pick_site(session)
    urls = sorted(set(sitemap_urls(SITEMAP)))
    print(f'Property: {site}; {len(urls)} sitemap URLs')
    if len(urls) > DAILY_QUOTA:
        print(f'Only the first {DAILY_QUOTA} URLs fit in the daily quota.')
        urls = urls[:DAILY_QUOTA]

    # Check permission on one URL before starting the thread pool.
    try:
        rows = [inspect(session, site, urls[0])]
    except ApiError as e:
        sys.exit(
            f'{e}\n\nURL inspection was refused. In Search Console > Settings > Users and permissions, '
            'give the service account Full permission instead of Restricted.'
        )
    with ThreadPoolExecutor(WORKERS) as pool:
        for i, row in enumerate(pool.map(lambda u: inspect(session, site, u), urls[1:]), start=2):
            rows.append(row)
            if i % 100 == 0:
                print(f'  {i}/{len(urls)}')

    out = DATA / 'index'
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'status.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)

    today = dt.date.today().isoformat()
    counts = {}
    for row in rows:
        key = (row['type'], row.get('coverageState') or ('error' if row.get('error') else 'unknown'))
        counts[key] = counts.get(key, 0) + 1
    summary = out / 'summary.csv'
    kept = []
    if summary.exists():
        with summary.open(newline='', encoding='utf-8') as f:
            kept = [r for r in csv.DictReader(f) if r['date'] != today]
    new = [{'date': today, 'type': t, 'coverageState': c, 'count': n} for (t, c), n in sorted(counts.items())]
    with summary.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['date', 'type', 'coverageState', 'count'], lineterminator='\n')
        writer.writeheader()
        writer.writerows(kept + new)

    for (t, c), n in sorted(counts.items()):
        print(f'  {t:9} {c:45} {n}')
    print('Done.')


if __name__ == '__main__':
    try:
        main()
    except ApiError as e:
        sys.exit(str(e))

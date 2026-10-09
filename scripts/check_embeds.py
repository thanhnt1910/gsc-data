#!/usr/bin/env python3
"""Check that every embedded game still loads at its source.

Reads data/embeds.csv (slug,url: the iframe URL each game page loads,
exported from the site's game list) and fetches each URL. A source can
remove a game at any time; the game page then shows an empty or broken
frame. Writes data/audits/embeds-YYYY-MM-DD.csv with, per game:

  status, final_url  HTTP status after redirects, and where it ended up
  bytes, title       size and <title> of the returned page
  flag               ok / dead (non-200, error) / suspect (redirected
                     away from the embed, a "not found"-style page, or a
                     page much smaller than the typical one)

CONTROL (optional) is one URL known to be dead, e.g. a made-up game id;
it is checked first and printed in full so the "dead" look is known.
"""

import concurrent.futures as cf
import csv
import datetime as dt
import os
import pathlib
import re
import statistics

import requests

DATA = pathlib.Path(__file__).resolve().parent.parent / 'data'
OUT = DATA / 'audits'
UA = 'Mozilla/5.0 (compatible; MathLogicEmbedCheck/1.0; +https://mathlogicgames.com/)'
NOT_FOUND = re.compile(r'not\s+found|404|no longer available|has been removed|unavailable|does not exist', re.I)


def fetch(url: str) -> dict:
    row = {'status': '', 'final_url': '', 'bytes': '', 'title': '', 'not_found_text': ''}
    try:
        r = requests.get(url, timeout=30, headers={'User-Agent': UA}, allow_redirects=True)
    except requests.RequestException as e:
        row['status'] = f'error: {type(e).__name__}'
        return row
    body = r.text
    m = re.search(r'(?is)<title[^>]*>(.*?)</title>', body)
    row.update(status=r.status_code, final_url=r.url, bytes=len(r.content),
               title=re.sub(r'\s+', ' ', m.group(1)).strip()[:120] if m else '',
               not_found_text='yes' if NOT_FOUND.search(re.sub(r'(?s)<script.*?</script>', ' ', body)[:20000]) else '')
    return row


def main() -> None:
    games = list(csv.DictReader((DATA / 'embeds.csv').open(encoding='utf-8')))
    control = os.environ.get('CONTROL', '').strip()
    if control:
        c = fetch(control)
        print(f'CONTROL {control}\n  {c}')
        try:
            print('  body start:', requests.get(control, timeout=30, headers={'User-Agent': UA}).text[:600].replace('\n', ' '))
        except requests.RequestException:
            pass

    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda g: {**g, **fetch(g['url'])}, games))

    sizes = [r['bytes'] for r in results if r['status'] == 200 and r['bytes']]
    median = statistics.median(sizes) if sizes else 0
    for r in results:
        if r['status'] != 200:
            r['flag'] = 'dead'
        elif r['final_url'].split('?')[0].rstrip('/') != r['url'].split('?')[0].rstrip('/'):
            r['flag'] = 'suspect: redirected'
        elif r['not_found_text']:
            r['flag'] = 'suspect: not-found text'
        elif median and r['bytes'] < median * 0.3:
            r['flag'] = 'suspect: small page'
        else:
            r['flag'] = 'ok'

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'embeds-{dt.date.today().isoformat()}.csv'
    cols = ['flag', 'slug', 'url', 'status', 'final_url', 'bytes', 'title', 'not_found_text']
    results.sort(key=lambda r: (r['flag'] == 'ok', r['flag'], r['slug']))
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore', lineterminator='\n')
        w.writeheader()
        w.writerows(results)

    counts = {}
    for r in results:
        counts[r['flag']] = counts.get(r['flag'], 0) + 1
    print(f'{len(results)} embeds, median page {median:.0f} bytes: {counts}')
    for r in results:
        if r['flag'] != 'ok':
            print(f"  {r['flag']:24} {r['slug']:40} {r['status']} {r['bytes']} {r['title'][:50]}")
    print(f'Wrote {path.relative_to(DATA.parent)}')


if __name__ == '__main__':
    main()

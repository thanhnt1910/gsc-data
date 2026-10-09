#!/usr/bin/env python3
"""Check that every embedded game still loads at its source.

Reads data/embeds.csv (slug,url: the iframe URL each game page loads,
exported from the site's game list). A source can remove a game at any
time; the game page then shows an empty or "no longer available" frame.
Writes data/audits/embeds-YYYY-MM-DD.csv with one row per game and a flag:

  WGPlayground  The /ifr/<id> page is fetched. A removed game still
                answers 200, but with the title "Game no longer
                available" and without its id in the page data (checked
                2026-10-09 against a made-up id). Flag: dead when that
                title shows or the status is not 200; suspect when the id
                is missing from the page or the URL redirects elsewhere.
  GamePix       The /embed page is the same JavaScript shell for every
                game, so it says nothing. Instead the game's namespace is
                looked up in GamePix's public catalogue feed. Flag: dead
                when it is no longer listed.

CONTROL (optional) is one URL known to be dead, e.g. a made-up game id;
it is checked like the others and written as the "__control__" row, so
each run shows the dead signature still holds.
"""

import concurrent.futures as cf
import csv
import datetime as dt
import os
import pathlib
import re

import requests

DATA = pathlib.Path(__file__).resolve().parent.parent / 'data'
OUT = DATA / 'audits'
UA = 'Mozilla/5.0 (compatible; MathLogicEmbedCheck/1.0; +https://mathlogicgames.com/)'
DEAD_TITLE = re.compile(r'no longer available', re.I)
GAMEPIX_FEED = 'https://feeds.gamepix.com/v2/json?order=quality&page={}&pagination=96'


def check_wg(url: str) -> dict:
    row = {'status': '', 'final_url': '', 'title': '', 'id_in_page': ''}
    try:
        r = requests.get(url, timeout=30, headers={'User-Agent': UA}, allow_redirects=True)
    except requests.RequestException as e:
        row.update(status=f'error: {type(e).__name__}', flag='dead')
        return row
    m = re.search(r'(?is)<title[^>]*>(.*?)</title>', r.text)
    gid = url.rstrip('/').split('/')[-1]
    row.update(status=r.status_code, final_url=r.url,
               title=re.sub(r'\s+', ' ', m.group(1)).strip()[:120] if m else '',
               id_in_page='yes' if gid in r.text else 'no')
    if r.status_code != 200 or DEAD_TITLE.search(row['title']):
        row['flag'] = 'dead'
    elif r.url.rstrip('/') != url.rstrip('/'):
        row['flag'] = 'suspect: redirected'
    elif row['id_in_page'] == 'no':
        row['flag'] = 'suspect: id not in page'
    else:
        row['flag'] = 'ok'
    return row


def gamepix_namespaces() -> set[str] | None:
    """Every namespace in GamePix's catalogue feed, or None if it can't be read."""
    found, page = set(), 1
    while page < 500:
        try:
            items = requests.get(GAMEPIX_FEED.format(page), timeout=30, headers={'User-Agent': UA}).json().get('items', [])
        except (requests.RequestException, ValueError):
            return None
        if not items:
            break
        for it in items:
            m = re.search(r'play\.gamepix\.com/([^/]+)/embed', it.get('url', '') or '')
            if m:
                found.add(m.group(1))
            if it.get('namespace'):
                found.add(it['namespace'])
        page += 1
    return found


def main() -> None:
    games = list(csv.DictReader((DATA / 'embeds.csv').open(encoding='utf-8')))
    control = os.environ.get('CONTROL', '').strip()
    if control:
        games.insert(0, {'slug': '__control__', 'url': control})

    wg = [g for g in games if 'wgplayground.com' in g['url']]
    gp = [g for g in games if 'gamepix.com' in g['url']]
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda g: {**g, **check_wg(g['url'])}, wg))

    listed = gamepix_namespaces() if gp else set()
    for g in gp:
        ns = g['url'].split('play.gamepix.com/')[1].split('/')[0]
        if listed is None:
            flag = 'suspect: feed unreadable'
        else:
            flag = 'ok' if ns in listed else 'dead'
        results.append({**g, 'status': 'in feed' if flag == 'ok' else 'not in feed', 'flag': flag})

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'embeds-{dt.date.today().isoformat()}.csv'
    cols = ['flag', 'slug', 'url', 'status', 'final_url', 'title', 'id_in_page']
    results.sort(key=lambda r: (r['slug'] != '__control__', r['flag'] == 'ok', r['flag'], r['slug']))
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore', lineterminator='\n')
        w.writeheader()
        w.writerows(results)

    games_only = [r for r in results if r['slug'] != '__control__']
    counts = {}
    for r in games_only:
        counts[r['flag']] = counts.get(r['flag'], 0) + 1
    print(f'{len(games_only)} games ({len(wg) - bool(control)} WGPlayground, {len(gp)} GamePix): {counts}')
    for r in results:
        if r['flag'] != 'ok':
            print(f"  {r['flag']:24} {r['slug']:40} {r['status']} {r.get('title', '')[:50]}")
    print(f'Wrote {path.relative_to(DATA.parent)}')


if __name__ == '__main__':
    main()

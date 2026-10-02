#!/usr/bin/env python3
"""Check whether pages are worth a backlink: indexable, dofollow, not spam.

For each URL in URLS (comma- or newline-separated, at most 50) it fetches
the page and records:

  indexable        HTTP status, robots.txt (Googlebot), meta robots and the
                   X-Robots-Tag header. A noindex page passes nothing.
  page_nofollow    meta robots / X-Robots-Tag "nofollow": every link on the
                   page is then nofollow, whatever its rel says.
  external links   how many links point to other sites, and how many of
                   them carry no nofollow / ugc / sponsored rel.
  game sites       which known game sites the page already links to, a
                   sign it lists sites like ours.
  links_to_us      whether it already links to mathlogicgames.com, and
                   with which rel (handy for checking live backlinks).
  spam signals     gambling/pharma/crypto words, or a link-farm sized page.

and ends each row with a verdict: good / check / skip, plus the reason.
Results go to data/prospects/rel-YYYY-MM-DD.csv and the log.
"""

import csv
import datetime as dt
import os
import re
import sys
import urllib.parse
import urllib.robotparser
from html.parser import HTMLParser

import requests

from fetch_gsc import DATA

OUT = DATA / 'prospects'
OUR_HOST = 'mathlogicgames.com'
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
MAX_URLS = 50
GAME_SITES = (
    'poki.com', 'crazygames.com', 'coolmathgames.com', 'mathplayground.com', 'arkadium.com',
    '247solitaire.com', '247mahjong.com', '247sudoku.com', 'worldofsolitaire.com', 'solitaired.com',
    'sudoku.com', 'mahjongg.com', 'freecell.net', 'games.aarp.org', 'pogo.com', 'msn.com/en-us/play',
    'abcya.com', 'funbrain.com', 'primarygames.com', 'chess.com', 'lichess.org', 'lumosity.com',
    'armorgames.com', 'kongregate.com', 'miniclip.com', 'gamesgames.com', 'y8.com',
)
SPAM = re.compile(r'\b(casino|betting|sportsbook|slots?|poker bonus|payday loan|viagra|cialis|cbd|escort|'
                  r'crypto ?casino|forex|essay writing|replica|porn|xxx)\b', re.I)
NOFOLLOW_RELS = {'nofollow', 'ugc', 'sponsored'}


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, set[str]]] = []
        self.robots: list[str] = []
        self.canonical = ''
        self.title = ''
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or '') for k, v in attrs}
        if tag == 'a' and a.get('href'):
            self.links.append((a['href'], set(a.get('rel', '').lower().split())))
        elif tag == 'meta' and a.get('name', '').lower() in ('robots', 'googlebot'):
            self.robots.append(a.get('content', '').lower())
        elif tag == 'link' and 'canonical' in a.get('rel', '').lower():
            self.canonical = a.get('href', '')
        elif tag == 'title':
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and len(self.title) < 120:
            self.title += data.strip()


def host_of(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower().split(':')[0].removeprefix('www.')


def robots_allows(session: requests.Session, url: str) -> bool | None:
    parts = urllib.parse.urlparse(url)
    try:
        r = session.get(f'{parts.scheme}://{parts.netloc}/robots.txt', timeout=15)
    except requests.RequestException:
        return None
    if r.status_code >= 400:
        return True  # no robots.txt means everything is allowed
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(r.text.splitlines())
    return rp.can_fetch('Googlebot', url)


def check(session: requests.Session, url: str) -> dict:
    row = {'url': url, 'status': '', 'title': '', 'indexable': '', 'page_nofollow': '', 'external_links': '',
           'external_dofollow': '', 'dofollow_share': '', 'game_sites_linked': '', 'links_to_us': '',
           'spam_signals': '', 'verdict': '', 'reason': ''}
    try:
        r = session.get(url, timeout=30, allow_redirects=True)
    except requests.RequestException as e:
        row.update(status=f'error: {type(e).__name__}', verdict='skip', reason='page did not load')
        return row
    row['status'] = r.status_code if r.url == url else f'{r.status_code} via {r.url[:80]}'
    if r.status_code >= 400:
        row.update(verdict='skip', reason=f'HTTP {r.status_code}')
        return row

    page = r.text[:2_000_000]
    p = LinkParser()
    try:
        p.feed(page)
    except Exception:
        pass
    row['title'] = p.title

    header = r.headers.get('X-Robots-Tag', '').lower()
    robots_meta = ' '.join(p.robots) + ' ' + header
    allowed = robots_allows(session, r.url)
    noindex = 'noindex' in robots_meta or 'none' in robots_meta.split()
    canonical_elsewhere = bool(p.canonical) and host_of(urllib.parse.urljoin(r.url, p.canonical)) != host_of(r.url)
    row['indexable'] = 'no (noindex)' if noindex else 'no (robots.txt)' if allowed is False else \
        'no (canonical to other site)' if canonical_elsewhere else 'yes'
    page_nofollow = 'nofollow' in robots_meta or 'none' in robots_meta.split()
    row['page_nofollow'] = 'yes' if page_nofollow else 'no'

    page_host = host_of(r.url)
    external = dofollow = 0
    games, ours = set(), []
    for href, rel in p.links:
        full = urllib.parse.urljoin(r.url, href)
        if not full.startswith(('http://', 'https://')):
            continue
        h = host_of(full)
        if not h or h == page_host or h.endswith('.' + page_host):
            continue
        external += 1
        followed = not (rel & NOFOLLOW_RELS) and not page_nofollow
        dofollow += followed
        if h == OUR_HOST or h.endswith('.' + OUR_HOST):
            ours.append('dofollow' if followed else 'nofollow:' + ','.join(sorted(rel & NOFOLLOW_RELS) or ['page']))
        for g in GAME_SITES:
            if (h + urllib.parse.urlparse(full).path).startswith(g) or h == g or h.endswith('.' + g.split('/')[0]):
                games.add(g)
    row['external_links'] = external
    row['external_dofollow'] = dofollow
    row['dofollow_share'] = f'{dofollow / external:.0%}' if external else ''
    row['game_sites_linked'] = ' '.join(sorted(games))
    row['links_to_us'] = ' '.join(ours) if ours else 'no'

    text = re.sub(r'(?s)<[^>]+>', ' ', page)
    spam_hits = sorted({m.group(0).lower() for m in SPAM.finditer(text)})
    signals = spam_hits[:5]
    if external > 150:
        signals.append(f'{external} external links')
    row['spam_signals'] = ' '.join(signals)

    if row['indexable'] != 'yes':
        row.update(verdict='skip', reason=f"not indexable: {row['indexable']}")
    elif page_nofollow:
        row.update(verdict='skip', reason='whole page is nofollow')
    elif spam_hits or external > 150:
        row.update(verdict='skip', reason='spam signals')
    elif external and dofollow / external >= 0.5:
        row.update(verdict='good', reason='indexable, mostly dofollow outbound links' +
                   (', already lists game sites' if games else ''))
    elif external == 0:
        row.update(verdict='check', reason='no outbound links on this page; look for a resources/links page')
    else:
        row.update(verdict='check', reason=f"only {row['dofollow_share']} of outbound links are dofollow")
    return row


def main() -> None:
    raw = os.environ.get('URLS', '')
    urls = []
    for part in re.split(r'[,\s]+', raw):
        part = part.strip()
        if part and not part.startswith(('http://', 'https://')):
            part = 'https://' + part
        if part and part not in urls:
            urls.append(part)
    urls = urls[:MAX_URLS]
    if not urls:
        sys.exit('Set URLS to a comma- or newline-separated list of page URLs.')

    session = requests.Session()
    session.headers.update({'User-Agent': UA, 'Accept-Language': 'en'})
    rows = [check(session, u) for u in urls]
    order = {'good': 0, 'check': 1, 'skip': 2}
    rows.sort(key=lambda r: (order[r['verdict']], r['url']))

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'rel-{dt.date.today().isoformat()}.csv'
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    for r in rows:
        print(f"{r['verdict']:5}  dofollow {r['external_dofollow']!s:>4}/{r['external_links']!s:<4} "
              f"us={r['links_to_us']:10} {r['url']}\n       {r['reason']}"
              + (f"  | games: {r['game_sites_linked']}" if r['game_sites_linked'] else '')
              + (f"  | spam: {r['spam_signals']}" if r['spam_signals'] else ''))
    print(f'Wrote {path.relative_to(DATA.parent)}')


if __name__ == '__main__':
    main()

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
  nofollow_to      the external hosts whose links are nofollow / ugc /
                   sponsored, with the rel. On a listing page, check that
                   the listed site is not among them: the dofollow share
                   counts menu and footer links too.
  spam signals     gambling/pharma/essay-mill phrases (skip), or more than
                   300 outbound links (check by hand: a curated list or a
                   link farm).

and ends each row with a verdict: good / check / skip, plus the reason.

SAMPLE mode: with SAMPLE_POSTS=N, each URL is treated as a site, not a
page. The script reads the site's sitemap (robots.txt, then the usual
WordPress / Yoast / Shopify locations), picks the N most recently
modified article URLs and checks those instead. Use it to vet guest-post
and blog targets: a site's "write for us" page or homepage can be fully
dofollow while the articles where a guest link would live are not (see
BACKLINK_CHECKLIST.md). Each sampled row's reason names the site it was
sampled from.
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
# Strong phrases only: single words like "slot" or "xxx" turned up on clean
# pages (time slots, placeholder text) in the first run.
SPAM = re.compile(r'\b(online casino|casino bonus|sportsbook|online slots|slot gacor|judi online|togel|'
                  r'payday loans?|viagra|cialis|escort service|essay writing service|porn|xxx videos|'
                  r'crypto casino|forex signals|replica watches)\b', re.I)
BIG_LIST = 300  # more outbound links than this: a directory or link list, judge by hand
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
           'nofollow_to': '', 'spam_signals': '', 'verdict': '', 'reason': ''}
    try:
        r = session.get(url, timeout=30, allow_redirects=True)
    except requests.RequestException as e:
        row.update(status=f'error: {type(e).__name__}', verdict='skip', reason='page did not load')
        return row
    row['status'] = r.status_code if r.url == url else f'{r.status_code} via {r.url[:80]}'
    if r.status_code in (401, 403, 429, 503):
        row.update(verdict='check', reason=f'HTTP {r.status_code}: the site blocks automated checks, look by hand')
        return row
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
    games, ours, unfollowed = set(), [], {}
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
        if not followed:
            unfollowed.setdefault(h, set()).update(rel & NOFOLLOW_RELS or {'page'})
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
    row['nofollow_to'] = ' '.join(f"{h}({','.join(sorted(r))})" for h, r in sorted(unfollowed.items())[:15])

    text = re.sub(r'(?s)<[^>]+>', ' ', page)
    spam_hits = sorted({m.group(0).lower() for m in SPAM.finditer(text)})
    signals = spam_hits[:5]
    if external > BIG_LIST:
        signals.append(f'{external} external links')
    row['spam_signals'] = ' '.join(signals)

    if row['indexable'] != 'yes':
        row.update(verdict='skip', reason=f"not indexable: {row['indexable']}")
    elif page_nofollow:
        row.update(verdict='skip', reason='whole page is nofollow')
    elif spam_hits:
        row.update(verdict='skip', reason='spam signals')
    elif external > BIG_LIST:
        row.update(verdict='check', reason=f'very long link list ({external} outbound links); '
                   'fine for a curated wiki, a red flag for a link farm')
    elif external and dofollow / external >= 0.5:
        row.update(verdict='good', reason='indexable, mostly dofollow outbound links' +
                   (', already lists game sites' if games else ''))
    elif external == 0:
        row.update(verdict='check', reason='no outbound links on this page; look for a resources/links page')
    else:
        row.update(verdict='check', reason=f"only {row['dofollow_share']} of outbound links are dofollow")
    return row


SITEMAP_PATHS = ('/sitemap_index.xml', '/sitemap.xml', '/wp-sitemap.xml', '/post-sitemap.xml',
                 '/sitemap_blogs_1.xml')
# Sitemap or URL parts that are not articles: listing, taxonomy and legal pages.
NOT_ARTICLE = re.compile(r'/(tag|tags|category|categories|author|page|product|products|collections|'
                         r'cart|account|search|wp-content|feed)(/|$)|'
                         r'(about|contact|privacy|terms|write-for-us|advertise|disclaimer|cookie|sitemap)',
                         re.I)
LOC = re.compile(r'<loc>\s*(.*?)\s*</loc>(?:\s*<lastmod>\s*(.*?)\s*</lastmod>)?', re.S)


def sitemap_urls(session: requests.Session, root: str) -> list[str]:
    """Candidate sitemap URLs for a site: robots.txt entries first, then the usual paths."""
    parts = urllib.parse.urlparse(root)
    base = f'{parts.scheme}://{parts.netloc}'
    found = []
    try:
        r = session.get(base + '/robots.txt', timeout=15)
        if r.ok:
            found += [line.split(':', 1)[1].strip() for line in r.text.splitlines()
                      if line.lower().startswith('sitemap:')]
    except requests.RequestException:
        pass
    return found + [base + p for p in SITEMAP_PATHS]


def sample_articles(session: requests.Session, root: str, n: int) -> list[str]:
    """Up to n recent article URLs from a site's sitemaps (child sitemaps named *post* first)."""
    host = host_of(root)
    queue, seen, pages = sitemap_urls(session, root), set(), []
    while queue and len(seen) < 12:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            r = session.get(sm, timeout=20)
        except requests.RequestException:
            continue
        if r.status_code >= 400 or '<loc>' not in r.text:
            continue
        locs = LOC.findall(r.text[:3_000_000])
        if '<sitemapindex' in r.text:
            children = [u for u, _ in locs if host_of(u).endswith(host)]
            # Article sitemaps first, then anything else that is not a taxonomy/page sitemap.
            children.sort(key=lambda u: (0 if re.search(r'post|blog|article|news', u, re.I) else
                                         2 if re.search(r'tag|categor|author|page|product|collection', u, re.I) else 1))
            queue = children + queue
            continue
        for u, lastmod in locs:
            path = urllib.parse.urlparse(u).path.strip('/')
            if host_of(u).endswith(host) and path and not NOT_ARTICLE.search('/' + path):
                pages.append((lastmod or '', u))
        if len(pages) >= 50:
            break
    pages.sort(reverse=True)
    out = []
    for _, u in pages:
        if u not in out:
            out.append(u)
        if len(out) >= n:
            break
    return out


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
    sample = int(os.environ.get('SAMPLE_POSTS') or 0)
    if sample:
        rows = []
        for root in urls:
            articles = sample_articles(session, root, min(sample, 5))
            if not articles:
                rows.append({**check(session, root), 'verdict': 'check',
                             'reason': f'sample: no article sitemap found for {host_of(root)}, look by hand'})
                continue
            for a in articles:
                row = check(session, a)
                row['reason'] = f'sample of {host_of(root)}: ' + row['reason']
                rows.append(row)
    else:
        rows = [check(session, u) for u in urls]
    order = {'good': 0, 'check': 1, 'skip': 2}
    rows.sort(key=lambda r: (order[r['verdict']], r['url']))

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'rel-{dt.date.today().isoformat()}.csv'
    # Several runs on one day share the file: keep earlier rows, newest result per URL wins.
    merged = {}
    if path.exists():
        with path.open(newline='', encoding='utf-8') as f:
            merged = {r['url']: r for r in csv.DictReader(f)}
    merged.update({r['url']: r for r in rows})
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n', extrasaction='ignore')
        writer.writeheader()
        writer.writerows(sorted(merged.values(), key=lambda r: (order.get(r['verdict'], 9), r['url'])))
    for r in rows:
        print(f"{r['verdict']:5}  dofollow {r['external_dofollow']!s:>4}/{r['external_links']!s:<4} "
              f"us={r['links_to_us']:10} {r['url']}\n       {r['reason']}"
              + (f"  | games: {r['game_sites_linked']}" if r['game_sites_linked'] else '')
              + (f"  | spam: {r['spam_signals']}" if r['spam_signals'] else ''))
    print(f'Wrote {path.relative_to(DATA.parent)}')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""List WGPlayground catalogue games whose title matches a keyword pattern.

WGPlayground's site can't be reached from every machine, so this runs in
GitHub Actions and prints to the log. For each page in URLS (comma-
separated catalogue pages or XML sitemaps; the catalogue is rendered by
JavaScript, so the sitemap is the reliable source), it collects
/game/<studio>/<slug> links, follows child sitemaps and
"page" links found on the same catalogue up to MAX_PAGES, and prints every
game whose title or slug matches KEYWORDS (a regex, case-insensitive). For
each match it then opens the game page and prints its meta description and
embed id, so a person can judge the game before adding it to the site.
"""

import html
import os
import re
import sys
import urllib.parse
import urllib.request

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'


def get(url: str) -> str:
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'en'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(2_000_000).decode('utf-8', 'replace')


def clean(text: str) -> str:
    return html.unescape(re.sub(r'\s+', ' ', re.sub(r'(?s)<[^>]+>', ' ', text))).strip()


def main() -> None:
    urls = [u.strip() for u in os.environ.get('URLS', '').split(',') if u.strip()]
    pattern = re.compile(os.environ.get('KEYWORDS') or '.', re.I)
    max_pages = int(os.environ.get('MAX_PAGES') or 30)
    if not urls:
        sys.exit('Set URLS to comma-separated catalogue page URLs.')

    games = {}  # game url -> title
    seen_pages = set()
    queue = list(urls)
    while queue and len(seen_pages) < max_pages * len(urls):
        page_url = queue.pop(0)
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)
        try:
            page = get(page_url)
        except Exception as e:  # noqa: BLE001
            print(f'  {page_url}: {e}')
            continue
        found = 0
        # XML sitemaps (and sitemap indexes): the catalogue itself is
        # rendered by JavaScript, so its plain HTML has no game links.
        for loc in re.findall(r'<loc>\s*([^<\s]+)\s*</loc>', page):
            loc = html.unescape(loc)
            path = urllib.parse.urlparse(loc).path
            if re.match(r'^/game/[^/]+/[^/]+/?$', path):
                games.setdefault(loc, path.rstrip('/').rsplit('/', 1)[-1].replace('-', ' '))
                found += 1
            elif loc.endswith('.xml') and loc not in seen_pages and loc not in queue:
                queue.append(loc)
        for href, label in re.findall(r'(?is)<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', page):
            url = urllib.parse.urljoin(page_url, href)
            path = urllib.parse.urlparse(url).path
            if re.match(r'^/game/[^/]+/[^/]+/?$', path):
                title = clean(label) or path.rsplit('/', 1)[-1].replace('-', ' ')
                if url not in games or len(title) > len(games[url]):
                    games[url] = title
                found += 1
            elif re.search(r'[?&]page=\d+|/page/\d+', url) and urllib.parse.urlparse(url).netloc.endswith('wgplayground.com'):
                if url not in seen_pages and url not in queue:
                    queue.append(url)
        print(f'{page_url}: {found} game links')

    print(f'\n{len(games)} games collected; matching /{pattern.pattern}/:')
    matches = [(u, t) for u, t in games.items() if pattern.search(t) or pattern.search(u)]
    for url, title in sorted(matches, key=lambda x: x[1].lower()):
        print(f'\n* {title} | {url}')
        try:
            page = get(url)
        except Exception as e:  # noqa: BLE001
            print(f'    (could not open: {e})')
            continue
        desc = re.search(r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', page)
        if desc:
            print(f'    {html.unescape(desc.group(1))[:300]}')
        embed = re.search(r'play\.wgplayground\.com/ifr/([0-9a-f]{32})', page) or re.search(r'data-id=["\']([0-9a-f]{32})', page)
        if embed:
            print(f'    wgId: {embed.group(1)}')


if __name__ == '__main__':
    main()

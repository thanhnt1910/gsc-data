#!/usr/bin/env python3
"""On-page SEO snapshot of one or more sites, for side-by-side comparison.

For each domain in SITES (comma-separated) it reads robots.txt and the
sitemap(s), counts URLs by first path segment, then fetches the home page
plus a sample of pages from the biggest sections and records, per page:
title, meta description, canonical, meta robots, H1, the first H2/H3s,
visible word count, internal/external link counts, iframe hosts (where the
games come from), JSON-LD types, FAQ presence, image alt coverage, ad
scripts, and page weight. Output: data/audits/<domain>-<date>.md (readable)
and .json (complete), so a person or the weekly review can compare sites.
"""

import datetime as dt
import gzip
import json
import os
import re
import sys
import urllib.parse
from collections import Counter
from html.parser import HTMLParser

import requests

from fetch_gsc import DATA

OUT = DATA / 'audits'
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
MAX_SITEMAP_URLS = 50_000
SAMPLES_PER_SECTION = 4
AD_MARKERS = {
    'adsense': 'adsbygoogle', 'google ad manager': 'googletagservices', 'gpt': 'securepubads',
    'gamedistribution': 'gamedistribution', 'gamemonetize': 'gamemonetize', 'ezoic': 'ezoic',
    'mediavine': 'mediavine', 'adinplay': 'adinplay', 'playwire': 'playwire',
}


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ''
        self.meta = {}
        self.canonical = ''
        self.lang = ''
        self.headings: list[tuple[str, str]] = []
        self.links: list[str] = []
        self.iframes: list[str] = []
        self.images = 0
        self.images_no_alt = 0
        self.jsonld: list[str] = []
        self.scripts_src: list[str] = []
        self.text_parts: list[str] = []
        self._stack: list[str] = []
        self._buf = ''

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or '') for k, v in attrs}
        if tag == 'html':
            self.lang = a.get('lang', '')
        elif tag == 'meta':
            key = (a.get('name') or a.get('property') or '').lower()
            if key:
                self.meta[key] = a.get('content', '')
        elif tag == 'link' and 'canonical' in a.get('rel', '').lower():
            self.canonical = a.get('href', '')
        elif tag == 'a' and a.get('href'):
            self.links.append(a['href'])
        elif tag == 'iframe' and a.get('src'):
            self.iframes.append(a['src'])
        elif tag == 'img':
            self.images += 1
            if not a.get('alt', '').strip():
                self.images_no_alt += 1
        elif tag == 'script':
            if a.get('src'):
                self.scripts_src.append(a['src'])
            if 'ld+json' in a.get('type', ''):
                self._stack.append('jsonld')
                self._buf = ''
                return
        if tag in ('title', 'h1', 'h2', 'h3', 'script', 'style', 'noscript'):
            self._stack.append(tag)
            self._buf = ''

    def handle_endtag(self, tag):
        if not self._stack:
            return
        top = self._stack[-1]
        if top == 'jsonld' and tag == 'script':
            self.jsonld.append(self._buf)
            self._stack.pop()
        elif top == tag:
            if tag == 'title':
                self.title = ' '.join(self._buf.split())
            elif tag in ('h1', 'h2', 'h3'):
                self.headings.append((tag, ' '.join(self._buf.split())[:120]))
            self._stack.pop()

    def handle_data(self, data):
        if self._stack:
            if self._stack[-1] in ('title', 'h1', 'h2', 'h3', 'jsonld'):
                self._buf += data
            if self._stack[-1] in ('script', 'style', 'noscript', 'jsonld'):
                return
        self.text_parts.append(data)


def jsonld_types(blobs: list[str]) -> list[str]:
    types = []
    def walk(x):
        if isinstance(x, dict):
            t = x.get('@type')
            if t:
                types.extend(t if isinstance(t, list) else [t])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    for b in blobs:
        try:
            walk(json.loads(b))
        except Exception:
            types.append('(invalid JSON-LD)')
    return sorted(set(map(str, types)))


def host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower().removeprefix('www.')


def fetch(session, url, limit=3_000_000):
    try:
        r = session.get(url, timeout=30)
        return r.status_code, r.url, r.content[:limit]
    except requests.RequestException as e:
        return f'error: {type(e).__name__}', url, b''


def sitemap_urls(session, base: str) -> tuple[list[str], list[str]]:
    status, _, robots = fetch(session, base + '/robots.txt')
    maps = re.findall(rb'(?im)^\s*sitemap:\s*(\S+)', robots) if status == 200 else []
    queue = [m.decode() for m in maps] or [base + '/sitemap.xml', base + '/sitemap_index.xml', base + '/sitemap-index.xml']
    seen, urls = set(), []
    while queue and len(seen) < 60 and len(urls) < MAX_SITEMAP_URLS:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        status, _, body = fetch(session, sm, 50_000_000)
        if status != 200 or not body:
            continue
        if body[:2] == b'\x1f\x8b':
            try:
                body = gzip.decompress(body)
            except OSError:
                continue
        locs = [l.decode('utf-8', 'replace').strip() for l in re.findall(rb'<loc>\s*([^<]+?)\s*</loc>', body)]
        if b'<sitemapindex' in body:
            queue.extend(locs)
        else:
            urls.extend(locs)
    return urls, sorted(seen)


def audit_page(session, url: str, site_host: str) -> dict:
    status, final, body = fetch(session, url)
    row = {'url': url, 'status': status, 'final_url': final if final != url else ''}
    if status != 200:
        return row
    html = body.decode('utf-8', 'replace')
    p = Page()
    try:
        p.feed(html)
    except Exception:
        pass
    words = len(re.findall(r"[A-Za-z]{2,}", ' '.join(p.text_parts)))
    internal = external = 0
    for href in p.links:
        h = host(urllib.parse.urljoin(final, href))
        if not h:
            continue
        if h == site_host or h.endswith('.' + site_host):
            internal += 1
        else:
            external += 1
    lowered = html.lower()
    row.update({
        'title': p.title, 'title_len': len(p.title),
        'meta_description': p.meta.get('description', ''), 'meta_description_len': len(p.meta.get('description', '')),
        'canonical': p.canonical, 'meta_robots': p.meta.get('robots', ''), 'lang': p.lang,
        'og_image': bool(p.meta.get('og:image')),
        'h1': [t for tag, t in p.headings if tag == 'h1'],
        'h2': [t for tag, t in p.headings if tag == 'h2'][:15],
        'h3_count': sum(1 for tag, _ in p.headings if tag == 'h3'),
        'words': words, 'internal_links': internal, 'external_links': external,
        'iframe_hosts': sorted({host(urllib.parse.urljoin(final, s)) for s in p.iframes}),
        'jsonld_types': jsonld_types(p.jsonld),
        'faq': 'FAQPage' in ' '.join(jsonld_types(p.jsonld)) or bool(re.search(r'>\s*(faq|frequently asked)', lowered)),
        'images': p.images, 'images_no_alt': p.images_no_alt,
        'ads': sorted(k for k, m in AD_MARKERS.items() if m in lowered),
        'html_kb': round(len(body) / 1024),
    })
    return row


def section_of(url: str) -> str:
    parts = [s for s in urllib.parse.urlparse(url).path.split('/') if s]
    return '/' + parts[0] + '/' if len(parts) > 1 else ('/' if not parts else '/(top-level pages)')


def audit_site(domain: str) -> dict:
    domain = domain.strip().lower().removeprefix('https://').removeprefix('http://').strip('/')
    session = requests.Session()
    session.headers.update({'User-Agent': UA, 'Accept-Language': 'en'})
    status, final, _ = fetch(session, f'https://{domain}/')
    base = f'{urllib.parse.urlparse(final).scheme}://{urllib.parse.urlparse(final).netloc}' if isinstance(status, int) else f'https://{domain}'
    site_host = host(base)
    urls, maps = sitemap_urls(session, base)
    sections = Counter(section_of(u) for u in urls)
    samples = [base + '/']
    for sec, _ in sections.most_common(4):
        picks = [u for u in urls if section_of(u) == sec and u.rstrip('/') != base][:200]
        step = max(1, len(picks) // SAMPLES_PER_SECTION)
        samples.extend(picks[::step][:SAMPLES_PER_SECTION])
    pages = [audit_page(session, u, site_host) for u in dict.fromkeys(samples)]
    return {'domain': domain, 'base': base, 'home_status': status, 'sitemaps': maps,
            'sitemap_url_count': len(urls), 'sections': sections.most_common(15), 'pages': pages}


def to_markdown(a: dict) -> str:
    md = [f"# Site audit: {a['domain']} ({dt.date.today().isoformat()})", '',
          f"Home status: {a['home_status']} · Sitemap URLs: {a['sitemap_url_count']} · Sitemaps read: {len(a['sitemaps'])}", '',
          '## URLs by section', '', '| Section | URLs |', '|---|---|']
    md += [f'| `{s}` | {n} |' for s, n in a['sections']]
    for p in a['pages']:
        md += ['', f"## {p['url']}", '']
        if p.get('status') != 200:
            md.append(f"Status: {p.get('status')}")
            continue
        for k in ('title', 'title_len', 'meta_description', 'meta_description_len', 'canonical', 'meta_robots', 'lang',
                  'og_image', 'h1', 'h2', 'h3_count', 'words', 'internal_links', 'external_links', 'iframe_hosts',
                  'jsonld_types', 'faq', 'images', 'images_no_alt', 'ads', 'html_kb'):
            md.append(f'- **{k}**: {p.get(k)}')
    return '\n'.join(md) + '\n'


def main() -> None:
    sites = [s for s in re.split(r'[,\s]+', os.environ.get('SITES', '')) if s]
    if not sites:
        sys.exit('Set SITES to a comma-separated list of domains.')
    OUT.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    for s in sites[:5]:
        a = audit_site(s)
        name = re.sub(r'[^a-z0-9.-]', '_', a['domain'])
        (OUT / f'{name}-{today}.json').write_text(json.dumps(a, indent=1, ensure_ascii=False), encoding='utf-8')
        (OUT / f'{name}-{today}.md').write_text(to_markdown(a), encoding='utf-8')
        print(f"{a['domain']}: {a['sitemap_url_count']} sitemap URLs, {len(a['pages'])} pages audited")


if __name__ == '__main__':
    main()

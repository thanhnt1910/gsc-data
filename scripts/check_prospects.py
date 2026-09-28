#!/usr/bin/env python3
"""Quick look at link prospects: is the site up, and how do you get listed?

For each domain in SITES (comma-separated), fetches the home page, prints
its title, and follows up to three links whose text or URL looks like a
submission or contact page ("submit", "suggest", "add url", "contact"...).
For each of those pages it prints the title and any lines mentioning
prices, "free", "reciprocal" or "backlink", so a person can judge whether
the listing is free, paid or needs a link back. Output goes to the log.
"""

import html
import os
import re
import sys
import urllib.parse
import urllib.request

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
LINK_WORDS = re.compile(r'submit|suggest|add[-_ ]?(url|site|link|your)|list[-_ ]?your|contact|advertis|recommend|tip', re.I)
TERMS = re.compile(r'(\$|€|£|usd|price|paid|payment|fee|free|reciprocal|backlink|link back|nofollow|review)', re.I)


def get(url: str) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'en'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.geturl(), r.read(600_000).decode('utf-8', 'replace')


def text_of(page: str) -> str:
    page = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', page)
    page = re.sub(r'(?s)<[^>]+>', '\n', page)
    lines = (html.unescape(l).strip() for l in page.splitlines())
    return '\n'.join(l for l in lines if l)


def title_of(page: str) -> str:
    m = re.search(r'(?is)<title[^>]*>(.*?)</title>', page)
    return html.unescape(re.sub(r'\s+', ' ', m.group(1))).strip()[:120] if m else '(no title)'


def candidate_links(base: str, page: str) -> list[str]:
    host = urllib.parse.urlparse(base).netloc
    seen = []
    for href, label in re.findall(r'(?is)<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', page):
        label = re.sub(r'(?s)<[^>]+>', ' ', label)
        if not (LINK_WORDS.search(href) or LINK_WORDS.search(label)):
            continue
        url = urllib.parse.urljoin(base, href)
        if urllib.parse.urlparse(url).netloc.split(':')[0].endswith(host.split(':')[0].removeprefix('www.')) and url not in seen:
            seen.append(url)
    return seen[:3]


def main() -> None:
    sites = [s.strip() for s in os.environ.get('SITES', '').split(',') if s.strip()]
    if not sites:
        sys.exit('Set SITES to a comma-separated list of domains.')
    for site in sites:
        print(f'\n===== {site}')
        try:
            url, page = get(f'https://{site}/')
        except Exception as e:  # noqa: BLE001
            try:
                url, page = get(f'http://{site}/')
            except Exception:
                print(f'  DOWN: {e}')
                continue
        print(f'  {url} | {title_of(page)}')
        for link in candidate_links(url, page):
            try:
                final, sub = get(link)
            except Exception as e:  # noqa: BLE001
                print(f'  -> {link}: {e}')
                continue
            print(f'  -> {final} | {title_of(sub)}')
            hits = [l for l in text_of(sub).splitlines() if TERMS.search(l) and len(l) < 220]
            for l in hits[:8]:
                print(f'       {l}')


if __name__ == '__main__':
    main()

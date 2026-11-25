#!/usr/bin/env python3
"""Look at the domains that link to several competitors but not to us.

Reads a gap file (default: the newest data/backlinks/<release>/gap.csv; set
GAP_FILE to e.g. gap-custom.csv to use that file in the same release folder),
keeps domains that link to at least MIN_COMPETITORS competitors and rank better
than MAX_RANK, and for each one fetches the home page and up to three
submission/contact pages. Writes data/prospects/gap-<date>.csv with: is it up,
what kind of site it looks like, outbound link counts (and how many are
nofollow), whether its home page links to a competitor, the submission pages
found, whether they have a form or "suggest a resource" wording, and any lines
about price, "free", "reciprocal" or "nofollow".

Env: GAP_FILE, MIN_COMPETITORS (default 2), MAX_RANK (default 3000000),
LIMIT (default 60), SKIP (comma-separated substrings of domains to ignore),
OUT_NAME (default gap-<date>.csv).
"""

import concurrent.futures as cf
import csv
import datetime as dt
import html
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
ROOT = Path(__file__).resolve().parent.parent
LINK_WORDS = re.compile(r'submit|suggest|recommend|add[-_ ]?(url|site|link|game|resource|your)|list[-_ ]?your|contact|advertis|write[-_ ]?for|guest|partner', re.I)
SUGGEST = re.compile(r'(suggest|submit|recommend|add|share)\s+(a|an|your)?\s*(resource|site|website|link|game|tool)', re.I)
TERMS = re.compile(r'(\$|€|£|usd|price|paid|payment|fee|free|reciprocal|backlink|link back|nofollow|sponsor)', re.I)
EDU = re.compile(r'teacher|student|school|classroom|curriculum|k-?12|grade|education|learning|math', re.I)


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'en'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.geturl(), r.status, r.read(500_000).decode('utf-8', 'replace')


def fetch(domain):
    for u in (f'https://{domain}/', f'https://www.{domain}/', f'http://{domain}/'):
        try:
            return get(u)
        except Exception:
            continue
    return None


def title_of(page):
    m = re.search(r'(?is)<title[^>]*>(.*?)</title>', page)
    return re.sub(r'\s+', ' ', html.unescape(m.group(1))).strip()[:90] if m else ''


def links(page, base):
    out = []
    for m in re.finditer(r'(?is)<a\s+([^>]*?)>(.*?)</a>', page):
        attrs, text = m.group(1), re.sub(r'(?s)<[^>]+>', ' ', m.group(2))
        h = re.search(r'href\s*=\s*["\']([^"\']+)', attrs)
        if not h:
            continue
        rel = re.search(r'rel\s*=\s*["\']([^"\']+)', attrs)
        url = urllib.parse.urljoin(base, html.unescape(h.group(1)))
        out.append((url, re.sub(r'\s+', ' ', html.unescape(text)).strip(), (rel.group(1).lower() if rel else '')))
    return out


def host(url):
    return (urllib.parse.urlparse(url).hostname or '').removeprefix('www.')


def check(domain, competitors):
    row = {'domain': domain, 'status': 'down', 'title': '', 'kind': '', 'outbound': '', 'outbound_nofollow': '',
           'links_to_competitor': '', 'competitor_rel': '', 'submit_pages': '', 'has_form': '', 'suggest_wording': '', 'hints': ''}
    res = fetch(domain)
    if not res:
        return row
    final, status, page = res
    row['status'] = status
    row['title'] = title_of(page)
    ls = links(page, final)
    own = host(final)
    ext = [(u, t, r) for u, t, r in ls if host(u) and host(u) != own and u.startswith('http')]
    row['outbound'] = len(ext)
    row['outbound_nofollow'] = sum(1 for _, _, r in ext if 'nofollow' in r or 'ugc' in r or 'sponsored' in r)
    comp = [(u, r) for u, _, r in ext if any(host(u) == c or host(u).endswith('.' + c) for c in competitors)]
    row['links_to_competitor'] = len({host(u) for u, _ in comp})
    row['competitor_rel'] = ','.join(sorted({('nofollow' if 'nofollow' in r else 'dofollow') for _, r in comp}))
    text = (row['title'] + ' ' + re.sub(r'(?s)<[^>]+>', ' ', page)[:20000]).lower()
    if len(text) < 400:
        row['kind'] = 'parked/empty'
    elif own.endswith(('.edu', '.k12.us')) or '.k12.' in own or re.search(r'school|district|isd|usd|edu', own) or (EDU.search(row['title']) and len(EDU.findall(text)) > 8):
        row['kind'] = 'school/education'
    elif len(ext) > 60:
        row['kind'] = 'directory/list'
    elif re.search(r'game|puzzle|arcade', row['title'].lower()):
        row['kind'] = 'game site'
    else:
        row['kind'] = 'other'
    cand = []
    for u, t, _ in ls:
        if host(u) == own and LINK_WORDS.search(t + ' ' + u) and u not in cand:
            cand.append(u)
    found, hints = [], []
    has_form = suggest = False
    for u in cand[:3] + ([final] if SUGGEST.search(page) else []):
        try:
            _, _, p = get(u) if u != final else (final, status, page)
        except Exception:
            continue
        if u != final:
            found.append(u)
        if re.search(r'(?i)<form[^>]*>.*?<(textarea|input)', p, re.S):
            has_form = True
        if SUGGEST.search(p):
            suggest = True
        t = re.sub(r'(?s)<[^>]+>', '\n', re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', p))
        for line in (html.unescape(x).strip() for x in t.splitlines()):
            if line and 8 < len(line) < 160 and TERMS.search(line) and len(hints) < 4:
                hints.append(line)
    row['submit_pages'] = ' | '.join(found)
    row['has_form'] = 'yes' if has_form else ''
    row['suggest_wording'] = 'yes' if suggest else ''
    row['hints'] = ' | '.join(hints)
    return row


def main():
    rel_dir = sorted(p.parent for p in (ROOT / 'data' / 'backlinks').glob('*/gap.csv'))[-1]
    gap_path = rel_dir / (os.environ.get('GAP_FILE') or 'gap.csv')
    gap = list(csv.DictReader(open(gap_path, encoding='utf-8')))
    competitors = {r['target'] for r in csv.DictReader(open(rel_dir / 'summary.csv', encoding='utf-8'))}
    minc = int(os.environ.get('MIN_COMPETITORS') or 2)
    maxr = int(os.environ.get('MAX_RANK') or 3_000_000)
    limit = int(os.environ.get('LIMIT') or 60)
    skip = [s.strip() for s in (os.environ.get('SKIP') or 'link-legion,github.io,google.com,netlify.app,telegra.ph,blogspot.com,fmhy,com.de').split(',') if s.strip()]
    pool = [g for g in gap
            if int(g['competitors_linked']) >= minc and int(g['harmonic_rank'] or 10**12) < maxr
            and not any(s in g['referring_domain'] for s in skip)]
    pool.sort(key=lambda g: (-int(g['competitors_linked']), int(g['harmonic_rank'] or 10**12)))
    todo = [g['referring_domain'] for g in pool][:limit]
    print(f'{len(todo)} domains to check from {gap_path}')
    with cf.ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(lambda d: check(d, competitors), todo))
    out = ROOT / 'data' / 'prospects' / (os.environ.get('OUT_NAME') or f'gap-{dt.date.today():%Y-%m-%d}.csv')
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(r['domain'], r['status'], r['kind'], 'form:', r['has_form'], 'suggest:', r['suggest_wording'], '| submit:', bool(r['submit_pages']))


if __name__ == '__main__':
    main()

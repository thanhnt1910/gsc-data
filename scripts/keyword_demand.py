#!/usr/bin/env python3
"""Measure search demand for a list of keywords (usually game names).

For each keyword in KEYWORDS (comma- or newline-separated, at most 60;
without it, the lines of data/keywords/watchlist.txt) it
collects three independent signals and writes them to
data/keywords/YYYY-MM-DD.csv:

  Google autocomplete  How many suggestions Google offers for the keyword,
                       and how many of them carry game intent ("... game",
                       "... online", "play ...", "... free").
  Google Trends        US interest over the last 90 days relative to an
                       anchor keyword (ANCHOR, default "minesweeper"), plus
                       a rising ratio: the last 4 weeks against the 8 weeks
                       before. Trends often answers 429 to cloud IPs; then
                       the columns say "blocked" and the other signals
                       still count.
  Bing                 Weekly search impressions on Bing (US, en-US) from
                       the Bing Webmaster keyword API, read with the same
                       BING_API_KEY as fetch_bing.py. Skipped without a key.

Each row ends with a verdict (high / medium / low / none) built from
whichever signals came back, and "rising" when Trends shows growth.
The Bing URL carries the API key, so it is never printed.
"""

import csv
import datetime as dt
import json
import os
import re
import statistics
import sys
import time

import requests

from fetch_gsc import DATA

OUT = DATA / 'keywords'
WATCHLIST = OUT / 'watchlist.txt'
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'}
GAME_INTENT = re.compile(r'\b(game|games|online|play|free|app|level|levels|puzzle|2 player|vs computer)\b')
MAX_KEYWORDS = 60
TRENDS_BATCH = 4  # Trends compares at most 5 terms; one slot goes to the anchor


def parse_keywords(raw: str) -> list[str]:
    seen, out = set(), []
    for part in re.split(r'[,\n]', raw or ''):
        kw = ' '.join(part.lower().split())
        if kw and kw not in seen:
            seen.add(kw)
            out.append(kw)
    return out[:MAX_KEYWORDS]


# ---------- Google autocomplete ----------

def autocomplete(session: requests.Session, kw: str) -> tuple[int, int, str]:
    try:
        r = session.get('https://suggestqueries.google.com/complete/search',
                        params={'client': 'firefox', 'hl': 'en', 'gl': 'us', 'q': kw}, timeout=20)
        suggestions = [s.lower() for s in r.json()[1]]
    except Exception as e:  # network hiccup or a non-JSON answer
        print(f'  autocomplete failed for {kw!r}: {e}')
        return 0, 0, ''
    related = [s for s in suggestions if kw in s]
    intent = [s for s in related if GAME_INTENT.search(s.replace(kw, ' '))]
    return len(related), len(intent), ' | '.join(suggestions[:5])


# ---------- Google Trends ----------

def _trends_json(text: str):
    return json.loads(text[text.index('\n') + 1:])  # strip the ")]}'" guard line


def _trends_get(session: requests.Session, url: str, params: dict):
    for wait in (0, 20, 60):
        if wait:
            time.sleep(wait)
        r = session.get(url, params=params, timeout=30)
        if r.status_code != 429:
            r.raise_for_status()
            return _trends_json(r.text)
    raise RuntimeError('429')


def trends_batch(session: requests.Session, terms: list[str]) -> dict[str, list[int]]:
    req = {'comparisonItem': [{'keyword': t, 'geo': 'US', 'time': 'today 3-m'} for t in terms],
           'category': 0, 'property': ''}
    explore = _trends_get(session, 'https://trends.google.com/trends/api/explore',
                          {'hl': 'en-US', 'tz': '0', 'req': json.dumps(req)})
    widget = next(w for w in explore['widgets'] if w['id'] == 'TIMESERIES')
    data = _trends_get(session, 'https://trends.google.com/trends/api/widgetdata/multiline',
                       {'hl': 'en-US', 'tz': '0', 'req': json.dumps(widget['request']), 'token': widget['token']})
    points = data['default']['timelineData']
    return {t: [p['value'][i] for p in points] for i, t in enumerate(terms)}


def trends(keywords: list[str], anchor: str) -> dict[str, tuple]:
    """keyword -> (interest relative to the anchor, rising ratio), or ('blocked', reason)."""
    session = requests.Session()
    session.headers.update(UA)
    try:
        session.get('https://trends.google.com/trends/explore', params={'geo': 'US'}, timeout=30)  # sets the NID cookie
    except requests.RequestException:
        pass
    result = {}
    for i in range(0, len(keywords), TRENDS_BATCH):
        batch = keywords[i:i + TRENDS_BATCH]
        try:
            series = trends_batch(session, [anchor, *batch])
        except Exception as e:
            reason = str(e)[:60]
            print(f'  Trends unavailable ({reason}); marking the rest as blocked.')
            for kw in keywords[i:]:
                result[kw] = ('blocked', reason)
            return result
        anchor_mean = statistics.mean(series[anchor]) or 1
        for kw in batch:
            values = series[kw]
            recent, before = values[-28:], values[:-28]
            rising = (statistics.mean(recent) / statistics.mean(before)) if before and statistics.mean(before) else None
            result[kw] = (round(statistics.mean(values) / anchor_mean, 3), round(rising, 2) if rising else '')
        time.sleep(5)  # stay well under Trends' rate limit
    return result


# ---------- Bing keyword stats ----------

def bing_weekly(session: requests.Session, key: str, kw: str):
    url = 'https://ssl.bing.com/webmaster/api.svc/json/GetKeywordStats'
    try:
        r = session.get(url, params={'q': kw, 'country': 'us', 'language': 'en-US', 'apikey': key}, timeout=30)
    except requests.RequestException as e:
        return f'error: {type(e).__name__}'
    if r.status_code != 200:
        return f'error: HTTP {r.status_code}'  # never echo the URL: it carries the key
    rows = r.json().get('d') or []
    weekly = [row.get('Impressions', 0) for row in rows if isinstance(row, dict)]
    return round(statistics.mean(weekly)) if weekly else 0


# ---------- verdict ----------
# Trends thresholds are relative to the default anchor, minesweeper, a
# very popular US search: 0.2 of it is a big game (freecell scores ~0.3),
# 0.02 a solid niche one (mahjong solitaire ~0.07).

def verdict(ac_intent: int, trend, bing) -> str:
    rel = trend[0] if trend and isinstance(trend[0], float) else None
    bing_n = bing if isinstance(bing, int) else None
    if (rel is not None and rel >= 0.2) or (bing_n is not None and bing_n >= 1000):
        return 'high'
    if (rel is not None and rel >= 0.02) or (bing_n is not None and bing_n >= 100) or ac_intent >= 2:
        return 'medium'
    if (rel is not None and rel > 0) or (bing_n or 0) > 0 or ac_intent >= 1:
        return 'low'
    return 'none'


def main() -> None:
    keywords = parse_keywords(os.environ.get('KEYWORDS', ''))
    if not keywords and WATCHLIST.exists():
        # Scheduled runs pass no list: track the standing watchlist instead.
        lines = [ln.split('#')[0] for ln in WATCHLIST.read_text(encoding='utf-8').splitlines()]
        keywords = parse_keywords('\n'.join(lines))
    if not keywords:
        sys.exit('No keywords: pass KEYWORDS or fill data/keywords/watchlist.txt.')
    anchor = (os.environ.get('ANCHOR') or 'minesweeper').strip().lower()
    key = os.environ.get('BING_API_KEY', '').strip()
    print(f'{len(keywords)} keywords, Trends anchor "{anchor}", Bing {"on" if key else "off (no BING_API_KEY)"}')

    session = requests.Session()
    session.headers.update(UA)
    trend = trends(keywords, anchor)

    rows = []
    for kw in keywords:
        ac_n, ac_intent, ac_top = autocomplete(session, kw)
        bing = bing_weekly(session, key, kw) if key else ''
        t = trend.get(kw, ('', ''))
        rising = isinstance(t[1], float) and t[1] >= 1.3
        v = verdict(ac_intent, t, bing)
        rows.append({
            'keyword': kw,
            'verdict': v + (' + rising' if rising else ''),
            'trends_vs_anchor': t[0],
            'trends_rising_ratio': t[1],
            'bing_weekly_impressions': bing,
            'autocomplete_related': ac_n,
            'autocomplete_game_intent': ac_intent,
            'autocomplete_top': ac_top,
        })
        time.sleep(0.3)

    order = {'high': 0, 'medium': 1, 'low': 2, 'none': 3}
    rows.sort(key=lambda r: (order[r['verdict'].split()[0]], r['keyword']))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'{dt.date.today().isoformat()}.csv'
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    for r in rows:
        print(f"  {r['verdict']:16} trends={r['trends_vs_anchor']!s:8} rising={r['trends_rising_ratio']!s:5} "
              f"bing={r['bing_weekly_impressions']!s:7} ac={r['autocomplete_game_intent']}/{r['autocomplete_related']}  {r['keyword']}")
    print(f'Wrote {path.relative_to(DATA.parent)}')


if __name__ == '__main__':
    main()

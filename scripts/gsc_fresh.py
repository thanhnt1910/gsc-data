#!/usr/bin/env python3
"""Print Search Console data for the last 7 days including fresh
(not yet finalized) days, the same numbers the GSC web UI shows.
Run by the on-demand realtime workflow; prints only, commits nothing."""

import datetime as dt
import sys
from urllib.parse import quote

from fetch_gsc import API, ApiError, call, make_session, pick_site


def query(session, site, dims, days=7, limit=25):
    end = dt.date.today()
    body = {'startDate': (end - dt.timedelta(days=days)).isoformat(), 'endDate': end.isoformat(),
            'dimensions': dims, 'type': 'web', 'dataState': 'all', 'rowLimit': limit}
    return call(session, 'POST', f'{API}/sites/{quote(site, safe="")}/searchAnalytics/query', body).get('rows', [])


def main():
    session = make_session()
    site = pick_site(session)
    print('## GSC by day (fresh data included)\ndate | clicks | impressions | position')
    for r in query(session, site, ['date'], limit=50):
        print(f"{r['keys'][0]} | {int(r['clicks'])} | {int(r['impressions'])} | {r['position']:.1f}")
    for dims, title in ((['page'], 'pages'), (['query'], 'queries')):
        print(f'\n## GSC top {title}, last 7 days (fresh)\n{title} | clicks | impressions | position')
        for r in sorted(query(session, site, dims), key=lambda r: -r['impressions']):
            print(f"{r['keys'][0]} | {int(r['clicks'])} | {int(r['impressions'])} | {r['position']:.1f}")


if __name__ == '__main__':
    try:
        main()
    except ApiError as e:
        sys.exit(str(e))

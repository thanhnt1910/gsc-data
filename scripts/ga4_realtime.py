#!/usr/bin/env python3
"""Print GA4 activity for the last 30 minutes and for today so far.

Run on demand (Actions > GA4 realtime > Run workflow) to see who is on the
site right now: which pages, which play events (game_start,
game_play_60s, game_play_300s), from where and on what device. Prints
to the job log only; nothing is committed.
"""

import datetime as dt
import sys

from fetch_ga4 import DATA_API, SCOPES, pick_property
from fetch_gsc import ApiError, call, make_session


def realtime(session, prop, dims, metrics, minutes=29):
    body = {
        'dimensions': [{'name': d} for d in dims],
        'metrics': [{'name': m} for m in metrics],
        'minuteRanges': [{'startMinutesAgo': minutes, 'endMinutesAgo': 0}],
        'limit': 1000,
    }
    return call(session, 'POST', f'{DATA_API}/{prop}:runRealtimeReport', body).get('rows', [])


def today(session, prop, dims, metrics):
    d = dt.date.today().isoformat()
    body = {
        'dateRanges': [{'startDate': d, 'endDate': d}],
        'dimensions': [{'name': x} for x in dims],
        'metrics': [{'name': m} for m in metrics],
        'limit': 1000,
    }
    return call(session, 'POST', f'{DATA_API}/{prop}:runReport', body).get('rows', [])


def show(title, dims, metrics, rows):
    print(f'\n## {title} ({len(rows)} rows)')
    print(' | '.join(dims + metrics))
    for r in rows:
        print(' | '.join([v['value'] for v in r.get('dimensionValues', [])] + [v['value'] for v in r['metricValues']]))


def main():
    session = make_session(SCOPES)
    prop = pick_property(session)
    print(f'GA4 property: {prop}, UTC now {dt.datetime.utcnow():%Y-%m-%d %H:%M}')
    q = [
        ('Realtime: users by page', ['unifiedScreenName'], ['activeUsers', 'screenPageViews']),
        ('Realtime: events', ['eventName'], ['eventCount']),
        ('Realtime: users by minute', ['minutesAgo'], ['activeUsers']),
        ('Realtime: where from', ['country', 'city', 'deviceCategory'], ['activeUsers']),
    ]
    for title, dims, metrics in q:
        try:
            show(title, dims, metrics, realtime(session, prop, dims, metrics))
        except ApiError as e:
            print(f'\n## {title}: skipped ({str(e).splitlines()[0]})')
    t = [
        ('Today: pages', ['pagePath'], ['screenPageViews', 'activeUsers', 'userEngagementDuration']),
        ('Today: play events by page', ['pagePath', 'eventName'], ['eventCount']),
        ('Today: sessions by source', ['sessionSource', 'sessionMedium'], ['sessions', 'engagedSessions', 'averageSessionDuration']),
        ('Today: sessions by hour (UTC) and city', ['hour', 'city', 'deviceCategory'], ['sessions', 'averageSessionDuration', 'screenPageViews']),
    ]
    for title, dims, metrics in t:
        try:
            show(title, dims, metrics, today(session, prop, dims, metrics))
        except ApiError as e:
            print(f'\n## {title}: skipped ({str(e).splitlines()[0]})')


if __name__ == '__main__':
    try:
        main()
    except ApiError as e:
        sys.exit(str(e))

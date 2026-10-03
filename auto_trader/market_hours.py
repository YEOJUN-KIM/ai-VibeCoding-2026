"""Read regular-session status from broker calendar timestamps, never weekday guesses."""
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

KST = timezone(timedelta(hours=9))
NEW_YORK = ZoneInfo('America/New_York')

def regular_status(calendar, market, now):
    today = calendar.get('today')
    if not isinstance(today, dict):
        raise ValueError('Missing market day')
    sessions = []
    for key in ('previousBusinessDay', 'today', 'nextBusinessDay'):
        day = calendar.get(key)
        if not isinstance(day, dict):
            continue
        container = day.get('integrated') if market == 'KR' else day
        if container is None:
            continue
        if not isinstance(container, dict) or 'regularMarket' not in container:
            raise ValueError('Missing regular session')
        session = container['regularMarket']
        if session is None:
            continue
        start = datetime.fromisoformat(session['startTime'].replace('Z', '+00:00'))
        end = datetime.fromisoformat(session['endTime'].replace('Z', '+00:00'))
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError('Invalid session timestamps')
        sessions.append((start, end))
    active = next(((start, end) for start, end in sessions if start <= now < end), None)
    future = sorted(start for start, end in sessions if start > now)
    container = today.get('integrated') if market == 'KR' else today
    holiday = container is None or container.get('regularMarket') is None
    return {'state': 'open' if active else 'closed',
            'basis': '국내 정규장' if market == 'KR' else '미국 정규장',
            'holiday': holiday, 'calendar_date': today.get('date'),
            'next_open_at': future[0].isoformat() if future else None,
            'closes_at': active[1].isoformat() if active else None}

def trading_status(calendar, now):
    """Continuous KR sessions only: paper fills cannot model opening/closing auctions."""
    today = calendar.get('today')
    if not isinstance(today, dict) or today.get('date') != now.astimezone(KST).date().isoformat():
        raise ValueError('Calendar date mismatch')
    windows = []
    holiday = True
    for key in ('today', 'nextBusinessDay'):
        day = calendar.get(key)
        if not isinstance(day, dict):
            continue
        if 'integrated' not in day:
            raise ValueError('Missing integrated sessions')
        container = day['integrated']
        if container is None:
            continue
        if not isinstance(container, dict):
            raise ValueError('Invalid integrated sessions')
        for name in ('preMarket', 'regularMarket', 'afterMarket'):
            if name not in container:
                raise ValueError('Missing session')
            session = container[name]
            if session is None:
                continue
            if key == 'today':
                holiday = False
            start = datetime.fromisoformat(session['startTime'].replace('Z', '+00:00'))
            end = datetime.fromisoformat(session['endTime'].replace('Z', '+00:00'))
            if start.tzinfo is None or end.tzinfo is None or end <= start:
                raise ValueError('Invalid session timestamps')
            for field in ('singlePriceAuctionEndTime', 'singlePriceAuctionStartTime'):
                if session.get(field):
                    boundary = datetime.fromisoformat(session[field].replace('Z', '+00:00'))
                    if boundary.tzinfo is None or not start <= boundary <= end:
                        raise ValueError('Invalid auction boundary')
                    if field == 'singlePriceAuctionEndTime':
                        start = boundary
                    else:
                        end = boundary
            if start < end:
                windows.append((start, end))
    active = next(((start, end) for start, end in windows if start <= now < end), None)
    future = sorted(start for start, end in windows if start > now)
    return {'state': 'open' if active else 'closed', 'holiday': holiday,
            'calendar_date': today['date'],
            'opens_at': active[0].isoformat() if active else None,
            'closes_at': active[1].isoformat() if active else None,
            'next_open_at': future[0].isoformat() if future else None}

class MarketHours:
    def __init__(self, client):
        self.client = client
        self.cache = {}
        self.lock = Lock()

    def status(self, market, client=None, *, now=None, trading=False):
        client = client if client is not None else self.client
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(KST if market == 'KR' else NEW_YORK).date().isoformat()
        key = (client, market)
        try:
            with self.lock:
                saved = self.cache.get(key)
                if saved and saved[0] == day and monotonic() < saved[1]:
                    calendar = saved[2]
                else:
                    payload = client._authorized_json_request(
                        f'{client.base_url}/api/v1/market-calendar/{market}?{urlencode({"date": day})}')
                    calendar = payload.get('result')
                    if not isinstance(calendar, dict):
                        raise ValueError('Invalid calendar')
                    regular_status(calendar, market, now)
                    self.cache[key] = (day, monotonic() + 60, calendar)
            return trading_status(calendar, now) if trading else regular_status(calendar, market, now)
        except Exception:
            with self.lock:
                saved = self.cache.get(key)
                if not (saved and saved[0] == day and saved[2] is None and monotonic() < saved[1]):
                    self.cache[key] = (day, monotonic() + 30, None)
            return {'state': 'unknown', 'basis': '국내 정규장' if market == 'KR' else '미국 정규장'}

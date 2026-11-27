"""UI-only mock: no application imports, DB, credentials or outbound connections.

Run python -B scripts/pagination_mock.py --port 55449, then open /paper-mvp,
/stocks or /long-term. All data is synthetic. Only this mock's /__mock state
can change; every POST/PUT/DELETE is rejected. Never use a product app as fallback.
"""
import argparse
import json
import math
import mimetypes
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'auto_trader/static'
STATE = dict(records=1074, holdings=1074, directory=1074)
NOW = datetime(2026, 11, 25, 2, tzinfo=timezone.utc)


def paper_status(period):
    holdings = [dict(symbol=f'{i:06}', name=f'Mock {i:04}', quantity=1, value='10000', profit='0', profit_pct='0',
                     lots=[dict(quantity=1, average_price='10000', stop_pct='3', take_pct='6', max_days=5, opened_at=NOW.isoformat())])
                for i in range(STATE['holdings'])]
    # Every existing category plus an unknown category; filter tests must retain all.
    kinds = ['CONTROL', 'WATCH', 'CANDIDATE', 'HOLD_DATA', 'UNKNOWN', 'BUY', 'SELL', 'REJECTED']
    control_titles = ['자동매매 시작', '일시정지', '긴급정지', '투자 설정 적용', '설정 복구 실패']
    trades, decisions = [], []
    for i in range(STATE['records']):
        kind = kinds[i % len(kinds)]
        at = (NOW - timedelta(seconds=i)).isoformat()
        if kind in ('BUY', 'SELL', 'REJECTED'):
            trades.append(dict(at=at, side='SELL' if kind == 'SELL' else 'BUY', status='REJECTED' if kind == 'REJECTED' else 'FILLED',
                               symbol=f'{i:06}', name=f'Mock trade {i}', quantity=1, price='10000', fee='0', reason=f'Mock reason {i}', message='Mock only', realized='0' if kind == 'SELL' else None))
        else:
            title = control_titles[(i // 8) % len(control_titles)] if kind == 'CONTROL' else f'Mock {kind} {i}'
            decisions.append(dict(at=at, kind=kind, title=title, reason=f'Mock reason {i}', symbols=[]))
    return dict(checked_at=NOW.isoformat(), data_source='SYNTHETIC', ready_reason='',
                account=dict(total_asset='10000000', initial_cash='10000000', cash='10000000', holdings_value='0', total_profit='0', unrealized_profit='0', realized_profit='0', total_fees='0', return_percent='0'),
                settings=dict(budget=5000000), pnl=dict(period=period, gain='0', percent='0', basis='Synthetic fixture'), series=[],
                run=dict(state='idle', running=False, label='Mock idle', message='Synthetic only', held_symbols=[], stop_note='Mock only'),
                risk=dict(invested_amount='0', new_buys_allowed=True, block_reason=''), holdings=holdings, trades=trades, decisions=decisions)


def directory(query):
    size = int(query.get('page_size', ['20'])[0])
    total = STATE['directory']
    pages = max(1, math.ceil(total / size))
    page = max(1, min(pages, int(query.get('page', ['1'])[0])))
    etf = query.get('security_type', ['STOCK'])[0] == 'ETF'
    items = [dict(symbol=f'{i:06}', name=f'Mock {i:04}', market='KOSPI', security_type='ETF' if etf else 'STOCK', is_common_share=True,
                  price=10000, previous_close=10000, change_rate_percent=0, market_cap=1000000000, trading_amount=1000000, is_favorite=False)
             for i in range((page-1)*size, min(page*size, total))]
    return dict(page=page, page_size=size, total_pages=pages, total=total, results=items)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, data, content_type='application/json', status=200):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        # Prevent synthetic pages/scripts/images from reaching any other origin.
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        path = url.path
        if path == '/__mock':
            for key in STATE:
                if key in query:
                    STATE[key] = max(0, min(5000, int(query[key][0])))
            return self.send(STATE)
        if path == '/__checks.js':
            return self.send((ROOT / 'tests/pagination_browser.js').read_bytes(), 'application/javascript')
        if path in ('/paper-mvp', '/stocks', '/long-term'):
            html = (STATIC / (path[1:] + '.html')).read_text(encoding='utf-8')
            html = html.replace('</body>', '<script src="/__checks.js"></script></body>')
            return self.send(html.encode('utf-8'), 'text/html')
        if path.startswith('/static/'):
            relative = path.removeprefix('/static/')
            target = STATIC / relative
            if '/' not in relative and '\\' not in relative and target.suffix in ('.js', '.css', '.svg') and target.is_file():
                return self.send(target.read_bytes(), mimetypes.guess_type(target.name)[0] or 'text/plain')
            return self.send({}, status=404)
        if path == '/auth/me':
            return self.send(dict(username='mock-only', csrf_token='synthetic-public-fixture'))
        if path == '/paper/mvp/status':
            return self.send(paper_status(query.get('period', ['all'])[0]))
        if path == '/live/stocks/list':
            return self.send(directory(query))
        if path == '/research/long-term/watchlist':
            return self.send([dict(symbol=f'{i:06}', name=f'Mock {i:04}', market='KOSPI', analysis=None, created_at=NOW.isoformat(), note='Mock', added_manually=True) for i in range(STATE['directory'])])
        if path in ('/research/long-term/candidates', '/live/favorites'):
            return self.send([])
        if path.endswith('candidate-status') or path.endswith('watchlist-status'):
            return self.send(dict(running=False, phase='idle', message='Synthetic mock'))
        if path == '/live/stocks/sparklines':
            return self.send(dict(results=[]))
        if path == '/live/stocks/stream':
            return self.send(b'data: {"type":"status","state":"connected"}\n\n', 'text/event-stream')
        return self.send(dict(status='mock', ready=False))

    def do_POST(self):
        self.send(dict(detail='UI mock rejects all writes'), status=405)

    do_PUT = do_DELETE = do_PATCH = do_POST


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=55449)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'UI mock only: http://127.0.0.1:{args.port}/paper-mvp', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

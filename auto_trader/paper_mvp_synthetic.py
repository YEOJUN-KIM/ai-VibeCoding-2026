"""Fictional, deterministic market for PAPER-MVP-01 verification (tests and the isolated demo server only).

Not FOLIO RAW/ML data and never read from or written to the DB. Prices are made up; a virtual clock stands in
for wall time. The real app never constructs this: without a data source PaperMvp simply holds.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal as D

from . import day01_scout as sc
from .day01_scout_orderbook import parse_orderbook
from .models import LiveStockCandle
from .paper_mvp import PipelineInput
from .trading import KST

DAY = datetime(2026, 11, 19, 9, 0, tzinfo=KST)   # a Thursday


class VirtualClock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> datetime:
        self.now += timedelta(**kw)
        return self.now


def _price(kind: str, i: int) -> D:
    base = 50000
    if kind == "rise_take":          # entry signal near minute 31, then a +16% run: take profit
        return D(base if i < 30 else base + 400 * min(i - 29, 20))
    if kind == "rise_fall":          # small rise (entry), then falls: moving-average / stop exit
        return D(base if i < 30 else base + 300 * (i - 29) if i < 38 else 52400 - 700 * (i - 37))
    return D(base)                   # flat: a valid candidate that never gives a buy signal


# symbol -> (name, price pattern, defect)  defect: None | 'no_book' | 'gap'
SCENARIO = {
    "100001": ("합성전자A", "rise_take", None),
    "100002": ("합성화학B", "rise_fall", None),
    "100003": ("합성금융C", "flat", None),
    "100004": ("합성호가D", "rise_take", "no_book"),   # excluded by Scout: no usable order book
    "100005": ("합성결측E", "rise_take", "gap"),       # excluded by Scout: missing minute bars
}


class SyntheticSource:
    """`MvpDataSource` over SCENARIO. `degrade` switches (tests): 'unavailable', 'stale', 'no_clock', 'empty'."""

    def __init__(self, clock: VirtualClock, scenario=None, *, clock_uncertainty=timedelta(seconds=1)):
        self.clock, self.scenario = clock, dict(scenario or SCENARIO)
        self.clock_uncertainty = clock_uncertainty
        self.degrade: str | None = None

    # ---- series
    def _index(self, t: datetime) -> int:
        return int((t - DAY).total_seconds() // 60)

    def _bar_indexes(self, symbol, now):
        defect = self.scenario[symbol][2]
        last = self._index(now) - 1                     # completed minute bars
        return [i for i in range(0, last + 1) if not (defect == "gap" and i == 7)]

    def name(self, symbol):
        return self.scenario[symbol][0]

    def price(self, symbol, now):
        if self.degrade == "unavailable" or symbol not in self.scenario:
            return None
        i = self._index(now) - 1
        return _price(self.scenario[symbol][1], max(i, 0)) if i >= 0 else None

    def bars(self, symbol, now, count):
        kind = self.scenario[symbol][1]
        out = []
        for i in self._bar_indexes(symbol, now)[-count:]:
            p = _price(kind, i)
            out.append(LiveStockCandle(timestamp=DAY + timedelta(minutes=i), open_price=p, high_price=p, low_price=p,
                                       close_price=p, volume=D(100)))
        return out

    # ---- Scout input
    def pipeline_input(self, now):
        if self.degrade in ("unavailable", "empty"):
            return None
        as_of = now - timedelta(minutes=10) if self.degrade == "stale" else now
        rows = []
        for symbol, (_, kind, defect) in self.scenario.items():
            for i in self._bar_indexes(symbol, as_of):
                event = DAY + timedelta(minutes=i)
                avail = event + timedelta(minutes=1, seconds=2)
                if avail > as_of:
                    continue
                p, v = _price(kind, i), D(100)
                rows.append(dict(source="TOSS", symbol=symbol, interval="1m", event_at=event, available_at=avail,
                                 collected_at=avail, open_price=p, high_price=p, low_price=p, close_price=p,
                                 volume=v, turnover=p * v))
        symbols = list(self.scenario)
        ranking = [{"rank": n, "symbol": s, "tradingAmount": "20000000000", "tradingVolume": "400000",
                    "price": {"lastPrice": str(_price(self.scenario[s][1], max(self._index(as_of) - 1, 0))), "changeRate": "1"}}
                   for n, s in enumerate(symbols, 1)]
        snapshot = sc.snapshot_from_rankings(as_of - timedelta(seconds=20), ranking, received_at=as_of - timedelta(seconds=10))
        instruments = [sc.InstrumentFacts(s, as_of - timedelta(seconds=3), market="KOSPI", currency="KRW", security_type="STOCK",
                                          is_common_share=True, status="ACTIVE", krx_trading_suspended=False,
                                          liquidation_trading=False) for s in symbols]
        books = []
        for s in symbols:
            if self.scenario[s][2] == "no_book":
                continue
            price = _price(self.scenario[s][1], max(self._index(as_of) - 1, 0))
            body = {"result": {"timestamp": (as_of - timedelta(seconds=3)).isoformat(), "currency": "KRW",
                               "asks": [{"price": str(price), "volume": "100"}],
                               "bids": [{"price": str(price - 5), "volume": "100"}]}}
            books.append(parse_orderbook(s, body, received_at=as_of - timedelta(seconds=2)))
        return PipelineInput(as_of=as_of, snapshot=snapshot, candle_rows=tuple(rows), orderbooks=tuple(books),
                             instruments=tuple(instruments),
                             clock_uncertainty=None if self.degrade == "no_clock" else self.clock_uncertainty,
                             collection_universe=tuple(symbols))

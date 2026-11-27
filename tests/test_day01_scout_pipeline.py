"""Synthetic end-to-end tests: ranking + candle readiness + order book -> Scout -> DAY-01 replay.
No network, DB, files, clock or broker. All data is made up in this file."""
import ast
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from auto_trader import day01_scout as sc
from auto_trader import day01_scout_pipeline as pipe
from auto_trader.day01_scout_link import WatchTimeline
from auto_trader.day01_scout_orderbook import parse_orderbook
from auto_trader.day01_scout_pipeline import EXCLUDED, HOLD, PASS, PROCEED, run_scout_pipeline
from auto_trader.ml.day01 import Config, KST, replay
from auto_trader.ml.day01_research import adapt_rows

CFG = Config(execution='research_estimated_open', vwap_mode='exact')     # lenient timing (no on-time-bar rule), exact VWAP
STRICT = Config(execution='legacy_close', vwap_mode='exact')            # strict timing: bar must arrive at its minute end
APPROX = Config(execution='research_estimated_open', vwap_mode='typical_approximation')
AS_OF = datetime(2026, 11, 13, 9, 19, 50, tzinfo=KST)
SAFE = timedelta(seconds=1)


def at(h, m, s=0):
    return datetime(2026, 11, 13, h, m, s, tzinfo=KST)


def bar(symbol, i, price='5000', volume='100', *, delay=2, turnover=True, **changes):
    event = at(9, 0) + timedelta(minutes=i)
    avail = event + timedelta(minutes=1, seconds=delay)
    p, v = D(price), D(volume)
    row = dict(source='TOSS', symbol=symbol, interval='1m', event_at=event, available_at=avail, collected_at=avail,
               open_price=p, high_price=p, low_price=p, close_price=p, volume=v)
    if turnover:
        row['turnover'] = p * v
    row.update(changes)
    return row


def breakout(symbol, *, delay=2, count=20, zero_volume=False, **kw):
    """Flat 5000 opening range, 09:15..09:19 at 5100 with doubled volume (DAY-01 entry pattern)."""
    base, up = ('0', '0') if zero_volume else ('100', '200')
    rows = [bar(symbol, i, volume=base, delay=delay, **kw) for i in range(15)]
    rows += [bar(symbol, i, '5100', up, delay=delay, **kw) for i in range(15, count)]
    return rows


def snap(symbols, *, received=at(9, 19, 40), ranked=at(9, 19, 30)):
    rows = [{"rank": i, "symbol": s, "tradingAmount": "20000000000", "tradingVolume": "100000",
             "price": {"lastPrice": "5000", "changeRate": "1"}} for i, s in enumerate(symbols, 1)]
    return sc.snapshot_from_rankings(ranked, rows, received_at=received)


def instruments(symbols, observed=at(9, 19, 47)):
    return [sc.InstrumentFacts(s, observed, market='KOSPI', currency='KRW', security_type='STOCK',
                               is_common_share=True, status='ACTIVE', krx_trading_suspended=False,
                               liquidation_trading=False) for s in symbols]


def portfolio(cfg=CFG, **kw):
    from auto_trader.day01_scout_link import portfolio_limits_for
    return sc.Portfolio(at(9, 19, 47), cash='1000000', total_asset='1000000', **portfolio_limits_for(cfg), **kw)


def book(symbol, *, bid='4995', ask='5000', provider=at(9, 19, 47), received=at(9, 19, 48), **payload):
    body = {"result": {"timestamp": provider.isoformat(), "currency": "KRW",
                       "asks": [{"price": ask, "volume": "100"}], "bids": [{"price": bid, "volume": "100"}]}}
    body["result"].update(payload)
    return parse_orderbook(symbol, body, received_at=received)


def run(symbols, rows, books=None, *, cfg=CFG, clock=SAFE, as_of=AS_OF, snapshot=None, **kw):
    books = [book(s) for s in symbols] if books is None else books
    return run_scout_pipeline(as_of=as_of, snapshot=snapshot or snap(symbols), candle_rows=rows,
                              orderbook_results=books, instruments=instruments(symbols), portfolio=portfolio(cfg),
                              day01_config=cfg, clock_uncertainty=clock, **kw)


def by_symbol(result):
    return {d.symbol: d for d in result.symbols}


class FullFlowTests(unittest.TestCase):
    """Eight candidates, each failing (or passing) for a different reason."""
    NAMES = ['GOOD01', 'GOOD02', 'NOCNDL', 'GAPPED', 'ZEROVL', 'WILD01', 'STALEB', 'NOBOOK']

    def setUp(self):
        rows = breakout('GOOD01') + breakout('GOOD02')
        rows += [r for r in breakout('GAPPED') if r['event_at'] != at(9, 7)]                # missing minute
        rows += breakout('ZEROVL', zero_volume=True)                                              # traded volume 0 all window
        rows += [dict(r, high_price=r['high_price'] * 2) if r['event_at'] == at(9, 10) else r
                 for r in breakout('WILD01')]                                                # high 2x low in one bar
        rows += breakout('STALEB') + breakout('NOBOOK')
        books = [book('GOOD01'), book('GOOD02'), book('GAPPED'), book('ZEROVL'), book('WILD01'),
                 book('STALEB', provider=at(9, 18, 0), received=at(9, 18, 1)),           # old book
                 ]
        self.result = run(self.NAMES, rows, books)

    def test_decision_and_counts(self):
        r = self.result
        self.assertEqual(r.decision, PROCEED)
        self.assertEqual(r.hold_reasons, ())
        self.assertEqual(sorted(r.passed), ['GOOD01', 'GOOD02'])
        self.assertEqual(sum(d.verdict == EXCLUDED for d in r.symbols), 6)
        self.assertEqual(r.exact_vwap_symbols, ('GOOD01', 'GOOD02'))
        self.assertEqual(r.approximate_vwap_symbols, ())

    def test_every_exclusion_has_a_named_reason(self):
        d = by_symbol(self.result)
        for name in ('NOCNDL', 'GAPPED', 'ZEROVL', 'WILD01', 'STALEB', 'NOBOOK'):
            self.assertEqual(d[name].verdict, EXCLUDED, name)
            self.assertTrue(d[name].reasons, name)
        self.assertIn('NO_CANDLES', d['NOCNDL'].candle_codes)
        self.assertIn('DAY01_READINESS_MISSING', d['NOCNDL'].reasons)
        self.assertIn('OPENING_MISSING', d['GAPPED'].candle_codes)
        self.assertIn('DAY01_VWAP_NOT_READY', d['GAPPED'].reasons)
        self.assertIn('VWAP_ZERO_VOLUME', d['ZEROVL'].candle_codes)
        self.assertIn('DAY01_VWAP_NOT_READY', d['ZEROVL'].reasons)
        self.assertIn('IMPLAUSIBLE_PRICE', d['WILD01'].candle_codes)
        self.assertEqual(d['STALEB'].orderbook, 'STALE_AT_AS_OF')
        self.assertIn('LIQUIDITY_SPREAD_UNKNOWN', d['STALEB'].reasons)
        self.assertEqual(d['NOBOOK'].orderbook, 'NONE')
        self.assertIn('LIQUIDITY_SPREAD_UNKNOWN', d['NOBOOK'].reasons)
        self.assertEqual(d['GOOD01'].orderbook, 'OK_USED')
        self.assertEqual(d['GOOD01'].spread_pct is not None, True)

    def test_replay_only_trades_what_the_pipeline_passed(self):
        rows = []
        for n in self.NAMES:
            rows += breakout(n, delay=0)                      # replay fills use legacy_close (candle-only)
            rows += [bar(n, i, '5100', '100', delay=0) for i in range(20, 24)]   # later bars (not seen by the pipeline)
        candles = adapt_rows(rows, universe=self.NAMES).candles
        timeline = WatchTimeline([self.result.scout])
        out = replay(candles, STRICT, universe=self.NAMES, entry_allowed=timeline.entry_allowed)
        bought = sorted({t['symbol'] for t in out['trades'] if t['side'] == 'BUY'})
        self.assertEqual(bought, ['GOOD01', 'GOOD02'])
        vetoed = {x['symbol'] for x in out['reasons'] if x['reason'] == 'SCOUT_NOT_WATCHED'}
        self.assertTrue({'STALEB', 'NOBOOK', 'ZEROVL', 'WILD01', 'GAPPED'} <= vetoed, vetoed)
        self.assertFalse(vetoed & {'GOOD01', 'GOOD02'})


class HoldTests(unittest.TestCase):
    def test_no_data_at_all_holds(self):
        r = run(['AAAAAA'], [], books=[])
        self.assertEqual(r.decision, HOLD)
        self.assertEqual(r.passed, ())
        self.assertIn('NO_CANDIDATES', r.hold_reasons)
        self.assertIn('NO_CANDLE_DATA', r.hold_reasons)
        self.assertIn('NO_USABLE_ORDERBOOK', r.hold_reasons)

    def test_missing_ranking_holds_with_scout_status(self):
        late = snap(['AAAAAA'], received=at(9, 21, 0), ranked=at(9, 20, 55))              # received after as_of
        r = run(['AAAAAA'], breakout('AAAAAA'), snapshot=late)
        self.assertEqual(r.decision, HOLD)
        self.assertIn('SCOUT_NO_SNAPSHOT', r.hold_reasons)
        self.assertEqual(r.symbols, ())                                                # not-yet-received rows are not listed
        self.assertIn('NO_RANKING_DATA', r.hold_reasons)

    def test_stale_ranking_holds(self):
        old = snap(['AAAAAA'], received=at(9, 10, 0), ranked=at(9, 9, 55))
        r = run(['AAAAAA'], breakout('AAAAAA'), snapshot=old)
        self.assertEqual(r.decision, HOLD)
        self.assertEqual(r.passed, ())
        self.assertTrue(any(h.startswith('SCOUT_') or h == 'NO_CANDIDATES' for h in r.hold_reasons))

    def test_only_unusable_books_holds_even_with_perfect_candles(self):
        bad = [book('AAAAAA', bid='5000', ask='5000')]                                    # locked book
        r = run(['AAAAAA'], breakout('AAAAAA'), bad)
        self.assertEqual(r.decision, HOLD)
        self.assertTrue(by_symbol(r)['AAAAAA'].orderbook.startswith('UNKNOWN:'))
        self.assertIn('NO_USABLE_ORDERBOOK', r.hold_reasons)


class OrderbookSafetyTests(unittest.TestCase):
    def test_unknown_and_failed_books_are_never_normal_liquidity(self):
        names = ['AAAAAA', 'BBBBBB', 'CCCCCC', 'DDDDDD']
        rows = sum((breakout(n) for n in names), [])
        books = [book('AAAAAA', currency=None),                                           # CURRENCY_MISSING -> UNKNOWN
                 book('BBBBBB', timestamp=None),                                          # no provider time
                 parse_orderbook('CCCCCC', {"result": None}, received_at=at(9, 19, 48)),  # malformed
                 book('DDDDDD', bid='1000', ask='5000')]                                  # 133% spread -> ANOMALY
        r = run(names, rows, books)
        d = by_symbol(r)
        self.assertEqual(r.passed, ())
        for n in names:
            self.assertIsNone(d[n].spread_pct)
            self.assertIn('LIQUIDITY_SPREAD_UNKNOWN', d[n].reasons)
        self.assertTrue(d['DDDDDD'].orderbook.startswith('ANOMALY'))

    def test_wide_but_valid_spread_is_excluded_by_scouts_own_gate(self):
        r = run(['AAAAAA'], breakout('AAAAAA'), [book('AAAAAA', bid='4900', ask='5000')])       # ~2.02 % > 1 %
        self.assertEqual(r.passed, ())
        self.assertEqual(by_symbol(r)['AAAAAA'].orderbook, 'OK_USED')
        self.assertTrue(any('SPREAD' in x for x in by_symbol(r)['AAAAAA'].reasons))

    def test_book_received_after_as_of_does_not_exist_yet(self):
        late = book('AAAAAA', provider=at(9, 19, 52), received=at(9, 19, 53))
        r = run(['AAAAAA'], breakout('AAAAAA'), [late])
        self.assertEqual(by_symbol(r)['AAAAAA'].orderbook, 'NOT_YET_RECEIVED')
        self.assertEqual(r.passed, ())

    def test_book_becomes_stale_as_the_decision_time_moves_on(self):
        b = book('AAAAAA')                                                                # provider time 09:19:47
        ok = run(['AAAAAA'], breakout('AAAAAA'), [b])
        self.assertEqual(ok.passed, ('AAAAAA',))
        later = run(['AAAAAA'], breakout('AAAAAA'), [b], as_of=at(9, 19, 59, ))              # age 12 s > 10 s
        self.assertEqual(by_symbol(later)['AAAAAA'].orderbook, 'STALE_AT_AS_OF')
        self.assertEqual(later.passed, ())

    def test_duplicate_conflicting_books_are_not_used(self):
        r = run(['AAAAAA'], breakout('AAAAAA'), [book('AAAAAA'), book('AAAAAA', bid='4990')])
        self.assertEqual(by_symbol(r)['AAAAAA'].orderbook, 'AMBIGUOUS')
        self.assertEqual(r.passed, ())
        self.assertIn(('AAAAAA', 'ORDERBOOK_DUPLICATE_RESULT'), r.notes)

    def test_book_for_a_symbol_outside_the_ranking_is_ignored_and_noted(self):
        r = run(['AAAAAA'], breakout('AAAAAA'), [book('AAAAAA'), book('ZZZ99Z')])
        self.assertEqual(r.passed, ('AAAAAA',))
        self.assertIn(('ZZZ99Z', 'ORDERBOOK_SYMBOL_NOT_IN_RANKING'), r.notes)


class ClockUncertaintyTests(unittest.TestCase):
    def test_undeclared_clock_holds_even_with_perfect_data(self):
        r = run(['AAAAAA'], breakout('AAAAAA'), clock=None)
        self.assertEqual(r.decision, HOLD)
        self.assertFalse(r.clock_declared)
        self.assertEqual(r.passed, ())
        self.assertIn('CLOCK_UNCERTAINTY_UNDECLARED', r.hold_reasons)
        self.assertEqual(r.scout.candidates, ())                  # the Scout result itself is fail-closed too
        self.assertIn('CLOCK_UNCERTAINTY_UNDECLARED', by_symbol(r)['AAAAAA'].notes)

    def test_explicit_zero_is_an_attestation_and_passes(self):
        r = run(['AAAAAA'], breakout('AAAAAA'), clock=timedelta(0))
        self.assertEqual(r.decision, PROCEED)
        self.assertTrue(r.clock_declared)

    def test_strict_modes_can_never_pass_with_a_positive_uncertainty(self):
        """Strict modes need the newest bar stamped exactly at its minute end; any positive uncertainty makes
        such a bar untrusted. So `legacy_close`/`next_open` pipelines pass only with an explicit zero."""
        rows = breakout('AAAAAA', delay=0)
        self.assertEqual(run(['AAAAAA'], rows, cfg=STRICT, clock=timedelta(0)).decision, PROCEED)
        blocked = run(['AAAAAA'], rows, cfg=STRICT, clock=timedelta(seconds=1))
        self.assertEqual(blocked.decision, HOLD)
        self.assertIn('CLOCK_UNCERTAIN_BAR', by_symbol(blocked)['AAAAAA'].candle_codes)
        late = run(['AAAAAA'], breakout('AAAAAA', delay=2), cfg=STRICT, clock=timedelta(0))
        self.assertEqual(late.decision, HOLD)                       # strict: a bar stamped 2 s late is not on time
        self.assertIn('NOT_ON_TIME_FOR_STRICT_MODE', by_symbol(late)['AAAAAA'].candle_codes)

    def test_a_larger_declared_uncertainty_blocks_bars_that_arrived_too_early(self):
        rows = breakout('AAAAAA', delay=2)                              # stamped 2 s after minute end
        trusted = run(['AAAAAA'], rows, clock=timedelta(seconds=1))
        blocked = run(['AAAAAA'], rows, clock=timedelta(seconds=10))
        self.assertEqual(trusted.decision, PROCEED)
        self.assertEqual(blocked.decision, HOLD)
        self.assertIn('CLOCK_UNCERTAIN_BAR', by_symbol(blocked)['AAAAAA'].candle_codes)
        self.assertEqual(blocked.passed, ())

    def test_out_of_range_uncertainty_is_an_argument_error(self):
        with self.assertRaises(ValueError):
            run(['AAAAAA'], breakout('AAAAAA'), clock=timedelta(hours=2))
        with self.assertRaises(ValueError):
            run(['AAAAAA'], breakout('AAAAAA'), clock=timedelta(seconds=-1))


class VwapBasisTests(unittest.TestCase):
    def test_exact_mode_needs_turnover_and_reports_the_basis(self):
        ok = run(['AAAAAA'], breakout('AAAAAA'))
        self.assertEqual(by_symbol(ok)['AAAAAA'].vwap_basis, 'EXACT_TURNOVER')
        no_turnover = run(['AAAAAA'], breakout('AAAAAA', turnover=False))
        d = by_symbol(no_turnover)['AAAAAA']
        self.assertEqual(no_turnover.passed, ())
        self.assertEqual(d.vwap_basis, 'UNAVAILABLE')
        self.assertIn('VWAP_NEEDS_TURNOVER', d.candle_codes)

    def test_approximation_must_be_explicitly_allowed_and_is_labelled(self):
        rows = breakout('AAAAAA', turnover=False)
        denied = run(['AAAAAA'], rows, cfg=APPROX)
        self.assertEqual(denied.passed, ())
        self.assertIn('VWAP_APPROXIMATION_NOT_ALLOWED', by_symbol(denied)['AAAAAA'].candle_codes)
        allowed = run(['AAAAAA'], rows, cfg=APPROX, allow_approximate_vwap=True)
        self.assertEqual(allowed.passed, ('AAAAAA',))
        self.assertEqual(allowed.exact_vwap_symbols, ())
        self.assertEqual(allowed.approximate_vwap_symbols, ('AAAAAA',))
        self.assertIn('APPROXIMATE_VWAP', by_symbol(allowed)['AAAAAA'].notes)

    def test_zero_volume_window_is_not_a_vwap(self):
        rows = breakout('AAAAAA', zero_volume=True, turnover=False)
        r = run(['AAAAAA'], rows, cfg=APPROX, allow_approximate_vwap=True)
        d = by_symbol(r)['AAAAAA']
        self.assertEqual(r.passed, ())
        self.assertIn('VWAP_ZERO_VOLUME', d.candle_codes)
        self.assertEqual(d.vwap_basis, 'UNAVAILABLE')


class DataLimitsTests(unittest.TestCase):
    def test_a_legitimate_large_move_is_blocked_by_the_30_percent_filter(self):
        """Documented limitation (fail-closed): the +-30 % plausibility filter cannot tell bad data from a
        genuine wide bar (e.g. a first-day listing). The symbol is excluded, not traded."""
        rows = [dict(r, high_price=D('7000'), close_price=D('7000')) if r['event_at'] == at(9, 10) else r
                for r in breakout('AAAAAA')]
        r = run(['AAAAAA'], rows)
        self.assertEqual(r.passed, ())
        self.assertIn('IMPLAUSIBLE_PRICE', by_symbol(r)['AAAAAA'].candle_codes)

    def test_candle_rows_available_after_as_of_are_ignored(self):
        rows = breakout('AAAAAA', count=20) + [bar('AAAAAA', 20, '9000', '999')]            # available 09:21, after as_of
        same = run(['AAAAAA'], breakout('AAAAAA', count=20))
        with_future = run(['AAAAAA'], rows)
        self.assertEqual(with_future.passed, same.passed)
        self.assertEqual(with_future.candles, same.candles)

    def test_instrument_facts_observed_after_as_of_are_ignored(self):
        r = run_scout_pipeline(as_of=AS_OF, snapshot=snap(['AAAAAA']), candle_rows=breakout('AAAAAA'),
                               orderbook_results=[book('AAAAAA')], instruments=instruments(['AAAAAA'], at(9, 19, 55)),
                               portfolio=portfolio(), day01_config=CFG, clock_uncertainty=SAFE)
        self.assertEqual(r.decision, HOLD)
        self.assertEqual(r.passed, ())

    def test_scout_candidates_and_collection_universe_mismatch_is_reported(self):
        names = ['AAAAAA', 'BBBBBB']
        rows = breakout('AAAAAA') + breakout('OLD001')                                     # BBB not collected; OLD001 not ranked
        r = run(names, rows, collection_universe=['AAAAAA', 'OLD001'])
        d = by_symbol(r)
        self.assertEqual(r.passed, ('AAAAAA',))
        self.assertIn('NOT_IN_COLLECTION_UNIVERSE', d['BBBBBB'].notes)
        self.assertIn('NO_CANDLES', d['BBBBBB'].candle_codes)
        self.assertEqual(d['BBBBBB'].verdict, EXCLUDED)
        self.assertTrue(any(code.startswith('CANDLE_SYMBOLS_NOT_IN_RANKING') and 'OLD001' in code
                            for _, code in r.notes))


class LimitsUnchangedTests(unittest.TestCase):
    def test_scout_overrides_cannot_loosen_gates_or_limits(self):
        for key in ('quantity', 'max_spread_pct', 'require_liquidity', 'require_dayone_readiness',
                    'fee_rate', 'min_turnover'):
            with self.assertRaises(ValueError, msg=key):
                run(['AAAAAA'], breakout('AAAAAA'), scout_overrides={key: None})
        r = run(['AAAAAA'], breakout('AAAAAA'), scout_overrides={'target_count': 10})
        self.assertEqual(r.scout.target_count, 10)

    def test_portfolio_limits_must_equal_the_day01_config(self):
        loose = sc.Portfolio(at(9, 19, 47), cash='1000000', total_asset='1000000', max_positions=99,
                             min_cash_ratio_pct=D(0), daily_loss_limit=D('999999999'))
        with self.assertRaises(ValueError) as ctx:
            run_scout_pipeline(as_of=AS_OF, snapshot=snap(['AAAAAA']), candle_rows=breakout('AAAAAA'),
                               instruments=instruments(['AAAAAA']), portfolio=loose, day01_config=CFG,
                               clock_uncertainty=SAFE)
        self.assertIn('MAX_POSITIONS', str(ctx.exception))

    def test_amount_limit_still_excludes_through_the_pipeline(self):
        cfg = replace(CFG, max_order_amount=D('1000'))                                  # 10 x 5000 > 1000
        r = run(['AAAAAA'], breakout('AAAAAA'), cfg=cfg)
        self.assertEqual(r.passed, ())
        self.assertTrue(any('ORDER' in x for x in by_symbol(r)['AAAAAA'].reasons), by_symbol(r)['AAAAAA'].reasons)

    def test_default_day01_limits_are_untouched_by_this_work(self):
        d = Config()
        self.assertEqual((d.max_order_amount, d.max_symbol_amount, d.max_total_investment, d.order_quantity,
                          d.daily_loss_limit), (D('100000'), D('200000'), D('500000'), 10, D('20000')))


class PurityTests(unittest.TestCase):
    def test_module_imports_only_pure_dependencies(self):
        tree = ast.parse(Path(pipe.__file__).read_text(encoding='utf-8'))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(('.' * node.level) + (node.module or ''))
        allowed = {'__future__', 'dataclasses', 'datetime', 'typing', '.day01_scout', '.day01_scout_link',
                   '.day01_scout_market', '.day01_scout_orderbook'}
        self.assertEqual(names - allowed, set())


if __name__ == '__main__':
    unittest.main()

"""Synthetic Scout -> DAY-01 integration tests. No network, DB, files, clock or broker."""
import ast
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

from auto_trader import day01_scout as sc
from auto_trader import day01_scout_link as link
from auto_trader.day01_scout import (
    DayOneFacts, InstrumentFacts, LiquidityFacts, Portfolio, Position, ScoutConfig,
    select_watchlist, snapshot_from_rankings,
)
from auto_trader.day01_scout_link import WatchTimeline
from auto_trader.ml.day01 import Candle, Config as DefaultConfig, KST, replay

CFG = replace(DefaultConfig(), execution='legacy_close')  # candle-only fills; rules untouched
DAY = 13


def at(h, m, s=0):
    return datetime(2026, 11, DAY, h, m, s, tzinfo=KST)


def candle(symbol, index, price, volume='100', **changes):
    start = at(9, 0) + timedelta(minutes=index)
    p, v = D(price), D(volume)
    return replace(Candle(symbol, start, start + timedelta(minutes=1), p, p, p, p, v, p * v), **changes)


def breakout(symbol, base='5000', up='5100', *, tail=0, crash_at=None, crash='5000'):
    """Flat opening range, 09:20 higher-volume breakout, then optional flat tail / stop crash."""
    rows = [candle(symbol, i, base) for i in range(15)]
    rows += [candle(symbol, i, up, '200') for i in range(15, 20)]
    for i in range(20, 20 + tail):
        px = crash if crash_at is not None and i >= crash_at else up
        rows.append(candle(symbol, i, px))
    return rows


def scout_result(as_of, symbols, *, held=(), price='5000', day01=CFG, risk=None, **cfg):
    """Scout result built with every input available strictly before `as_of`."""
    ranked, received, observed = as_of - timedelta(seconds=20), as_of - timedelta(seconds=10), as_of - timedelta(seconds=5)
    rows = [{"rank": i, "symbol": s, "tradingAmount": "20000000000", "tradingVolume": "100000",
             "price": {"lastPrice": price, "changeRate": "1.5"}} for i, s in enumerate(symbols, 1)]
    snap = snapshot_from_rankings(ranked, rows, received_at=received)
    names = tuple(symbols) + tuple(s for s, *_ in held)
    positions = tuple(Position(s, 10, value, observed, px, observed) for s, value, px in held)
    return select_watchlist(
        as_of, [snap],
        instruments=[InstrumentFacts(s, observed, market="KOSPI", currency="KRW", security_type="STOCK",
                                     is_common_share=True, status="ACTIVE", krx_trading_suspended=False,
                                     liquidation_trading=False) for s in names],
        liquidity=[LiquidityFacts(s, observed, turnover="20000000000", volume="100000", spread_pct="0.5") for s in names],
        dayone=[DayOneFacts(s, observed, completed_1m_bars=20, opening_range_ready=True, vwap_ready=True) for s in names],
        portfolio=Portfolio(observed, cash="1000000", total_asset="1000000", positions=positions,
                            **link.portfolio_limits_for(day01)),
        config=link.scout_config_for(day01, target_count=10, **cfg), risk_config=day01 if risk is None else risk)


def run(rows, timeline, config=CFG, universe=None):
    return replay(rows, config, universe=timeline.universe() if universe is None else universe,
                  entry_allowed=timeline.entry_allowed)


def buys(result):
    return [t['symbol'] for t in result['trades'] if t['side'] == 'BUY']


class LimitConsistencyTests(unittest.TestCase):
    def test_scout_config_and_limits_mirror_day01(self):
        cfg = link.scout_config_for(CFG)
        self.assertEqual((cfg.quantity, cfg.fee_rate, cfg.buy_slippage_rate),
                         (CFG.order_quantity, CFG.fee_rate, CFG.slippage_rate))
        pf = Portfolio(at(9, 0), cash="1", **link.portfolio_limits_for(CFG))
        self.assertEqual(link.limit_mismatches(CFG, cfg, pf, CFG), ())

    def test_every_divergence_is_reported(self):
        cfg = ScoutConfig(quantity=1, fee_rate="0.1", buy_slippage_rate="0.1")
        pf = Portfolio(at(9, 0), cash="1")
        risk = type("R", (), dict(max_order_amount=D(1), max_symbol_amount=D(1), max_total_investment=D(1)))()
        self.assertEqual(set(link.limit_mismatches(CFG, cfg, pf, risk)), {
            "QUANTITY", "FEE_RATE", "BUY_SLIPPAGE_RATE", "MAX_POSITIONS", "MIN_CASH_RATIO", "DAILY_LOSS_LIMIT",
            "MAX_ORDER_AMOUNT", "MAX_SYMBOL_AMOUNT", "MAX_TOTAL_INVESTMENT"})
        self.assertIn("MAX_ORDER_AMOUNT", link.limit_mismatches(CFG, link.scout_config_for(CFG), Portfolio(
            at(9, 0), cash="1", **link.portfolio_limits_for(CFG)), type("R", (), dict(
                max_order_amount=D(1), max_symbol_amount=CFG.max_symbol_amount,
                max_total_investment=CFG.max_total_investment))()))

    def test_scout_order_limit_agrees_with_day01_risk_at_the_boundary(self):
        # price 9995 -> notional 99999.975 + fee > 100000 (rejected); 9990 -> within the limit.
        over = scout_result(at(9, 19, 50), ['AAA'], price='9995')
        under = scout_result(at(9, 19, 50), ['AAA'], price='9990')
        self.assertEqual(over.candidates, ())
        self.assertEqual([c.symbol for c in under.candidates], ['AAA'])
        # DAY-01 rejects the same price for the same cause when the signal arrives.
        rows = breakout('AAA', base='9800', up='9995')
        timeline = WatchTimeline([scout_result(at(9, 19, 50), ['AAA'], price='9990')])
        out = run(rows, timeline)
        self.assertEqual(buys(out), [])
        self.assertIn(('AAA', 'ORDER_AMOUNT_LIMIT'), [(x['symbol'], x['reason']) for x in out['reasons']])

    def test_total_investment_counts_held_value_and_equal_to_limit_is_allowed(self):
        held_ok = scout_result(at(9, 19, 50), ['BBB'], price='5000', held=[('HLD', '449900', '5000')])
        held_bad = scout_result(at(9, 19, 50), ['BBB'], price='5000', held=[('HLD', '450000', '5000')])
        self.assertEqual([c.symbol for c in held_ok.candidates], ['BBB'])
        self.assertEqual(held_bad.candidates, ())
        reason = next(e.reasons for e in held_bad.exclusions if e.symbol == 'BBB')
        self.assertTrue(any('TOTAL_INVESTMENT' in r for r in reason), reason)
        self.assertEqual([h.symbol for h in held_bad.held], ['HLD'])


class CausalEntryGateTests(unittest.TestCase):
    def test_watched_symbol_enters_unwatched_is_vetoed(self):
        timeline = WatchTimeline([scout_result(at(9, 19, 50), ['AAA'])])
        rows = breakout('AAA', tail=3) + breakout('BBB', tail=3)
        out = run(rows, timeline, universe=['AAA', 'BBB'])
        self.assertEqual(buys(out), ['AAA'])
        self.assertIn(('BBB', 'SCOUT_NOT_WATCHED'), [(x['symbol'], x['reason']) for x in out['reasons']])

    def test_later_scout_result_cannot_authorize_earlier_signal(self):
        early = scout_result(at(9, 19, 50), ['AAA'])
        late = scout_result(at(10, 0), ['BBB'])
        rows = breakout('AAA', tail=3) + breakout('BBB', tail=3)
        with_future = run(rows, WatchTimeline([early, late]))
        without_future = run(rows, WatchTimeline([early]), universe=['AAA', 'BBB'])
        self.assertEqual(WatchTimeline([early, late]).universe(), ('AAA', 'BBB'))  # superset only filters data
        self.assertEqual(buys(with_future), ['AAA'])
        self.assertEqual(with_future['trades'], without_future['trades'])
        self.assertEqual(with_future['signals'], without_future['signals'])
        self.assertEqual(with_future['equity_curve'], without_future['equity_curve'])

    def test_snapshot_received_after_decision_time_is_not_usable(self):
        # Ranking provider time 09:19:40 but we only received it at 09:20:30: not available at 09:20.
        ranked, received = at(9, 19, 40), at(9, 20, 30)
        rows = [{"rank": 1, "symbol": "AAA", "tradingAmount": "20000000000", "tradingVolume": "100000",
                 "price": {"lastPrice": "5000", "changeRate": "1"}}]
        snap = snapshot_from_rankings(ranked, rows, received_at=received)
        early = select_watchlist(at(9, 20), [snap], portfolio=Portfolio(at(9, 19, 55), cash="1000000"),
                                 config=link.scout_config_for(CFG, target_count=10), risk_config=CFG)
        self.assertEqual(early.status, sc.NO_SNAPSHOT)
        self.assertNotEqual(snap.provider_ranked_at, snap.received_at)
        timeline = WatchTimeline([early])
        self.assertFalse(timeline.entry_allowed('AAA', at(9, 20)))
        self.assertEqual(buys(run(breakout('AAA', tail=3), timeline, universe=['AAA'])), [])

    def test_no_result_not_ok_or_expired_result_fails_closed(self):
        ok = scout_result(at(9, 19, 50), ['AAA'])
        expiry = ok.candidates[0].entry_expires_at
        timeline = WatchTimeline([ok])
        self.assertTrue(timeline.entry_allowed('AAA', at(9, 20)))
        self.assertTrue(timeline.entry_allowed('AAA', expiry - timedelta(microseconds=1)))
        self.assertFalse(timeline.entry_allowed('AAA', expiry))                    # exclusive
        self.assertFalse(timeline.entry_allowed('AAA', at(9, 19, 49)))             # before it existed
        self.assertFalse(WatchTimeline([]).entry_allowed('AAA', at(9, 20)))
        stale = scout_result(at(9, 30), ['AAA'], price='5000')
        stale = replace(stale, status=sc.STALE, candidates=())
        self.assertFalse(WatchTimeline([ok, stale]).entry_allowed('AAA', at(9, 30)))   # no older fallback
        dropped = scout_result(at(9, 25), ['BBB'])
        self.assertFalse(WatchTimeline([ok, dropped]).entry_allowed('AAA', at(9, 25, 10)))

    def test_conflicting_results_at_same_instant_are_rejected(self):
        with self.assertRaises(ValueError):
            WatchTimeline([scout_result(at(9, 19, 50), ['AAA']), scout_result(at(9, 19, 50), ['BBB'])])
        same = scout_result(at(9, 19, 50), ['AAA'])
        self.assertEqual(WatchTimeline([same, same]).universe(), ('AAA',))

    def test_naive_datetimes_are_rejected(self):
        timeline = WatchTimeline([scout_result(at(9, 19, 50), ['AAA'])])
        with self.assertRaises(ValueError):
            timeline.entry_allowed('AAA', datetime(2026, 11, DAY, 9, 20))

    def test_gate_is_off_by_default_and_replay_rules_are_unchanged(self):
        rows = breakout('AAA', tail=3)
        self.assertEqual(replay(rows, CFG, universe=['AAA']), replay(rows, CFG, universe=['AAA'], entry_allowed=None))
        self.assertEqual(buys(replay(rows, CFG, universe=['AAA'])), ['AAA'])
        always = replay(rows, CFG, universe=['AAA'], entry_allowed=lambda s, t: True)
        self.assertEqual(always['trades'], replay(rows, CFG, universe=['AAA'])['trades'])


class RotationRiskMonitoringTests(unittest.TestCase):
    def rotation(self):
        entry = scout_result(at(9, 19, 50), ['AAA'])
        # 09:30: AAA no longer a candidate and absent from the ranking; only the held monitor remains.
        rotated = scout_result(at(9, 30), ['BBB'], held=[('AAA', '51000', '5100')])
        # 09:40: a result that no longer mentions AAA at all (e.g. portfolio view lagging) must not matter.
        gone = scout_result(at(9, 40), ['BBB'])
        return entry, rotated, gone

    def test_rotated_out_holding_still_hits_stop_and_is_liquidated(self):
        rows = breakout('AAA', tail=40, crash_at=30)
        gated = run(rows, WatchTimeline(self.rotation()))
        plain = replay(rows, CFG, universe=['AAA'])
        self.assertEqual([(t['symbol'], t['side'], t['reason']) for t in gated['trades']],
                         [('AAA', 'BUY', 'ENTRY'), ('AAA', 'SELL', 'STOP')])
        self.assertEqual(gated['trades'], plain['trades'])
        self.assertEqual(gated['summary']['status'], 'FLAT')
        self.assertEqual(gated['summary']['open_positions'], {})

    def test_rotated_out_holding_is_liquidated_at_session_end_without_data_events(self):
        rows = breakout('AAA', tail=40)
        gated = run(rows, WatchTimeline(self.rotation()))
        sells = [t for t in gated['trades'] if t['side'] == 'SELL']
        self.assertTrue(sells or gated['summary']['open_positions'] or gated['failures'],
                        'holding must be either exited or reported as unresolved, never silently dropped')
        self.assertEqual(gated['trades'], replay(rows, CFG, universe=['AAA'])['trades'])

    def test_held_symbol_is_in_universe_and_monitor_survives_rotation(self):
        entry, rotated, gone = self.rotation()
        self.assertEqual([c.symbol for c in rotated.candidates], ['BBB'])
        self.assertEqual([h.symbol for h in rotated.held], ['AAA'])
        self.assertTrue(rotated.held[0].monitor_only)
        self.assertEqual({w.symbol: w.sources for w in rotated.watch_union},
                         {'BBB': ('CANDIDATE',), 'AAA': ('HELD',)})
        self.assertEqual(WatchTimeline([entry, rotated, gone]).universe(), ('AAA', 'BBB'))
        self.assertEqual(WatchTimeline([rotated]).universe(), ('AAA', 'BBB'))
        self.assertFalse(WatchTimeline([entry, rotated]).entry_allowed('AAA', at(9, 30, 5)))

    def test_new_candidate_cannot_exceed_day01_position_limit(self):
        # Two watched breakouts at the same instant; max_positions=1 still binds inside DAY-01.
        one = replace(CFG, max_positions=1)
        timeline = WatchTimeline([scout_result(at(9, 19, 50), ['AAA', 'BBB'], day01=one)])
        out = run(breakout('AAA', tail=3) + breakout('BBB', tail=3), timeline, config=one)
        self.assertEqual(buys(out), ['AAA'])
        self.assertIn(('BBB', 'POSITION_LIMIT'), [(x['symbol'], x['reason']) for x in out['reasons']])
        self.assertTrue(all(c.joint_orders_guaranteed is False for c in timeline.latest(at(9, 20)).candidates))


class PurityTests(unittest.TestCase):
    def test_link_imports_only_pure_modules(self):
        tree = ast.parse(Path(link.__file__).read_text(encoding='utf-8'))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(('.' + (node.module or '')) if node.level else node.module.split('.')[0])
        self.assertLessEqual(modules, {'__future__', 'datetime', 'timezone', '.day01_scout'})


if __name__ == '__main__':
    unittest.main()

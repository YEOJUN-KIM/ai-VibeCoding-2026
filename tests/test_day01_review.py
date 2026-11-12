"""Independent (Claude) adversarial review of DAY-01. Synthetic data only.

Expected values are hand-computed from the written contract (docs/specs/DAY01_BACKTEST.md
and the review requirements), never produced by calling implementation helpers.
Only auto_trader.ml.day01 is imported; no settings, DB, broker or file access.
Tests named *_DEFECT_* assert CORRECT behaviour that the reviewed commit violates and are
marked expectedFailure; remove the marker once the defect is fixed.
"""
import ast
import random
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D, ROUND_DOWN
from pathlib import Path

from auto_trader.ml.day01 import (
    Candle, Config, KST, aggregate_five_minutes, evaluate_entry,
    execution_costs, replay, select_universe,
)

DAY = 12
M1 = timedelta(minutes=1)


def at(index, day=DAY):
    return datetime(2026, 11, day, 9, tzinfo=KST) + timedelta(minutes=index)


def bar(i, close='100', *, day=DAY, sym='AAA', vol='100', o=None, lo=None, hi=None,
        avail=None, turn='auto'):
    c, v = D(close), D(vol)
    op = D(o) if o is not None else c
    low = D(lo) if lo is not None else min(c, op)
    high = D(hi) if hi is not None else max(c, op)
    return Candle(sym, at(i, day), avail if avail is not None else at(i, day) + M1,
                  op, high, low, c, v, c * v if turn == 'auto' else turn)


def scenario(day=DAY, sym='AAA'):
    """15 flat 09:00-09:15 minutes (range high 100), then a 200-volume 102 breakout bar set.
    First signal: 09:20 (5m bar 09:15-09:20)."""
    return ([bar(i, day=day, sym=sym) for i in range(15)] +
            [bar(i, '102', day=day, sym=sym, vol='200') for i in range(15, 20)])


# Hand-computed constants for the default Config: buy fill 102 -> 102 * 1.0005.
ENTRY = D('102.051')
STOP = D('101.03049')      # ENTRY * 0.99
TARGET = D('104.09202')    # ENTRY * 1.02


def entered(*later, **kw):
    """Scenario + fill bar 09:20 (filled at 09:21) + later rows."""
    return scenario() + [bar(20, '102')] + list(later)


def run(rows, universe=('AAA',), **cfg):
    return replay(rows, replace(Config(), **cfg), universe=list(universe))


def sells(result):
    return [t for t in result['trades'] if t['side'] == 'SELL']


class Costs(unittest.TestCase):
    def test_round_trip_exact_krw(self):
        # 500 sh: buy ref 102 -> 102.051, amount 51025.5, fee floor(7.65)=7.
        # sell ref 103 -> 102.9485, amount 51474.25, fee floor(7.72)=7, tax floor(102.95)=102.
        rows = (scenario() + [bar(20, '102', vol='1000'), bar(350, '103', vol='1000')])
        r = run(rows, order_quantity=500)
        buy, sell = r['trades']
        self.assertEqual((buy['price'], buy['amount'], buy['fee'], buy['tax']),
                         (ENTRY, D('51025.5'), D(7), D(0)))
        self.assertEqual((sell['price'], sell['amount'], sell['fee'], sell['tax']),
                         (D('102.9485'), D('51474.25'), D(7), D(102)))
        self.assertEqual(sell['realized_profit'], D('332.75'))   # 51365.25 - 51032.5
        s = r['summary']
        self.assertEqual((s['fees'], s['taxes'], s['slippage']), (D(14), D(102), D('51.25')))
        self.assertEqual(s['net_profit'], D('332.75'))
        self.assertEqual(s['cash'], D('1000332.75'))
        self.assertEqual(s['status'], 'FLAT')

    def test_zero_cost_vs_cost_difference_is_exactly_the_costs(self):
        rows = (scenario() + [bar(20, '102', vol='1000'), bar(350, '103', vol='1000')])
        zero = run(rows, order_quantity=500, fee_rate=D(0), sell_tax_rate=D(0), slippage_rate=D(0))
        cost = run(rows, order_quantity=500)
        self.assertEqual(zero['summary']['net_profit'], D(500))     # 500 * (103 - 102)
        self.assertEqual(zero['summary']['fees'] + zero['summary']['taxes'] + zero['summary']['slippage'], 0)
        diff = zero['summary']['net_profit'] - cost['summary']['net_profit']
        c = cost['summary']
        self.assertEqual(diff, c['fees'] + c['taxes'] + c['slippage'])   # 167.25
        self.assertEqual(diff, D('167.25'))

    def test_fee_and_tax_round_down_never_up(self):
        cfg = replace(Config(), slippage_rate=D(0), fee_rate=D('0.0001'), sell_tax_rate=D('0.002'))
        buy = execution_costs(D('19999'), 1, 'BUY', cfg)      # fee 1.9999 -> 1
        sell = execution_costs(D('19999'), 1, 'SELL', cfg)    # fee 1.9999 -> 1, tax 39.998 -> 39
        self.assertEqual((buy['fee'], buy['tax'], buy['cash_delta']), (D(1), D(0), D(-20000)))
        self.assertEqual((sell['fee'], sell['tax'], sell['cash_delta']), (D(1), D(39), D(19959)))

    def test_slippage_is_adverse_and_recorded(self):
        cfg = replace(Config(), fee_rate=D(0), sell_tax_rate=D(0))
        b, s = execution_costs(D(1000), 3, 'BUY', cfg), execution_costs(D(1000), 3, 'SELL', cfg)
        self.assertEqual(b['price'], D('1000.5'))
        self.assertEqual(s['price'], D('999.5'))
        self.assertEqual((b['slippage'], s['slippage']), (D('1.5'), D('1.5')))


class Temporal(unittest.TestCase):
    def test_fill_uses_later_bar_not_signal_bar(self):
        rows = scenario() + [bar(20, '103', o='102', hi='103', lo='102')]
        t, = run(rows)['trades']
        self.assertEqual((t['signal_at'], t['event_at'], t['execution_at']), (at(20), at(20), at(21)))
        self.assertEqual(t['evidence_close'], D(103))
        self.assertEqual(t['price'], D('103.0515'))      # close, not open 102 and not signal close 102

    def test_late_candidate_bar_skipped_fill_waits_for_next_on_time_bar(self):
        late = bar(20, '150', hi='150', lo='100', o='100', avail=at(27))
        r = run(scenario() + [late, bar(21, '103')])
        t, = r['trades']
        self.assertEqual((t['event_at'], t['execution_at']), (at(21), at(22)))
        self.assertEqual(t['price'], D('103.0515'))      # never 150
        self.assertTrue(any(x['reason'] == 'LATE_OBSERVATION_NOT_EXECUTABLE' for x in r['reasons']))

    def test_future_bar_cannot_change_earlier_history(self):
        base = entered(bar(21, '102'), bar(350, '103'))
        a = run(base + [bar(100, '101')])
        b = run(base + [bar(100, '1', lo='1')])
        cut = at(101)    # availability of the mutated bar
        for key, field in (('trades', 'execution_at'), ('signals', 'at'), ('reasons', 'at'),
                           ('equity_curve', 'at')):
            self.assertEqual([x for x in a[key] if x[field] < cut], [x for x in b[key] if x[field] < cut], key)

    def test_trade_times_obey_signal_to_observation_chain(self):
        r = run(entered(bar(21, '102'), bar(350, '103')))
        for t in r['trades']:
            self.assertEqual(t['execution_at'], t['observed_at'])
            self.assertEqual(t['execution_at'], t['event_at'] + M1)
            self.assertGreaterEqual(t['event_at'], t['signal_at'])
            self.assertGreater(t['execution_at'], t['signal_at'])

    def test_five_minute_aggregate_values_and_late_constituent(self):
        rows = [bar(0, '100', o='99', hi='101', lo='98', vol='10'),
                bar(1, '101', vol='20'), bar(2, '97', o='101', hi='101', lo='96', vol='30'),
                bar(3, '99', vol='40'), bar(4, '100', o='99', hi='100', lo='99', vol='50')]
        b, = aggregate_five_minutes(rows, at(5))
        self.assertEqual((b.open_price, b.high_price, b.low_price, b.close_price, b.volume),
                         (D(99), D(101), D(96), D(100), D(150)))
        self.assertEqual((b.event_at, b.end_at, b.available_at), (at(0), at(5), at(5)))
        self.assertEqual(b.turnover, sum(r.close_price * r.volume for r in rows))
        rows[2] = replace(rows[2], available_at=at(9))
        self.assertEqual(aggregate_five_minutes(rows, at(8)), [])
        self.assertEqual(aggregate_five_minutes(rows, at(9))[0].available_at, at(9))

    def test_missing_bar_inside_five_minutes_means_no_aggregate(self):
        rows = [bar(i) for i in range(5) if i != 2]
        self.assertEqual(aggregate_five_minutes(rows, at(10)), [])

    def test_late_bar_may_warm_but_cannot_retro_trigger(self):
        # 09:19 bar (the signal's last minute) arrives only at 09:30: no 09:20 signal, and
        # the 09:25 evaluation must not fabricate one for the 09:20 boundary afterwards.
        rows = scenario()
        rows[19] = replace(rows[19], available_at=at(30))
        r = run(rows + [bar(i, '102', vol='200') for i in range(20, 30)] + [bar(30, '102'), bar(31, '102')])
        sig = [s for s in r['signals'] if s['side'] == 'BUY']
        for s in sig:
            self.assertGreaterEqual(s['at'], at(30))
        self.assertTrue(all(t['execution_at'] > at(30) for t in r['trades']))

    def test_utc_inputs_equal_kst_inputs(self):
        rows = entered(bar(21, '102'), bar(350, '103'))
        utc = [replace(x, event_at=x.event_at.astimezone(timezone.utc),
                       available_at=x.available_at.astimezone(timezone.utc)) for x in rows]
        a, b = run(rows), run(utc)
        self.assertEqual(a['trades'], b['trades'])
        self.assertEqual(a['equity_curve'], b['equity_curve'])


class Strategy(unittest.TestCase):
    def decide(self, rows, mode='exact'):
        return evaluate_entry(rows, rows[-1].available_at, replace(Config(), vwap_mode=mode))

    def test_missing_0900_blocks_the_day_but_not_other_symbols(self):
        a, b = scenario(sym='AAA')[1:], scenario(sym='BBB')
        r = run(a + b + [bar(20, '102', sym='AAA'), bar(20, '102', sym='BBB')], universe=('AAA', 'BBB'))
        self.assertEqual({t['symbol'] for t in r['trades']}, {'BBB'})
        self.assertTrue(any(x['symbol'] == 'AAA' and x['reason'] == 'INCOMPLETE_OPENING_RANGE' for x in r['reasons']))

    def test_missing_mid_range_bar_blocks(self):
        rows = [r for r in scenario() if r.event_at != at(7)]
        self.assertEqual(self.decide(rows).reason, 'INCOMPLETE_OPENING_RANGE')

    def test_missing_bar_after_range_blocks_vwap_no_interpolation(self):
        rows = [r for r in scenario() if r.event_at != at(16)]
        self.assertEqual(self.decide(rows).reason, 'NO_FRESH_FIVE_MINUTE_BAR')

    def test_breakout_close_must_strictly_exceed_range_high(self):
        def rows(close):
            base = [bar(i) for i in range(14)] + [bar(14, '100', hi='101')]
            return base + [bar(i, close, vol='200') for i in range(15, 20)]
        eq = self.decide(rows('101'))
        self.assertEqual((eq.reason, eq.range_high), ('NO_RANGE_BREAKOUT', D(101)))
        self.assertTrue(self.decide(rows('101.01')).enter)

    def test_range_excludes_0915_bar(self):
        rows = scenario()
        rows[15] = bar(15, '102', hi='140', o='102', vol='200')   # 09:15 spike must not enter range
        d = self.decide(rows)
        self.assertEqual(d.range_high, D(100))
        self.assertTrue(d.enter)

    def test_volume_multiplier_boundary_inclusive(self):
        def rows(v):
            return [bar(i) for i in range(15)] + [bar(i, '102', vol=v) for i in range(15, 20)]
        self.assertTrue(self.decide(rows('120')).enter)               # 600 == 500 * 1.2
        self.assertEqual(self.decide(rows('119')).reason, 'INSUFFICIENT_VOLUME_INCREASE')

    def test_exact_vs_approximate_vwap_are_distinct_and_labelled(self):
        cur = [Candle('AAA', at(i), at(i) + M1, D(100), D(110), D(100), D(102), D(200), D(20000))
               for i in range(15, 20)]
        rows = [bar(i) for i in range(15)] + cur
        exact, approx = self.decide(rows, 'exact'), self.decide(rows, 'typical_approximation')
        self.assertEqual((exact.enter, exact.reason, exact.vwap, exact.previous_vwap),
                         (False, 'NO_RISING_VWAP', D(100), D(100)))
        # typical price (110+100+102)/3 = 104 -> (150000 + 5*200*104) / 2500 = 101.6
        self.assertEqual((approx.enter, approx.vwap, approx.previous_vwap), (True, D('101.6'), D(100)))
        r = run(rows + [bar(20, '102')], vwap_mode='typical_approximation')
        self.assertEqual(r['vwap_mode'], 'typical_approximation')
        self.assertEqual(run(rows + [bar(20, '102')])['trades'], [])

    def test_exact_mode_refuses_missing_turnover_approx_does_not(self):
        rows = scenario()
        rows[3] = replace(rows[3], turnover=None)
        self.assertEqual(self.decide(rows, 'exact').reason, 'MISSING_EXACT_TURNOVER')
        self.assertTrue(self.decide(rows, 'typical_approximation').enter)

    def test_entry_window_0915_and_1130_boundaries(self):
        rows = scenario()
        self.assertEqual(evaluate_entry(rows[:15], at(15), Config()).reason, 'OUTSIDE_ENTRY_WINDOW')
        flat = [bar(i) for i in range(145)]
        late_breakout = flat + [bar(i, '102', vol='200') for i in range(145, 150)]   # signal at 11:30
        self.assertEqual(evaluate_entry(late_breakout, at(150), Config()).reason, 'OUTSIDE_ENTRY_WINDOW')
        r = run(late_breakout + [bar(150, '102')])
        self.assertEqual(r['signals'], [])
        # signal at 11:25 is inside the window and fills at 11:26
        inside = [bar(i) for i in range(140)] + [bar(i, '102', vol='200') for i in range(140, 145)]
        r = run(inside + [bar(145, '102')])
        self.assertEqual([(t['side'], t['signal_at'], t['execution_at']) for t in r['trades'] if t['side'] == 'BUY'],
                         [('BUY', at(145), at(146))])

    def test_fill_at_1130_is_expired(self):
        inside = [bar(i) for i in range(140)] + [bar(i, '102', vol='200') for i in range(140, 145)]
        r = run(inside + [bar(149, '102')])        # first on-time bar after signal is observed at 11:30
        self.assertEqual([t for t in r['trades'] if t['side'] == 'BUY'], [])
        self.assertTrue(any(x['reason'] == 'ENTRY_EXPIRED' for x in r['reasons']))

    def test_one_entry_per_symbol_per_day(self):
        extra = [bar(i, '104', vol='400') for i in range(20, 25)]
        r = run(scenario() + [bar(20, '102')] + extra[1:] + [bar(25, '105', vol='800'), bar(26, '105')])
        self.assertEqual(len([s for s in r['signals'] if s['side'] == 'BUY']), 1)

    def test_fixed_universe_ignores_outside_symbols_even_if_corrupt(self):
        base = entered(bar(21, '102'), bar(350, '103'))
        noisy = base + scenario(sym='ZZZ') + [bar(20, '999', sym='ZZZ'),
                 replace(bar(5, sym='ZZZ'), event_at=datetime(2026, 11, 12, 9, 0, 30))]
        self.assertEqual(run(noisy), run(base))

    def test_universe_helper(self):
        self.assertEqual(select_universe(['BBB', 'AAA', 'AAA']), ('AAA', 'BBB'))
        for bad in ('AAA', [], ['aaa'], [' AAA'], ['']):
            with self.assertRaises(ValueError):
                select_universe(bad)

    def test_evaluate_entry_rejects_mixed_symbols(self):
        with self.assertRaises(ValueError):
            evaluate_entry(scenario() + scenario(sym='BBB'), at(20), Config())


class Exits(unittest.TestCase):
    # Entry fills 09:21 at 102.051 -> stop 101.03049, target 104.09202. Bar 21 is the trigger
    # bar (first bar allowed to trigger), bar 22 the first executable bar, fill observed 09:23.
    def test_no_retroactive_stop_on_fill_bar(self):
        r = run(scenario() + [bar(20, '102', lo='90', o='102', hi='102')])
        self.assertFalse({s['reason'] for s in r['signals']} & {'STOP', 'AMBIGUOUS_STOP_FIRST', 'TARGET'})

    def test_stop_never_better_than_trigger_after_wick_rebound(self):
        rows = entered(bar(21, '102', lo='100'), bar(22, '102'))
        t, = sells(run(rows))
        self.assertEqual((t['reason'], t['execution_at'], t['evidence_close']), ('STOP', at(23), D(102)))
        self.assertEqual(t['reference_price'], STOP)
        self.assertEqual(t['price'], D('100.979974755'))    # STOP * 0.9995 (< evidence close 102)

    def test_gap_down_fills_at_gap_not_at_stop_price(self):
        rows = entered(bar(21, '96', o='95', lo='94', hi='96'), bar(22, '97', o='96', hi='97', lo='96'))
        t, = sells(run(rows))
        self.assertEqual(t['reason'], 'STOP')
        self.assertEqual(t['reference_price'], D(95))
        self.assertEqual(t['price'], D('94.9525'))

    def test_same_bar_stop_and_target_is_stop_first(self):
        rows = entered(bar(21, '105', o='102', lo='100', hi='110'), bar(22, '103'))
        r = run(rows)
        t, = sells(r)
        self.assertEqual(t['reason'], 'AMBIGUOUS_STOP_FIRST')
        self.assertEqual(t['reference_price'], STOP)
        self.assertNotIn('TARGET', {x['reason'] for x in r['signals']})

    def test_target_fill_capped_at_target_even_if_next_bar_gaps_higher(self):
        rows = entered(bar(21, '104.5', o='102', lo='102', hi='105'), bar(22, '120'))
        t, = sells(run(rows))
        self.assertEqual((t['reason'], t['reference_price']), ('TARGET', TARGET))
        self.assertEqual(t['price'], D('104.03997399'))

    def test_exit_waits_when_next_bar_is_zero_volume_or_late(self):
        rows = entered(bar(21, '102', lo='100'), bar(22, '102', vol='0'),
                       bar(23, '102', avail=at(40)), bar(24, '102'))
        t, = sells(run(rows))
        self.assertEqual((t['event_at'], t['execution_at']), (at(24), at(25)))

    def test_1450_timer_ignores_1449_bar(self):
        r = run(entered(bar(349, '103'), bar(350, '103')))
        t, = sells(r)
        self.assertEqual((t['reason'], t['signal_at'].strftime('%H:%M'), t['event_at'].strftime('%H:%M'),
                          t['execution_at'].strftime('%H:%M')), ('SESSION_LIQUIDATION', '14:50', '14:50', '14:51'))
        self.assertEqual(t['price'], D('102.9485'))

    def test_1450_zero_volume_and_late_bars_do_not_liquidate(self):
        rows = entered(bar(350, '103', vol='0'), bar(351, '103', avail=at(380)), bar(352, '103'))
        t, = sells(run(rows))
        self.assertEqual(t['event_at'].strftime('%H:%M'), '14:52')

    def test_partial_liquidation_across_bars_counts_one_exit_order(self):
        r = run(entered(bar(350, '103', vol='4'), bar(351, '103')))
        self.assertEqual([(t['quantity'], t['event_at'].strftime('%H:%M')) for t in sells(r)],
                         [(4, '14:50'), (6, '14:51')])
        s = r['summary']
        self.assertEqual((s['filled_orders'], s['entry_orders'], s['total_orders'], s['status']), (3, 1, 2, 'FLAT'))
        self.assertEqual(sum(t['realized_profit'] for t in sells(r)), s['realized_profit'])

    def test_1510_is_exclusive_and_failure_reports_quantity(self):
        ok = run(entered(bar(368, '103')))           # observed 15:09
        self.assertEqual(ok['summary']['status'], 'FLAT')
        r = run(entered(bar(369, '103')))            # observed 15:10: too late
        self.assertEqual(sells(r), [])
        self.assertEqual(r['summary']['status'], 'UNLIQUIDATED')
        self.assertEqual(r['summary']['unresolved_quantities'], {'AAA': 10})
        f, = r['failures']
        self.assertEqual((f['reason'], f['quantity'], f['at'].strftime('%H:%M')), ('UNLIQUIDATED_AT_1510', 10, '15:10'))
        self.assertEqual(r['daily_results'][0]['unresolved_quantities'], {'AAA': 10})

    def test_partial_then_1510_reports_remaining_quantity(self):
        r = run(entered(bar(350, '103', vol='4')))
        self.assertEqual([t['quantity'] for t in sells(r)], [4])
        self.assertEqual(r['failures'][0]['quantity'], 6)
        self.assertEqual(r['summary']['unresolved_quantities'], {'AAA': 6})

    def test_unresolved_equity_is_costed_liquidation_not_realized(self):
        r = run(entered(bar(21, '102')))
        s = r['summary']
        self.assertEqual(s['realized_profit'], D(0))
        # cash -1020.51; net liquidation 10*101.949 = 1019.49 - fee 0 - tax floor(2.0389)=2 -> -3.02
        self.assertEqual(s['net_profit'], D('-3.02'))
        self.assertEqual(s['net_liquidation_equity'], D('999996.98'))

    def test_zero_volume_bar_neither_marks_nor_triggers(self):
        r = run(entered(bar(21, '50', vol='0')))
        self.assertEqual(r['summary']['net_profit'], D('-3.02'))      # still marked at 102
        self.assertFalse({s['reason'] for s in r['signals']} & {'STOP', 'AMBIGUOUS_STOP_FIRST'})

    def test_overnight_unresolved_is_frozen_flagged_and_blocks_new_entries(self):
        rows = entered(bar(21, '102')) + scenario(day=13) + [bar(20, '102', day=13), bar(21, '102', day=13)]
        r = run(rows)
        self.assertEqual([t['side'] for t in r['trades']], ['BUY'])          # no day-13 entry
        self.assertEqual(r['summary']['status'], 'UNLIQUIDATED')
        d12, d13 = r['daily_results']
        self.assertEqual(d12['unresolved_quantities'], {'AAA': 10})
        self.assertEqual(d13['unresolved_quantities'], {'AAA': 10})          # still flagged on later days
        self.assertTrue(r['equity_curve'][-1]['unresolved'])
        self.assertEqual(d13['net_change'], D(0))       # documented limitation: not re-marked


class Limits(unittest.TestCase):
    def two(self, **cfg):
        """AAA signals 09:20 and fills 09:21; BBB breaks out later (09:25)."""
        b = ([bar(i, sym='BBB') for i in range(20)] +
             [bar(i, '102', sym='BBB', vol='200') for i in range(20, 25)] + [bar(25, '102', sym='BBB')])
        a = entered(*[bar(i, '102') for i in range(21, 26)] + [bar(350, '103')])
        return run(a + b, universe=('AAA', 'BBB'), **cfg)

    def codes(self, r, symbol):
        return [(x['reason'], x.get('phase')) for x in r['reasons'] if x['symbol'] == symbol]

    def test_daily_loss_boundary_blocks_at_equality_but_allows_one_unit_more(self):
        # After AAA's fill the day is exactly -3.02 net of liquidation costs.
        blocked = self.two(daily_loss_limit=D('3.02'))
        self.assertIn(('DAILY_LOSS_LIMIT', 'SIGNAL'), self.codes(blocked, 'BBB'))
        self.assertEqual({t['symbol'] for t in blocked['trades']}, {'AAA'})
        allowed = self.two(daily_loss_limit=D('3.03'))
        self.assertEqual({t['symbol'] for t in allowed['trades'] if t['side'] == 'BUY'}, {'AAA', 'BBB'})
        # risk-reducing exit is still allowed once the limit is hit
        self.assertEqual([t['symbol'] for t in sells(blocked)], ['AAA'])
        self.assertTrue(any(c['loss_limit_exceeded'] for c in blocked['equity_curve']))

    def test_loss_limit_rechecked_at_fill_time(self):
        a = scenario(sym='AAA')                       # signals 09:20, no on-time bar until 09:30
        b = scenario(sym='BBB') + [bar(20, '102', sym='BBB'), bar(25, '70', sym='BBB', lo='70', o='102', hi='102')]
        r = run(a + b + [bar(30, '102', sym='AAA'), bar(31, '102', sym='AAA')],
                universe=('AAA', 'BBB'), daily_loss_limit=D(300))
        self.assertTrue(any(s['symbol'] == 'AAA' and s['side'] == 'BUY' for s in r['signals']))
        self.assertIn(('DAILY_LOSS_LIMIT', 'FILL'), self.codes(r, 'AAA'))
        self.assertEqual([t['symbol'] for t in r['trades']], ['BBB'])
        self.assertLess(r['equity_curve'][-1]['daily_net_change'], -300)      # overshoot reported, not clipped

    def test_entry_order_limit_counts_accepted_unfilled_not_rejected(self):
        a = scenario(sym='AAA')                                              # accepted, never filled
        b = ([bar(i, sym='BBB') for i in range(20)] + [bar(i, '102', sym='BBB', vol='200') for i in range(20, 25)])
        r = run(a + b + [bar(25, '102', sym='BBB')], universe=('AAA', 'BBB'), daily_order_limit=1)
        self.assertEqual(r['trades'], [])
        self.assertIn(('DAILY_ENTRY_ORDER_LIMIT', None), self.codes(r, 'BBB'))
        self.assertEqual((r['summary']['entry_orders'], r['summary']['total_orders']), (1, 1))

    def test_exits_unlimited_and_counted_and_limit_resets_daily(self):
        rows = []
        for d in (12, 13):
            rows += scenario(day=d) + [bar(20, '102', day=d), bar(350, '103', day=d)]
        r = run(rows, daily_order_limit=1)
        self.assertEqual([t['side'] for t in r['trades']], ['BUY', 'SELL', 'BUY', 'SELL'])
        self.assertEqual([(d['entry_orders'], d['total_orders']) for d in r['daily_results']], [(1, 2), (1, 2)])

    def test_same_time_signals_ordered_by_symbol_not_input_order(self):
        rows = scenario(sym='BBB') + scenario(sym='AAA') + [bar(20, '102', sym='BBB'), bar(20, '102', sym='AAA')]
        r = run(rows, universe=('BBB', 'AAA'), daily_order_limit=1)
        self.assertEqual([t['symbol'] for t in r['trades']], ['AAA'])
        self.assertIn(('DAILY_ENTRY_ORDER_LIMIT', None), self.codes(r, 'BBB'))

    def test_order_amount_limit_includes_fee_boundary(self):
        rows = scenario() + [bar(20, '102')]
        self.assertEqual(len(run(rows, max_order_amount=D('1020.51'))['trades']), 1)        # 1020.51 + fee 0
        r = run(rows, max_order_amount=D('1020.50'))
        self.assertEqual(r['trades'], [])
        self.assertIn(('ORDER_AMOUNT_LIMIT', 'SIGNAL'), self.codes(r, 'AAA'))

    def test_symbol_total_cash_and_ratio_limits(self):
        rows = scenario() + [bar(20, '102')]
        for cfg, code in ((dict(max_symbol_amount=D('1020.50')), 'SYMBOL_EXPOSURE_LIMIT'),
                          (dict(max_total_investment=D('1020.50')), 'TOTAL_EXPOSURE_LIMIT'),
                          (dict(initial_cash=D(1000)), 'INSUFFICIENT_CASH'),
                          (dict(initial_cash=D(1030)), 'MIN_CASH_RATIO')):    # 9.49 / 1026.98 < 10%
            r = run(rows, **cfg)
            self.assertEqual(r['trades'], [], code)
            self.assertIn((code, 'SIGNAL'), self.codes(r, 'AAA'), code)

    def test_position_limit(self):
        r = self.two(max_positions=1)
        self.assertIn(('POSITION_LIMIT', 'SIGNAL'), self.codes(r, 'BBB'))

    def test_participation_caps_quantity_and_cancels_remainder(self):
        r = run(scenario() + [bar(20, '102', vol='6')])
        t, = r['trades']
        self.assertEqual(t['quantity'], 6)
        self.assertTrue(any(x['reason'] == 'ENTRY_REMAINDER_CANCELLED' and x['quantity'] == 4 for x in r['reasons']))
        half = run(scenario() + [bar(20, '102', vol='9')], volume_participation=D('0.5'))
        self.assertEqual(half['trades'][0]['quantity'], 4)       # floor(9 * 0.5)


class MultiDayAndDeterminism(unittest.TestCase):
    def day(self, d):
        return scenario(day=d) + [bar(20, '102', day=d), bar(21, '102', day=d), bar(350, '103', day=d)]

    def test_second_day_result_is_independent_of_first_day(self):
        both = run(self.day(12) + self.day(13))
        only = run(self.day(13))
        t13 = [t for t in both['trades'] if t['signal_at'].day == 13]
        self.assertEqual(t13, only['trades'])
        self.assertEqual(both['daily_results'][1]['net_change'], only['daily_results'][0]['net_change'])
        self.assertEqual(sum(d['net_change'] for d in both['daily_results']), both['summary']['net_profit'])

    def test_day_loss_does_not_leak_into_next_day_limit(self):
        # Day 12 ends exactly at the loss limit; day 13 must still be allowed to trade.
        r = run(self.day(12) + self.day(13), daily_loss_limit=D('3.02'))
        self.assertEqual(len([t for t in r['trades'] if t['side'] == 'BUY']), 2)

    def test_symbol_independence_when_limits_not_binding(self):
        a = self.day(12)
        b = [replace(x, symbol='BBB') for x in scenario() + [bar(20, '104'), bar(21, '104')]]
        solo = run(a)
        duo = run(a + b, universe=('AAA', 'BBB'))
        self.assertEqual([t for t in duo['trades'] if t['symbol'] == 'AAA'], solo['trades'])

    def test_input_order_does_not_matter_and_input_not_mutated(self):
        rows = entered(bar(21, '102'), bar(350, '103')) + scenario(sym='BBB') + [bar(20, '102', sym='BBB')]
        snapshot = list(rows)
        shuffled = list(rows)
        random.Random(7).shuffle(shuffled)
        a = run(rows, universe=('AAA', 'BBB'))
        b = run(shuffled, universe=('AAA', 'BBB'))
        self.assertEqual(rows, snapshot)
        self.assertEqual(a, b)

    def test_replay_is_repeatable(self):
        rows = entered(bar(21, '102'), bar(350, '103'))
        self.assertEqual(run(rows), run(rows))


class Rejections(unittest.TestCase):
    def bad(self, row):
        with self.assertRaises(ValueError):
            run(scenario() + [row])

    def test_corrupt_rows_are_rejected(self):
        good = bar(20, '102')
        naive = datetime(2026, 11, 12, 9, 20)
        cases = [
            replace(good, event_at=naive, available_at=naive + M1),
            replace(good, available_at=good.event_at + timedelta(seconds=59)),
            replace(good, event_at=good.event_at + timedelta(seconds=30), available_at=good.event_at + M1 + timedelta(seconds=30)),
            bar(-1), replace(bar(0), event_at=at(391), available_at=at(392)),      # 08:59 and 15:31
            replace(good, event_at=at(390), available_at=at(391)),                   # 15:30
            replace(good, high_price=D(90)), replace(good, low_price=D(103)),
            replace(good, close_price=D('NaN')), replace(good, open_price=D(0)),
            replace(good, close_price=D(-1)), replace(good, volume=D(-1)),
            replace(good, close_price=102.0),                                        # float, not Decimal
            replace(good, turnover=D('1')),                                          # below low*volume
            replace(good, turnover=D('999999')),                                     # above high*volume
        ]
        for row in cases:
            with self.subTest(row=row):
                self.bad(row)

    def test_duplicates_rejected_even_when_identical(self):
        for second in (bar(20, '102'), bar(20, '103')):
            with self.assertRaises(ValueError):
                run(scenario() + [bar(20, '102'), second])

    def test_no_selected_candles(self):
        with self.assertRaises(ValueError):
            run(scenario(), universe=('ZZZ',))

    def test_config_validation(self):
        for cfg in (dict(order_quantity=0), dict(order_quantity=True), dict(max_positions=-1),
                    dict(initial_cash=D(0)), dict(fee_rate=D('NaN')), dict(slippage_rate=D(1)),
                    dict(stop_rate=D('0.02'), target_rate=D('0.03')),     # RR 1.5 < 2
                    dict(vwap_mode='mixed'), dict(volume_participation=D('1.1')),
                    dict(min_cash_ratio=D(101)), dict(daily_loss_limit=D(0)), dict(daily_order_limit=0),
                    dict(fee_rate=D('0.6'), sell_tax_rate=D('0.6')), dict(order_quantity=1.5)):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                replace(Config(), **cfg).validate()


def synth(seed, sym, day):
    rng = random.Random(f'{seed}:{sym}:{day}')
    price, rows = D(100), []
    for i in range(381):
        drift = D('0.0006') if 15 <= i <= 90 else D(0)
        close = (price * (1 + drift + D(rng.randint(-30, 30)) / D(10000))).quantize(D('0.01'))
        vol = D(rng.randint(60, 220)) * (3 if i % 5 == 4 and rng.random() < .3 else 1)
        high = max(price, close) + D(rng.randint(0, 20)) / 100
        low = min(price, close) - D(rng.randint(0, 20)) / 100
        if i > 20 and rng.random() < .03:
            continue                                                        # missing bar
        delay = timedelta(minutes=rng.randint(1, 6)) if i > 20 and rng.random() < .04 else timedelta(0)
        rows.append(Candle(sym, at(i, day), at(i, day) + M1 + delay, price, high, low, close, vol, close * vol))
        price = close
    return rows


class Properties(unittest.TestCase):
    def test_invariants_over_random_multi_symbol_multi_day_paths(self):
        cfg = replace(Config(), order_quantity=20, max_order_amount=D(100000), daily_order_limit=10)
        buys = sells_ = 0
        for seed in range(30):
            rows = [r for d in (12, 13) for s in ('AAA', 'BBB') for r in synth(seed, s, d)]
            res = replay(rows, cfg, universe=['AAA', 'BBB'])
            vol = {(r.symbol, r.event_at): r for r in rows}
            held = {}
            for t in res['trades']:
                bar_ = vol[t['symbol'], t['event_at']]
                self.assertEqual(t['execution_at'], bar_.available_at)
                self.assertEqual(bar_.available_at, bar_.event_at + M1)            # only on-time evidence
                self.assertGreaterEqual(t['event_at'], t['signal_at'])
                self.assertLessEqual(t['quantity'], int(bar_.volume))
                self.assertEqual(t['evidence_close'], bar_.close_price)
                amount = t['price'] * t['quantity']
                self.assertEqual(t['amount'], amount)
                self.assertEqual(t['fee'], (amount * cfg.fee_rate).quantize(D(1), rounding=ROUND_DOWN))
                if t['side'] == 'BUY':
                    buys += 1
                    self.assertEqual(t['price'], bar_.close_price * (1 + cfg.slippage_rate))
                    self.assertEqual(t['tax'], 0)
                    self.assertTrue(at(21) <= t['execution_at'].replace(day=12, month=11) < at(150))
                    held[t['symbol']] = held.get(t['symbol'], 0) + t['quantity']
                else:
                    sells_ += 1
                    self.assertLessEqual(t['reference_price'], bar_.close_price)
                    self.assertEqual(t['price'], t['reference_price'] * (1 - cfg.slippage_rate))
                    self.assertEqual(t['tax'], (amount * cfg.sell_tax_rate).quantize(D(1), rounding=ROUND_DOWN))
                    held[t['symbol']] -= t['quantity']
                    self.assertGreaterEqual(held[t['symbol']], 0)
            self.assertEqual(res['summary']['cash'],
                             cfg.initial_cash + sum(t['cash_delta'] for t in res['trades']))
            self.assertTrue(all(c['cash'] >= 0 for c in res['equity_curve']))
            self.assertEqual({s: q for s, q in held.items() if q}, res['summary']['open_positions'])
            per_day = {}
            for s in res['signals']:
                if s['side'] == 'BUY':
                    per_day[s['symbol'], s['at'].date()] = per_day.get((s['symbol'], s['at'].date()), 0) + 1
            self.assertTrue(all(n == 1 for n in per_day.values()))
            if not res['summary']['open_positions']:
                self.assertEqual(sum(d['net_change'] for d in res['daily_results']), res['summary']['net_profit'])
        self.assertGreaterEqual(buys, 10, 'property test must exercise entries')
        self.assertGreaterEqual(sells_, 10, 'property test must exercise exits')


class KnownDefects(unittest.TestCase):
    """Correct behaviour that the reviewed commit does NOT show. expectedFailure until fixed."""

    @unittest.expectedFailure
    def test_DEFECT_late_adverse_bar_must_still_reach_stop_and_mark(self):
        # A crash bar (low 50) for 09:22 reaches us late at 10:00. From 10:00 the information is
        # known: it must queue a STOP (executed on a later fresh bar) and/or mark equity down.
        crash = bar(22, '50', o='102', lo='50', hi='102', avail=at(60))
        r = run(entered(bar(21, '102'), crash, bar(61, '102'), bar(62, '102')))
        self.assertTrue(any(s['reason'] in ('STOP', 'AMBIGUOUS_STOP_FIRST') and s['at'] < at(300) for s in r['signals']),
                        'late bar with low below stop never produced a STOP signal')

    @unittest.expectedFailure
    def test_DEFECT_late_adverse_bar_must_not_leave_equity_overstated(self):
        crash = bar(22, '50', o='102', lo='50', hi='102', avail=at(60))
        r = run(entered(bar(21, '102'), crash))
        after = [c for c in r['equity_curve'] if c['at'] >= at(60)][0]
        self.assertLess(after['net_liquidation_equity'], D('999900'))   # 10 sh known down to ~50

    @unittest.expectedFailure
    def test_DEFECT_stale_entry_must_not_fill_hours_after_signal(self):
        # Signal 09:20, only next on-time bar at 11:20 (price 90, below the 100 range): the
        # breakout premise is long gone. Spec for the old backtest used a 2-minute window.
        r = run(scenario() + [bar(140, '90', lo='90'), bar(141, '90')])
        self.assertEqual([t for t in r['trades'] if t['side'] == 'BUY'], [])

    @unittest.expectedFailure
    def test_DEFECT_overnight_block_reason_should_be_unresolved_exposure(self):
        rows = entered(bar(21, '102')) + scenario(day=13) + [bar(20, '102', day=13)]
        r = run(rows)
        day13 = [x['reason'] for x in r['reasons'] if x['at'].day == 13]
        self.assertIn('UNRESOLVED_EXPOSURE', day13)    # currently ALREADY_ENTERED_OR_PENDING


class StaticPurity(unittest.TestCase):
    ALLOWED = {'__future__', 'collections', 'dataclasses', 'datetime', 'decimal', 'typing'}

    def imports(self, path):
        found = set()
        for node in ast.walk(ast.parse(Path(path).read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                found |= {a.name.split('.')[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                found.add(('.' * node.level) + (node.module or ''))
        return found

    def test_day01_imports_only_stdlib_and_risk_policy(self):
        import auto_trader.ml.day01 as mod
        found = self.imports(mod.__file__)
        self.assertEqual(found - self.ALLOWED, {'..risk_policy'})
        tree = ast.parse(Path(mod.__file__).read_text(encoding='utf-8'))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertFalse(names & {'open', 'print', 'input', 'eval', 'exec', '__import__', 'random', 'os'})
        self.assertFalse(attrs & {'now', 'utcnow', 'today', 'environ', 'read_text', 'write_text'})

    def test_risk_policy_is_pure_decimal_module(self):
        import auto_trader.risk_policy as mod
        self.assertEqual(self.imports(mod.__file__), {'decimal'})

    def test_this_review_imports_only_day01_from_the_project(self):
        project = {m for m in self.imports(__file__) if m.startswith('auto_trader')}
        self.assertEqual(project, {'auto_trader', 'auto_trader.ml.day01'})   # 'auto_trader': import a.b.c
        src = Path(__file__).read_text(encoding='utf-8')
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.ImportFrom) and (node.module or '').startswith('auto_trader'):
                self.assertEqual(node.module, 'auto_trader.ml.day01')


if __name__ == '__main__':
    unittest.main()

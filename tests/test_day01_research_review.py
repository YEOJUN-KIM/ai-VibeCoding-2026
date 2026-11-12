"""Independent (Claude) review of DAY-01 research inputs and estimated-open mode. Synthetic only.

Expected values are hand-computed from the written contract, not produced by implementation
helpers. Imports only auto_trader.ml.day01 and auto_trader.ml.day01_research (no settings,
database, broker or file access). Tests named test_defect_* pin findings of this review and are
marked expectedFailure until Codex fixes them.
"""
import ast
import random
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from auto_trader.ml.day01 import Candle, Config, KST, replay
from auto_trader.ml.day01_research import adapt_rows, coverage, run_input

DAY = 12
M1 = timedelta(minutes=1)


def at(index, day=DAY):
    return datetime(2026, 11, day, 9, tzinfo=KST) + timedelta(minutes=index)


def bar(i, close='100', *, day=DAY, sym='AAA', vol='100', o=None, lo=None, hi=None, avail=None):
    c, v = D(close), D(vol)
    op = D(o) if o is not None else c
    low = D(lo) if lo is not None else min(c, op)
    high = D(hi) if hi is not None else max(c, op)
    return Candle(sym, at(i, day), avail if avail is not None else at(i, day) + M1, op, high, low, c, v, c * v)


def scenario(day=DAY, sym='AAA'):
    """Signal at 09:20 (first 5m bar after a flat 09:00-09:15 range)."""
    return ([bar(i, day=day, sym=sym) for i in range(15)] +
            [bar(i, '102', day=day, sym=sym, vol='200') for i in range(15, 20)])


def rrun(rows, universe=('AAA',), **cfg):
    cfg.setdefault('execution', 'research_estimated_open')
    return replay(rows, replace(Config(), **cfg), universe=list(universe))


def reasons(r, symbol=None, code=None):
    return [x for x in r['reasons'] if (symbol is None or x['symbol'] == symbol) and (code is None or x['reason'] == code)]


def codes(r, symbol):
    return [(x['reason'], x.get('phase')) for x in r['reasons'] if x['symbol'] == symbol]


class Defaults(unittest.TestCase):
    def test_strict_default_and_labels_unchanged(self):
        self.assertEqual(Config().execution, 'next_open')
        rows = scenario() + [bar(21, '103')]
        strict = replay(rows, Config(), universe=['AAA'])
        self.assertEqual(strict['trades'], [])                                  # no quotes => no strict fill
        self.assertEqual(strict['execution_model'], 'causal_next_1m_open_quote')
        legacy = replay(rows, replace(Config(), execution='legacy_close'), universe=['AAA'])
        self.assertEqual(legacy['execution_model'], 'later_on_time_1m_close_with_adverse_trigger_proxy')
        for r in (strict, legacy):
            self.assertEqual(r['research_assumptions']['run_status'], 'NOT_APPLICABLE')
            self.assertEqual(r['research_assumptions']['non_funded_liability'], 0)
            self.assertEqual(r['summary']['estimated_open_fills'], 0)
            self.assertFalse(r['research_assumptions']['active'])

    def test_research_is_only_selected_explicitly_and_rejects_quotes(self):
        with self.assertRaises(ValueError):
            replace(Config(), execution='estimated_open').validate()
        from auto_trader.ml.day01 import OpenQuote
        q = OpenQuote('AAA', at(21), at(21), D(103), D(1000))
        with self.assertRaises(ValueError):
            replay(scenario(), replace(Config(), execution='research_estimated_open'), universe=['AAA'], open_quotes=[q])

    def test_lag_config_bounds(self):
        for good in (D(0), D(2), D(30)):
            replace(Config(), research_max_observation_lag_minutes=good).validate()
        for bad in (D(31), D(-1), D('NaN'), 2, 2.0):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                replace(Config(), research_max_observation_lag_minutes=bad).validate()


class EstimatedOpen(unittest.TestCase):
    def test_settles_at_observation_with_counterfactual_open_and_honest_labels(self):
        rows = scenario() + [bar(20, '102'), bar(21, '99', o='103', hi='103', lo='99', vol='5')]
        r = rrun(rows)
        t, = r['trades']
        self.assertEqual((t['signal_at'], t['estimated_execution_at'], t['execution_at'], t['observation_at'],
                          t['settlement_at']), (at(20), at(21), at(22), at(22), at(22)))
        self.assertEqual(t['settlement_lag'], M1)
        self.assertEqual((t['price_source'], t['reference_price'], t['price'], t['quantity']),
                         ('ESTIMATED_CANDLE_OPEN', D(103), D('103.0515'), 10))        # open, NOT close 99; full configured qty
        self.assertIn('positive_volume_veto', t['hindsight_fields'])
        self.assertFalse(t['physical_fill_claim'])
        self.assertTrue(t['uncertain'])
        self.assertEqual(t['evidence_close'], None)
        self.assertEqual(r['execution_model'], 'research_estimated_open_observation_settlement')
        self.assertEqual(r['research_assumptions']['capacity'], 'configured_order_quantity_not_liquidity')
        self.assertEqual((r['summary']['estimated_open_fills'], r['summary']['open_quote_fills'],
                          r['summary']['legacy_close_fills']), (1, 0, 0))
        self.assertEqual(r['research_assumptions']['run_status'], 'ESTIMATED_ONLY')
        # no backdated ledger: nothing is booked before the observation time
        for c in r['equity_curve']:
            if c['at'] < at(22):
                self.assertEqual((c['cash'], c['net_liquidation_equity']), (D(1000000), D(1000000)), c['at'])
        self.assertEqual([c['cash'] for c in r['equity_curve'] if c['at'] == at(22)], [D('998969.485')])

    def test_target_volume_never_sizes_the_order_and_zero_volume_is_a_labelled_veto(self):
        r = rrun(scenario() + [bar(21, '103', vol='1')])
        self.assertEqual(r['trades'][0]['quantity'], 10)                        # configured qty, not 1
        veto = rrun(scenario() + [bar(21, '103', vol='0')])
        self.assertEqual(veto['trades'], [])
        self.assertTrue(reasons(veto, 'AAA', 'RESEARCH_ZERO_VOLUME_OUTCOME_VETO'))
        nxt = rrun(scenario() + [bar(21, '103', vol='0'), bar(22, '104')], max_signal_to_fill_minutes=D(5))
        t, = nxt['trades']
        self.assertEqual((t['estimated_execution_at'], t['execution_at'], t['reference_price']), (at(22), at(23), D(104)))

    def test_age_is_exclusive_on_estimated_open_and_expiry_includes_grace(self):
        r = rrun(scenario() + [bar(22, '104')])                                  # candidate 09:22 is age 2m: excluded
        self.assertEqual(r['trades'], [])
        old = reasons(r, 'AAA', 'ENTRY_SIGNAL_TOO_OLD')
        self.assertEqual([x['at'] for x in old], [at(25)])                       # signal + 2m age + 1m + 2m lag grace
        self.assertEqual(len(rrun(scenario() + [bar(21, '104')])['trades']), 1)  # age 1m

    def test_lag_is_measured_from_bar_end_with_inclusive_limit_and_no_backdating(self):
        edge = rrun(scenario() + [bar(21, '103', avail=at(24))])                 # end 09:22, lag exactly 2m
        t, = edge['trades']
        self.assertEqual((t['estimated_execution_at'], t['execution_at']), (at(21), at(24)))
        over = rrun(scenario() + [bar(21, '103', avail=at(24) + timedelta(seconds=1))])
        self.assertEqual(over['trades'], [])
        self.assertTrue(reasons(over, 'AAA', 'RESEARCH_MISSING_OUTCOME'))                 # timeout at bar end + lag fired first
        fallback = rrun(scenario() + [bar(21, '103', avail=at(30)), bar(22, '104')], max_signal_to_fill_minutes=D(5))
        t, = fallback['trades']
        self.assertEqual((t['estimated_execution_at'], t['reference_price']), (at(22), D(104)))   # never bar 21's price

    def test_earlier_candidate_is_awaited_and_faster_later_bar_is_not_preferred(self):
        rows = scenario() + [bar(21, '103', avail=at(24)), bar(22, '109')]       # bar 22 arrives first (09:23)
        t, = rrun(rows, max_signal_to_fill_minutes=D(5))['trades']
        self.assertEqual((t['estimated_execution_at'], t['reference_price'], t['execution_at']), (at(21), D(103), at(24)))

    def test_missing_earlier_candidate_times_out_then_next_is_used(self):
        rows = scenario() + [bar(22, '104')]                                    # bar 21 never arrives
        r = rrun(rows, max_signal_to_fill_minutes=D(5))
        t, = r['trades']
        self.assertEqual((t['estimated_execution_at'], t['reference_price']), (at(22), D(104)))
        self.assertEqual(t['execution_at'], at(24))                              # settles when 21's end+lag timeout fires
        self.assertTrue(reasons(r, 'AAA', 'RESEARCH_MISSING_OUTCOME'))

    def test_estimated_exit_symmetry_and_1450_timer(self):
        rows = scenario() + [bar(21, '103'), bar(351, '104'), bar(352, '104')]
        r = rrun(rows)
        buy, sell = r['trades']
        self.assertEqual(sell['reason'], 'SESSION_LIQUIDATION')
        self.assertEqual((sell['signal_at'], sell['estimated_execution_at'], sell['execution_at']),
                         (at(350), at(351), at(352)))                           # 14:50 signal, 14:51 open, observed 14:52
        self.assertEqual(sell['price'], D('103.948'))                              # 104 x 0.9995
        self.assertEqual(r['summary']['status'], 'FLAT')

    def test_1510_estimated_open_observed_after_freeze_is_unresolved(self):
        # Zero-volume bars 14:51.. are observed (vetoed), so candidate ordering is not blocked by missing bars.
        vetoed = lambda stop: [bar(i, '104', vol='0') for i in range(351, stop)]
        ok = rrun(scenario() + [bar(21, '103')] + vetoed(368) + [bar(368, '104')])      # open 15:08 observed 15:09
        self.assertEqual(ok['summary']['status'], 'FLAT')
        self.assertEqual(sells(ok)[0]['estimated_execution_at'], at(368))
        late = rrun(scenario() + [bar(21, '103')] + vetoed(369) + [bar(369, '104')])    # open 15:09 observed 15:10 (frozen)
        self.assertEqual(late['summary']['status'], 'UNLIQUIDATED')
        self.assertEqual(sells(late), [])
        self.assertEqual(late['failures'][0]['reason'], 'UNLIQUIDATED_AT_1510')
        self.assertTrue(any(f['reason'] == 'UNRESOLVED_OBSERVED_AFTER_1510' for f in late['failures']))
        s = late['summary']
        self.assertEqual((s['unresolved_quantities'], s['frozen_valuation_symbols']), ({'AAA': 10}, ['AAA']))
        self.assertTrue(s['overnight_gap_exposure_unmodeled'])
        self.assertGreater(late['valuation_marks']['AAA']['price'], 0)

    def test_missing_candidates_delay_settlement_until_bar_end_plus_lag(self):
        # Contract consequence (not a defect): an absent earlier candidate blocks later ones until its timeout,
        # so with bars 14:51-15:07 missing, the 15:08 bar observed at 15:09 cannot settle before the 15:10 freeze.
        r = rrun(scenario() + [bar(21, '103'), bar(368, '104')])
        self.assertEqual(sells(r), [])
        self.assertEqual(r['summary']['status'], 'UNLIQUIDATED')

    @unittest.expectedFailure
    def test_defect_unrelated_late_bar_must_not_be_reported_as_unresolved_outcome(self):
        # 14:50 exit is pending with no candidate bars. An unrelated late bar (event 09:30) arriving
        # after 15:10 is not an outcome candidate for this order.
        rows = scenario() + [bar(21, '103'), bar(10, '100', day=DAY, avail=at(380))]
        # (bar 10 duplicates a scenario event, so use a different minute that is absent)
        rows = scenario() + [bar(21, '103'), bar(40, '100', avail=at(380))]
        r = rrun(rows)
        self.assertFalse(any(f['reason'] == 'UNRESOLVED_OBSERVED_AFTER_1510' for f in r['failures']))


class FreshDecisions(unittest.TestCase):
    def late_last_minute(self, avail):
        rows = scenario()
        rows[19] = replace(rows[19], available_at=avail)                          # 09:19 bar arrives late
        return rows

    def test_signal_is_taken_at_actual_arrival_within_lag_and_candidate_follows_it(self):
        r = rrun(self.late_last_minute(at(20) + timedelta(seconds=30)) + [bar(21, '103')])
        sig, = [s for s in r['signals'] if s['side'] == 'BUY']
        self.assertEqual(sig['at'], at(20) + timedelta(seconds=30))               # available time preserved, not 09:20:00
        t, = r['trades']
        self.assertEqual((t['estimated_execution_at'], t['reference_price']), (at(21), D(103)))
        edge = rrun(self.late_last_minute(at(22)) + [bar(22, '103'), bar(23, '104')], max_signal_to_fill_minutes=D(5))
        sig, = [s for s in edge['signals'] if s['side'] == 'BUY']
        self.assertEqual(sig['at'], at(22))                                       # lag exactly 2m from the 5m bar end
        self.assertEqual(edge['trades'][0]['estimated_execution_at'], at(23))     # strictly after the signal, not bar 22

    def test_arrival_beyond_lag_is_not_a_fresh_decision(self):
        r = rrun(self.late_last_minute(at(22) + timedelta(seconds=1)) + [bar(i, '102') for i in range(22, 26)],
                 max_signal_to_fill_minutes=D(5))
        self.assertEqual([s for s in r['signals'] if s['at'] < at(25)], [])
        self.assertTrue(reasons(r, 'AAA', 'NO_FRESH_FIVE_MINUTE_BAR'))

    def test_late_old_row_cannot_create_a_signal_for_a_stale_five_minute_bar(self):
        rows = scenario() + [bar(i, '102', vol='200') for i in range(20, 25)]
        rows[3] = replace(rows[3], available_at=at(45))                           # 09:03 bar arrives at 09:45
        r = rrun(rows, max_signal_to_fill_minutes=D(5))
        self.assertEqual([s for s in r['signals'] if s['at'] >= at(45)], [])      # 09:20 bar is 25 min stale by then


def sells(r):
    return [t for t in r['trades'] if t['side'] == 'SELL']


class RiskAndReservations(unittest.TestCase):
    def test_pending_reservations_count_for_position_cash_exposure_and_order_limits(self):
        def two(**cfg):
            bbb = [bar(i, sym='BBB') for i in range(20)] + [bar(i, '102', sym='BBB', vol='200') for i in range(20, 25)]
            return rrun(scenario(sym='AAA') + bbb, universe=('AAA', 'BBB'), **cfg)
        # AAA (pending, no outcome yet) reserves a slot, cash and exposure at 09:20; BBB signals at 09:25
        # after AAA's reservation expired (signal + 2m + 1m + 2m grace == 09:25) and is therefore allowed.
        free = two()
        self.assertTrue([s for s in free['signals'] if s['symbol'] == 'BBB'])
        self.assertTrue(reasons(free, 'AAA', 'ENTRY_SIGNAL_TOO_OLD')[0]['at'] == at(25))
        # same instant, reservation still held: simultaneous AAA/BBB signals at 09:20
        both = rrun(scenario(sym='AAA') + scenario(sym='BBB'), universe=('AAA', 'BBB'), max_positions=1)
        self.assertIn(('POSITION_LIMIT', 'SIGNAL'), codes(both, 'BBB'))
        cash = rrun(scenario(sym='AAA') + scenario(sym='BBB'), universe=('AAA', 'BBB'),
                    initial_cash=D(1100), min_cash_ratio=D(0))
        self.assertIn(('INSUFFICIENT_CASH', 'SIGNAL'), codes(cash, 'BBB'))
        exp = rrun(scenario(sym='AAA') + scenario(sym='BBB'), universe=('AAA', 'BBB'), max_total_investment=D(1500))
        self.assertIn(('TOTAL_EXPOSURE_LIMIT', 'SIGNAL'), codes(exp, 'BBB'))
        cnt = rrun(scenario(sym='AAA') + scenario(sym='BBB'), universe=('AAA', 'BBB'), daily_order_limit=1)
        self.assertIn(('DAILY_ENTRY_ORDER_LIMIT', None), codes(cnt, 'BBB'))

    def test_curve_exposes_reservations(self):
        r = rrun(scenario())
        c = [x for x in r['equity_curve'] if x['at'] == at(20)][-1]
        self.assertEqual((c['reserved_position_slots'], c['reserved_cash']), (1, D('1020.51')))   # 10 x 102 x 1.0005 + fee 0

    def test_loss_known_after_modeled_open_cannot_reject_a_reserved_buy(self):
        # AAA entered at 09:21 (settled 09:22). AAA crashes on-time (bar 22, observed 09:23) so the 300 KRW
        # day-loss limit is hit at 09:23 - AFTER BBB's modeled open 09:21 - but BBB's bar 21 is observed late (09:24).
        crash = bar(22, '50', sym='AAA', o='103', lo='50', hi='103')
        rows = (scenario(sym='AAA') + [bar(21, '103', sym='AAA'), crash] + scenario(sym='BBB') +
                [bar(21, '103', sym='BBB', avail=at(24))])
        r = rrun(rows, universe=('AAA', 'BBB'), daily_loss_limit=D(300))
        self.assertEqual([(t['symbol'], t['estimated_execution_at']) for t in r['trades']],
                         [('AAA', at(21)), ('BBB', at(21))])
        self.assertTrue(any(c['loss_limit_exceeded'] and c['at'] == at(23) for c in r['equity_curve']))
        self.assertEqual(r['research_assumptions']['run_status'], 'ESTIMATED_ONLY')

    def test_loss_known_strictly_before_modeled_open_may_cancel_the_buy(self):
        crash = bar(22, '50', sym='AAA', o='103', lo='50', hi='103')              # known 09:23
        bbb = scenario(sym='BBB') + [bar(24, '103', sym='BBB')]                  # bars 21-23 missing; modeled open 09:24
        rows = scenario(sym='AAA') + [bar(21, '103', sym='AAA'), crash] + bbb
        r = rrun(rows, universe=('AAA', 'BBB'), daily_loss_limit=D(300), max_signal_to_fill_minutes=D(5))
        self.assertEqual([t['symbol'] for t in r['trades']], ['AAA'])
        pre = [x for x in r['reasons'] if x['symbol'] == 'BBB' and x.get('phase') == 'RESEARCH_PRE_OPEN']
        self.assertEqual([(x['reason'], x['estimated_execution_at']) for x in pre], [('DAILY_LOSS_LIMIT', at(24))])
        self.assertLess(pre[0]['information_at'], at(24))

    def test_catch_up_applies_already_known_bars_stop_first_and_marks_latest(self):
        # BBB's... AAA settles late (09:24); bar 22 (on-time, observed 09:23) already breached the stop.
        crash = bar(22, '91', o='103', lo='90', hi='103')
        rows = scenario() + [bar(21, '103', avail=at(24)), crash, bar(25, '95')]
        r = rrun(rows)
        stop, = [s for s in r['signals'] if s['reason'] == 'STOP']
        self.assertEqual(stop['at'], at(24))                                     # learned at settlement, never earlier
        c = [x for x in r['equity_curve'] if x['at'] == at(24)][-1]
        # marked at the latest KNOWN close 91 (not the 103 entry): 10 x 91 x 0.9995 = 909.545, tax 1, fee 0
        self.assertEqual(c['net_liquidation_equity'], D('1000000') - D('1030.515') + D('908.545'))
        t, = sells(r)
        self.assertEqual((t['reason'], t['estimated_execution_at'], t['reference_price'], t['execution_at']),
                         ('STOP', at(25), D(95), at(26)))                        # next open after the signal time

    def test_catch_up_entry_bar_range_and_dual_hit_stop_first(self):
        rows = scenario() + [bar(21, '105', o='103', lo='90', hi='115'), bar(23, '100')]
        r = rrun(rows)
        self.assertEqual([s['reason'] for s in r['signals'] if s['side'] == 'SELL'][:1], ['AMBIGUOUS_STOP_FIRST'])

    def test_same_instant_sells_settle_before_buys(self):
        # BBB is held and stopped on-time; AAA's modeled open is observed late at exactly BBB's exit observation.
        crash = bar(22, '50', sym='BBB', o='103', lo='50', hi='103')
        rows = (scenario(sym='BBB') + [bar(21, '103', sym='BBB'), crash, bar(24, '95', sym='BBB')] +
                scenario(sym='AAA') + [bar(21, '103', sym='AAA', avail=at(25))])
        r = rrun(rows, universe=('AAA', 'BBB'), research_max_observation_lag_minutes=D(5), max_positions=2)
        at25 = [(t['symbol'], t['side']) for t in r['trades'] if t['execution_at'] == at(25)]
        self.assertEqual(at25, [('BBB', 'SELL'), ('AAA', 'BUY')])


class ResourceInvalid(unittest.TestCase):
    def overrun(self, extra=()):
        # signal price 102 passes the 1100 KRW snapshot; the modeled open 150 does not.
        rows = scenario() + [bar(21, '150')] + list(extra)
        return rrun(rows, initial_cash=D(1100), min_cash_ratio=D(0))

    def test_gap_beyond_snapshot_is_flagged_invalid_with_non_funded_liability(self):
        r = self.overrun()
        a = r['research_assumptions']
        self.assertEqual(a['run_status'], 'INVALID_RESOURCE_OVERRUN')
        self.assertEqual(a['non_funded_liability'], D('400.75'))                 # 1500.75 cost - 1100 cash
        self.assertEqual(a['risk_limit_adherence'], 'INVALID')
        self.assertIn('SIGNAL_CASH_OVERRUN', a['resource_breaches'][0]['reasons'])
        t, = r['trades']
        self.assertEqual(t['price'], D('150.075'))
        self.assertIn('SIGNAL_CASH_OVERRUN', t['resource_breaches'])
        self.assertEqual(r['summary']['cash'], D(0))                             # never negative
        # equity: 0 - 400.75 + liquidation(10 x 150 x 0.9995 = 1499.25 - tax floor(2.9985)=2) = 1096.50
        self.assertEqual(r['summary']['net_liquidation_equity'], D('1096.50'))
        self.assertEqual(r['summary']['net_profit'], D('-3.50'))

    def test_invalid_run_blocks_new_entries_but_allows_exit_and_sale_retires_liability(self):
        bbb = ([bar(i, sym='BBB') for i in range(20)] + [bar(i, '102', sym='BBB', vol='200') for i in range(20, 25)] +
               [bar(26, '102', sym='BBB')])
        r = rrun(scenario() + [bar(21, '150')] + bbb + [bar(351, '150'), bar(352, '150')],
                 universe=('AAA', 'BBB'), initial_cash=D(1100), min_cash_ratio=D(0))
        self.assertIn(('RESEARCH_RUN_INVALID', 'SIGNAL'), codes(r, 'BBB'))
        self.assertEqual([(t['symbol'], t['side']) for t in r['trades']], [('AAA', 'BUY'), ('AAA', 'SELL')])
        a = r['research_assumptions']
        self.assertEqual(a['run_status'], 'INVALID_RESOURCE_OVERRUN')              # stays invalid
        self.assertEqual(a['non_funded_liability'], D(0))                          # retired by the sale
        # sale: 1499.25 - fee floor(0.22)=0 - tax 2 = 1497.25; retire 400.75 => cash 1096.50
        self.assertEqual(r['summary']['cash'], D('1096.50'))
        self.assertEqual(r['summary']['net_liquidation_equity'], D('1096.50'))
        self.assertTrue(all(c['cash'] >= 0 and c['research_liability'] >= 0 for c in r['equity_curve']))

    def test_valid_run_has_no_liability(self):
        r = rrun(scenario() + [bar(21, '103')])
        self.assertEqual(r['research_assumptions']['non_funded_liability'], 0)
        self.assertEqual(r['research_assumptions']['run_status'], 'ESTIMATED_ONLY')

    @unittest.expectedFailure
    def test_defect_run_input_top_level_status_must_surface_invalid_run(self):
        rows = [raw_from(c) for c in scenario() + [bar(21, '150')]]
        out = run_input(adapt_rows(rows, universe=['AAA']), replace(Config(), execution='research_estimated_open',
                        initial_cash=D(1100), min_cash_ratio=D(0)), universe=['AAA'], expected_session_dates=[date(2026, 11, 12)])
        self.assertEqual(out['result']['research_assumptions']['run_status'], 'INVALID_RESOURCE_OVERRUN')
        self.assertNotEqual(out['run_status'], 'RAN')           # a consumer reading only the top level must not see success


def raw_from(c, **kw):
    row = dict(symbol=c.symbol, source='TOSS', interval='1m', event_at=c.event_at.isoformat(),
               available_at=c.available_at.isoformat(), collected_at=(c.available_at + timedelta(seconds=1)).isoformat(),
               open_price=str(c.open_price), high_price=str(c.high_price), low_price=str(c.low_price),
               close_price=str(c.close_price), volume=str(c.volume), collection_run_id=7)
    if c.turnover is not None:
        row['turnover'] = str(c.turnover)
    row.update(kw)
    return row


class Adapter(unittest.TestCase):
    def test_preserves_actual_availability_source_and_original_offset(self):
        c = bar(0, '100', avail=at(0) + M1 + timedelta(seconds=37))
        stored = {k: v for k, v in raw_from(c).items() if k != 'turnover'}
        d = adapt_rows([stored], universe=['AAA'])
        got, = d.candles
        self.assertEqual(got.available_at, c.available_at)                      # not rewritten to event+1m
        p, = d.provenance
        self.assertEqual((p['source'], p['interval'], p['symbol'], p['collection_run_id']), ('TOSS', '1m', 'AAA', 7))
        self.assertEqual(p['available_at'] - p['event_at'], M1 + timedelta(seconds=37))
        self.assertEqual(p['collected_at'] - p['available_at'], timedelta(seconds=1))
        self.assertIsNone(got.turnover)                                          # none invented
        utc = adapt_rows([raw_from(c, event_at=c.event_at.astimezone().isoformat() if False else c.event_at.isoformat())],
                         universe=['AAA'])
        self.assertEqual(utc.candles[0].event_at, c.event_at)

    def test_1530_and_out_of_session_rows_are_separated_not_dropped_and_do_not_crash_replay(self):
        rows = [raw_from(c) for c in scenario() + [bar(21, '103')]]
        closing = Candle('AAA', at(390), at(391), D(100), D(100), D(100), D(100), D(5), D(500))     # 15:30
        pre = Candle('AAA', at(-1), at(0), D(100), D(100), D(100), D(100), D(5), D(500))            # 08:59
        rows += [raw_from(closing), raw_from(pre)]
        d = adapt_rows(rows, universe=['AAA'])
        self.assertEqual(sorted((r['reason'], r['candle'].event_at) for r in d.excluded_rows),
                         [('CLOSING_1530', at(390)), ('OUTSIDE_REPLAY_SESSION', at(-1))])
        self.assertEqual(len(d.candles), 21)
        self.assertEqual(len(d.provenance), 21)
        out = run_input(d, replace(Config(), execution='research_estimated_open'), universe=['AAA'],
                        expected_session_dates=[date(2026, 11, 12)])
        self.assertEqual(out['run_status'], 'RAN')
        self.assertEqual(out['coverage']['closing_1530_count'], 1)
        self.assertEqual(out['coverage']['outside_session_count'], 1)
        self.assertEqual(len(out['excluded_rows']), 2)
        with self.assertRaises(ValueError):
            replay([closing], Config(), universe=['AAA'])                        # core replay still strict

    def test_rejections(self):
        c = bar(0)
        good = raw_from(c)
        bad = [dict(good, available_at=None), {k: v for k, v in good.items() if k != 'available_at'},
               dict(good, interval='5m'), dict(good, interval=None), dict(good, source=''), {k: v for k, v in good.items() if k != 'source'},
               dict(good, open_price=100.0), dict(good, close_price=True), dict(good, volume='NaN'), dict(good, volume='-1'),
               dict(good, event_at='2026-11-12T09:00:00'), dict(good, available_at=(c.event_at + timedelta(seconds=30)).isoformat()),
               dict(good, collected_at=(c.available_at - timedelta(seconds=1)).isoformat()),
               dict(good, collection_run_id=0), dict(good, high_price='50'), 'not a mapping']
        for row in bad:
            with self.subTest(row=str(row)[:60]), self.assertRaises(ValueError):
                adapt_rows([row], universe=['AAA'])
        with self.assertRaises(ValueError):
            adapt_rows([good, dict(good, source='OTHER')], universe=['AAA'])       # duplicate even across sources
        with self.assertRaises(ValueError):
            adapt_rows([good], universe=[])
        with self.assertRaises(ValueError):
            adapt_rows([good], universe=['aaa'])
        kept, = adapt_rows([raw_from(c)], universe=['AAA']).candles
        self.assertEqual(kept.turnover, c.turnover)                              # given turnover is preserved

    def test_out_of_universe_rows_are_ignored_even_if_malformed_and_input_not_mutated(self):
        good = raw_from(bar(0))
        junk = dict(raw_from(bar(1, sym='ZZZ')), interval='junk', available_at=None)
        rows = [good, junk]
        snapshot = [dict(r) for r in rows]
        d = adapt_rows(rows, universe=['AAA'])
        self.assertEqual(len(d.candles), 1)
        self.assertEqual(rows, snapshot)

    def test_output_is_sorted_and_independent_of_input_order(self):
        rows = [raw_from(c) for c in scenario(sym='BBB') + scenario(sym='AAA')]
        shuffled = list(rows)
        random.Random(3).shuffle(shuffled)
        self.assertEqual(adapt_rows(rows, universe=['AAA', 'BBB']), adapt_rows(shuffled, universe=['BBB', 'AAA']))


def full_day(day=DAY, sym='AAA', skip=(), late=()):
    out = []
    for i in range(390):
        if i in skip:
            continue
        avail = at(i, day) + M1 + (timedelta(seconds=45) if i in late else timedelta(0))
        out.append(bar(i, '100', day=day, sym=sym, avail=avail))
    return out


class Coverage(unittest.TestCase):
    def cov(self, rows, dates, universe=('AAA',)):
        return coverage(adapt_rows([raw_from(c) for c in rows], universe=list(universe)),
                        universe=list(universe), expected_session_dates=dates)

    def test_complete_missing_and_no_rows_are_distinguished_without_calendar_guessing(self):
        rows = full_day(skip=(5, 6)) + full_day(day=13)
        rep = self.cov(rows, [date(2026, 11, 12), date(2026, 11, 13), date(2026, 11, 16)])
        cells = {(c['day'], c['symbol']): c for c in rep['cells']}
        a, b, c = (cells[date(2026, 11, d), 'AAA'] for d in (12, 13, 16))
        self.assertEqual((a['status'], a['count'], a['missing_count']), ('MISSING', 388, 2))
        self.assertEqual(a['missing_minutes'], [at(5), at(6)])
        self.assertEqual((b['status'], b['count'], b['missing_count']), ('COMPLETE', 390, 0))
        self.assertEqual((c['status'], c['count'], c['missing_count']), ('NO_ROWS', 0, 390))   # not claimed a holiday
        self.assertEqual(rep['missing_days'], [date(2026, 11, 16)])
        self.assertEqual(rep['actual_stored_date_range'], 'UNKNOWN')
        self.assertEqual(rep['scope'], 'CALLER_PROVIDED_ROWS_ONLY')
        self.assertEqual(rep['missing_minute_count'], 392)

    def test_expected_dates_are_caller_supplied_and_unexpected_days_reported(self):
        rep = self.cov(full_day(day=13), [date(2026, 11, 12)])
        self.assertEqual(rep['unexpected_days'], [date(2026, 11, 13)])
        self.assertEqual(rep['missing_days'], [date(2026, 11, 12)])
        for bad in ([], [datetime(2026, 11, 12, 9, 0)]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.cov(full_day(), bad)

    def test_fixed_universe_cells_and_missing_symbols_no_top50(self):
        rows = full_day(sym='AAA') + full_day(sym='ZZZ')
        rep = self.cov(rows, [date(2026, 11, 12)], universe=('AAA', 'BBB'))
        self.assertEqual(rep['universe'], ('AAA', 'BBB'))
        self.assertEqual(rep['missing_symbols'], ['BBB'])
        self.assertEqual([(c['symbol'], c['status']) for c in rep['cells']], [('AAA', 'COMPLETE'), ('BBB', 'NO_ROWS')])
        self.assertEqual(rep['replay_count'], 390)                                # ZZZ never counted

    def test_availability_zero_volume_turnover_and_closing_counts(self):
        rows = full_day(late=(1, 2, 3))
        rows[10] = replace(rows[10], volume=D(0), turnover=D(0))
        rep = self.cov(rows, [date(2026, 11, 12)])
        av = rep['availability']
        self.assertEqual((av['on_time_count'], av['late_count'], av['max_lag_seconds'], av['historical_backfill_count']),
                         (387, 3, 45, 0))
        self.assertEqual((rep['zero_volume_count'], rep['missing_turnover_count']), (1, 0))
        self.assertTrue(rep['exact_vwap_turnover_ready'])
        stored = [raw_from(c) for c in full_day()]
        for r in stored:
            r['turnover'] = None
        closing = raw_from(Candle('AAA', at(390), at(391), D(100), D(100), D(100), D(100), D(5), D(500)))
        d = adapt_rows(stored + [closing], universe=['AAA'])
        rep2 = coverage(d, universe=['AAA'], expected_session_dates=[date(2026, 11, 12)])
        self.assertEqual((rep2['closing_1530_count'], rep2['input_count'], rep2['replay_count']), (1, 391, 390))
        self.assertFalse(rep2['exact_vwap_turnover_ready'])
        self.assertEqual(rep2['strict_open_quote_evidence'], 'NOT_INFERABLE_FROM_CANDLE_INPUT')

    def test_backfill_is_counted_when_available_on_a_later_day(self):
        rows = full_day()
        rows[100] = replace(rows[100], available_at=at(100, 13))
        rep = self.cov(rows, [date(2026, 11, 12)])
        self.assertEqual(rep['availability']['historical_backfill_count'], 1)

    def test_coverage_does_not_filter_replay_and_is_order_independent(self):
        rows = [raw_from(c) for c in full_day()]
        shuffled = list(rows)
        random.Random(5).shuffle(shuffled)
        a = coverage(adapt_rows(rows, universe=['AAA']), universe=['AAA'], expected_session_dates=[date(2026, 11, 12)])
        b = coverage(adapt_rows(shuffled, universe=['AAA']), universe=['AAA'], expected_session_dates=[date(2026, 11, 12)])
        self.assertEqual(a, b)


class RunInput(unittest.TestCase):
    def test_run_input_wires_provenance_and_labels(self):
        rows = [raw_from(c) for c in scenario() + [bar(21, '103')]]
        d = adapt_rows(rows, universe=['AAA'])
        out = run_input(d, replace(Config(), execution='research_estimated_open'), universe=['AAA'],
                        expected_session_dates=[date(2026, 11, 12)])
        self.assertEqual((out['run_status'], out['mode'], out['uncertain'], out['physical_fill_claim']),
                         ('RAN', 'research_estimated_open', True, False))
        self.assertEqual(len(out['result']['trades']), 1)
        self.assertEqual(len(out['provenance']), 21)
        self.assertEqual(out['costs']['sell_tax_rate'], D('0.002'))
        self.assertEqual(out['unfilled_entry_quantity'], 0)
        with self.assertRaises(ValueError):
            run_input(d, Config(), universe=['AAA'], expected_session_dates=[], open_quotes=())
        strict = run_input(d, Config(), universe=['AAA'], expected_session_dates=[date(2026, 11, 12)])
        self.assertEqual(strict['result']['trades'], [])                          # strict default never fills candle-only
        self.assertEqual(strict['unfilled_entry_quantity'], 10)

    def test_empty_selection_reports_no_replay_rows_instead_of_inventing(self):
        d = adapt_rows([raw_from(bar(0))], universe=['AAA'])
        out = run_input(d, Config(), universe=['BBB'], expected_session_dates=[date(2026, 11, 12)])
        self.assertEqual((out['run_status'], out['result']), ('NO_REPLAY_ROWS', None))
        self.assertEqual(out['coverage']['missing_symbols'], ['BBB'])


def synth(seed, sym, day):
    rng = random.Random(f'r{seed}:{sym}:{day}')
    price, rows = D(100), []
    for i in range(390):
        drift = D('0.0006') if 15 <= i <= 90 else D(0)
        close = (price * (1 + drift + D(rng.randint(-30, 30)) / D(10000))).quantize(D('0.01'))
        vol = D(rng.randint(60, 220)) * (3 if i % 5 == 4 and rng.random() < .3 else 1)
        high = max(price, close) + D(rng.randint(0, 20)) / 100
        low = min(price, close) - D(rng.randint(0, 20)) / 100
        if i > 20 and rng.random() < .03:
            continue
        delay = timedelta(seconds=rng.randint(1, 240)) if rng.random() < .15 else timedelta(0)
        rows.append(Candle(sym, at(i, day), at(i, day) + M1 + delay, price, high, low, close, vol, close * vol))
        price = close
    return rows


class Properties(unittest.TestCase):
    def test_invariants_over_random_multi_symbol_multi_day_paths(self):
        cfg = replace(Config(), execution='research_estimated_open', order_quantity=20,
                      max_order_amount=D(100000), max_signal_to_fill_minutes=D(4))
        buys = exits = invalid = 0
        for seed in range(25):
            rows = [r for d in (12, 13) for s in ('AAA', 'BBB') for r in synth(seed, s, d)]
            res = replay(rows, cfg, universe=['AAA', 'BBB'])
            by = {(r.symbol, r.event_at): r for r in rows}
            lag_limit = M1 + timedelta(minutes=2)
            for t in res['trades']:
                row = by[t['symbol'], t['estimated_execution_at']]
                self.assertGreater(t['estimated_execution_at'], t['signal_at'])
                self.assertEqual(t['observation_at'], row.available_at)
                self.assertLessEqual(row.available_at - row.event_at, lag_limit)       # bar-end lag <= 2m
                self.assertGreaterEqual(t['execution_at'], t['observation_at'])        # never booked before observed
                self.assertGreater(row.volume, 0)
                self.assertEqual((t['price_source'], t['quantity'] if t['side'] == 'BUY' else 20, t['physical_fill_claim']),
                                 ('ESTIMATED_CANDLE_OPEN', 20, False))
                self.assertLess(t['observation_at'].replace(day=12), at(370))          # nothing settled at/after 15:10
                if t['side'] == 'BUY':
                    buys += 1
                    self.assertEqual(t['reference_price'], row.open_price)
                    self.assertEqual(t['price'], row.open_price * (1 + cfg.slippage_rate))
                    self.assertLess(t['estimated_execution_at'] - t['signal_at'], timedelta(minutes=4))
                else:
                    exits += 1
                    self.assertEqual(t['price'], row.open_price * (1 - cfg.slippage_rate))
            a = res['research_assumptions']
            self.assertGreaterEqual(a['non_funded_liability'], 0)
            self.assertTrue(all(c['cash'] >= 0 and c['research_liability'] >= 0 for c in res['equity_curve']))
            if a['run_status'] == 'INVALID_RESOURCE_OVERRUN':
                invalid += 1
                self.assertTrue(a['resource_breaches'])
            else:
                self.assertEqual(a['non_funded_liability'], 0)
            self.assertEqual(res['summary']['estimated_open_fills'], len(res['trades']))
            per = {}
            for s in res['signals']:
                if s['side'] == 'BUY':
                    per[s['symbol'], s['at'].date()] = per.get((s['symbol'], s['at'].date()), 0) + 1
            self.assertTrue(all(n == 1 for n in per.values()))
            if seed < 3:
                shuffled = list(rows)
                random.Random(seed).shuffle(shuffled)
                self.assertEqual(replay(shuffled, cfg, universe=['BBB', 'AAA']), res)
        self.assertGreaterEqual(buys, 5, 'property test must exercise estimated entries')
        self.assertGreaterEqual(exits, 3, 'property test must exercise estimated exits')


class StaticPurity(unittest.TestCase):
    def test_imports_are_only_the_two_day01_modules(self):
        tree = ast.parse(Path(__file__).read_text(encoding='utf-8'))
        project = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('auto_trader'):
                project.add(n.module)
            if isinstance(n, ast.Import):
                project |= {a.name for a in n.names if a.name.startswith('auto_trader')}
        self.assertLessEqual(project, {'auto_trader.ml.day01', 'auto_trader.ml.day01_research'})

    def test_research_module_imports_only_stdlib_and_day01(self):
        import auto_trader.ml.day01_research as mod
        tree = ast.parse(Path(mod.__file__).read_text(encoding='utf-8'))
        found = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                found |= {a.name.split('.')[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                found.add(('.' * n.level) + (n.module or ''))
        self.assertEqual(found, {'dataclasses', 'datetime', 'decimal', 'typing', '.day01'})
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertFalse(names & {'open', 'print', 'input', 'eval', 'exec', '__import__', 'os', 'random'})


if __name__ == '__main__':
    unittest.main()

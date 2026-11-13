"""Synthetic tests for DAY-01 dynamic sizing (`sizing='AFFORDABLE_UP_TO_CAP'`) and its Scout link.
No network, DB, files, clock or broker. FIXED stays the default and must not change."""
import random
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D

from auto_trader import day01_scout_link as link
from auto_trader.ml.day01 import Candle, Config, KST, OpenQuote, execution_costs, replay
from auto_trader.risk_policy import RiskPolicy
from tests.test_day01_scout_link import candle, scout_result

FIXED_LEGACY = replace(Config(), execution='legacy_close')
DYN_LEGACY = replace(FIXED_LEGACY, sizing='AFFORDABLE_UP_TO_CAP')
DYN_OPEN = replace(Config(), sizing='AFFORDABLE_UP_TO_CAP')                       # next_open
DYN_RESEARCH = replace(Config(), execution='research_estimated_open', sizing='AFFORDABLE_UP_TO_CAP')
FIXED_RESEARCH = replace(Config(), execution='research_estimated_open')


def at(h, m, s=0):
    return datetime(2026, 11, 13, h, m, s, tzinfo=KST)


def series(symbol, base, up, *, start=15, tail=0, tail_prices=None, volume_up='200'):
    """Flat opening range, five up bars from `start` (signal at 09:(start+5)), then a flat tail."""
    rows = [candle(symbol, i, base) for i in range(start)]
    rows += [candle(symbol, i, up, volume_up) for i in range(start, start + 5)]
    for k in range(tail):
        i = start + 5 + k
        rows.append(candle(symbol, i, (tail_prices or {}).get(i, up)))
    return rows


def signal_quantity(result, symbol=None):
    return [s['quantity'] for s in result['signals'] if s['side'] == 'BUY' and (symbol is None or s['symbol'] == symbol)]


def buy_signals(result):
    """BUY signals without the accounting-only reservation snapshot (present in dynamic/research modes)."""
    return [{k: v for k, v in s.items() if k != 'reservation'} for s in result['signals'] if s['side'] == 'BUY']


def reason_codes(result, symbol=None):
    return [r['reason'] for r in result['reasons'] if symbol is None or r['symbol'] == symbol]


def buys(result):
    return [t for t in result['trades'] if t['side'] == 'BUY']


def per_share_total(price, quantity, config=FIXED_LEGACY):
    c = execution_costs(D(price), quantity, 'BUY', config)
    return c['amount'] + c['fee']


class FixedModeIsUnchangedTests(unittest.TestCase):
    def test_default_is_fixed_and_explicit_fixed_is_identical(self):
        self.assertEqual(Config().sizing, 'FIXED')
        rows = series('AAA', '4900', '5000', tail=5)
        a = replay(rows, FIXED_LEGACY, universe=['AAA'])
        b = replay(rows, replace(FIXED_LEGACY, sizing='FIXED'), universe=['AAA'])
        self.assertEqual({k: v for k, v in a.items() if k != 'config'}, {k: v for k, v in b.items() if k != 'config'})
        self.assertEqual(signal_quantity(a), [10])                     # historical 10-share behavior
        self.assertNotIn('reserved_cash', a['equity_curve'][-1])       # no new accounting keys in FIXED
        self.assertFalse({'ZERO_AFFORDABLE_QUANTITY', 'ENTRY_QUANTITY_SIZED', 'ENTRY_QUANTITY_REDUCED'} & set(reason_codes(a)))

    def test_fixed_still_rejects_an_unaffordable_fixed_quantity_with_the_old_reason(self):
        rows = series('AAA', '19900', '20000', tail=3)                 # 10 shares = ~200k > 100k limit
        out = replay(rows, FIXED_LEGACY, universe=['AAA'])
        self.assertEqual((out['signals'], out['trades']), ([], []))
        self.assertIn('ORDER_AMOUNT_LIMIT', reason_codes(out))
        self.assertNotIn('ZERO_AFFORDABLE_QUANTITY', reason_codes(out))

    def test_dynamic_equals_fixed_when_the_cap_already_fits(self):
        rows = series('AAA', '4900', '5000', tail=5)
        fixed = replay(rows, FIXED_LEGACY, universe=['AAA'])
        dynamic = replay(rows, DYN_LEGACY, universe=['AAA'])
        self.assertEqual(dynamic['trades'], fixed['trades'])
        self.assertEqual(buy_signals(dynamic), buy_signals(fixed))
        self.assertEqual([s for s in dynamic['signals'] if s['side'] == 'SELL'], [s for s in fixed['signals'] if s['side'] == 'SELL'])
        self.assertEqual([p['net_liquidation_equity'] for p in dynamic['equity_curve']],
                         [p['net_liquidation_equity'] for p in fixed['equity_curve']])

    def test_invalid_sizing_values_are_rejected(self):
        for bad in ('dynamic', '', None, 5):
            with self.assertRaises(ValueError):
                replace(Config(), sizing=bad).validate()
        with self.assertRaises(ValueError):
            replace(Config(), sizing='AFFORDABLE_UP_TO_CAP', order_quantity=1001).validate()


class SizingRuleTests(unittest.TestCase):
    def run_dyn(self, price, *, config=DYN_LEGACY, base=None, tail=3, **kw):
        rows = series('AAA', base or str(int(int(price) * 0.99)), price, tail=tail, **kw)
        return replay(rows, config, universe=['AAA'])

    def test_zero_one_and_many_shares_with_the_cap(self):
        self.assertEqual(signal_quantity(self.run_dyn('20000')), [4])
        self.assertEqual(signal_quantity(self.run_dyn('60000')), [1])
        self.assertEqual(signal_quantity(self.run_dyn('5000')), [10])                     # capped at order_quantity
        self.assertEqual(signal_quantity(self.run_dyn('20000', config=replace(DYN_LEGACY, order_quantity=3))), [3])
        zero = self.run_dyn('120000')
        self.assertEqual((zero['signals'], zero['trades'], zero['summary']['entry_orders']), ([], [], 0))
        codes = [(r['reason'], r.get('blocking')) for r in zero['reasons'] if r['reason'] == 'ZERO_AFFORDABLE_QUANTITY']
        self.assertEqual(codes, [('ZERO_AFFORDABLE_QUANTITY', 'ORDER_AMOUNT_LIMIT')])
        self.assertNotIn('ENTRY_QUANTITY_SIZED', reason_codes(zero))

    def test_order_limit_boundary_with_fee_and_slippage(self):
        limit = FIXED_LEGACY.max_order_amount
        probed = 0
        for price in range(99880, 99960):
            expected_one = per_share_total(price, 1) <= limit
            out = self.run_dyn(str(price), base=str(price - 100), tail=2)
            self.assertEqual(signal_quantity(out) == [1], expected_one, price)
            if expected_one:
                probed += 1
                self.assertLessEqual(per_share_total(price, 1), limit)
        self.assertTrue(0 < probed < 80)                                                    # the window straddles the limit
        for price, cap in (('20000', 10), ('33333', 10), ('7777', 20)):                     # q is the MAXIMUM integer
            q = signal_quantity(self.run_dyn(price, config=replace(DYN_LEGACY, order_quantity=cap)))[0]
            self.assertLessEqual(per_share_total(price, q), limit)
            self.assertTrue(q == cap or per_share_total(price, q + 1) > limit)

    def test_cash_and_min_cash_ratio_limit_the_quantity(self):
        by_cash = replace(DYN_LEGACY, initial_cash=D('50000'))
        self.assertEqual(signal_quantity(self.run_dyn('20000', config=by_cash)), [2])        # 3 shares need 60,030
        by_ratio = replace(DYN_LEGACY, initial_cash=D('100000'), min_cash_ratio=D('30'))
        self.assertEqual(signal_quantity(self.run_dyn('20000', config=by_ratio)), [3])       # 4 would leave ~20% cash
        hopeless = replace(DYN_LEGACY, initial_cash=D('10000'))
        out = self.run_dyn('20000', config=hopeless)
        self.assertEqual(out['signals'], [])
        self.assertIn(('ZERO_AFFORDABLE_QUANTITY', 'INSUFFICIENT_CASH'),
                      [(r['reason'], r.get('blocking')) for r in out['reasons'] if r['reason'].startswith('ZERO')])

    def test_symbol_and_total_exposure_limit_the_quantity(self):
        self.assertEqual(signal_quantity(self.run_dyn('20000', config=replace(DYN_LEGACY, max_symbol_amount=D('50000')))), [2])
        out = self.run_dyn('20000', config=replace(DYN_LEGACY, max_total_investment=D('30000')))
        self.assertEqual(signal_quantity(out), [1])

    def test_quantity_independent_blocks_keep_their_own_reason(self):
        rows = series('AAA', '19800', '20000', tail=15) + series('BBB', '19800', '20000', start=25, tail=3)
        cfg = replace(DYN_LEGACY, daily_loss_limit=D('100'))
        out = replay(rows, cfg, universe=['AAA', 'BBB'])
        self.assertEqual([t['symbol'] for t in buys(out)], ['AAA'])
        self.assertIn(('BBB', 'DAILY_LOSS_LIMIT'), [(r['symbol'], r['reason']) for r in out['reasons']])
        self.assertNotIn('ZERO_AFFORDABLE_QUANTITY', reason_codes(out, 'BBB'))
        capped = replay(rows, replace(DYN_LEGACY, max_positions=1), universe=['AAA', 'BBB'])
        self.assertIn(('BBB', 'POSITION_LIMIT'), [(r['symbol'], r['reason']) for r in capped['reasons']])

    def test_existing_holding_reduces_the_next_sizing(self):
        rows = series('AAA', '19800', '20000', tail=15) + series('BBB', '19800', '20000', start=25, tail=3)
        cfg = replace(DYN_LEGACY, max_total_investment=D('120000'))
        out = replay(rows, cfg, universe=['AAA', 'BBB'])
        self.assertEqual((signal_quantity(out, 'AAA'), signal_quantity(out, 'BBB')), ([4], [1]))      # 80k held, 40,000 left
        tight = replay(rows, replace(DYN_LEGACY, max_total_investment=D('100000')), universe=['AAA', 'BBB'])
        self.assertEqual(signal_quantity(tight, 'BBB'), [])
        self.assertIn(('ZERO_AFFORDABLE_QUANTITY', 'TOTAL_EXPOSURE_LIMIT'),
                      [(r['reason'], r.get('blocking')) for r in tight['reasons'] if r['symbol'] == 'BBB' and r['reason'].startswith('ZERO')])

    def test_zero_quantity_does_not_consume_the_daily_entry_count(self):
        rows = series('AAA', '119000', '120000', tail=3) + series('BBB', '19800', '20000', tail=3)
        cfg = replace(DYN_LEGACY, daily_order_limit=1)
        out = replay(rows, cfg, universe=['AAA', 'BBB'])
        self.assertEqual([t['symbol'] for t in buys(out)], ['BBB'])
        self.assertNotIn('DAILY_ENTRY_ORDER_LIMIT', reason_codes(out))


class ReservationAndFillTests(unittest.TestCase):
    def two_symbols(self, config):
        rows = series('AAA', '19800', '20000', tail=4) + series('BBB', '19800', '20000', tail=4)
        return replay(rows, config, universe=['AAA', 'BBB'])

    def test_simultaneous_signals_share_the_total_limit_through_reservations(self):
        cfg = replace(DYN_LEGACY, max_total_investment=D('150000'))
        out = self.two_symbols(cfg)
        self.assertEqual((signal_quantity(out, 'AAA'), signal_quantity(out, 'BBB')), ([4], [3]))
        self.assertEqual([t['quantity'] for t in buys(out)], [4, 3])
        self.assertLessEqual(sum(t['amount'] for t in buys(out)), cfg.max_total_investment)
        reserved = [p['reserved_cash'] for p in out['equity_curve']]
        self.assertTrue(any(v > 0 for v in reserved))
        self.assertEqual(reserved[-1], D(0))                                  # all reservations released after fills
        self.assertEqual({p['reserved_position_slots'] for p in out['equity_curve'][-3:]}, {0})
        fixed_like = self.two_symbols(replace(FIXED_LEGACY, order_quantity=4, max_total_investment=D('150000')))
        self.assertEqual([s['quantity'] for s in buy_signals(fixed_like)], [4, 4])      # FIXED does not reserve: 2nd is cut at fill

    def test_reserved_slots_block_a_fourth_simultaneous_entry(self):
        rows = []
        for name in ('AAA', 'BBB', 'CCC', 'DDD'):
            rows += series(name, '19800', '20000', tail=3)
        out = replay(rows, DYN_LEGACY, universe=['AAA', 'BBB', 'CCC', 'DDD'])
        self.assertEqual([t['symbol'] for t in buys(out)], ['AAA', 'BBB', 'CCC'])
        self.assertIn(('DDD', 'POSITION_LIMIT'), [(r['symbol'], r['reason']) for r in out['reasons']])

    def test_price_rise_before_the_fill_reduces_the_quantity_never_increases_it(self):
        up = series('AAA', '19800', '20000', tail=3, tail_prices={20: '26000'})        # fill candle closes 26,000
        out = replay(up, DYN_LEGACY, universe=['AAA'])
        self.assertEqual(signal_quantity(out), [4])
        trade, = buys(out)
        self.assertEqual(trade['quantity'], 3)
        reduced = [r for r in out['reasons'] if r['reason'] == 'ENTRY_QUANTITY_REDUCED']
        self.assertEqual([(r['from_quantity'], r['quantity'], r['signal_quantity']) for r in reduced], [(4, 3, 4)])
        self.assertEqual([r['quantity'] for r in out['reasons'] if r['reason'] == 'ENTRY_REMAINDER_CANCELLED'], [1])
        self.assertEqual(out['summary']['unfilled_entry_quantity'], 0 + 4 - 3)
        self.assertLessEqual(trade['amount'] + trade['fee'], DYN_LEGACY.max_order_amount)
        down = series('AAA', '19800', '20000', tail=3, tail_prices={20: '10000'})      # price falls: still 4, not 10
        self.assertEqual([t['quantity'] for t in buys(replay(down, DYN_LEGACY, universe=['AAA']))], [4])

    def test_price_spike_makes_even_one_share_unaffordable_so_the_order_is_cancelled_and_released(self):
        rows = series('AAA', '19800', '20000', tail=3, tail_prices={20: '130000'})
        out = replay(rows, DYN_LEGACY, universe=['AAA'])
        self.assertEqual(signal_quantity(out), [4])
        self.assertEqual(buys(out), [])
        self.assertIn(('AAA', 'ZERO_AFFORDABLE_QUANTITY'), [(r['symbol'], r['reason']) for r in out['reasons']
                                                           if r.get('phase') == 'FILL'])
        self.assertEqual(out['summary']['status'], 'FLAT')
        self.assertEqual(out['summary']['cash'], DYN_LEGACY.initial_cash)                # nothing spent
        self.assertTrue(all(p['reserved_cash'] == 0 for p in out['equity_curve'][-5:]))

    def test_next_open_mode_reduces_at_the_open_quote_and_conserves_cash(self):
        rows = series('AAA', '19800', '20000', tail=3)
        quotes = [OpenQuote('AAA', at(9, 21), at(9, 21), D('26000'), D('100'))]
        out = replay(rows, DYN_OPEN, universe=['AAA'], open_quotes=quotes)
        self.assertEqual(signal_quantity(out), [4])
        trade, = buys(out)
        self.assertEqual((trade['quantity'], trade['price_source']), (3, 'OPEN_QUOTE'))
        self.assertEqual(out['summary']['filled_orders'], len(out['trades']))
        spent = -trade['cash_delta']
        mid = next(p for p in out['equity_curve'] if p['at'] > trade['execution_at'] and p['reserved_cash'] == 0)
        self.assertEqual(mid['cash'], DYN_OPEN.initial_cash - spent)
        self.assertEqual(out['summary']['open_positions'], {} if out['summary']['status'] == 'FLAT' else {'AAA': 3})


class ReservationReleaseTests(unittest.TestCase):
    def test_zero_capacity_wait_keeps_the_reservation_until_expiry_then_releases_it(self):
        rows = series('AAA', '19800', '20000', tail=3)
        quotes = [OpenQuote('AAA', at(9, 21), at(9, 21), D('20000'), D('0.5'))]        # int(0.5) == 0 executable shares
        out = replay(rows, DYN_OPEN, universe=['AAA'], open_quotes=quotes)
        self.assertEqual((signal_quantity(out), buys(out)), ([4], []))
        self.assertIn('NO_EXECUTABLE_CAPACITY', reason_codes(out))
        waiting = [p for p in out['equity_curve'] if at(9, 21) <= p['at'] < at(9, 22)]
        self.assertTrue(waiting and all(p['reserved_cash'] > 0 for p in waiting))        # still reserved while it waits
        after = [p for p in out['equity_curve'] if p['at'] >= at(9, 22)]
        self.assertTrue(after and all(p['reserved_cash'] == 0 and p['reserved_position_slots'] == 0 for p in after))
        self.assertIn('ENTRY_SIGNAL_TOO_OLD', reason_codes(out))
        self.assertEqual(out['summary']['cash'], DYN_OPEN.initial_cash)

    def test_volume_limited_partial_fill_releases_the_whole_reservation(self):
        rows = series('AAA', '19800', '20000', tail=3)
        quotes = [OpenQuote('AAA', at(9, 21), at(9, 21), D('20000'), D('2'))]          # only 2 of 4 shares executable
        out = replay(rows, DYN_OPEN, universe=['AAA'], open_quotes=quotes)
        trade, = buys(out)
        self.assertEqual((signal_quantity(out), trade['quantity']), ([4], 2))
        self.assertEqual([r['quantity'] for r in out['reasons'] if r['reason'] == 'ENTRY_REMAINDER_CANCELLED'], [2])
        later = [p for p in out['equity_curve'] if p['at'] > trade['execution_at']]
        self.assertTrue(all(p['reserved_cash'] == 0 for p in later))
        self.assertEqual(out['summary']['cash'] + (trade['amount'] + trade['fee']) - sum(
            t['cash_delta'] for t in out['trades'] if t['side'] == 'SELL'), DYN_OPEN.initial_cash)


class ResearchModeTests(unittest.TestCase):
    """research_estimated_open keeps its overrun invalidation; only a reduction that FITS is normal sizing."""

    def rows(self, open_price, *, symbol='AAA'):
        rows = series(symbol, '19800', '20000', tail=6)
        bar = next(r for r in rows if r.event_at == at(9, 21))
        spike = replace(bar, open_price=D(open_price), high_price=max(bar.high_price, D(open_price)),
                        close_price=max(bar.close_price, D(open_price)),
                        turnover=max(bar.close_price, D(open_price)) * bar.volume)
        return [spike if r is bar else r for r in rows]

    def test_a_gap_that_fits_after_reduction_is_normal_sizing_not_an_invalid_run(self):
        rows = self.rows('26000')
        dynamic = replay(rows, DYN_RESEARCH, universe=['AAA'])
        trade, = buys(dynamic)
        self.assertEqual((signal_quantity(dynamic), trade['quantity']), ([4], 3))
        self.assertIn('QUANTITY_REDUCED_AT_OPEN_PRICE', trade['uncertainty_flags'])
        self.assertEqual(dynamic['research_assumptions']['run_status'], 'ESTIMATED_ONLY')
        self.assertEqual(dynamic['research_assumptions']['resource_breaches'], [])
        self.assertEqual(dynamic['research_assumptions']['non_funded_liability'], D(0))
        self.assertEqual([r['phase'] for r in dynamic['reasons'] if r['reason'] == 'ENTRY_QUANTITY_REDUCED'], ['SETTLEMENT'])
        # control: the FIXED 4-share order at the same gap is flagged INVALID, exactly as before
        fixed = replay(rows, replace(FIXED_RESEARCH, order_quantity=4), universe=['AAA'])
        self.assertEqual(fixed['research_assumptions']['run_status'], 'INVALID_RESOURCE_OVERRUN')

    def test_when_even_one_share_does_not_fit_the_existing_invalidation_is_kept(self):
        out = replay(self.rows('130000'), DYN_RESEARCH, universe=['AAA'])
        self.assertEqual(out['research_assumptions']['run_status'], 'INVALID_RESOURCE_OVERRUN')
        self.assertTrue(out['research_assumptions']['resource_breaches'])
        self.assertNotIn('QUANTITY_REDUCED_AT_OPEN_PRICE', [f for t in buys(out) for f in t['uncertainty_flags']])

    def test_a_later_fill_of_another_order_cannot_reshape_an_earlier_quantity_and_is_flagged_instead(self):
        """Delayed multi-symbol settlement: AAA's open (09:21 @24,000) is only observed at 09:40, after BBB
        (signal 09:25) already used the cash. AAA must keep its signal quantity and the run is INVALID."""
        late_cfg = replace(DYN_RESEARCH, initial_cash=D('170000'), min_cash_ratio=D('0'),
                           research_max_observation_lag_minutes=D('30'))
        aaa = series('AAA', '19800', '20000', tail=25)
        aaa = [replace(r, open_price=D('24000'), high_price=D('24000'), close_price=D('24000'),
                       turnover=D('24000') * r.volume, available_at=at(9, 40)) if r.event_at == at(9, 21) else r
               for r in aaa]
        bbb = series('BBB', '19800', '20000', start=20, tail=20)
        out = replay(aaa + bbb, late_cfg, universe=['AAA', 'BBB'])
        self.assertEqual((signal_quantity(out, 'AAA'), signal_quantity(out, 'BBB')), ([4], [4]))
        by_symbol = {t['symbol']: t['quantity'] for t in buys(out)}
        self.assertEqual(by_symbol.get('AAA'), 4)                                    # not shrunk by BBB's use of cash
        self.assertNotIn('QUANTITY_REDUCED_AT_OPEN_PRICE', [f for t in buys(out) for f in t['uncertainty_flags']])
        self.assertEqual(out['research_assumptions']['run_status'], 'INVALID_RESOURCE_OVERRUN')
        self.assertIn('RESERVATION_RESOURCE_OVERRUN',
                      [b for br in out['research_assumptions']['resource_breaches'] for b in br['reasons']])

    def test_dynamic_research_audits_the_actual_concurrent_exposure_without_changing_quantities(self):
        cfg = replace(DYN_RESEARCH, max_total_investment=D('150000'))
        def gapped(symbol):
            rows = series(symbol, '19800', '20000', tail=6)
            return [replace(r, open_price=D('22000'), high_price=D('22000'), close_price=D('22000'),
                            turnover=D('22000') * r.volume) if r.event_at == at(9, 21) else r for r in rows]
        out = replay(gapped('AAA') + gapped('BBB'), cfg, universe=['AAA', 'BBB'])
        self.assertEqual((signal_quantity(out, 'AAA'), signal_quantity(out, 'BBB')), ([4], [3]))
        self.assertEqual({t['symbol']: t['quantity'] for t in buys(out)}, {'AAA': 4, 'BBB': 3})
        breaches = [b for br in out['research_assumptions']['resource_breaches'] for b in br['reasons']]
        self.assertIn('ACTUAL_TOTAL_EXPOSURE_OVERRUN', breaches)
        self.assertEqual(out['research_assumptions']['run_status'], 'INVALID_RESOURCE_OVERRUN')
        # without the gap the same pair fits and stays valid
        calm = replay(series('AAA', '19800', '20000', tail=6) + series('BBB', '19800', '20000', tail=6), cfg,
                      universe=['AAA', 'BBB'])
        self.assertEqual(calm['research_assumptions']['run_status'], 'ESTIMATED_ONLY')
        # FIXED research keeps its original (snapshot-only) audit scope: no new breach kind
        fixed = replay(gapped('AAA') + gapped('BBB'), replace(FIXED_RESEARCH, order_quantity=3,
                       max_total_investment=D('150000')), universe=['AAA', 'BBB'])
        self.assertNotIn('ACTUAL_TOTAL_EXPOSURE_OVERRUN',
                         [b for br in fixed['research_assumptions']['resource_breaches'] for b in br['reasons']])

    def test_no_gap_keeps_the_signal_quantity_and_reservation_accounting(self):
        out = replay(self.rows('20000'), DYN_RESEARCH, universe=['AAA'])
        trade, = buys(out)
        self.assertEqual((signal_quantity(out), trade['quantity']), ([4], 4))
        self.assertEqual(out['research_assumptions']['run_status'], 'ESTIMATED_ONLY')
        self.assertEqual(out['equity_curve'][-1]['reserved_cash'], D(0))
        self.assertTrue(any(p['reserved_cash'] > 0 for p in out['equity_curve']))
        self.assertEqual(out['research_assumptions']['non_funded_liability'], D(0))

    def test_fixed_research_is_unchanged(self):
        rows = self.rows('20000')
        cfg = replace(FIXED_RESEARCH, order_quantity=4)
        out = replay(rows, cfg, universe=['AAA'])
        self.assertEqual(signal_quantity(out), [4])
        self.assertNotIn('ENTRY_QUANTITY_REDUCED', reason_codes(out))
        self.assertNotIn('ENTRY_QUANTITY_SIZED', reason_codes(out))


class CausalityAndScoutTests(unittest.TestCase):
    def test_future_data_after_the_signal_cannot_change_the_signal_quantity(self):
        base = series('AAA', '19800', '20000', tail=3)
        extra = series('AAA', '19800', '20000', tail=8, tail_prices={24: '1000', 25: '300000'})
        a = replay(base, DYN_LEGACY, universe=['AAA'])
        b = replay(extra, DYN_LEGACY, universe=['AAA'])
        self.assertEqual(buy_signals(a), buy_signals(b))
        self.assertEqual([r for r in a['reasons'] if r['at'] <= at(9, 21)], [r for r in b['reasons'] if r['at'] <= at(9, 21)])

    def test_scout_veto_applies_before_sizing(self):
        rows = series('AAA', '19800', '20000', tail=3)
        out = replay(rows, DYN_LEGACY, universe=['AAA'], entry_allowed=lambda s, t: False)
        self.assertEqual(out['signals'], [])
        self.assertIn('SCOUT_NOT_WATCHED', reason_codes(out))
        self.assertNotIn('ENTRY_QUANTITY_SIZED', reason_codes(out))

    def test_scout_quantity_gate_is_one_share_for_dynamic_and_the_fixed_quantity_otherwise(self):
        self.assertEqual(link.scout_quantity_for(FIXED_LEGACY), 10)
        self.assertEqual(link.scout_quantity_for(DYN_LEGACY), 1)
        self.assertEqual(link.scout_config_for(DYN_LEGACY).quantity, 1)
        self.assertEqual(link.scout_config_for(FIXED_LEGACY).quantity, 10)
        scout_cfg = link.scout_config_for(DYN_LEGACY)
        pf = link.Portfolio(at(9, 0), cash="1", **link.portfolio_limits_for(DYN_LEGACY))
        self.assertEqual(link.limit_mismatches(DYN_LEGACY, scout_cfg, pf, DYN_LEGACY), ())
        self.assertIn('QUANTITY', link.limit_mismatches(DYN_LEGACY, link.scout_config_for(FIXED_LEGACY), pf, DYN_LEGACY))
        self.assertIn('QUANTITY', link.limit_mismatches(FIXED_LEGACY, scout_cfg, pf, FIXED_LEGACY))

    def test_scout_candidate_matches_dynamic_day01_at_least_one_share(self):
        disagreements = []
        for price in (3000, 20000, 60000, 99000, 99860, 99870, 99880, 99890, 99900, 99910, 99936, 99950, 100000, 120000, 400000):
            scout = scout_result(at(9, 19, 50), ['AAA'], price=str(price), day01=DYN_LEGACY)
            scout_ok = bool(scout.candidates)
            rows = series('AAA', str(int(price * 0.99)), str(price), tail=3)
            day01_ok = bool(signal_quantity(replay(rows, DYN_LEGACY, universe=['AAA'])))
            if scout_ok and not day01_ok:
                self.fail(f"Scout accepted {price} but DAY-01 sized 0 shares")        # Scout may only be stricter
            if scout_ok != day01_ok:
                disagreements.append(price)
        for price in disagreements:                                                    # only inside the 1-KRW fee-rounding sliver
            self.assertTrue(99860 <= price <= 99960, price)

    def test_scout_candidate_count_reflects_one_share_while_day01_sizes_the_rest(self):
        prices = {'A1': '3000', 'A2': '20000', 'A3': '60000', 'A4': '99000', 'A5': '120000', 'A6': '400000'}
        candidates, sized = [], {}
        for symbol, price in prices.items():
            if scout_result(at(9, 19, 50), [symbol], price=price, day01=DYN_LEGACY).candidates:
                candidates.append(symbol)
            rows = series(symbol, str(int(int(price) * 0.99)), price, tail=2)
            q = signal_quantity(replay(rows, DYN_LEGACY, universe=[symbol]))
            sized[symbol] = q[0] if q else 0
        self.assertEqual(candidates, ['A1', 'A2', 'A3', 'A4'])                          # Scout: "at least 1 share"
        self.assertEqual(sized, {'A1': 10, 'A2': 4, 'A3': 1, 'A4': 1, 'A5': 0, 'A6': 0})  # DAY-01: the actual quantity
        self.assertEqual({s for s, q in sized.items() if q}, set(candidates))


class InvariantTests(unittest.TestCase):
    def test_random_scenarios_never_exceed_limits_or_the_signal_quantity(self):
        rng = random.Random(20261113)
        for case in range(30):
            prices = {s: rng.choice([3000, 8000, 15000, 25000, 40000, 70000, 95000, 130000]) for s in ('AAA', 'BBB', 'CCC')}
            cfg = replace(DYN_LEGACY, order_quantity=rng.choice([1, 3, 10, 25]),
                          max_total_investment=D(rng.choice([60000, 150000, 300000, 500000])),
                          max_symbol_amount=D(rng.choice([60000, 120000, 200000])),
                          initial_cash=D(rng.choice([80000, 400000, 1000000])), max_positions=rng.choice([1, 2, 3]))
            if cfg.max_order_amount > cfg.max_symbol_amount:
                cfg = replace(cfg, max_symbol_amount=cfg.max_order_amount)
            rows = []
            for symbol, price in prices.items():
                jump = {20: str(int(price * rng.choice([0.8, 1.0, 1.0, 1.15, 1.4, 2.0])))}
                rows += series(symbol, str(int(price * 0.99)), str(price), tail=4, tail_prices=jump)
            out = replay(rows, cfg, universe=list(prices))
            cap_by_symbol = {s['symbol']: s['quantity'] for s in out['signals'] if s['side'] == 'BUY'}
            open_symbols = set()
            for t in out['trades']:
                if t['side'] == 'BUY':
                    self.assertLessEqual(t['quantity'], cap_by_symbol[t['symbol']], case)
                    self.assertLessEqual(cap_by_symbol[t['symbol']], cfg.order_quantity, case)
                    self.assertTrue(RiskPolicy.order_amount_allowed(cfg, t['amount'], t['fee']), case)
                    self.assertTrue(RiskPolicy.symbol_amount_allowed(cfg, D(0), t['amount']), case)
                    open_symbols.add(t['symbol'])
                    self.assertLessEqual(len(open_symbols), cfg.max_positions, case)
                else:
                    open_symbols.discard(t['symbol'])
            self.assertTrue(all(p['cash'] >= 0 for p in out['equity_curve']), case)
            self.assertTrue(all(p['reserved_cash'] >= 0 for p in out['equity_curve']), case)
            self.assertEqual(out['equity_curve'][-1]['reserved_cash'], D(0), case)
            reasons_seen = {r['reason'] for r in out['reasons']}
            self.assertNotIn('RESEARCH_RESOURCE_OVERRUN', reasons_seen)


if __name__ == '__main__':
    unittest.main()

"""Synthetic-only DAY-01 tests; imports do not load settings, DB or brokers."""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from auto_trader.ml.day01 import (
    Candle, Config, KST, aggregate_five_minutes, evaluate_entry,
    execution_costs, replay, select_universe,
)


def minute(index, price='100', *, day=12, symbol='AAA', volume='100', **changes):
    """09:00 + index minute; exact turnover at the flat synthetic price."""
    at = datetime(2026, 11, day, 9, tzinfo=KST) + timedelta(minutes=index)
    p, v = D(price), D(volume)
    return replace(Candle(symbol, at, at+timedelta(minutes=1), p, p, p, p, v, p*v), **changes)


def scenario(*, day=12, symbol='AAA'):
    # Complete opening range, then a higher-volume rising-VWAP breakout at 09:20.
    return [minute(i, day=day, symbol=symbol) for i in range(15)] + [
        minute(i, '102', day=day, symbol=symbol, volume='200') for i in range(15, 20)]


class Day01Tests(unittest.TestCase):
    def cfg(self, **kw):
        return replace(Config(), **kw)

    def run_rows(self, rows, **kw):
        return replay(rows, self.cfg(**kw), universe=['AAA'])

    def test_normal_entry_and_timer_liquidation(self):
        rows = scenario()+[minute(20, '102'), minute(350, '103')]
        result = self.run_rows(rows)
        buy, sell = result['trades']
        self.assertEqual(buy['side'], 'BUY')
        self.assertEqual(buy['signal_at'].strftime('%H:%M'), '09:20')
        self.assertEqual(buy['execution_at'].strftime('%H:%M'), '09:21')
        self.assertEqual(sell['reason'], 'SESSION_LIQUIDATION')
        self.assertEqual(result['summary']['status'], 'FLAT')
        self.assertEqual(result['summary']['net_profit'], result['summary']['realized_profit'])

    def test_signal_without_later_bar_never_fills(self):
        r = self.run_rows(scenario())
        self.assertEqual(len(r['signals']), 1)
        self.assertEqual(r['trades'], [])
        self.assertTrue(any(x['reason']=='ENTRY_SIGNAL_TOO_OLD' for x in r['reasons']))

    def test_five_minute_alignment_availability(self):
        rows = [minute(i) for i in range(5)]
        end = rows[-1].available_at
        self.assertEqual(aggregate_five_minutes(rows, end-timedelta(microseconds=1)), [])
        bar, = aggregate_five_minutes(rows, end)
        self.assertEqual(bar.event_at, rows[0].event_at)
        self.assertEqual(bar.available_at, end)
        self.assertEqual(bar.volume, D(500))
        self.assertEqual(aggregate_five_minutes(rows[1:], end), [])

    def test_late_constituent_sets_five_minute_availability(self):
        rows = [minute(i) for i in range(5)]
        late = rows[-1].available_at+timedelta(minutes=10)
        rows[0] = replace(rows[0], available_at=late)
        self.assertEqual(aggregate_five_minutes(rows, late-timedelta(seconds=1)), [])
        self.assertEqual(aggregate_five_minutes(rows, late)[0].available_at, late)

    def test_missing_0900_invalidates_opening_range(self):
        rows = scenario()[1:]
        decision = evaluate_entry(rows, rows[-1].available_at, Config())
        self.assertEqual(decision.reason, 'INCOMPLETE_OPENING_RANGE')
        self.assertEqual(self.run_rows(rows+[minute(20,'102')])['trades'], [])

    def test_opening_range_excludes_0915(self):
        rows = scenario()
        self.assertTrue(evaluate_entry(rows, rows[-1].available_at, Config()).enter)
        self.assertEqual(evaluate_entry(rows, rows[-1].available_at, Config()).range_high, D(100))

    def test_late_opening_range_cannot_retroactively_signal(self):
        rows = scenario()
        rows[5] = replace(rows[5], available_at=minute(40).event_at)
        result = self.run_rows(rows+[minute(20,'102'),minute(40,'103')])
        self.assertEqual(result['signals'], [])

    def test_late_execution_bar_never_backfills(self):
        late = replace(minute(20,'102'), available_at=minute(40).event_at)
        r = self.run_rows(scenario()+[late])
        self.assertEqual(r['trades'], [])

    def test_same_information_batch_bar_is_not_later_evidence(self):
        # Historical 09:19 minute is the signal evidence itself, never a fill.
        r = self.run_rows(scenario())
        self.assertEqual(r['summary']['filled_orders'], 0)

    def test_exact_vwap_never_silently_approximates(self):
        rows = [replace(r, turnover=None) for r in scenario()]
        self.assertEqual(evaluate_entry(rows, rows[-1].available_at, Config()).reason, 'MISSING_EXACT_TURNOVER')
        self.assertTrue(evaluate_entry(rows, rows[-1].available_at,
            self.cfg(vwap_mode='typical_approximation')).enter)

    def test_volume_and_vwap_conditions(self):
        rows = scenario()
        self.assertEqual(evaluate_entry(rows, rows[-1].available_at,
            self.cfg(volume_multiplier=D(3))).reason, 'INSUFFICIENT_VOLUME_INCREASE')
        flat = [minute(i) for i in range(20)]
        self.assertEqual(evaluate_entry(flat, flat[-1].available_at, Config()).reason,'NO_RANGE_BREAKOUT')

    def test_exact_vwap_trend_and_full_session_completeness(self):
        rows = scenario()
        rows[15:] = [replace(r,low_price=D(50),turnover=D(50)*r.volume) for r in rows[15:]]
        self.assertEqual(evaluate_entry(rows,minute(20).event_at,Config()).reason,'NO_RISING_VWAP')
        missing = scenario()[:15] + scenario()[16:] + [minute(i,'104',volume='300') for i in range(20,30)]
        self.assertEqual(evaluate_entry(missing,minute(30).event_at,Config()).reason,'INCOMPLETE_SESSION_VWAP')

    def test_future_observations_cannot_change_earlier_results(self):
        prefix = scenario()+[minute(20,'102')]
        a = self.run_rows(prefix+[minute(350,'103')])
        b = self.run_rows(prefix+[minute(350,'1000')])
        cutoff = minute(350).available_at
        self.assertEqual([t for t in a['trades'] if t['observed_at']<cutoff],
                         [t for t in b['trades'] if t['observed_at']<cutoff])
        self.assertEqual([p for p in a['equity_curve'] if p['at']<cutoff],
                         [p for p in b['equity_curve'] if p['at']<cutoff])

    def test_entry_time_boundaries(self):
        rows = [minute(i, '100' if i<145 else '102', volume='100' if i<145 else '200') for i in range(150)]
        self.assertEqual(evaluate_entry(rows, minute(150).event_at, Config()).reason, 'OUTSIDE_ENTRY_WINDOW')
        self.assertEqual(evaluate_entry(scenario()[:15], minute(15).event_at, Config()).reason, 'OUTSIDE_ENTRY_WINDOW')
        self.assertTrue(evaluate_entry(scenario(), minute(20).event_at, Config()).enter)

    def test_late_fill_outside_window_expires(self):
        r = self.run_rows(scenario()+[minute(150,'102')])
        self.assertEqual(r['trades'], [])
        self.assertTrue(any(x['reason']=='ENTRY_SIGNAL_TOO_OLD' for x in r['reasons']))

    def test_late_crash_marks_at_arrival_and_queues_later_stop(self):
        crash = minute(22, '50', open_price=D(102), high_price=D(102),
                       available_at=minute(60).event_at)
        prefix = scenario()+[minute(20,'102'),minute(21,'102')]
        before = self.run_rows(prefix)
        result = self.run_rows(prefix+[crash,minute(61,'102'),minute(62,'102')])
        arrival = minute(60).event_at
        # Existing past points are never rewritten when crash information arrives.
        self.assertEqual([p for p in before['equity_curve'] if p['at']<arrival],
                         [p for p in result['equity_curve'] if p['at']<arrival])
        at_arrival = next(p for p in result['equity_curve'] if p['at']==arrival)
        self.assertEqual(at_arrival['net_liquidation_equity'],D('999479.240'))
        stop = next(s for s in result['signals'] if s['reason']=='STOP')
        self.assertEqual(stop['at'],arrival)
        sell = result['trades'][-1]
        self.assertEqual(sell['signal_at'],arrival)
        self.assertEqual(sell['event_at'],minute(61).event_at)
        self.assertEqual(sell['execution_at'],minute(62).event_at)

    def test_late_old_observation_cannot_overwrite_newer_mark(self):
        crash = minute(22,'50',open_price=D(102),high_price=D(102),available_at=minute(60).event_at)
        rows = scenario()+[minute(20,'102'),minute(30,'103'),crash]
        r = self.run_rows(rows)
        self.assertEqual(r['valuation_marks']['AAA']['price'],D(103))
        self.assertEqual(r['valuation_marks']['AAA']['event_at'],minute(30).event_at)
        self.assertEqual(r['valuation_marks']['AAA']['observed_at'],minute(31).event_at)
        self.assertTrue(any(s['reason']=='STOP' and s['at']==crash.available_at for s in r['signals']))
        self.assertTrue(any(x['reason']=='OLDER_OBSERVATION_MARK_PRESERVED' for x in r['reasons']))

    def test_late_stop_information_has_no_same_arrival_batch_fill(self):
        crash = minute(22,'50',open_price=D(102),high_price=D(102),available_at=minute(60).event_at)
        r = self.run_rows(scenario()+[minute(20,'102'),crash,minute(59,'102')])
        self.assertEqual(len(r['trades']),1)
        self.assertEqual(r['signals'][1]['at'],minute(60).event_at)
        self.assertEqual(r['valuation_marks']['AAA']['price'],D(102))

    def test_late_stop_replaces_pending_target_at_actual_arrival(self):
        crash = minute(22,'50',open_price=D(102),high_price=D(102),available_at=minute(60).event_at)
        r = self.run_rows(scenario()+[minute(20,'102'),minute(21,'106'),crash,minute(61,'102')])
        self.assertEqual([s['reason'] for s in r['signals']],['ENTRY','TARGET','STOP'])
        self.assertEqual(r['trades'][-1]['reason'],'STOP')
        self.assertEqual(r['trades'][-1]['signal_at'],crash.available_at)

    def test_pre_entry_delayed_wick_never_causes_held_position_stop(self):
        # Fill at 09:22 via a fresh 09:21 bar; delayed 09:20 precedes the holding period.
        delayed = minute(20,'102',low_price=D(50),available_at=minute(25).event_at)
        r = self.run_rows(scenario()+[minute(21,'102'),delayed],max_signal_to_fill_minutes=D(3))
        self.assertFalse(any(s['reason'] in {'STOP','AMBIGUOUS_STOP_FIRST'} for s in r['signals']))

    def test_late_risk_information_blocks_new_account_exposure(self):
        # An old adverse observation must not overwrite a newer 103 mark, but still halts buys.
        crash = minute(22,'50',open_price=D(102),high_price=D(102),available_at=minute(60).event_at)
        b = [minute(i,symbol='BBB') for i in range(55)]
        b += [minute(i,'102',symbol='BBB',volume='200') for i in range(55,60)]
        b += [minute(60,'102',symbol='BBB')]
        r = replay(scenario()+[minute(20,'102'),minute(30,'103'),crash]+b,
                   Config(),universe=['AAA','BBB'])
        self.assertEqual([t['symbol'] for t in r['trades']],['AAA'])
        self.assertTrue(any(x['symbol']=='BBB' and x['reason']=='LATE_RISK_EXIT_PENDING' for x in r['reasons']))

    def test_late_newest_mark_reaches_daily_loss_check(self):
        crash = minute(22,'50',open_price=D(102),high_price=D(102),available_at=minute(60).event_at)
        b = [minute(i,symbol='BBB') for i in range(55)]
        b += [minute(i,'102',symbol='BBB',volume='200') for i in range(55,60)]
        r = replay(scenario()+[minute(20,'102'),crash]+b,
                   self.cfg(daily_loss_limit=D(300)),universe=['AAA','BBB'])
        self.assertTrue(any(x['symbol']=='BBB' and x['reason']=='DAILY_LOSS_LIMIT' for x in r['reasons']))
        self.assertTrue(next(p for p in r['equity_curve'] if p['at']==crash.available_at)['loss_limit_exceeded'])

    def test_signal_age_expiry_timer_and_exclusive_boundary(self):
        r = self.run_rows(scenario()+[minute(140,'90'),minute(141,'90')])
        self.assertEqual(r['trades'],[])
        expiry = next(x for x in r['reasons'] if x['reason']=='ENTRY_SIGNAL_TOO_OLD')
        self.assertEqual(expiry['at'],minute(22).event_at)
        # At age exactly two minutes the signal has expired before execution.
        self.assertEqual(self.run_rows(scenario()+[minute(21,'102')])['trades'],[])
        allowed = self.run_rows(scenario()+[minute(21,'102')],max_signal_to_fill_minutes=D(3))
        self.assertEqual(allowed['trades'][0]['execution_at'],minute(22).event_at)

    def test_signal_age_validation(self):
        for age in (D('NaN'),D('Infinity'),D(0),D(-1),D(131),2):
            with self.subTest(age=age),self.assertRaises(ValueError):
                self.run_rows(scenario(),max_signal_to_fill_minutes=age)

    def test_no_same_entry_bar_stop(self):
        fill = minute(20,'102', low_price=D(80), high_price=D(110))
        r = self.run_rows(scenario()+[fill])
        self.assertEqual([s['side'] for s in r['signals']],['BUY','SELL'])
        self.assertEqual(r['signals'][-1]['reason'],'SESSION_LIQUIDATION')

    def test_ambiguous_stop_first_and_conservative_rebound(self):
        trigger = minute(21,'102',low_price=D(98),high_price=D(106))
        r = self.run_rows(scenario()+[minute(20,'102'),trigger,minute(22,'105')])
        sell = r['trades'][-1]
        self.assertEqual(sell['reason'],'AMBIGUOUS_STOP_FIRST')
        self.assertLessEqual(sell['reference_price'], r['trades'][0]['price']*(1-Config().stop_rate))
        self.assertGreater(sell['event_at'],trigger.event_at)

    def test_gap_down_stop_uses_adverse_open_or_close(self):
        r = self.run_rows(scenario()+[minute(20,'102'),minute(21,'98'),minute(22,'95')])
        self.assertEqual(r['trades'][-1]['reference_price'],D(95))
        self.assertEqual(r['trades'][-1]['reason'],'STOP')

    def test_target_not_optimistic(self):
        r = self.run_rows(scenario()+[minute(20,'102'),minute(21,'106'),minute(22,'110')])
        self.assertEqual(r['trades'][-1]['reason'],'TARGET')
        self.assertLessEqual(r['trades'][-1]['reference_price'],r['trades'][0]['price']*(1+Config().target_rate))

    def test_zero_volume_or_missing_liquidation_is_unresolved(self):
        for extra in ([],[minute(350,'103',volume='0')],[replace(minute(350,'103'),available_at=minute(380).event_at)]):
            with self.subTest(extra=extra):
                r = self.run_rows(scenario()+[minute(20,'102')]+extra)
                self.assertEqual(r['summary']['unresolved_quantities'],{'AAA':10})
                self.assertEqual(r['failures'][0]['at'].strftime('%H:%M'),'15:10')
                self.assertEqual(r['summary']['realized_profit'],D(0))

    def test_partial_liquidation_and_capacity(self):
        r = self.run_rows(scenario()+[minute(20,'102'),minute(350,'103',volume='3')])
        self.assertEqual(r['trades'][-1]['quantity'],3)
        self.assertEqual(r['summary']['unresolved_quantities'],{'AAA':7})
        self.assertEqual(r['summary']['total_orders'],2)

    def test_1510_is_exclusive_and_pre_1450_bar_cannot_liquidate(self):
        r = self.run_rows(scenario()+[minute(20,'102'),minute(349,'102'),minute(369,'103')])
        self.assertEqual(len(r['trades']),1)
        self.assertEqual(r['summary']['unresolved_quantities'],{'AAA':10})

    def test_subunit_capacity_never_phantom_fills(self):
        r = self.run_rows(scenario()+[minute(20,'102',volume='0.5')])
        self.assertEqual(r['trades'],[])
        self.assertTrue(any(x['reason']=='NO_EXECUTABLE_CAPACITY' for x in r['reasons']))

    def test_partial_buy_remainder_cancelled(self):
        r = self.run_rows(scenario()+[minute(20,'102',volume='3')])
        self.assertEqual(r['summary']['unresolved_quantities'],{'AAA':3})
        self.assertTrue(any(x['reason']=='ENTRY_REMAINDER_CANCELLED' for x in r['reasons']))

    def test_multiday_flat_resets_history_and_orders(self):
        rows = scenario()+[minute(20,'102'),minute(350,'103')]
        rows += scenario(day=13)+[minute(20,'102',day=13),minute(350,'103',day=13)]
        r = self.run_rows(rows,daily_order_limit=1)
        self.assertEqual(len(r['trades']),4)
        self.assertEqual([d['entry_orders'] for d in r['daily_results']],[1,1])

    def test_unresolved_overnight_freezes_marks_and_blocks_new_entry(self):
        rows = scenario()+[minute(20,'102')]
        r = self.run_rows(rows+scenario(day=13)+[minute(20,'200',day=13),minute(350,'200',day=13)])
        first = self.run_rows(rows)
        self.assertEqual(len(r['trades']),1)
        self.assertEqual(r['summary']['net_liquidation_equity'],first['summary']['net_liquidation_equity'])
        self.assertEqual(r['positions']['AAA']['day'].day,12)
        self.assertIn('UNRESOLVED_EXPOSURE',[x['reason'] for x in r['reasons'] if x['at'].day==13])
        self.assertTrue(r['summary']['overnight_gap_exposure_unmodeled'])
        self.assertEqual(r['summary']['frozen_valuation_symbols'],['AAA'])
        self.assertTrue(r['valuation_marks']['AAA']['frozen'])
        self.assertEqual(r['daily_results'][1]['net_change'],D(0))

    def test_unresolved_blocks_other_symbols_next_day(self):
        rows = scenario()+[minute(20,'102')]+scenario(day=13,symbol='BBB')+[minute(20,'102',day=13,symbol='BBB')]
        r = replay(rows,Config(),universe=['AAA','BBB'])
        self.assertEqual(len(r['trades']),1)
        self.assertTrue(any(x['reason']=='UNRESOLVED_EXPOSURE' for x in r['reasons']))

    def test_universe_and_shuffle_deterministic(self):
        rows = scenario()+[minute(20,'102'),minute(350,'103')]
        a = self.run_rows(rows)
        self.assertEqual(a,self.run_rows(list(reversed(rows))))
        self.assertEqual(a,self.run_rows(rows+[minute(25,'10000',symbol='ZZZ')]))
        self.assertEqual(select_universe(['BBB','AAA','AAA']),('AAA','BBB'))

    def test_utc_input_has_same_kst_result(self):
        rows = scenario()+[minute(20,'102')]
        utc = [replace(r,event_at=r.event_at.astimezone(timezone.utc),available_at=r.available_at.astimezone(timezone.utc)) for r in rows]
        self.assertEqual(self.run_rows(rows),self.run_rows(utc))

    def test_risk_limits_at_signal(self):
        cases = [({'max_order_amount':D(1)},'ORDER_AMOUNT_LIMIT'),
                 ({'max_symbol_amount':D(1)},'SYMBOL_EXPOSURE_LIMIT'),
                 ({'max_total_investment':D(1)},'TOTAL_EXPOSURE_LIMIT'),
                 ({'initial_cash':D(1)},'INSUFFICIENT_CASH'),
                 ({'min_cash_ratio':D(100)},'MIN_CASH_RATIO')]
        for cfg, code in cases:
            with self.subTest(code=code):
                r = self.run_rows(scenario()+[minute(20,'102')],**cfg)
                self.assertEqual(r['trades'],[])
                self.assertTrue(any(x['reason']==code for x in r['reasons']))

    def test_fill_rechecks_gap_risk(self):
        r = self.run_rows(scenario()+[minute(20,'1000')],max_order_amount=D(2000))
        self.assertEqual(r['trades'],[])
        self.assertTrue(any(x['reason']=='ORDER_AMOUNT_LIMIT' and x['phase']=='FILL' for x in r['reasons']))

    def test_position_and_daily_order_limits_with_risk_exits_allowed(self):
        rows = scenario()+scenario(symbol='BBB')+[minute(20,'102'),minute(20,'102',symbol='BBB'),minute(350,'103')]
        r = replay(rows,self.cfg(max_positions=1),universe=['AAA','BBB'])
        self.assertEqual([t['symbol'] for t in r['trades']],['AAA','AAA'])
        self.assertTrue(any(x['reason']=='POSITION_LIMIT' for x in r['reasons']))
        r = replay(rows,self.cfg(daily_order_limit=1),universe=['AAA','BBB'])
        self.assertEqual(r['summary']['entry_orders'],1)
        self.assertEqual(r['summary']['total_orders'],2)
        self.assertEqual(r['summary']['status'],'FLAT')

    def test_daily_loss_blocks_second_fill_and_exits_still_work(self):
        # Both entry signals submitted at 09:20; AAA fills first and costs cause limit hit.
        rows = scenario()+scenario(symbol='BBB')+[minute(20,'102'),minute(20,'102',symbol='BBB'),minute(350,'103')]
        r = replay(rows,self.cfg(daily_loss_limit=D('0.01')),universe=['AAA','BBB'])
        self.assertEqual([t['symbol'] for t in r['trades']],['AAA','AAA'])
        self.assertTrue(any(x['reason']=='DAILY_LOSS_LIMIT' and x['phase']=='FILL' for x in r['reasons']))

    def test_paper_fee_tax_round_down_parity(self):
        cfg = Config()
        buy = execution_costs(D(10000),10,'BUY',cfg)
        sell = execution_costs(D(10000),10,'SELL',cfg)
        self.assertEqual(buy['price'],D('10005'))
        self.assertEqual(buy['fee'],D(15))
        self.assertEqual(buy['tax'],D(0))
        self.assertEqual(sell['fee'],D(14))
        self.assertEqual(sell['tax'],D(199))
        self.assertEqual(sell['cash_delta'],D(99737))

    def test_config_validation(self):
        for changes in ({'stop_rate':D('NaN')},{'target_rate':D('Infinity')},
            {'order_quantity':True},{'max_positions':0},{'daily_order_limit':-1},
            {'volume_participation':D('1.1')},{'slippage_rate':D(1)},
            {'fee_rate':D(-1)},{'vwap_mode':'automatic'},{'min_reward_risk':D(3)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_rows(scenario(),**changes)

    def test_data_validation(self):
        row = minute(0)
        for bad in (replace(row,event_at=row.event_at.replace(tzinfo=None)),
            replace(row,available_at=row.event_at),replace(row,volume=D(-1)),
            replace(row,close_price=D('NaN')),replace(row,high_price=D(90)),
            replace(row,turnover=D(1)),replace(row,symbol=' aaa '),
            replace(row,event_at=row.event_at+timedelta(seconds=1))):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.run_rows([bad])
        with self.assertRaises(ValueError):
            self.run_rows([row,row])
        with self.assertRaises(ValueError):
            self.run_rows([])


if __name__ == '__main__':
    unittest.main()

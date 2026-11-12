"""Synthetic-only preparation and explicitly uncertain historical-open outcomes."""
import unittest
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

from auto_trader.ml.day01 import Candle, Config, KST, replay
from auto_trader.ml.day01_research import ResearchInput, adapt_rows, coverage, run_input
from test_day01 import minute, scenario


def cfg(**changes):
    return replace(Config(),execution='research_estimated_open',**changes)


def raw(row, **changes):
    return dict(source='SYNTHETIC',interval='1m',**asdict(row),**changes)


class InputTests(unittest.TestCase):
    def test_preserves_times_source_optional_collection_and_turnover(self):
        row = minute(0)
        mapping = raw(row)
        mapping.update(event_at=row.event_at.astimezone(timezone.utc).isoformat(),
                       available_at=row.available_at.isoformat(),collected_at=row.available_at+timedelta(seconds=1),
                       collection_run_id=17,close_price='100',volume='100')
        data = adapt_rows([mapping],universe=['AAA'])
        self.assertEqual(data.candles[0].event_at.utcoffset(),timedelta(0))
        self.assertEqual(data.candles[0].available_at,row.available_at)
        self.assertEqual(data.candles[0].turnover,D(10000))
        self.assertEqual(data.provenance[0]['source'],'SYNTHETIC')
        self.assertEqual(data.provenance[0]['collection_run_id'],17)
        self.assertEqual(data.provenance[0]['collected_at'],mapping['collected_at'])
        self.assertIsInstance(mapping['event_at'],str)

    def test_missing_availability_or_interval_never_fabricated(self):
        for field in ('available_at','source','interval'):
            mapping = raw(minute(0))
            mapping.pop(field)
            with self.subTest(field=field),self.assertRaises(ValueError):
                adapt_rows([mapping],universe=['AAA'])
        mapping = raw(minute(0))
        mapping['interval']='5m'
        with self.assertRaises(ValueError):
            adapt_rows([mapping],universe=['AAA'])

    def test_1530_and_outside_session_reported_not_dropped(self):
        data = adapt_rows([raw(minute(0)),raw(minute(390)),raw(minute(-1))],universe=['AAA'])
        self.assertEqual(len(data.candles),1)
        self.assertEqual([r['reason'] for r in data.excluded_rows],['OUTSIDE_REPLAY_SESSION','CLOSING_1530'])
        report = coverage(data,universe=['AAA'],expected_session_dates=[date(2026,11,12)])
        self.assertEqual((report['input_count'],report['closing_1530_count'],report['outside_session_count']),(3,1,1))
        self.assertEqual(report['missing_minute_count'],389)

    def test_full_391_input_is_390_replay_plus_closing(self):
        data = adapt_rows([raw(minute(i)) for i in range(391)],universe=['AAA'])
        report = coverage(data,universe=['AAA'],expected_session_dates=['2026-11-12'])
        self.assertEqual(report['status'],'COMPLETE')
        self.assertEqual((report['replay_count'],report['closing_1530_count']),(390,1))
        self.assertEqual(report['missing_minute_count'],0)
        self.assertEqual(report['actual_stored_date_range'],'UNKNOWN')

    def test_missing_days_symbols_minutes_and_unexpected_dates(self):
        data = adapt_rows([raw(minute(0)),raw(minute(2)),raw(minute(0,day=14))],universe=['AAA','BBB'])
        report = coverage(data,universe=['AAA','BBB'],expected_session_dates=[date(2026,11,12),date(2026,11,13)])
        self.assertEqual(report['missing_days'],[date(2026,11,13)])
        self.assertEqual(report['unexpected_days'],[date(2026,11,14)])
        self.assertEqual(report['missing_symbols'],['BBB'])
        self.assertEqual(report['missing_minute_count'],1558)
        self.assertEqual(report['cells'][0]['status'],'MISSING')
        self.assertEqual(report['cells'][1]['status'],'NO_ROWS')

    def test_no_rows_unknown_storage_is_not_invented_coverage(self):
        data = adapt_rows([],universe=['AAA'])
        report = coverage(data,universe=['AAA'],expected_session_dates=['2026-11-12'])
        self.assertEqual(report['status'],'NO_ROWS')
        self.assertIsNone(report['event_min'])
        self.assertEqual(report['actual_stored_date_range'],'UNKNOWN')
        self.assertEqual(report['missing_minute_count'],390)
        result = run_input(data,Config(),universe=['AAA'],expected_session_dates=['2026-11-12'])
        self.assertIsNone(result['result'])
        self.assertEqual(result['run_status'],'NO_REPLAY_ROWS')
        self.assertEqual(result['mode'],'next_open')
        self.assertTrue(result['uncertain'])
        self.assertEqual(result['costs']['fee_rate'],Config().fee_rate)

    def test_expected_dates_required_and_never_guessed(self):
        data = adapt_rows([],universe=['AAA'])
        for dates in ([],[datetime(2026,11,12,9,tzinfo=KST)]):
            with self.assertRaises(ValueError):
                coverage(data,universe=['AAA'],expected_session_dates=dates)

    def test_availability_quality_backfill_and_missing_turnover(self):
        delayed = replace(minute(1),available_at=minute(0,day=13).event_at,turnover=None)
        data = adapt_rows([raw(minute(0)),raw(delayed)],universe=['AAA'])
        report = coverage(data,universe=['AAA'],expected_session_dates=['2026-11-12'])
        self.assertEqual(report['availability']['on_time_count'],1)
        self.assertEqual(report['availability']['late_count'],1)
        self.assertEqual(report['availability']['historical_backfill_count'],1)
        self.assertEqual(report['missing_turnover_count'],1)
        self.assertFalse(report['exact_vwap_turnover_ready'])
        self.assertEqual(data.provenance[1]['available_at'],delayed.available_at)
        self.assertNotIn('collected_at',data.provenance[1])

    def test_invalid_rows_duplicates_and_collected_times(self):
        for malformed in (1,{}):
            with self.assertRaises(ValueError):
                adapt_rows([malformed],universe=['AAA'])
        for changes in ({'volume':1.0},{'open_price':'NaN'},{'available_at':None},
                        {'collected_at':minute(0).event_at},{'collection_run_id':True}):
            mapping = raw(minute(0))
            mapping.update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                adapt_rows([mapping],universe=['AAA'])
        with self.assertRaises(ValueError):
            adapt_rows([raw(minute(0)),raw(minute(0))],universe=['AAA'])

    def test_universe_filter_and_order_are_explicit(self):
        data = adapt_rows([{'symbol':'ZZZ'},raw(minute(2)),raw(minute(0))],universe=['AAA'])
        other = adapt_rows([raw(minute(0)),raw(minute(2))],universe=['AAA'])
        self.assertEqual(data,other)
        self.assertEqual(len(data.candles),2)

    def test_run_input_keeps_provenance_exclusions_and_explicit_mode(self):
        data = adapt_rows([raw(c) for c in scenario()+[minute(21,'102'),minute(390)]],universe=['AAA'])
        strict = run_input(data,Config(),universe=['AAA'],expected_session_dates=['2026-11-12'])
        research = run_input(data,cfg(),universe=['AAA'],expected_session_dates=['2026-11-12'])
        self.assertEqual(strict['result']['trades'],[])
        self.assertEqual(len(research['result']['trades']),1)
        self.assertEqual(research['coverage']['closing_1530_count'],1)
        self.assertEqual(research['provenance'],data.provenance)
        self.assertEqual(research['excluded_rows'][0]['reason'],'CLOSING_1530')

    def test_run_input_propagates_invalid_even_after_liability_repaid(self):
        for extra in ([],[minute(351,'150')]):
            with self.subTest(extra=extra):
                data = adapt_rows([raw(c) for c in scenario()+[minute(21,'150')]+extra],universe=['AAA'])
                out = run_input(data,cfg(initial_cash=D(1100),min_cash_ratio=D(0)),
                                universe=['AAA'],expected_session_dates=['2026-11-12'])
                self.assertEqual(out['run_status'],'INVALID_RESOURCE_OVERRUN')
                self.assertEqual(out['run_status'],out['result']['research_assumptions']['run_status'])
                if extra:
                    self.assertEqual(out['result']['research_assumptions']['non_funded_liability'],D(0))

    def test_run_input_normal_status_is_preserved(self):
        data = adapt_rows([raw(c) for c in scenario()+[minute(21,'102')]],universe=['AAA'])
        for config in (Config(),cfg()):
            out = run_input(data,config,universe=['AAA'],expected_session_dates=['2026-11-12'])
            self.assertEqual(out['run_status'],'RAN')


class EstimatedOutcomeTests(unittest.TestCase):
    def run_rows(self, rows, **changes):
        return replay(rows,cfg(**changes),universe=['AAA'])

    def test_observation_settlement_not_physical_open(self):
        result = self.run_rows(scenario()+[minute(21,'102',volume='1'),minute(351,'103')])
        buy,sell = result['trades']
        self.assertEqual(buy['estimated_execution_at'],minute(21).event_at)
        self.assertEqual(buy['execution_at'],minute(22).event_at)
        self.assertEqual(buy['observation_at'],minute(22).event_at)
        self.assertFalse(buy['physical_fill_claim'])
        self.assertEqual(buy['price_source'],'ESTIMATED_CANDLE_OPEN')
        self.assertEqual(buy['quantity'],10) # configured assumption, NOT target volume=1
        self.assertIn('positive_volume_veto',buy['hindsight_fields'])
        self.assertEqual(sell['execution_at'],minute(352).event_at)
        self.assertEqual(result['summary']['net_profit'],D('6.9750'))
        self.assertEqual(result['research_assumptions']['run_status'],'ESTIMATED_ONLY')

    def test_future_close_does_not_select_away_losing_buy(self):
        good = self.run_rows(scenario()+[minute(21,'102')])
        bad = self.run_rows(scenario()+[minute(21,'1',open_price=D(102),high_price=D(102))])
        self.assertEqual(len(bad['trades']),1)
        self.assertEqual(bad['trades'][0]['price'],good['trades'][0]['price'])
        self.assertEqual(bad['trades'][0]['quantity'],good['trades'][0]['quantity'])
        self.assertLess(bad['summary']['net_profit'],D(-1000))
        cutoff = minute(22).event_at
        self.assertEqual([p for p in good['equity_curve'] if p['at']<cutoff],
                         [p for p in bad['equity_curve'] if p['at']<cutoff])

    def test_lagged_entry_signal_and_outcome_preserve_actual_times(self):
        rows = scenario()
        rows[-1] = replace(rows[-1],available_at=minute(20).event_at+timedelta(seconds=2))
        outcome = replace(minute(21,'102'),available_at=minute(22).event_at+timedelta(seconds=5))
        r = self.run_rows(rows+[outcome])
        buy, = r['trades']
        self.assertEqual(buy['signal_at'],rows[-1].available_at)
        self.assertEqual(buy['execution_at'],outcome.available_at)
        self.assertEqual(buy['estimated_execution_at'],outcome.event_at)

    def test_catch_up_already_received_crash_without_retro_exit(self):
        first = replace(minute(21,'102'),available_at=minute(24).event_at)
        crash = minute(22,'50',open_price=D(102),high_price=D(102))
        r = self.run_rows(scenario()+[first,crash,minute(25,'49')])
        buy,sell = r['trades']
        self.assertEqual(buy['execution_at'],minute(24).event_at)
        self.assertEqual(sell['signal_at'],minute(24).event_at)
        self.assertEqual(sell['estimated_execution_at'],minute(25).event_at)
        self.assertEqual(sell['execution_at'],minute(26).event_at)
        self.assertLess(r['summary']['net_profit'],D(-500))

    def test_own_entry_bar_dual_hit_queues_stop_first(self):
        own = minute(21,'102',low_price=D(90),high_price=D(110))
        r = self.run_rows(scenario()+[own,minute(23,'89')])
        self.assertEqual(r['trades'][1]['reason'],'AMBIGUOUS_STOP_FIRST')
        self.assertEqual(r['trades'][1]['signal_at'],minute(22).event_at)
        self.assertEqual(r['trades'][1]['estimated_execution_at'],minute(23).event_at)

    def test_reserved_position_slot_and_cash(self):
        rows = scenario()+scenario(symbol='BBB')+[minute(21,'102'),minute(21,'102',symbol='BBB')]
        for changes,code in (({'max_positions':1},'POSITION_LIMIT'),
                             ({'initial_cash':D(1500),'min_cash_ratio':D(0)},'INSUFFICIENT_CASH')):
            result = replay(rows,cfg(**changes),universe=['AAA','BBB'])
            self.assertEqual([t['symbol'] for t in result['trades']],['AAA'])
            self.assertTrue(any(r['symbol']=='BBB' and r['reason']==code for r in result['reasons']))
            reserved = next(p for p in result['equity_curve'] if p['at']==minute(20).event_at)
            self.assertEqual(reserved['reserved_position_slots'],1)

    def test_later_loss_cannot_veto_pending_reserved_buy(self):
        aaa = scenario()+[minute(21,'50',open_price=D(102),high_price=D(102))]
        bbb = scenario(symbol='BBB')+[replace(minute(21,'102',symbol='BBB'),available_at=minute(23).event_at)]
        r = replay(aaa+bbb,cfg(daily_loss_limit=D(300)),universe=['AAA','BBB'])
        self.assertEqual([t['symbol'] for t in r['trades']],['AAA','BBB'])
        self.assertEqual(r['trades'][1]['estimated_execution_at'],minute(21).event_at)
        self.assertEqual(r['trades'][1]['execution_at'],minute(23).event_at)

    def test_risk_known_before_estimated_open_can_cancel(self):
        # AAA entered earlier; BBB signals09:25, then a known crash09:25:30 precedes09:26 open.
        aaa = scenario()+[minute(21,'102'),replace(minute(23,'50',open_price=D(102),high_price=D(102)),
                                                available_at=minute(25).event_at+timedelta(seconds=30))]
        bbb = [minute(i,symbol='BBB') for i in range(20)]
        bbb += [minute(i,'102',volume='200',symbol='BBB') for i in range(20,25)]
        bbb += [minute(26,'102',symbol='BBB')]
        r = replay(aaa+bbb,cfg(daily_loss_limit=D(300)),universe=['AAA','BBB'])
        self.assertEqual([t['symbol'] for t in r['trades']],['AAA'])
        self.assertTrue(any(x['symbol']=='BBB' and x.get('phase')=='RESEARCH_PRE_OPEN' and
                            x['reason']=='DAILY_LOSS_LIMIT' for x in r['reasons']))

    def test_gap_resource_overrun_is_invalid_not_favorable_no_fill(self):
        outcome = minute(21,'50',open_price=D(1000),high_price=D(1000),volume='1')
        r = self.run_rows(scenario()+[outcome],initial_cash=D(1100),min_cash_ratio=D(0))
        self.assertEqual(len(r['trades']),1)
        self.assertEqual(r['trades'][0]['price'],D('1000.5000'))
        self.assertEqual(r['summary']['cash'],D(0))
        self.assertEqual(r['research_assumptions']['non_funded_liability'],D(8906))
        self.assertEqual(r['research_assumptions']['run_status'],'INVALID_RESOURCE_OVERRUN')
        self.assertEqual(r['summary']['net_profit'],D('-9506.2500'))
        self.assertTrue(all(p['cash']>=0 for p in r['equity_curve']))

    def test_counterfactual_debt_repaid_without_hiding_loss(self):
        outcome = minute(21,'50',open_price=D(1000),high_price=D(1000),volume='1')
        r = self.run_rows(scenario()+[outcome,minute(23,'49')],initial_cash=D(1100),min_cash_ratio=D(0))
        self.assertEqual(r['summary']['status'],'FLAT')
        self.assertEqual(r['summary']['net_profit'],r['summary']['realized_profit'])
        self.assertGreater(r['research_assumptions']['non_funded_liability'],D(0))
        self.assertEqual(r['research_assumptions']['run_status'],'INVALID_RESOURCE_OVERRUN')

    def test_zero_missing_and_beyond_lag_do_not_fill(self):
        for rows,reason in ((scenario()+[minute(21,volume='0')],'RESEARCH_ZERO_VOLUME_OUTCOME_VETO'),
                            (scenario(),'RESEARCH_MISSING_OUTCOME'),
                            (scenario()+[replace(minute(21),available_at=minute(25).event_at)],'RESEARCH_OBSERVATION_LAG_EXCEEDED')):
            with self.subTest(reason=reason):
                r = self.run_rows(rows)
                self.assertEqual(r['trades'],[])
                self.assertIn(reason,[v['reason'] for v in r['reasons']])

    def test_expiry_grace_and_reservation_release(self):
        r = self.run_rows(scenario())
        expiry = next(x for x in r['reasons'] if x['reason']=='ENTRY_SIGNAL_TOO_OLD')
        self.assertEqual(expiry['at'],minute(25).event_at)
        before = next(p for p in r['equity_curve'] if p['at']==minute(24).event_at)
        after = next(p for p in r['equity_curve'] if p['at']==minute(25).event_at)
        self.assertEqual(before['reserved_position_slots'],1)
        self.assertEqual(after['reserved_position_slots'],0)

    def test_arrival_1510_does_not_settle_1509_estimate(self):
        r = self.run_rows(scenario()+[minute(21,'102'),minute(369,'103')])
        self.assertEqual(len(r['trades']),1)
        self.assertEqual(r['summary']['unresolved_quantities'],{'AAA':10})
        self.assertIn('UNRESOLVED_OBSERVED_AFTER_1510',[f['reason'] for f in r['failures']])
        tomorrow = replace(minute(369,'103'),available_at=minute(0,day=13).event_at)
        late = self.run_rows(scenario()+[minute(21,'102'),tomorrow])
        self.assertEqual(len(late['trades']),1)
        self.assertIn('UNRESOLVED_OBSERVED_AFTER_1510',[f['reason'] for f in late['failures']])

    def test_unrelated_late_bars_do_not_claim_pending_outcome_provenance(self):
        # Old event, already-timed-out candidate, post-cutoff event, and another session.
        extra = [replace(minute(i,'102'),available_at=minute(380).event_at) for i in (40,351,371)]
        extra += [minute(40,'102',day=13)]
        r = self.run_rows(scenario()+[minute(21,'102')]+extra)
        self.assertEqual(r['summary']['unresolved_quantities'],{'AAA':10})
        self.assertEqual([f['reason'] for f in r['failures']],['UNLIQUIDATED_AT_1510'])

    def test_related_pending_candidate_failure_carries_order_evidence(self):
        r = self.run_rows(scenario()+[minute(21,'102'),minute(369,'103')])
        failure = next(f for f in r['failures'] if f['reason']=='UNRESOLVED_OBSERVED_AFTER_1510')
        self.assertEqual(failure['candidate_event_at'],minute(369).event_at)
        self.assertEqual(failure['order_signal_at'],minute(350).event_at)
        self.assertLessEqual(failure['pending_candidate_from'],failure['candidate_event_at'])

    def test_configured_fixed_quantity_not_participation_liquidity(self):
        r = self.run_rows(scenario()+[minute(21,volume='1')],volume_participation=D('0.01'))
        self.assertEqual(r['trades'][0]['quantity'],10)
        self.assertEqual(r['execution_evidence']['capacity_source'],'configured_quantity_assumption')

    def test_exact_vwap_is_not_silently_approximated(self):
        rows = [replace(c,turnover=None) for c in scenario()+[minute(21,'102')]]
        self.assertEqual(self.run_rows(rows)['trades'],[])
        self.assertEqual(len(self.run_rows(rows,vwap_mode='typical_approximation')['trades']),1)

    def test_lag_bounds_and_determinism(self):
        for lag in (D(-1),D('NaN'),D('Infinity'),D(31),2):
            with self.assertRaises(ValueError):
                self.run_rows(scenario(),research_max_observation_lag_minutes=lag)
        rows = scenario()+[minute(21,'102'),minute(351,'103')]
        self.assertEqual(self.run_rows(rows),self.run_rows(list(reversed(rows))))


if __name__=='__main__':
    unittest.main()

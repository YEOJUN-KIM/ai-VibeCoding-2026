import copy
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest

from auto_trader.ml.data_readiness import (
    Config, KST, TABLES, audit_series, build_samples, generate, macro_feature_rows, net_return)


class ReadinessTests(unittest.TestCase):
    def fixture(self, count=105, start=None):
        start = start or datetime(2026, 10, 15, 9, tzinfo=KST)
        self.start = start
        self.as_of = datetime(2026, 10, 30, 16, tzinfo=KST)
        def rows(key, name, source):
            return [dict(id=i+1, source=source, **{key: name}, interval='1m',
                         event_at=start+timedelta(minutes=i),
                         available_at=start+timedelta(minutes=i+1, seconds=2),
                         collected_at=start+timedelta(minutes=i+1, seconds=2),
                         open_price=D(100+i), high_price=D(102+i), low_price=D(99+i),
                         close_price=D(101+i), volume=D(0)) for i in range(count)]
        self.stocks = rows('symbol', 'A', 'TOSS')
        self.indices = rows('indicator', 'KOSPI', 'TOSS_MARKET_INDICATOR')+rows('indicator', 'KOSDAQ', 'TOSS_MARKET_INDICATOR')
        self.macros = []
        return self

    def build(self):
        day = self.start.date().isoformat()
        audits = audit_series(self.stocks, 'symbol', ['A'], [day], [], self.as_of)
        return build_samples(self.stocks, self.indices, self.macros, [], ['A'], [day], self.as_of, Config(), audits)

    def sample(self, minute):
        return next(s for s in self.build() if s['prediction_event_at'] == self.start+timedelta(minutes=minute))

    def test_backfill_not_feature_but_can_be_outcome(self):
        self.fixture()
        self.stocks[45]['available_at'] = self.as_of
        self.stocks[45]['collected_at'] = self.as_of
        s = self.sample(40)
        self.assertTrue(s['usable'])
        self.assertIsNotNone(s['labels']['future_return_5m'])
        self.assertNotIn('future_return_5m', s['features'])
        self.assertIn('LATE_LOOKBACK_DATA', self.sample(46)['exclusion_reasons'])
        self.assertFalse(self.sample(45)['usable'])

    def test_missing_lookback(self):
        self.fixture()
        del self.stocks[20]
        self.assertIn('MISSING_LOOKBACK', self.sample(40)['exclusion_reasons'])
        self.assertTrue(self.sample(65)['usable'])

    def test_provider_absent_opening_is_not_day_exclusion(self):
        self.fixture()
        del self.stocks[0]
        s = self.sample(39)
        self.assertFalse(s['usable'])
        self.assertTrue(self.sample(40)['usable'])
        self.assertIsNone(self.sample(40)['features']['price_vs_session_open'])

    def test_extended_hours_excluded_raw_unchanged(self):
        self.fixture()
        outside = copy.deepcopy(self.stocks[0])
        outside['event_at'] = self.start-timedelta(minutes=2)
        self.stocks.append(outside)
        before = copy.deepcopy(self.stocks)
        s = next(s for s in self.build() if s['prediction_event_at'] == outside['event_at'])
        self.assertIn('OUTSIDE_SESSION', s['exclusion_reasons'])
        self.assertEqual(before, self.stocks)

    def test_index_cannot_wait_for_future_receipt(self):
        self.fixture()
        self.indices[40]['available_at'] += timedelta(minutes=10)
        self.indices[40]['collected_at'] += timedelta(minutes=10)
        self.assertIn('MISSING_KOSPI', self.sample(40)['exclusion_reasons'])

    def test_end_of_day_partial_horizons(self):
        self.fixture(100, datetime(2026, 10, 15, 13, 51, tzinfo=KST))
        s = self.sample(60)
        self.assertTrue(s['usable'])
        self.assertIsNotNone(s['labels']['future_return_5m'])
        self.assertIsNone(s['labels']['future_return_60m'])

    def test_invalid_ohlc_not_fixed(self):
        self.fixture()
        self.stocks[40]['low_price'] = D(900)
        self.assertIn('INVALID_OHLC', self.sample(41)['exclusion_reasons'])
        self.assertEqual(self.stocks[40]['low_price'], D(900))

    def test_zero_volume_valid_and_macro_optional(self):
        self.fixture()
        s = self.sample(40)
        self.assertTrue(s['usable'])
        self.assertFalse(s['features']['has_usd_krw'])
        self.assertIsNone(s['features']['relative_volume_5m'])

    def test_macro_available_at_filter_and_latest_event(self):
        self.fixture()
        def macro(event, available, value):
            return dict(indicator='USD_KRW', source='TOSS', event_at=event, available_at=available,
                        collected_at=available, value=D(value), frequency='SNAPSHOT')
        self.macros = [macro(self.start, self.start, 1300),
                       macro(self.start+timedelta(minutes=20), self.start+timedelta(minutes=20), 1400),
                       macro(self.start, self.start+timedelta(minutes=30), 1200),
                       macro(self.start+timedelta(minutes=41), self.start+timedelta(minutes=50), 1500)]
        s = self.sample(40)
        self.assertEqual(s['features']['usd_krw'], 1400)
        self.assertLessEqual(s['feature_available_at_max'], s['prediction_at'])

    def test_next_open_strictly_after_prediction_and_missing_bar(self):
        self.fixture()
        s = self.sample(40)
        self.assertEqual(s['entry_event_at'], self.start+timedelta(minutes=42))
        self.assertLess(s['prediction_at'], s['entry_event_at'])
        del self.stocks[42]
        s = self.sample(40)
        self.assertIn('NO_EXECUTABLE_NEXT_BAR', s['flags'])
        self.assertIsNone(s['labels']['executable_net_return_5m'])
        self.assertIsNone(s['labels']['future_return_5m'])

    def test_bad_time_order_excluded(self):
        self.fixture()
        self.stocks[40]['available_at'] = self.start
        self.assertIn('POINT_IN_TIME_VIOLATION', self.sample(40)['exclusion_reasons'])

    def test_deterministic_artifacts_and_no_label_feature_overlap(self):
        self.fixture()
        data = dict(zip(TABLES, (self.stocks, self.indices, [], [], [])))
        before = copy.deepcopy(data)
        temp_root = Path(__file__).resolve().parents[1]/'.tmp'
        temp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as a, tempfile.TemporaryDirectory(dir=temp_root) as b:
            report, first = generate(data, {'as_of': self.as_of}, Config(), ['A'], a)
            _, second = generate(data, {'as_of': self.as_of}, Config(), ['A'], b)
            self.assertEqual(first['dataset_hash'], second['dataset_hash'])
            self.assertEqual(first['artifacts'], second['artifacts'])
            self.assertTrue(set(first['feature_list']).isdisjoint(first['label_list']))
            self.assertEqual(report['feature_point_in_time_violations'], 0)
            self.assertEqual(Path(a, 'samples.csv').read_bytes(), Path(b, 'samples.csv').read_bytes())
        self.assertEqual(before, data)

    def test_paper_cost_rounding(self):
        self.assertAlmostEqual(net_return(D(10000), D(10000), Config()), (9995-1-19)/(10005+1)-1)

    def test_macro_cycle_start_waits_for_finished_run(self):
        self.fixture()
        raw = dict(indicator='USD_KRW', source='TOSS_EXCHANGE_RATE', event_at=self.start,
                   available_at=self.start, collected_at=self.start, value=D(1300),
                   frequency='SNAPSHOT', collection_run_id=1)
        before = copy.deepcopy(raw)
        runs = [dict(id=1, source='MACRO_CONTEXT', finished_at=self.start+timedelta(minutes=50))]
        self.macros = macro_feature_rows([raw], runs)
        self.assertFalse(self.sample(40)['features']['has_usd_krw'])
        self.assertTrue(self.sample(55)['features']['has_usd_krw'])
        self.assertEqual(raw, before)
        self.assertEqual(macro_feature_rows([raw], []), [])


if __name__ == '__main__':
    unittest.main()

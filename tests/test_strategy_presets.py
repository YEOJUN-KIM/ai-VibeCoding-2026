import unittest
from decimal import Decimal as D
from unittest.mock import patch
from datetime import datetime, timezone
from uuid import uuid4

from psycopg import sql

from auto_trader import database, strategy_presets
from auto_trader.strategy_presets import build_preset, weekly_preset
from auto_trader.models import LiveStockSearchResult


def candidate(symbol='005930', price=50000, rank=1, **changes):
    values = dict(symbol=symbol, name='기업', is_common_share=True, security_type='STOCK',
                  market='KOSPI', currency='KRW', price=D(price) if price is not None else None, change_rate_percent=D(1),
                  trading_amount_rank=rank, trading_amount=D('20000000000'), market_cap=D('2000000000000'))
    values.update(changes)
    return LiveStockSearchResult(**values), {'status':'ACTIVE', 'koreanMarketDetail':{'krxTradingSuspended':False}}


def build(kind, rows, amount=300000):
    return build_preset(kind, D(amount), rows, fee_rate=D('.00015'), slippage_rate=D('.0005'))


class PresetTests(unittest.TestCase):
    def test_max_count_and_eligibility_explain_shortfalls(self):
        rows = [candidate(f'{i:06d}', rank=i) for i in range(1,13)]
        kwargs = dict(fee_rate=D('.00015'), slippage_rate=D('.0005'))
        for count in (3,5,10,20):
            draft=build_preset('popular',D(300000),rows,max_count=count,**kwargs)
            self.assertEqual(len(draft['targets']),min(count,12))
            self.assertEqual(draft['eligible_count'],12)
            self.assertEqual(draft['max_count'],count)
            self.assertEqual(draft['planned_budget'],300000*min(count,12))
        draft=build_preset('affordable',D(300000),rows[:2]+[candidate('000099',price=150000)],max_count=5,**kwargs)
        self.assertEqual(draft['market_eligible_count'],3)
        self.assertEqual(draft['eligible_count'],2)
        self.assertEqual(len(draft['targets']),2)
        for count in (0,21,True,3.5):
            with self.assertRaises(ValueError):
                build_preset('popular',D(300000),rows,max_count=count,**kwargs)

    def test_filter_rejects_ineligible_and_missing_data_and_keeps_rank_order(self):
        rows = [candidate('000001', rank=3), candidate('000002', rank=2), candidate('000003', rank=1)]
        rows += [candidate('000004', price=999), candidate('000005', change_rate_percent=D(6)),
                 candidate('000006', security_type='ETF'), candidate('000007', market_cap=D(100)),
                 candidate('000008', trading_amount_rank=51), candidate('000009', price=None),
                 candidate('000010', is_common_share=False), candidate('000011', change_rate_percent=None)]
        suspended = candidate('000012'); suspended[1]['koreanMarketDetail']['krxTradingSuspended'] = True
        unknown = candidate('000013'); unknown[1]['koreanMarketDetail'] = {}
        rows += [suspended, unknown, candidate('000014', trading_amount=D(100))]
        draft = build('popular', rows)
        self.assertEqual([r['symbol'] for r in draft['targets']], ['000003', '000002', '000001'])
        self.assertEqual(draft['payload']['symbols'], ['000003', '000002', '000001'])
        self.assertFalse(draft['payload']['enabled'])
        self.assertEqual(draft['payload']['execution_mode'], 'DRY_RUN')
        for invalid in rows[3:]:
            with self.subTest(symbol=invalid[0].symbol), self.assertRaises(ValueError):
                build('popular', [invalid])

    def test_affordability_includes_cost_and_does_not_fill_missing_slots(self):
        rows = [candidate('000001', price=100000), candidate('000002', price=99900, rank=2)]
        self.assertEqual(len(build('popular', rows)['targets']), 2)
        draft = build('affordable', rows)
        self.assertEqual([r['symbol'] for r in draft['targets']], ['000002'])
        self.assertEqual(draft['targets'][0]['quantity_estimate'], 3)
        self.assertEqual(draft['planned_budget'], 300000)
        with self.assertRaises(ValueError):
            build('popular', [candidate(price=300000)])

    def test_duplicates_and_empty_data_do_not_create_placeholder_symbols(self):
        self.assertEqual(len(build('popular', [candidate(), candidate()])['targets']), 1)
        with self.assertRaises(ValueError):
            build('popular', [])
        with self.assertRaises(ValueError):
            build('invalid', [candidate()])
        with self.assertRaises(ValueError):
            build('popular', [candidate()], amount=1000001)

    def test_preview_does_not_save_or_enable_a_strategy(self):
        from auto_trader import main
        with patch.object(main, 'weekly_preset', return_value=build('popular', [candidate()])), \
             patch.object(main, 'save_strategy') as save:
            draft = main.strategy_preset_preview('popular', D(300000), None)
        save.assert_not_called()
        self.assertEqual(draft['payload']['sizing_mode'], 'AMOUNT')
        with self.assertRaises(main.HTTPException) as error:
            main.strategy_preset_preview('unknown', D(300000), None)
        self.assertEqual(error.exception.status_code, 404)


class WeeklyPresetTests(unittest.TestCase):
    def setUp(self):
        self.schema = 'test_weekly_presets_' + uuid4().hex
        original = database.connect
        with original() as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))

        def cleanup():
            with original() as conn:
                conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
        self.addCleanup(cleanup)

        def isolated():
            conn = original()
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
            return conn
        for module in (database, strategy_presets):
            patcher = patch.object(module, 'connect', isolated)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.initialize()

    def weekly(self, date, fetch, amount=300000, max_count=5):
        return weekly_preset('popular', D(amount), fetch, fee_rate=D('.00015'), slippage_rate=D('.0005'),
                             now=datetime.fromisoformat(date).replace(tzinfo=timezone.utc),max_count=max_count)

    def test_same_week_persists_and_budget_changes_do_not_refetch(self):
        from unittest.mock import Mock
        fetch=Mock(return_value=[candidate()])
        first=self.weekly('2026-10-02T01:00:00', fetch)
        again=self.weekly('2026-10-04T14:59:59', fetch, amount=200000)
        expanded=self.weekly('2026-10-04T14:59:59',fetch,max_count=10)
        self.assertEqual(expanded['max_count'],10)
        fetch.assert_called_once()
        self.assertEqual(first['generated_at'],again['generated_at'])
        self.assertEqual(again['targets'][0]['quantity_estimate'],3)
        self.assertEqual(again['week_start'],'2026-09-28')
        self.assertEqual(again['next_refresh_on'],'2026-10-05')
        self.assertIn('09/28주',again['payload']['name'])
        self.assertEqual(again['payload']['name'],'인기 · 09/28주')
        self.assertEqual(again['payload']['trading_start'],'08:00')
        self.assertEqual(again['payload']['trading_end'],'20:00')

    def test_kst_monday_refresh_and_failed_refresh_keep_old_snapshot(self):
        first=self.weekly('2026-10-04T14:59:59',lambda:[candidate('000001')])
        with self.assertRaises(ValueError):
            self.weekly('2026-10-04T15:00:00',lambda:[])
        with strategy_presets.connect() as conn:
            row=conn.execute('SELECT week_start FROM strategy_preset_market').fetchone()
        self.assertEqual(row['week_start'].isoformat(),first['week_start'])
        next_week=self.weekly('2026-10-04T15:00:00',lambda:[candidate('000002')])
        self.assertEqual(next_week['week_start'],'2026-10-05')
        self.assertEqual(next_week['targets'][0]['symbol'],'000002')
        with strategy_presets.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM strategy_preset_market').fetchone()['n'],1)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM live_strategies').fetchone()['n'],0)

    def test_status_is_read_only_and_reports_pending_and_current_weeks(self):
        now=datetime.fromisoformat('2026-10-02T01:00:00+00:00')
        empty=strategy_presets.preset_snapshot_status(now)
        self.assertFalse(empty['current'])
        self.assertIsNone(empty['generated_at'])
        self.assertEqual(empty['next_refresh_on'],'2026-09-28')
        self.weekly('2026-10-02T01:00:00',lambda:[candidate('000001')])
        current=strategy_presets.preset_snapshot_status(now)
        self.assertTrue(current['current'])
        self.assertEqual(current['next_refresh_on'],'2026-10-05')
        stale=strategy_presets.preset_snapshot_status(datetime.fromisoformat('2026-10-04T15:00:00+00:00'))
        self.assertFalse(stale['current'])
        self.assertEqual(stale['snapshot_week_start'],'2026-09-28')
        self.assertEqual(stale['next_refresh_on'],'2026-10-05')
        with strategy_presets.connect() as conn:
            self.assertEqual(conn.execute('SELECT week_start FROM strategy_preset_market').fetchone()['week_start'].isoformat(),'2026-09-28')

import unittest
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql

from auto_trader import auth, database, live_strategies
from auto_trader.models import LiveStrategyWrite


class LiveStrategyStoreTests(unittest.TestCase):
    def setUp(self):
        self.schema = "test_live_strategies_" + uuid4().hex
        self.original_connect = database.connect
        with self.original_connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)

        def isolated():
            conn = self.original_connect()
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            return conn

        for module in (auth, database, live_strategies):
            patcher = patch.object(module, "connect", isolated)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.initialize()
        auth.create_admin("strategyadmin", "test-only-password-2026")
        with isolated() as conn:
            self.user_id = conn.execute("SELECT id FROM admin_users").fetchone()["id"]

    def cleanup_schema(self):
        with self.original_connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    @staticmethod
    def payload(**changes):
        values = dict(name="이동평균 기본", symbol="005930", short_period=5, long_period=20)
        values.update(changes)
        return LiveStrategyWrite(**values)

    def test_strategy_crud_is_user_scoped(self):
        created = live_strategies.save_strategy(self.user_id, self.payload(), "삼성전자")
        self.assertFalse(created.enabled)
        self.assertEqual(created.execution_mode, "DRY_RUN")
        self.assertEqual(created.trading_start, "09:00")
        updated = live_strategies.save_strategy(
            self.user_id, self.payload(order_quantity=2), "삼성전자", strategy_id=created.id
        )
        self.assertEqual(updated.order_quantity, 2)
        self.assertEqual(len(live_strategies.list_strategies(self.user_id)), 1)
        live_strategies.delete_strategy(self.user_id, created.id)
        self.assertEqual(live_strategies.list_strategies(self.user_id), [])

    def test_strategy_rejects_invalid_periods_and_duplicate_name(self):
        with self.assertRaisesRegex(ValueError, "단기"):
            live_strategies.save_strategy(
                self.user_id, self.payload(short_period=20, long_period=20), "삼성전자"
            )
        live_strategies.save_strategy(self.user_id, self.payload(), "삼성전자")
        with self.assertRaisesRegex(ValueError, "같은 이름"):
            live_strategies.save_strategy(self.user_id, self.payload(symbol="000660"), "SK하이닉스")

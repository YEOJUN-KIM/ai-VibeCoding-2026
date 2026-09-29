import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql

from auto_trader import auth, database, live_orders
from auto_trader.models import (
    LiveDryRunConfirmRequest,
    LiveOrderPreview,
    LiveOrderPreviewCheck,
    LiveRealOrderConfirmRequest,
    OrderSide,
)


class LiveOrderStoreTests(unittest.TestCase):
    def setUp(self):
        self.schema = "test_live_orders_" + uuid4().hex
        self.original_connect = database.connect
        with self.original_connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)

        def isolated():
            conn = self.original_connect()
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            return conn

        self.connect = isolated
        for module in (auth, database, live_orders):
            patcher = patch.object(module, "connect", isolated)
            patcher.start()
            self.addCleanup(patcher.stop)
        database.initialize()
        auth.create_admin("liveorderadmin", "test-only-password-2026")
        with self.connect() as conn:
            self.user_id = conn.execute("SELECT id FROM admin_users").fetchone()["id"]

    def cleanup_schema(self):
        with self.original_connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    @staticmethod
    def payload(client_order_id="dry-run-001", quantity=Decimal("1")):
        return LiveDryRunConfirmRequest(
            client_order_id=client_order_id,
            symbol="005930",
            side=OrderSide.BUY,
            mode="SINGLE",
            order_type="LIMIT",
            quantity=quantity,
            order_price=Decimal("70000"),
            trigger_price=Decimal("68000"),
            expire_date=date(2026, 12, 31),
        )

    @staticmethod
    def preview():
        return LiveOrderPreview(
            approved=True,
            symbol="005930",
            name="삼성전자",
            side=OrderSide.BUY,
            mode="SINGLE",
            order_type="LIMIT",
            quantity=Decimal("1"),
            reference_price=Decimal("70000"),
            estimated_amount=Decimal("70000"),
            checks=[LiveOrderPreviewCheck(name="LIVE PIN", passed=True, message="인증됨")],
            message="검토 통과",
        )

    @staticmethod
    def real_payload(client_order_id="live-order-001"):
        return LiveRealOrderConfirmRequest(
            client_order_id=client_order_id, confirmation="실제 주문", symbol="005930",
            side=OrderSide.BUY, mode="STANDARD", order_type="LIMIT", quantity=Decimal("1"),
            order_price=Decimal("70000"),
        )

    @staticmethod
    def real_preview():
        return LiveOrderPreview(
            approved=True, dry_run=False, symbol="005930", name="삼성전자", side=OrderSide.BUY,
            mode="STANDARD", order_type="LIMIT", quantity=Decimal("1"),
            reference_price=Decimal("70000"), estimated_amount=Decimal("70000"),
            checks=[LiveOrderPreviewCheck(name="LIVE PIN", passed=True, message="인증됨")],
            message="실제 주문 검토 통과",
        )

    def test_save_is_idempotent_and_scoped_to_user(self):
        account = {"accountSeq": 3}
        first, created = live_orders.save_dry_run_order(
            self.user_id, account, "****1234", self.payload(), self.preview()
        )
        duplicate, duplicate_created = live_orders.save_dry_run_order(
            self.user_id, account, "****1234", self.payload(), self.preview()
        )
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first.id, duplicate.id)
        self.assertEqual(len(live_orders.list_dry_run_orders(self.user_id)), 1)
        self.assertEqual(live_orders.list_dry_run_orders(self.user_id)[0].account_label, "****1234")

    def test_reusing_client_order_id_for_different_order_is_rejected(self):
        account = {"accountSeq": 3}
        live_orders.save_dry_run_order(
            self.user_id, account, "****1234", self.payload(), self.preview()
        )
        with self.assertRaisesRegex(ValueError, "clientOrderId"):
            live_orders.save_dry_run_order(
                self.user_id, account, "****1234", self.payload(quantity=Decimal("2")), self.preview()
            )

    def test_cancel_and_delete_today_are_user_scoped(self):
        order, _ = live_orders.save_dry_run_order(
            self.user_id, {"accountSeq": 3}, "****1234", self.payload(), self.preview()
        )
        cancelled = live_orders.cancel_dry_run_order(self.user_id, order.id)
        self.assertEqual(cancelled.status, "CANCELLED")
        self.assertEqual(live_orders.cancel_dry_run_order(self.user_id, order.id).status, "CANCELLED")
        self.assertEqual(live_orders.delete_today_dry_run_orders(self.user_id), 1)
        self.assertEqual(live_orders.list_dry_run_orders(self.user_id), [])

    def test_real_order_reserves_id_and_tracks_broker_status(self):
        order, created = live_orders.prepare_real_order(
            self.user_id, {"accountSeq": 3}, "****1234",
            self.real_payload(), self.real_preview(),
        )
        duplicate, duplicate_created = live_orders.prepare_real_order(
            self.user_id, {"accountSeq": 3}, "****1234",
            self.real_payload(), self.real_preview(),
        )
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(order.id, duplicate.id)
        submitted = live_orders.update_real_order(
            self.user_id, order.id, status="PENDING", external_order_id="toss-order-1",
            broker_status="PENDING", message="접수", details={
                "status": "PENDING", "execution": {
                    "filledQuantity": "0", "averageFilledPrice": None,
                    "filledAmount": None, "commission": None, "tax": None,
                },
            },
        )
        self.assertFalse(submitted.dry_run)
        self.assertEqual(submitted.order_source, "MANUAL")
        self.assertEqual(submitted.external_order_id, "toss-order-1")
        self.assertEqual(live_orders.real_order_for_user(self.user_id, order.id).status, "PENDING")
        self.assertEqual(len(live_orders.list_real_orders(self.user_id)), 1)
        self.assertEqual(submitted.reconciliation_status, "MATCHED")
        self.assertIsNotNone(submitted.last_synced_at)
        self.assertEqual(submitted.filled_quantity, 0)
        self.assertEqual(
            len(live_orders.orders_for_reconciliation("3", active_only=True)), 1
        )

    def test_real_order_source_is_server_controlled(self):
        auto_order, created = live_orders.prepare_real_order(
            self.user_id, {"accountSeq": 3}, "****1234",
            self.real_payload("live-auto-001"), self.real_preview(), order_source="AUTO",
        )
        self.assertTrue(created)
        self.assertEqual(auto_order.order_source, "AUTO")
        live_orders.update_real_order(
            self.user_id, auto_order.id, status="FILLED", external_order_id="auto-toss-1",
            broker_status="FILLED", message="자동 주문 체결", details={
                "status": "FILLED", "execution": {
                    "filledQuantity": "1", "averageFilledPrice": "70000",
                    "filledAmount": "70000", "commission": "5", "tax": "0",
                },
            },
        )
        self.assertEqual(live_orders.auto_position_quantities(self.user_id)["005930"], 1)
        with self.assertRaisesRegex(ValueError, "주문 출처"):
            live_orders.prepare_real_order(
                self.user_id, {"accountSeq": 3}, "****1234",
                self.real_payload("live-invalid-001"), self.real_preview(), order_source="OTHER",
            )

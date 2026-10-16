"""LIVE orchestration tests use only mocked broker and persistence calls."""

import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from fastapi import HTTPException

from auto_trader import runtime
from auto_trader.models import LiveRealOrderConfirmRequest
from auto_trader.services import live_order_service as service
from auto_trader.toss import TossApiError


class LiveOrderServiceTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.client = Mock()
        self.user = NS(id=11)
        self.payload = LiveRealOrderConfirmRequest(
            symbol="005930", side="BUY", mode="STANDARD", order_type="LIMIT",
            quantity=1, order_price=100, client_order_id="regression-order", confirmation="실제 주문")
        self.preview = NS(approved=True, checks=[], symbol="005930")
        self.order = NS(id=7, external_order_id="broker-order")
        def event(name, result):
            def call(*args, **kwargs):
                self.events.append(name)
                return result
            return call
        for name, result in (("selected_account", {"accountSeq": "account"}),
                             ("buying_power", NS(account_label="account")),
                             ("create_limit_order", {"orderId": "broker-order"}),
                             ("order_detail", {"orderId": "broker-order", "status": "PARTIAL_FILLED"})):
            getattr(self.client, name).side_effect = event(name, result)
        self.mocks = {}
        for name, result in (("_preview_live_order", self.preview),
                             ("prepare_real_order", (self.order, True)),
                             ("update_real_order", "saved"), ("real_order_for_user", self.order)):
            p = patch.object(service, name, side_effect=event(name, result))
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)
        p = patch.object(runtime, "toss_client", self.client)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(service, "settings", NS(live_trading_enabled=True))
        p.start()
        self.addCleanup(p.stop)

    def submit(self):
        return service.submit_live_real_order(self.payload, None, self.user)

    def test_submission_preserves_validation_reservation_and_broker_call_order(self):
        self.assertEqual(self.submit(), "saved")
        self.assertEqual(self.events, ["_preview_live_order", "selected_account", "buying_power",
                                       "prepare_real_order", "create_limit_order", "order_detail", "update_real_order"])
        self.client.create_limit_order.assert_called_once_with(
            symbol="005930", side="BUY", quantity=self.payload.quantity,
            price=self.payload.order_price, client_order_id=self.payload.client_order_id)
        self.assertEqual(self.mocks["update_real_order"].call_args.kwargs["status"], "PARTIAL_FILLED")

    def test_disabled_trading_invalid_mode_and_fractional_quantity_precede_preview(self):
        cases = [(False, {}, 423), (True, {"mode": "SINGLE"}, 422),
                 (True, {"order_type": "MARKET"}, 422), (True, {"quantity": "1.5"}, 422)]
        original = self.payload
        for enabled, changes, status in cases:
            with self.subTest(changes=changes, enabled=enabled):
                service.settings.live_trading_enabled = enabled
                self.payload = original.model_copy(update=changes)
                # model_copy intentionally bypasses validation, like a direct
                # caller; retain Decimal for the service's quantity contract.
                if "quantity" in changes:
                    from decimal import Decimal
                    self.payload.quantity = Decimal(changes["quantity"])
                with self.assertRaises(HTTPException) as error:
                    self.submit()
                self.assertEqual(error.exception.status_code, status)
        self.mocks["_preview_live_order"].assert_not_called()
        self.client.create_limit_order.assert_not_called()

    def test_duplicate_client_order_id_returns_saved_order_without_resubmission(self):
        self.mocks["prepare_real_order"].side_effect = None
        self.mocks["prepare_real_order"].return_value = (self.order, False)
        self.assertIs(self.submit(), self.order)
        self.client.create_limit_order.assert_not_called()
        self.client.order_detail.assert_not_called()

    def test_unknown_submission_is_saved_for_review_and_never_retried(self):
        self.client.create_limit_order.side_effect = TossApiError("timeout")
        with self.assertRaises(HTTPException) as error:
            self.submit()
        self.assertEqual(error.exception.status_code, 502)
        self.client.create_limit_order.assert_called_once()
        self.client.order_detail.assert_not_called()
        saved = self.mocks["update_real_order"].call_args.kwargs
        self.assertEqual((saved["status"], saved["reconciliation_status"]), ("UNKNOWN", "NEEDS_REVIEW"))

    def test_detail_failure_keeps_accepted_order_submitted(self):
        self.client.order_detail.side_effect = TossApiError("detail unavailable")
        self.assertEqual(self.submit(), "saved")
        saved = self.mocks["update_real_order"].call_args.kwargs
        self.assertEqual((saved["status"], saved["broker_status"]), ("SUBMITTED", "SUBMITTED"))
        self.assertEqual(saved["details"], {"orderId": "broker-order"})
        self.client.create_limit_order.assert_called_once()

    def test_persistence_conflict_prevents_broker_submission(self):
        self.mocks["prepare_real_order"].side_effect = ValueError("conflicting order")
        with self.assertRaises(HTTPException) as error:
            self.submit()
        self.assertEqual(error.exception.status_code, 409)
        self.client.create_limit_order.assert_not_called()

    def test_cancel_requires_safety_unlock_and_saved_broker_reference(self):
        service.settings.live_trading_enabled = False
        with self.assertRaises(HTTPException) as error:
            service.cancel_live_real_order(7, None, self.user)
        self.assertEqual(error.exception.status_code, 423)
        self.mocks["real_order_for_user"].assert_not_called()
        service.settings.live_trading_enabled = True
        self.order.external_order_id = None
        with self.assertRaises(HTTPException) as error:
            service.cancel_live_real_order(7, None, self.user)
        self.assertEqual(error.exception.status_code, 409)
        self.client.cancel_order.assert_not_called()

    def test_cancel_detail_failure_keeps_pending_cancel(self):
        self.client.cancel_order.return_value = {"orderId": "broker-order"}
        self.client.order_detail.side_effect = TossApiError("detail unavailable")
        self.assertEqual(service.cancel_live_real_order(7, None, self.user), "saved")
        self.client.cancel_order.assert_called_once_with("broker-order")
        saved = self.mocks["update_real_order"].call_args.kwargs
        self.assertEqual((saved["status"], saved["broker_status"]), ("PENDING_CANCEL", "PENDING_CANCEL"))

import unittest
from datetime import datetime, timezone
from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import patch, Mock
from fastapi import HTTPException
from auto_trader import main
from auto_trader.models import LiveOrderPreviewRequest, LiveRealOrderConfirmRequest, OrderSide


class ManualLimitTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.domestic_stock.return_value = {"name": "test"}
        self.client.domestic_stock_detail.return_value = NS(price=D(100))
        self.client.portfolio.return_value = NS(holdings=[], market_value=D(0))
        self.client.buying_power.return_value = NS(krw_cash_buying_power=D(10000),account_label="test")
        self.risk = Mock()
        self.risk.settings.return_value = NS(max_order_amount=D(1),max_symbol_amount=D(1),max_total_investment=D(1),min_cash_ratio=D(100))
        for target,value in [("toss_client",self.client),("risk_manager",self.risk),("live_pin_status",lambda *args:(True,datetime.now(timezone.utc)))]:
            p=patch.object(main,target,value);p.start();self.addCleanup(p.stop)
        self.payload=LiveOrderPreviewRequest(symbol="005930",side="BUY",mode="STANDARD",order_type="LIMIT",quantity=1,order_price=100)

    def preview(self):
        return main._preview_live_order(self.payload,None,None,relax_financial_limits=False)

    def test_policy_limits_warn_but_cash_still_blocks(self):
        result=self.preview()
        self.assertTrue(result.approved)
        self.assertEqual(sum(c.warning for c in result.checks),4)
        self.client.buying_power.return_value=NS(krw_cash_buying_power=D(0))
        self.assertFalse(self.preview().approved)

    def test_sell_quantity_and_pin_cannot_be_overridden(self):
        self.payload.side=OrderSide.SELL
        result=self.preview()
        self.assertFalse(result.approved)
        self.assertTrue(any(c.name=="보유 수량" and not c.passed and not c.warning for c in result.checks))
        with patch.object(main,"live_pin_status",return_value=(False,None)):
            with self.assertRaises(HTTPException) as error:self.preview()
            self.assertEqual(error.exception.status_code,403)

    def test_real_confirm_requires_explicit_warning_acceptance(self):
        payload=LiveRealOrderConfirmRequest(**self.payload.model_dump(),client_order_id="manual-test-001",confirmation="실제 주문")
        with patch.object(main,"settings",NS(live_trading_enabled=True)):
            with self.assertRaises(HTTPException) as error:main.submit_live_real_order(payload,None,NS(id=1))
            self.assertEqual(error.exception.status_code,409)
            self.client.create_limit_order.assert_not_called()
        payload.accept_financial_warnings=True
        with patch.object(main,"settings",NS(live_trading_enabled=True)), patch.object(main,"prepare_real_order",return_value=("existing-order",False)) as prepare:
            self.assertEqual(main.submit_live_real_order(payload,None,NS(id=1)),"existing-order")
            self.assertTrue(prepare.call_args.args[-1].approved)
            self.client.create_limit_order.assert_not_called()
        self.client.buying_power.return_value=NS(krw_cash_buying_power=D(0))
        with patch.object(main,"settings",NS(live_trading_enabled=True)):
            with self.assertRaises(HTTPException) as error:main.submit_live_real_order(payload,None,NS(id=1))
            self.assertEqual(error.exception.status_code,409)
        payload.accept_financial_warnings=True
        with patch.object(main,"settings",NS(live_trading_enabled=False)):
            with self.assertRaises(HTTPException) as error:main.submit_live_real_order(payload,None,NS(id=1))
            self.assertEqual(error.exception.status_code,423)

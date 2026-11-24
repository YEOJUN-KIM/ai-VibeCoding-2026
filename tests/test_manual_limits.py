import auto_trader.services.live_order_service as _services_live_order_service
import auto_trader.runtime as _runtime
import unittest
from datetime import datetime, timezone
from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import patch, Mock
from fastapi import HTTPException
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
            p=patch.object(_runtime if target in ("toss_client", "risk_manager") else _services_live_order_service,target,value);p.start();self.addCleanup(p.stop)
        self.payload=LiveOrderPreviewRequest(symbol="005930",side="BUY",mode="STANDARD",order_type="LIMIT",quantity=1,order_price=100)

    def preview(self):
        return _services_live_order_service._preview_live_order(self.payload,None,None,relax_financial_limits=False)

    def test_no_investment_policy_warning_remains_and_cash_still_blocks(self):
        # PAPER-SIMPLIFY-02: the stored policy (order / symbol / total amount, minimum cash ratio) no longer produces warnings;
        # a 100-won buy against a tiny stored policy is a plain approved preview. Cash shortfall still rejects.
        result=self.preview()
        self.assertTrue(result.approved)
        self.assertEqual(sum(c.warning for c in result.checks),0)
        names={c.name for c in result.checks}
        for gone in ("주문 금액","종목별 한도","전체 투자 한도","최소 현금"):
            self.assertNotIn(gone,names)
        self.risk.settings.assert_not_called()          # the stored policy is not even read
        self.client.buying_power.return_value=NS(krw_cash_buying_power=D(0))
        short=self.preview()
        self.assertFalse(short.approved)
        self.assertTrue(any(c.name=="주문 가능 금액" and not c.passed for c in short.checks))

    def test_large_buy_over_thirty_percent_is_a_neutral_preview(self):
        self.client.buying_power.return_value=NS(krw_cash_buying_power=D(1000),account_label="test")
        self.client.portfolio.return_value=NS(holdings=[],market_value=D(0))
        self.payload.quantity=D(5); self.payload.order_price=D(100)       # 500 of 1000 = 50% of the account
        result=self.preview()
        self.assertTrue(result.approved)
        by={i.label:i for i in result.impact}
        cash=by["원화 주문 가능 금액"]; self.assertEqual((cash.before,cash.after),(D(1000),D(500)))
        qty=by["보유 수량"]; self.assertEqual((qty.before,qty.after),(D(0),D(5)))
        share=by["종목 비중(주문가 기준 추정)"]; self.assertEqual((share.before,share.after),(D(0),D(50)))
        self.assertIn("확정할 수 없습니다",by["수수료·세금"].note)
        self.assertIsNone(by["수수료·세금"].after)                        # unknown value is never estimated
        self.assertNotIn("예상 매도 손익",''.join(by))                     # buys show no sell P&L
        self.assertNotIn("경고",result.message); self.assertNotIn("한도",result.message)

    def test_sell_impact_uses_the_real_average_price_only(self):
        holding=NS(symbol="005930",quantity=D(10),market_value=D(1000),average_purchase_price=D(80))
        self.client.portfolio.return_value=NS(holdings=[holding],market_value=D(1000))
        self.payload.side=OrderSide.SELL; self.payload.quantity=D(4)
        result=self.preview()
        self.assertTrue(result.approved)
        by={i.label:i for i in result.impact}
        self.assertEqual((by["보유 수량"].before,by["보유 수량"].after),(D(10),D(6)))
        pnl=next(i for i in result.impact if i.label.startswith("예상 매도 손익"))
        self.assertEqual(pnl.after,(D(100)-D(80))*4)
        self.assertIn("수수료·세금 제외",pnl.label)

    def test_sell_quantity_and_pin_cannot_be_overridden(self):
        self.payload.side=OrderSide.SELL
        result=self.preview()
        self.assertFalse(result.approved)
        self.assertTrue(any(c.name=="보유 수량" and not c.passed and not c.warning for c in result.checks))
        with patch.object(_services_live_order_service,"live_pin_status",return_value=(False,None)):
            with self.assertRaises(HTTPException) as error:self.preview()
            self.assertEqual(error.exception.status_code,403)

    def test_real_confirm_needs_no_policy_acceptance_but_keeps_every_safety_gate(self):
        payload=LiveRealOrderConfirmRequest(**self.payload.model_dump(),client_order_id="manual-test-001",confirmation="실제 주문")
        # approved without any warning acceptance (the field is deprecated and ignored)
        with patch.object(_services_live_order_service,"settings",NS(live_trading_enabled=True)), patch.object(_services_live_order_service,"prepare_real_order",return_value=("existing-order",False)) as prepare:
            self.assertEqual(_services_live_order_service.submit_live_real_order(payload,None,NS(id=1)),"existing-order")
            self.assertTrue(prepare.call_args.args[-1].approved)
            self.client.create_limit_order.assert_not_called()      # duplicate (not created) never reaches the broker
        # real cash shortfall is still rejected
        self.client.buying_power.return_value=NS(krw_cash_buying_power=D(0))
        with patch.object(_services_live_order_service,"settings",NS(live_trading_enabled=True)):
            with self.assertRaises(HTTPException) as error:_services_live_order_service.submit_live_real_order(payload,None,NS(id=1))
            self.assertEqual(error.exception.status_code,409)
        # the real-order safety lock still blocks everything
        with patch.object(_services_live_order_service,"settings",NS(live_trading_enabled=False)):
            with self.assertRaises(HTTPException) as error:_services_live_order_service.submit_live_real_order(payload,None,NS(id=1))
            self.assertEqual(error.exception.status_code,423)
            self.client.create_limit_order.assert_not_called()

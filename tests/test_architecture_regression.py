"""Behavior contracts captured before the architecture refactor."""

import hashlib
import json
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from auto_trader import main
from auto_trader.api import auth, live, market, ml, pages, paper, research, settings
from auto_trader.risk_policy import RiskPolicy
from auto_trader.strategy import MovingAverageEngine
from auto_trader.trading.engine import TradingEngine
from auto_trader.trading.strategies.base import SignalContext, StrategySignal
from auto_trader.trading.strategies.moving_average import MovingAverageStrategy
from refactor_scenarios import SCENARIOS, replay

FIXTURES = Path(__file__).with_name("fixtures")

# Approved, intentional API delta since the pre-refactor baseline in api_before_refactor.json.
# Verified by extracting each commit and comparing routes + OpenAPI (see docs/technical/PAPER_SIMPLIFY_02.md):
#   8c3d66b: routes and OpenAPI hash identical to the fixture.
#   9a4eb0a (PAPER-SIMPLIFY-01): routes identical; only schema MvpSettings changed (legacy risk fields removed).
#   39759a8 (PAPER-SIMPLIFY-02): exactly the 11 operations below removed (no route added); schemas
#     LiveStrategy*, RiskPreset, RiskSettings*, RiskStatus, StrategySettingsUpdate removed, LiveOrderImpactItem and
#     LiveOrderPreview.impact added, MvpSettings reduced to the operating amount.
# Any other route or schema change still fails this test.
APPROVED_REMOVED_ROUTES = (
    ["/paper/strategies/{strategy_id}/select", ["POST"]], ["/risk", ["GET"]], ["/risk/settings", ["PUT"]],
    ["/settings/strategies", ["GET"]], ["/settings/strategies", ["POST"]],
    ["/settings/strategies/{strategy_id}", ["DELETE"]], ["/settings/strategies/{strategy_id}", ["PUT"]],
    ["/settings/strategy-preset-status", ["GET"]], ["/settings/strategy-presets/{kind}", ["GET"]],
    ["/strategy/settings", ["PUT"]], ["/strategy/start", ["POST"]],
)
APPROVED_OPENAPI_SHA256 = "4481041c11cf52ede789ebfc97f56bf320dd43b309e83c0240b84756ac3370a8"


class ArchitectureRegressionTests(unittest.TestCase):
    def test_all_endpoints_and_openapi_match_original_contract(self):
        before = json.loads((FIXTURES / "api_before_refactor.json").read_text(encoding="utf-8"))
        routes = [[r.path, sorted(r.methods)]
                  for module in (auth, live, market, ml, pages, paper, research, settings)
                  for r in module.router.routes]
        for removed in APPROVED_REMOVED_ROUTES:
            self.assertIn(removed, before["routes"], removed)          # the approved delta is a subset of the baseline
        expected = [r for r in before["routes"] if r not in APPROVED_REMOVED_ROUTES]
        self.assertEqual(sorted(routes), expected)
        digest = hashlib.sha256(json.dumps(main.app.openapi(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(digest, APPROVED_OPENAPI_SHA256)

    def test_paper_fills_snapshots_and_ml_records_match_original_engine(self):
        before = json.loads((FIXTURES / "paper_engine_before_refactor.json").read_text(encoding="utf-8"))
        self.assertIs(MovingAverageEngine, TradingEngine)
        for name, options in SCENARIOS.items():
            with self.subTest(scenario=name):
                actual = replay(TradingEngine, options)
                self.assertEqual(actual["orders"], before[name]["orders"])
                self.assertEqual(actual["observations"][-1], before[name]["final_observation"])
                for part in ("observations", "ml_decisions"):
                    digest = hashlib.sha256(json.dumps(actual[part], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    self.assertEqual(digest, before[name][part + "_sha256"], part)

    def test_signal_equal_averages_count_as_below_and_do_not_buy(self):
        context = SignalContext([Decimal(100)] * 3, Decimal(100), 2, 3, "ABOVE", True, 2, True, Mock(return_value=0))
        signal = MovingAverageStrategy().generate_signal(context)
        self.assertEqual((signal.trend, signal.action, signal.entry_armed, signal.confirmation_count),
                         ("BELOW", "SELL", False, 0))
        context.position_quantity.assert_not_called()
        same_bar = MovingAverageStrategy().generate_signal(replace(context, new_bar=False))
        self.assertEqual((same_bar.action, same_bar.entry_armed, same_bar.confirmation_count), ("HOLD", True, 2))

    def test_risk_boundaries_retain_fee_and_zero_asset_semantics(self):
        config = SimpleNamespace(max_order_amount=Decimal(100), max_symbol_amount=Decimal(200), max_total_investment=Decimal(500))
        self.assertTrue(RiskPolicy.order_amount_allowed(config, Decimal(99), Decimal(1)))
        self.assertFalse(RiskPolicy.order_amount_allowed(config, Decimal(100), Decimal(1)))
        self.assertTrue(RiskPolicy.symbol_amount_allowed(config, Decimal(100), Decimal(100)))
        self.assertFalse(RiskPolicy.symbol_amount_allowed(config, Decimal(100), Decimal("100.01")))
        self.assertTrue(RiskPolicy.total_investment_allowed(config, Decimal(400), Decimal(100)))
        self.assertFalse(RiskPolicy.total_investment_allowed(config, Decimal(400), Decimal("100.01")))
        self.assertEqual(RiskPolicy.cash_ratio(Decimal(0), Decimal(0)), Decimal(0))
        self.assertEqual(RiskPolicy.cash_ratio(Decimal(30), Decimal(100)), Decimal(30))

    def test_engine_uses_injected_signal_strategy(self):
        from auto_trader.models import LiveStockCandle
        from auto_trader.paper import PaperBroker
        from auto_trader.simulator import MarketSimulator
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime(2026, 9, 30, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        simulator = MarketSimulator(symbols=("005930",))
        broker = PaperBroker(simulator)
        strategy = Mock()
        strategy.generate_signal.return_value = StrategySignal(
            Decimal(100), Decimal(100), "BELOW", False, 0, "test intent", "BUY", reason="test reason")
        engine = TradingEngine(simulator, broker, strategy=strategy, clock=lambda: now)
        engine.configure(interval_seconds=10, short_period=1, long_period=2, order_quantity=1)
        bars = [LiveStockCandle(timestamp=now, open_price=100, high_price=100,
                               low_price=100, close_price=100, volume=1)] * 2
        with patch.object(engine, "_buy", return_value=True) as buy:
            engine.step(candles={"005930": bars})
        strategy.generate_signal.assert_called_once()
        buy.assert_called_once_with("005930", simulator.quote("005930").price, reason="test reason")

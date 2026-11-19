"""PAPER-MVP-01 tests. Isolated: a throwaway schema in a TEST database only (refuses any other DB name)."""
import asyncio
import unittest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql

from auto_trader import database, paper, risk
from auto_trader.paper_mvp import MvpError, MvpSettings, PaperMvp, PRESETS
from auto_trader.paper_mvp_synthetic import DAY, SyntheticSource, VirtualClock
from auto_trader.settings import settings as app_settings
from auto_trader.simulator import MarketSimulator
from auto_trader.strategy import MovingAverageEngine


def run(coro):
    return asyncio.run(coro)


def holdings_value(broker):
    return sum((p.market_value for p in broker.account().positions), Decimal(0))


class MvpBase(unittest.TestCase):
    CASH = Decimal("10000000")

    def setUp(self):
        if not app_settings.postgres_db.startswith("folio_mvp_test"):
            self.fail("refusing to run: POSTGRES_DB is not an isolated test database")
        self.schema = "test_mvp_" + uuid4().hex
        self.original_connect = database.connect
        with self.original_connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))

        def isolated():
            conn = self.original_connect()
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            return conn

        self.patchers = [patch.object(m, "connect", isolated) for m in (database, paper, risk)]
        for p in self.patchers:
            p.start()
        self.clock = VirtualClock(DAY + timedelta(minutes=25, seconds=5))
        self.market = MarketSimulator()
        self.broker = paper.PaperBroker(self.market, self.CASH, account_name="mvp-test")
        self.broker.initialize()
        self.broker.set_risk_manager(risk.RiskManager(self.market))
        self.engine = MovingAverageEngine(self.market, self.broker, interval_seconds=1, clock=self.clock)
        self.source = SyntheticSource(self.clock)
        self.mvp = PaperMvp(self.broker, self.engine, source=self.source, clock=self.clock)

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        with self.original_connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def poll(self, minutes=1):
        for _ in range(minutes):
            run(self.engine.poll_market())
            self.clock.advance(minutes=1)

    def balanced(self, **changes):
        base = dict(budget=5000000, sectors=["FOLIO 추천"], profile="균형형", **PRESETS["균형형"])
        base.update(changes)
        return MvpSettings(**base)

    def reapply(self, **changes):
        self.engine.running = False
        self.mvp.apply(self.balanced(profile="직접 설정", **changes))
        self.engine.running = True


class SettingsTests(MvpBase):
    def test_save_apply_and_restore(self):
        self.mvp.apply(self.balanced())
        limits = self.broker.risk_manager.settings()
        self.assertEqual(limits.max_total_investment, Decimal(5000000))
        self.assertEqual(limits.max_symbol_amount, Decimal(2500000))
        self.assertEqual(limits.daily_loss_limit, Decimal(200000))
        self.assertEqual(self.engine.stop_loss_rate, Decimal(5))
        self.assertEqual(self.engine.take_profit_rate, Decimal(10))
        restarted = paper.PaperBroker(self.market, self.CASH, account_name="mvp-test")
        restarted.initialize()
        self.assertEqual(restarted.mvp_state()["settings"]["profile"], "균형형")
        self.assertEqual(restarted.mvp_state()["settings"]["budget"], 5000000)

    def test_invalid_settings_rejected_and_nothing_applied(self):
        before = self.broker.risk_manager.settings()
        for budget in (0, 10000001):
            with self.assertRaises(MvpError):
                self.mvp.apply(self.balanced(budget=budget))
        self.assertEqual(self.broker.risk_manager.settings().max_total_investment, before.max_total_investment)
        with self.assertRaises(ValueError):
            MvpSettings(budget=1, sectors=[], profile="x", **PRESETS["균형형"])

    def test_cannot_change_while_running(self):
        self.engine.running = True
        with self.assertRaises(MvpError):
            self.mvp.apply(self.balanced())

    def test_existing_lot_keeps_its_exit_rules_after_settings_change(self):
        self.mvp.apply(self.balanced())
        self.engine.running = True
        self.poll(8)                                    # first entries happen near minute 31
        lots = self.broker.managed_lots()
        self.assertTrue(lots, "expected an open lot")
        before = {l["id"]: (l["context"]["stop_loss_rate"], l["context"]["take_profit_rate"]) for l in lots}
        self.engine.running = False
        self.mvp.apply(self.balanced(stop_pct=Decimal(1), take_pct=Decimal(2), profile="직접 설정"))
        after = {l["id"]: (l["context"]["stop_loss_rate"], l["context"]["take_profit_rate"]) for l in self.broker.managed_lots()}
        self.assertEqual(before, after)
        self.assertEqual(self.engine.stop_loss_rate, Decimal(1))      # future buys use the new rule
        shown = {l["stop_pct"] for h in self.mvp.status()["holdings"] for l in h["lots"]}
        self.assertEqual(shown, {"5"})


class FlowTests(MvpBase):
    def setUp(self):
        super().setUp()
        self.mvp.apply(self.balanced())
        self.engine.running = True

    def test_scout_candidates_buy_and_sell_with_consistent_balances(self):
        symbols_traded = set()
        for _ in range(60):
            self.poll()
            account = self.broker.account()
            self.assertEqual(account.total_asset, account.cash + holdings_value(self.broker))
            self.assertGreaterEqual(account.cash, 0)
            cost = sum((p.quantity * p.average_price for p in account.positions), Decimal(0))
            self.assertLessEqual(cost, Decimal(5000000))                 # budget limits purchases, not later price gains
            symbols_traded |= {o.symbol for o in self.broker.orders()}
        orders = list(reversed(self.broker.orders()))
        self.assertEqual(symbols_traded, {"100001", "100002"})      # Scout-excluded 100004/100005 never traded; 100003 never signalled
        self.assertEqual([(o.symbol, o.side.value) for o in orders if o.status.value == "FILLED"],
                         [("100001", "BUY"), ("100002", "BUY"), ("100002", "SELL"), ("100001", "SELL")])
        self.assertTrue(all(o.source == "STRATEGY" for o in orders))
        self.assertEqual(self.broker.holding_quantity("100001") + self.broker.holding_quantity("100002"), 0)
        realized = sum((o.realized_profit or Decimal(0)) for o in orders)
        self.assertEqual(self.broker.account().total_profit, realized)
        kinds = {d["kind"] for d in self.mvp.status()["decisions"]}
        self.assertTrue({"CANDIDATE", "CONTROL"} <= kinds)

    def test_excluded_symbols_are_reported_with_reasons(self):
        self.poll()
        names = {d.symbol: d for d in self.mvp.last_pipeline.symbols}
        self.assertEqual(names["100004"].verdict, "EXCLUDED")
        self.assertIn("LIQUIDITY_SPREAD_UNKNOWN", names["100004"].reasons)
        self.assertEqual(names["100005"].verdict, "EXCLUDED")
        self.assertEqual(set(self.mvp.last_pipeline.passed), {"100001", "100002", "100003"})

    def test_duplicate_polls_in_one_minute_do_not_duplicate_orders(self):
        self.poll(9)
        count = len(self.broker.orders())
        self.assertGreater(count, 0)
        for _ in range(5):                                  # same virtual minute, same bars
            run(self.engine.poll_market())
        self.assertEqual(len(self.broker.orders()), count)

    def test_max_holdings_limits_new_entries(self):
        self.reapply(max_holdings=1)
        for _ in range(25):
            self.poll()
            self.assertLessEqual(len(self.broker.account().positions), 1)
        buys = [o for o in self.broker.orders() if o.side.value == "BUY" and o.status.value == "FILLED"]
        self.assertEqual(len({o.symbol for o in buys}), 1)

    def test_total_budget_and_symbol_cap_hold(self):
        self.reapply(budget=120000, per_unit="amount", per_value=Decimal(60000))
        for _ in range(40):
            self.poll()
            self.assertLessEqual(holdings_value(self.broker), Decimal(120000) + Decimal(8000))   # price drift after a buy
            for p in self.broker.account().positions:
                self.assertLessEqual(p.quantity * p.average_price, Decimal(60000))
        self.assertTrue(any(o.side.value == "BUY" for o in self.broker.orders()))

    def test_unaffordable_budget_never_buys(self):
        self.reapply(budget=40000, per_unit="amount", per_value=Decimal(40000))
        self.poll(40)
        self.assertEqual([o for o in self.broker.orders() if o.status.value == "FILLED"], [])
        self.assertEqual(self.broker.account().cash, self.CASH)

    def test_daily_loss_limit_blocks_new_buys(self):
        from auto_trader.paper_mvp_synthetic import SCENARIO
        self.source.scenario = {k: SCENARIO[k] for k in ("100002", "100003")}   # only the losing trade: net equity falls
        self.reapply(daily_loss_pct=Decimal("0.1"))                  # 10,000 KRW
        blocked = None
        for _ in range(60):
            self.poll()
            st = self.mvp.status()
            if not st["risk"]["new_buys_allowed"]:
                blocked = st
                break
        self.assertIsNotNone(blocked, "the synthetic stop-out loss should trip the daily loss limit")
        self.assertIn("손실", blocked["risk"]["block_reason"])
        self.assertIn("손실", self.broker.risk_manager.check_buy(symbol="100003", amount=Decimal(50000), fee=Decimal(0)))


class HoldTests(MvpBase):
    def setUp(self):
        super().setUp()
        self.mvp.apply(self.balanced())
        self.engine.running = True

    def assert_held(self, code):
        self.poll(45)
        self.assertEqual(self.broker.orders(), [])
        self.assertEqual(self.engine.target_symbols, [])
        codes = [d.get("code") for d in self.mvp.status()["decisions"]]
        self.assertIn(code, codes)

    def test_no_data_source(self):
        self.mvp.source = None
        self.assert_held("NO_DATA_SOURCE")

    def test_data_missing(self):
        self.source.degrade = "unavailable"
        self.assert_held("NO_DATA")

    def test_stale_data(self):
        self.source.degrade = "stale"
        self.assert_held("DATA_STALE")

    def test_clock_not_declared_is_hold(self):
        self.source.degrade = "no_clock"
        self.assert_held("SCOUT_HOLD")
        self.assertIn("CLOCK_UNCERTAINTY_UNDECLARED", " ".join(d["reason"] for d in self.mvp.status()["decisions"]))

    def test_provider_error_is_hold_not_crash(self):
        with patch.object(self.source, "pipeline_input", side_effect=RuntimeError("boom")):
            self.assert_held("DATA_ERROR")


class ControlTests(MvpBase):
    def test_start_stop_state_and_no_orders_while_stopped(self):
        async def scenario():
            self.assertEqual(self.mvp.status()["run"]["state"], "idle")
            await self.mvp.start()
            self.assertEqual(self.mvp.status()["run"]["state"], "running")
            self.assertTrue(self.mvp.status()["run"]["exit_monitoring"])
            for _ in range(12):                              # drive virtual time while the real loop polls
                self.clock.advance(minutes=1)
                await asyncio.sleep(1.1)
                if self.broker.account().positions:
                    break
            await self.mvp.stop()
        run(scenario())
        count = len(self.broker.orders())
        st = self.mvp.status()
        self.assertEqual(st["run"]["state"], "stopped")
        self.assertFalse(st["run"]["exit_monitoring"])
        self.assertIn("청산 감시는 꺼져 있습니다", st["run"]["stop_note"])
        self.assertTrue(self.broker.account().positions, "stop must not liquidate holdings")
        ticks = self.engine.tick_count

        async def after_stop():
            self.clock.advance(minutes=30)
            await asyncio.sleep(2.2)
        run(after_stop())
        self.assertEqual(len(self.broker.orders()), count)
        self.assertEqual(self.engine.tick_count, ticks)

    def test_emergency_stop_flag_and_label(self):
        async def scenario():
            await self.mvp.start()
            await self.mvp.stop(emergency=True)
        run(scenario())
        self.assertEqual(self.mvp.status()["run"]["state"], "emergency")


class SafetyTests(MvpBase):
    def test_only_paper_broker_is_accepted(self):
        class FakeLive:  # anything that is not a PaperBroker
            pass
        with self.assertRaises(TypeError):
            PaperMvp(FakeLive(), self.engine)

    def test_no_live_or_order_client_imports(self):
        import ast
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "auto_trader"
        banned = ("live_orders", "live_strategies", "toss", "services.live_order_service", "user_connections", "connection_vault")
        for name in ("paper_mvp.py", "paper_mvp_synthetic.py", "api/paper_mvp.py"):
            path = root / name
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.ImportFrom):
                    mods = [(node.module or "")] + [a.name for a in node.names]
                elif isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                for mod in mods:
                    self.assertFalse(any(mod == b or mod.endswith("." + b) or mod.startswith(b + ".") for b in banned),
                                     f"{name} imports {mod}")

    def test_full_flow_opens_no_network_connection_except_the_test_db(self):
        import socket
        real = socket.socket.connect
        seen = []

        def guard(sock, address):
            seen.append(address)
            if not (isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1")):   # loopback only (asyncio self-pipe, test DB)
                raise AssertionError(f"unexpected network connection {address}")
            return real(sock, address)
        with patch.object(socket.socket, "connect", guard):
            self.mvp.apply(self.balanced())
            self.engine.running = True
            self.poll(40)
        self.assertEqual(app_settings.postgres_port, 55432)         # libpq (not Python sockets) talks to the isolated test DB only

    def test_state_survives_restart_without_touching_cash(self):
        self.mvp.apply(self.balanced())
        self.engine.running = True
        self.poll(40)
        restarted = paper.PaperBroker(self.market, self.CASH, account_name="mvp-test")
        restarted.initialize()
        self.assertEqual(restarted.cash_balance(), self.broker.cash_balance())
        self.assertEqual(len(restarted.orders()), len(self.broker.orders()))
        self.assertEqual(restarted.mvp_state()["decisions"], self.broker.mvp_state()["decisions"])
        self.assertTrue(restarted.mvp_state()["equity"])

    def test_status_matches_broker(self):
        self.mvp.apply(self.balanced())
        self.engine.running = True
        self.poll(45)
        st = self.mvp.status()
        self.assertEqual([t["id"] for t in st["trades"]], [o.id for o in self.broker.orders()])
        self.assertEqual({h["symbol"]: h["quantity"] for h in st["holdings"]},
                         {p.symbol: p.quantity for p in self.broker.account().positions})
        self.assertEqual(Decimal(st["account"]["total_asset"]), self.broker.account().total_asset)
        self.assertEqual(Decimal(st["account"]["cash"]), self.broker.cash_balance())
        self.assertEqual(Decimal(st["pnl"]["gain"]), self.broker.account().total_asset - Decimal(st["pnl"]["base"]))

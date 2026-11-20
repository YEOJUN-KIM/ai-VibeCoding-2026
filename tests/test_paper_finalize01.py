"""PAPER-FINALIZE-01 regression tests for the independent review's P1 findings.
Same isolation as test_paper_mvp.py (throwaway schema in a TEST database, synthetic market, no network)."""
import asyncio
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from auto_trader import paper_mvp_isolated as iso
from auto_trader.api import paper_mvp as api
from auto_trader.models import OrderRequest
from tests.test_paper_mvp import MvpBase

SYMBOL = "005930"


class MixedHoldingAccounting(MvpBase):
    """P1-2: a strategy sale out of a mixed manual+auto holding must keep orders and account in agreement."""

    def at_price(self, price):
        original = self.market.quote
        quote = original(SYMBOL).model_copy(update={"price": Decimal(price)})
        return patch.object(self.market, "quote", lambda symbol: quote if symbol == SYMBOL else original(symbol))

    def test_strategy_sale_keeps_cost_basis_and_realized_profit_consistent(self):
        with self.at_price(100):
            self.assertEqual(self.broker.submit(OrderRequest(symbol=SYMBOL, side="BUY", quantity=10), source="STRATEGY").status.value, "FILLED")
        with self.at_price(200):
            self.assertEqual(self.broker.submit(OrderRequest(symbol=SYMBOL, side="BUY", quantity=10), source="MANUAL").status.value, "FILLED")
            sale = self.broker.submit(OrderRequest(symbol=SYMBOL, side="SELL", quantity=10), source="STRATEGY")
            self.assertEqual(sale.status.value, "FILLED", sale.message)
            account = self.broker.account()
            order_realized = sum((o.realized_profit for o in self.broker.orders() if o.realized_profit is not None), Decimal(0))
            self.assertAlmostEqual(account.realized_profit, order_realized, delta=Decimal('0.0001'))               # history and account tell the same story
            self.assertEqual(account.positions[0].quantity, 10)
            self.assertEqual(account.positions[0].average_price, self.broker.position_average_price(SYMBOL))
            # remaining 10 shares are the manual ones bought at ~200: cost basis must be that, not the stale mixed average
            self.assertGreater(account.positions[0].average_price, Decimal(190))

    def _realized_matches(self):
        account = self.broker.account()
        booked = sum((o.realized_profit for o in self.broker.orders() if o.realized_profit is not None), Decimal(0))
        self.assertAlmostEqual(account.realized_profit, booked, delta=Decimal('0.0001'))       # Decimal division noise only

    def test_manual_sale_first_then_strategy_sale_keeps_orders_and_account_equal(self):
        with self.at_price(100):
            self.broker.submit(OrderRequest(symbol=SYMBOL, side="BUY", quantity=10), source="STRATEGY")
        with self.at_price(200):
            self.broker.submit(OrderRequest(symbol=SYMBOL, side="BUY", quantity=10), source="MANUAL")
            self.assertEqual(self.broker.submit(OrderRequest(symbol=SYMBOL, side="SELL", quantity=10), source="MANUAL").status.value, "FILLED")
            self._realized_matches()
            remaining = self.broker.account().positions[0]
            self.assertEqual(remaining.quantity, 10)
            self.assertLess(remaining.average_price, Decimal(110))        # the remaining automatic lot's own cost (~100)
            self.assertEqual(self.broker.submit(OrderRequest(symbol=SYMBOL, side="SELL", quantity=10), source="STRATEGY").status.value, "FILLED")
            self._realized_matches()
            self.assertEqual(self.broker.holding_quantity(SYMBOL), 0)

    def test_partial_sales_in_both_orders_keep_orders_and_account_equal(self):
        for first, second in (("MANUAL", "STRATEGY"), ("STRATEGY", "MANUAL")):
            with self.subTest(first=first):
                self.broker.reset_practice()
                with self.at_price(100):
                    self.broker.submit(OrderRequest(symbol=SYMBOL, side="BUY", quantity=10), source="STRATEGY")
                with self.at_price(300):
                    self.broker.submit(OrderRequest(symbol=SYMBOL, side="BUY", quantity=6), source="MANUAL")
                with self.at_price(250):
                    for source, qty in ((first, 3), (second, 4), (first, 2), (second, 2)):
                        have = self.broker.strategy_quantity(SYMBOL) if source == "STRATEGY" else self.broker.holding_quantity(SYMBOL) - self.broker.strategy_quantity(SYMBOL)
                        if have >= qty:
                            self.assertEqual(self.broker.submit(OrderRequest(symbol=SYMBOL, side="SELL", quantity=qty), source=source).status.value, "FILLED")
                        self._realized_matches()

    def test_adopted_holding_sold_in_parts_keeps_cost_non_negative_and_consistent(self):
        """Adoption (scope ALL) stamps the lot with the current price as an exit reference, not as accounting cost."""
        with self.at_price(100):
            self.broker.submit(OrderRequest(symbol=SYMBOL, side="BUY", quantity=10), source="MANUAL")
        with self.at_price(200):
            self.broker.begin_strategy([SYMBOL], scope="ALL")
            lot = self.broker.managed_lots()[0]
            for quantity in (6, 4):
                order = self.broker.submit(OrderRequest(symbol=SYMBOL, side="SELL", quantity=quantity), source="STRATEGY", lot_id=lot["id"])
                self.assertEqual(order.status.value, "FILLED", order.message)
                average = self.broker.position_average_price(SYMBOL)
                self.assertTrue(average is None or average == Decimal(100), average)       # cost of the remaining shares unchanged
                self._realized_matches()
            self.assertEqual(self.broker.account().realized_profit, Decimal(1000))

    def test_adopted_plus_auto_lots_keep_orders_and_account_equal(self):
        with self.at_price(100):
            self.broker.submit(OrderRequest(symbol=SYMBOL, side="BUY", quantity=10), source="MANUAL")
        with self.at_price(200):
            self.broker.begin_strategy([SYMBOL], scope="ALL")
            self.broker.submit(OrderRequest(symbol=SYMBOL, side="BUY", quantity=10), source="STRATEGY")
        with self.at_price(300):
            for lot in list(self.broker.managed_lots()):
                order = self.broker.submit(OrderRequest(symbol=SYMBOL, side="SELL", quantity=lot["quantity"] - 1), source="STRATEGY", lot_id=lot["id"])
                self.assertEqual(order.status.value, "FILLED", order.message)
                self.assertGreaterEqual(self.broker.position_average_price(SYMBOL), 0)
                self._realized_matches()

    def test_pure_strategy_holding_average_is_unchanged_by_a_partial_sale(self):
        with self.at_price(100):
            self.broker.submit(OrderRequest(symbol=SYMBOL, side="BUY", quantity=10), source="STRATEGY")
            before = self.broker.position_average_price(SYMBOL)
            self.broker.submit(OrderRequest(symbol=SYMBOL, side="SELL", quantity=4), source="STRATEGY")
            self.assertEqual(self.broker.position_average_price(SYMBOL), before)


class StatusAfterLoss(MvpBase):
    """P1-3: status and the stop responses must not fail when the account has lost value since the settings were saved."""

    def test_status_survives_total_asset_below_saved_budget(self):
        settings = self.balanced(budget=int(self.CASH))               # 100% of the account
        self.mvp.apply(settings, activate=False)
        self.broker.fee_rate = Decimal("0.01")
        self.broker.submit(OrderRequest(symbol=SYMBOL, side="BUY", quantity=100))   # the fee makes total asset < budget
        self.assertLess(self.broker.account().total_asset, self.CASH)
        status = self.mvp.status()
        self.assertEqual(status["settings"]["budget"], int(self.CASH))
        asyncio.run(self.mvp.stop())
        self.assertFalse(self.engine.running)
        self.mvp.status()


class AccountSwitchRace(unittest.TestCase):
    """P1-1: a request that waited for the lock while the selected account changed must be refused (409)."""

    def make(self):
        broker, engine = object(), object()
        state = SimpleNamespace(broker=broker, engine=engine, lock=asyncio.Lock())
        mvp = SimpleNamespace(broker=broker, engine=engine, lock=state.lock)
        mvp.account_is_current = lambda: state.broker is mvp.broker and state.engine is mvp.engine
        return state, mvp

    def test_switch_while_waiting_for_the_lock_is_refused(self):
        async def scenario():
            state, mvp = self.make()
            await state.lock.acquire()                                # the account switch holds the lock
            ran = []

            async def request():
                async with api.locked(mvp):
                    ran.append(True)

            task = asyncio.create_task(request())
            await asyncio.sleep(0)
            state.broker, state.engine = object(), object()           # switch completes, then releases
            state.lock.release()
            with self.assertRaises(HTTPException) as caught:
                await task
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(ran, [])
        asyncio.run(scenario())

    def test_unchanged_account_proceeds(self):
        async def scenario():
            _, mvp = self.make()
            async with api.locked(mvp):
                return True
        self.assertTrue(asyncio.run(scenario()))


class IsolatedNeverLoadsStoredConnections(unittest.TestCase):
    """P1-4: with isolated mode active, no stored broker connection / local vault is ever read, whatever the DB holds."""

    def test_client_is_unconfigured_and_touches_neither_db_nor_vault(self):
        from auto_trader import user_connections as uc
        boom = unittest.mock.Mock(side_effect=AssertionError("must not touch DB or vault"))
        with patch.object(iso, "active", return_value=True), patch.object(uc, "connect", boom), patch.object(uc, "read_encrypted", boom):
            client = uc.UserConnections().client(1)
        self.assertFalse(client.configured)
        boom.assert_not_called()


class IsolatedNeverTouchesCredentialFiles(unittest.TestCase):
    """P1-B: isolated startup must not migrate (write) or read the local credential files."""

    def test_migration_and_vault_are_blocked(self):
        from auto_trader import connection_vault as vault, user_connections as uc
        boom = unittest.mock.Mock(side_effect=AssertionError("must not touch DB or vault"))
        with patch.object(iso, "active", return_value=True), patch.object(uc, "connect", boom), patch.object(uc, "write_encrypted", boom):
            uc.migrate_local_credentials()
            boom.assert_not_called()
            self.assertIsNone(vault.read_encrypted(1))
            with self.assertRaises(RuntimeError):
                vault.write_encrypted(1, b"x")


if __name__ == "__main__":
    unittest.main()

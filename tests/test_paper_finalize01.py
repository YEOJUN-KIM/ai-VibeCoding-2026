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
            self.assertEqual(account.realized_profit, order_realized)               # history and account tell the same story
            self.assertEqual(account.positions[0].quantity, 10)
            self.assertEqual(account.positions[0].average_price, self.broker.position_average_price(SYMBOL))
            # remaining 10 shares are the manual ones bought at ~200: cost basis must be that, not the stale mixed average
            self.assertGreater(account.positions[0].average_price, Decimal(190))

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


if __name__ == "__main__":
    unittest.main()

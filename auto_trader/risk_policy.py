"""Shared financial limit arithmetic, independent of accounts and execution mode.

Callers retain their own ordering and enforcement: PAPER rejects policy breaches,
whereas LIVE previews expose financial warnings requiring explicit confirmation.
"""

from decimal import Decimal


class RiskPolicy:
    @staticmethod
    def order_amount_allowed(config, amount, fee=None) -> bool:
        return (amount if fee is None else amount + fee) <= config.max_order_amount

    @staticmethod
    def symbol_amount_allowed(config, symbol_value, amount) -> bool:
        return symbol_value + amount <= config.max_symbol_amount

    @staticmethod
    def total_investment_allowed(config, invested, amount) -> bool:
        return invested + amount <= config.max_total_investment

    @staticmethod
    def cash_ratio(cash, total_asset):
        return cash / total_asset * 100 if total_asset else Decimal(0)

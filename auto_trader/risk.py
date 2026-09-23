"""영구 설정과 메모리 PAPER 계좌를 결합한 위험 한도 관리."""

from datetime import date
from decimal import Decimal

from .database import connect
from .models import RiskPreset, RiskSettings, RiskSettingsUpdate, RiskStatus


PRESETS = {
    RiskPreset.CONSERVATIVE: {
        "max_order_amount": Decimal("500000"), "max_symbol_amount": Decimal("1000000"),
        "max_total_investment": Decimal("3000000"), "min_cash_ratio": Decimal("50"),
        "daily_loss_limit": Decimal("200000"), "daily_order_limit": 30,
        "profit_target": Decimal("500000"),
    },
    RiskPreset.DEFAULT: {
        "max_order_amount": Decimal("1000000"), "max_symbol_amount": Decimal("2000000"),
        "max_total_investment": Decimal("7000000"), "min_cash_ratio": Decimal("30"),
        "daily_loss_limit": Decimal("500000"), "daily_order_limit": 100,
        "profit_target": Decimal("1000000"),
    },
}


class RiskManager:
    def __init__(self, market) -> None:
        self.market = market
        self.account_id = None
        self.broker = None
        self._baseline_date = date.today()
        self._opening_asset = Decimal(0)

    def initialize(self, account_id: int, broker) -> None:
        self.account_id = account_id
        self.broker = broker
        values = PRESETS[RiskPreset.DEFAULT]
        with connect() as conn:
            conn.execute(
                """INSERT INTO risk_settings(account_id,preset,max_order_amount,max_symbol_amount,
                   max_total_investment,min_cash_ratio,daily_loss_limit,daily_order_limit,profit_target)
                   VALUES (%s,'DEFAULT',%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(account_id) DO NOTHING""",
                (account_id, *values.values()),
            )
            current = conn.execute("SELECT preset FROM risk_settings WHERE account_id=%s", (account_id,)).fetchone()
            if current and current["preset"] in {RiskPreset.CONSERVATIVE.value, RiskPreset.DEFAULT.value}:
                named = PRESETS[RiskPreset(current["preset"])]
                fields = tuple(PRESETS[RiskPreset.DEFAULT])
                conn.execute(
                    """UPDATE risk_settings SET max_order_amount=%s,max_symbol_amount=%s,
                       max_total_investment=%s,min_cash_ratio=%s,daily_loss_limit=%s,
                       daily_order_limit=%s,profit_target=%s,updated_at=now() WHERE account_id=%s""",
                    (*(named[field] for field in fields), account_id),
                )
        self.reset_daily_baseline()

    def reset_daily_baseline(self) -> None:
        self._baseline_date = date.today()
        self._opening_asset = self.broker.account().total_asset if self.broker else Decimal(0)

    def settings(self) -> RiskSettings:
        with connect() as conn:
            row = conn.execute("SELECT * FROM risk_settings WHERE account_id=%s", (self.account_id,)).fetchone()
        return RiskSettings(**row)

    def update(self, payload: RiskSettingsUpdate) -> RiskSettings:
        field_names = tuple(PRESETS[RiskPreset.DEFAULT])
        if payload.preset == RiskPreset.CUSTOM:
            missing = [name for name in field_names if getattr(payload, name) is None]
            if missing:
                raise ValueError("직접 설정에는 모든 한도 값을 입력해야 합니다.")
            values = {name: getattr(payload, name) for name in field_names}
        else:
            values = PRESETS[payload.preset]
        if values["max_order_amount"] > values["max_symbol_amount"]:
            raise ValueError("1회 주문 한도는 종목별 한도보다 클 수 없습니다.")
        if values["max_symbol_amount"] > values["max_total_investment"]:
            raise ValueError("종목별 한도는 전체 투자 한도보다 클 수 없습니다.")
        with connect() as conn:
            row = conn.execute(
                """UPDATE risk_settings SET preset=%s,max_order_amount=%s,max_symbol_amount=%s,
                   max_total_investment=%s,min_cash_ratio=%s,daily_loss_limit=%s,daily_order_limit=%s,
                   profit_target=%s,updated_at=now() WHERE account_id=%s RETURNING *""",
                (payload.preset.value, *(values[name] for name in field_names), self.account_id),
            ).fetchone()
        return RiskSettings(**row)

    def _metrics(self):
        if self._baseline_date != date.today():
            self.reset_daily_baseline()
        account = self.broker.account()
        orders = sum(1 for order in self.broker.orders() if order.status.value == "FILLED"
                     and order.created_at.date() == date.today())
        invested = sum((position.market_value for position in account.positions), Decimal(0))
        return account.cash, invested, account.total_asset, account.total_asset - self._opening_asset, orders

    def status(self) -> RiskStatus:
        config = self.settings()
        cash, invested, total_asset, daily_profit, orders = self._metrics()
        reason = self.global_block_reason(config, daily_profit, orders)
        return RiskStatus(settings=config, invested_amount=invested,
                          cash_ratio=(cash / total_asset * 100 if total_asset else Decimal(0)),
                          daily_profit=daily_profit, daily_orders=orders,
                          new_buys_allowed=reason is None, block_reason=reason)

    @staticmethod
    def global_block_reason(config, daily_profit, orders):
        if daily_profit <= -config.daily_loss_limit:
            return "일일 손실 한도에 도달했습니다."
        if daily_profit >= config.profit_target:
            return "수익 목표에 도달했습니다."
        if orders >= config.daily_order_limit:
            return "일일 주문 횟수 한도에 도달했습니다."
        return None

    def check_buy(self, *, symbol: str, amount: Decimal, fee: Decimal) -> str | None:
        config = self.settings()
        cash, invested, total_asset, daily_profit, orders = self._metrics()
        reason = self.global_block_reason(config, daily_profit, orders)
        if reason:
            return reason
        symbol_value = self.broker.position_value(symbol)
        if amount + fee > config.max_order_amount:
            return "1회 최대 주문 금액을 초과했습니다."
        if symbol_value + amount > config.max_symbol_amount:
            return "종목별 최대 투자 금액을 초과했습니다."
        if invested + amount > config.max_total_investment:
            return "전체 최대 투자 금액을 초과했습니다."
        remaining_cash = cash - amount - fee
        if total_asset and remaining_cash / total_asset * 100 < config.min_cash_ratio:
            return "최소 현금 보유 비율 아래로 내려갑니다."
        return None

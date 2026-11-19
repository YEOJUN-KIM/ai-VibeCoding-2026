"""영구 설정과 메모리 PAPER 계좌를 결합한 위험 한도 관리."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from .database import connect
from .models import RiskPreset, RiskSettings, RiskSettingsUpdate, RiskStatus
from .risk_policy import RiskPolicy


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
        "daily_loss_limit": Decimal("500000"), "daily_order_limit": 0,
        "profit_target": Decimal("1000000"),
    },
}


@dataclass(frozen=True)
class AutoLimits:
    """In-memory limits of ONE automatic PAPER run (PAPER-SETTINGS-01). Never persisted, never applied to manual orders;
    they are measured against automatic holdings/results only (cash is shared with manual trading)."""
    max_order_amount: Decimal
    max_symbol_amount: Decimal
    max_total_investment: Decimal
    daily_loss_limit: Decimal
    daily_buy_limit: int = 0                  # filled automatic buys per KST day; 0 = no limit (same meaning as the engine's)


LAST_EVAL_WRITE_EVERY = timedelta(seconds=60)


class RiskManager:
    def __init__(self, market) -> None:
        self.market = market
        self.auto_limits: AutoLimits | None = None
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
        reason = self.global_block_reason(
            config, daily_profit, orders,
            ignore_daily_order_limit=bool(getattr(self.broker, "ignore_daily_order_limit", False)),
        )
        return RiskStatus(settings=config, invested_amount=invested,
                          effective_min_cash_ratio=(Decimal(0) if self.broker.ignore_min_cash_ratio else config.min_cash_ratio),
                          daily_order_limit_disabled=(self.broker.ignore_daily_order_limit or config.daily_order_limit == 0),
                          cash_ratio=(cash / total_asset * 100 if total_asset else Decimal(0)),
                          daily_profit=daily_profit, daily_orders=orders,
                          new_buys_allowed=reason is None, block_reason=reason)

    @staticmethod
    def global_block_reason(config, daily_profit, orders, *, ignore_daily_order_limit=False):
        if daily_profit <= -config.daily_loss_limit:
            return "일일 손실 한도에 도달했습니다."
        if daily_profit >= config.profit_target:
            return "수익 목표에 도달했습니다."
        if not ignore_daily_order_limit and config.daily_order_limit > 0 and orders >= config.daily_order_limit:
            return "일일 주문 횟수 한도에 도달했습니다."
        return None

    # ---- automatic-run limits (PAPER-SETTINGS-01)
    def _auto_day(self, limits: "AutoLimits"):
        """(profit today of automatic trades only, baseline_trusted). Profit = realised STRATEGY sells today + unrealised
        (net of estimated exit costs) of automatic lots - the unrealised part at the day's baseline.

        The baseline is read from the account's persisted state on every call (so an account reset invalidates it) and is
        trusted only when it can be proven from recorded facts, never from "whatever the first evaluation saw":
          * previous evaluation record (`auto_last`, rewritten at most once a minute) exists, no automatic order was filled
            since it and the holdings are identical -> that record is the day's starting point;
          * or nothing is held and no automatic order was filled today -> the day starts flat at 0.
        Anything else (carried holdings with no provable start, sells before the first evaluation, unreadable records) is
        untrusted: new buys are held, exits are never touched."""
        from .trading import KST
        now = self.broker.now().astimezone(KST)
        today, key = now.date(), now.date().isoformat()
        orders = [o for o in self.broker.orders() if o.source == "STRATEGY" and o.status.value == "FILLED"]
        realized = sum((o.realized_profit or Decimal(0) for o in orders
                        if o.side.value == "SELL" and o.created_at.astimezone(KST).date() == today), Decimal(0))
        holdings = {sym: int(self.broker.strategy_quantity(sym)) for sym in self.broker.strategy_symbols()}
        unrealized = self.broker.strategy_unrealized()
        state = self.broker.mvp_state()
        base, last = state.get("auto_open"), state.get("auto_last")

        if base is None or (isinstance(base, dict) and base.get("date") != key) or (isinstance(base, list) and base and base[0] != key):
            record = None
            try:
                last_at = datetime.fromisoformat(last["at"])
                last_holdings = {str(k): int(v) for k, v in last["holdings"].items()}
                last_unrealized = Decimal(last["unrealized"])
                since = [o for o in orders if o.created_at > last_at]
                if last_at.astimezone(KST).date() < today and not since and last_holdings == holdings:
                    record = dict(date=key, unrealized=str(last_unrealized), trusted=True, at=now.isoformat(), source="previous-evaluation")
            except (TypeError, KeyError, ValueError, AttributeError, ArithmeticError):
                pass                                                  # no usable previous record
            if record is None:
                flat_start = not holdings and not any(o.created_at.astimezone(KST).date() == today for o in orders)
                record = dict(date=key, unrealized="0" if flat_start else str(unrealized), trusted=flat_start, at=now.isoformat(),
                              source="flat-start" if flat_start else "unverifiable")
            self.broker.update_mvp(auto_open=record)                  # raises if it cannot be saved -> buys are held
        elif isinstance(base, dict):
            record = dict(date=base["date"], unrealized=str(Decimal(base["unrealized"])), trusted=bool(base["trusted"]))
        elif isinstance(base, list) and len(base) == 2:               # written by an earlier version: trust level unknown
            record = dict(date=base[0], unrealized=str(Decimal(base[1])), trusted=False)
        else:
            raise ValueError("unreadable day baseline")

        try:
            stale = (datetime.fromisoformat(last["at"]) + LAST_EVAL_WRITE_EVERY <= now
                     or {str(k): int(v) for k, v in last["holdings"].items()} != holdings)
        except (TypeError, KeyError, ValueError, AttributeError):
            stale = True
        if stale:                                                     # the next day's provable starting point
            self.broker.update_mvp(auto_last=dict(at=now.isoformat(), unrealized=str(unrealized), holdings=holdings))
        return realized + unrealized - Decimal(record["unrealized"]), bool(record["trusted"])

    def checkpoint(self) -> None:
        """End-of-tick record of the automatic book (holdings, unrealised), so the next day's starting point is provable even
        if the app stops right after a fill. Never raises: errors surface through auto_status."""
        if self.auto_limits is None:
            return
        try:
            self._auto_day(self.auto_limits)
        except Exception:
            pass

    def auto_profit(self, limits: "AutoLimits | None" = None) -> Decimal:
        return self._auto_day(limits or self.auto_limits or AutoLimits(Decimal(1), Decimal(1), Decimal(1), Decimal(1)))[0]

    def auto_status(self, limits: "AutoLimits") -> dict:
        base = dict(max_total_investment=limits.max_total_investment, max_symbol_amount=limits.max_symbol_amount,
                    daily_loss_limit=limits.daily_loss_limit)
        try:
            profit, trusted = self._auto_day(limits)
            invested = self.broker.strategy_value()
        except Exception:                               # cannot establish the day's figures: hold new buys, never guess
            return dict(new_buys_allowed=False, block_reason="자동매매 일일 손익 기준을 확인할 수 없어 신규 매수를 보류합니다.",
                        daily_profit=Decimal(0), invested_amount=Decimal(0), **base)
        reason = None
        if not trusted:
            reason = ("오늘의 손익 시작 기준을 확인할 수 없어(전일 평가 기록 없이 자동매매 보유분이 이월됨, 또는 첫 평가 전에 자동 매도가 있었음) "
                      "신규 매수를 보류합니다. 보유분의 청산 조건은 유지되며, 오늘 평가 기록이 쌓이면 다음 거래일부터 기준이 확정됩니다.")
        elif profit <= -limits.daily_loss_limit:
            reason = "일일 손실 한도에 도달했습니다."
        return dict(new_buys_allowed=reason is None, block_reason=reason, daily_profit=profit, invested_amount=invested, **base)

    def _check_auto_buy(self, symbol: str, amount: Decimal, fee: Decimal, limits: "AutoLimits") -> str | None:
        status = self.auto_status(limits)
        if status["block_reason"]:
            return status["block_reason"]
        if not RiskPolicy.order_amount_allowed(limits, amount, fee):
            return "1회 최대 주문 금액을 초과했습니다."
        if not RiskPolicy.symbol_amount_allowed(limits, self.broker.strategy_symbol_value(symbol), amount):
            return "종목별 최대 투자 금액을 초과했습니다."
        if not RiskPolicy.total_investment_allowed(limits, status["invested_amount"], amount):
            return "자동매매 투자 가능 금액을 초과했습니다."
        if limits.daily_buy_limit > 0:
            from .trading import KST
            try:
                used = self.broker.daily_buy_count(self.broker.now().astimezone(KST).date())
            except Exception:
                return "하루 매수 횟수를 확인할 수 없어 신규 매수를 보류합니다."
            if used >= limits.daily_buy_limit:
                return f"하루 매수 한도 {limits.daily_buy_limit}회에 도달했습니다."
        return None

    def check_buy(self, *, symbol: str, amount: Decimal, fee: Decimal,
                  ignore_min_cash_ratio: bool = False,
                  ignore_daily_order_limit: bool = False) -> str | None:
        if self.auto_limits is not None:                # an automatic run owns its own limits; saved settings are not used
            return self._check_auto_buy(symbol, amount, fee, self.auto_limits)
        config = self.settings()
        cash, invested, total_asset, daily_profit, orders = self._metrics()
        reason = self.global_block_reason(
            config, daily_profit, orders,
            ignore_daily_order_limit=ignore_daily_order_limit,
        )
        if reason:
            return reason
        symbol_value = self.broker.position_value(symbol)
        if not RiskPolicy.order_amount_allowed(config, amount, fee):
            return "1회 최대 주문 금액을 초과했습니다."
        if not RiskPolicy.symbol_amount_allowed(config, symbol_value, amount):
            return "종목별 최대 투자 금액을 초과했습니다."
        if not RiskPolicy.total_investment_allowed(config, invested, amount):
            return "전체 최대 투자 금액을 초과했습니다."
        remaining_cash = cash - amount - fee
        if not ignore_min_cash_ratio and total_asset and RiskPolicy.cash_ratio(remaining_cash, total_asset) < config.min_cash_ratio:
            return "최소 현금 보유 비율 아래로 내려갑니다."
        return None

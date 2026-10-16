"""Toss accounts methods. The facade owns shared locks and caches."""

from datetime import datetime, timedelta, timezone
from time import monotonic
from urllib.parse import urlencode
from decimal import Decimal
from ...models import LiveBuyingPower, LiveHolding, LivePortfolio
from .types import TossApiError, TossConnectionResult


class TossAccounts:
    def accounts(self) -> list[dict]:
        now = monotonic()
        if self._accounts_cache is not None and now < self._accounts_expires_at:
            return self._accounts_cache
        with self._accounts_lock:
            now = monotonic()
            if self._accounts_cache is not None and now < self._accounts_expires_at:
                return self._accounts_cache
            payload = self._authorized_json_request(f"{self.base_url}/api/v1/accounts")
            result = payload.get("result")
            if not isinstance(result, list):
                raise TossApiError("토스 API 계좌 목록 응답 형식이 예상과 다릅니다.")
            self._accounts_cache = result
            self._accounts_expires_at = monotonic() + 30
            return result


    def selected_account(self) -> dict:
        accounts = self.accounts()
        if not accounts:
            raise TossApiError("연결된 토스증권 계좌가 없습니다.")
        if self.account_ref:
            selected = next((item for item in accounts if str(item.get("accountSeq")) == self.account_ref), None)
            if not selected:
                raise TossApiError("TOSS_ACCOUNT와 일치하는 계좌를 찾지 못했습니다.")
            return selected
        if len(accounts) > 1:
            raise TossApiError("계좌가 여러 개입니다. .env의 TOSS_ACCOUNT에 사용할 accountSeq를 입력하세요.")
        return accounts[0]


    @staticmethod
    def _decimal(value) -> Decimal:
        return Decimal(str(value or 0))


    @classmethod
    def _krw_amount(cls, value) -> Decimal:
        """합계 금액은 통화별 객체, 종목 금액은 문자열로 오는 차이를 흡수한다."""
        if isinstance(value, dict):
            value = value.get("krw")
        return cls._decimal(value)


    @classmethod
    def _percent(cls, value) -> Decimal:
        """토스 API의 1 = 100% 비율 값을 화면용 퍼센트 값으로 변환한다."""
        return cls._decimal(value) * Decimal("100")


    def portfolio(self) -> LivePortfolio:
        now = monotonic()
        if self._portfolio_cache is not None and now < self._portfolio_expires_at:
            return self._portfolio_cache
        with self._portfolio_lock:
            now = monotonic()
            if self._portfolio_cache is not None and now < self._portfolio_expires_at:
                return self._portfolio_cache
            account = self.selected_account()
            account_seq = str(account["accountSeq"])
            payload = self._authorized_json_request(
                f"{self.base_url}/api/v1/holdings",
                headers={"X-Tossinvest-Account": account_seq},
            )
            result = payload.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("items"), list):
                raise TossApiError("토스 API 보유자산 응답 형식이 예상과 다릅니다.")
            try:
                reference_date, market_open_today = self.kr_market_open_today()
            except TossApiError:
                reference_date = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
                market_open_today = None
            holdings = []
            for item in result["items"]:
                market_value = item.get("marketValue") or {}
                profit = item.get("profitLoss") or {}
                daily = item.get("dailyProfitLoss") or {}
                holdings.append(LiveHolding(
                    symbol=str(item.get("symbol", "")), name=str(item.get("name", "")),
                    market_country=str(item.get("marketCountry", "")), currency=str(item.get("currency", "")),
                    quantity=self._decimal(item.get("quantity")),
                    average_purchase_price=self._decimal(item.get("averagePurchasePrice")),
                    last_price=self._decimal(item.get("lastPrice")),
                    purchase_amount=self._decimal(market_value.get("purchaseAmount")),
                    market_value=self._decimal(market_value.get("amount")),
                    profit_loss=self._decimal(profit.get("amount")), profit_rate=self._percent(profit.get("rate")),
                    daily_profit_loss=self._decimal(daily.get("amount")),
                ))
            account_no = str(account.get("accountNo", ""))
            label = ("*" * max(0, len(account_no) - 4) + account_no[-4:]) if account_no else f"계좌 {account_seq}"
            daily_profit_loss = self._krw_amount((result.get("dailyProfitLoss") or {}).get("amount"))
            daily_profit_rate = self._percent((result.get("dailyProfitLoss") or {}).get("rate"))
            portfolio = LivePortfolio(
                account_label=label,
                total_purchase_krw=self._decimal((result.get("totalPurchaseAmount") or {}).get("krw")),
                market_value=self._krw_amount((result.get("marketValue") or {}).get("amount")),
                profit_loss=self._krw_amount((result.get("profitLoss") or {}).get("amount")),
                profit_rate=self._percent((result.get("profitLoss") or {}).get("rate")),
                daily_profit_loss=daily_profit_loss,
                daily_profit_rate=daily_profit_rate,
                daily_profit_reference_date=reference_date,
                market_open_today=market_open_today,
                holdings=holdings,
            )
            self._portfolio_cache = portfolio
            self._portfolio_expires_at = monotonic() + 8
            return portfolio


    def buying_power(self) -> LiveBuyingPower:
        now = monotonic()
        if self._buying_power_cache is not None and now < self._buying_power_expires_at:
            return self._buying_power_cache
        with self._buying_power_lock:
            now = monotonic()
            if self._buying_power_cache is not None and now < self._buying_power_expires_at:
                return self._buying_power_cache
            account = self.selected_account()
            account_seq = str(account["accountSeq"])
            amounts: dict[str, Decimal] = {}
            for currency in ("KRW", "USD"):
                query = urlencode({"currency": currency})
                payload = self._authorized_json_request(
                    f"{self.base_url}/api/v1/buying-power?{query}",
                    headers={"X-Tossinvest-Account": account_seq},
                )
                result = payload.get("result")
                if not isinstance(result, dict) or result.get("currency") != currency:
                    raise TossApiError("토스 API 매수 가능 금액 응답 형식이 예상과 다릅니다.")
                amounts[currency] = self._decimal(result.get("cashBuyingPower"))
            account_no = str(account.get("accountNo", ""))
            label = ("*" * max(0, len(account_no) - 4) + account_no[-4:]) if account_no else f"계좌 {account_seq}"
            buying_power = LiveBuyingPower(
                account_label=label,
                krw_cash_buying_power=amounts["KRW"],
                usd_cash_buying_power=amounts["USD"],
            )
            self._buying_power_cache = buying_power
            self._buying_power_expires_at = monotonic() + 8
            return buying_power


    def test_connection(self) -> TossConnectionResult:
        accounts = self.accounts()
        return TossConnectionResult(True, len(accounts), "토큰 발급과 계좌 목록 조회에 성공했습니다.")

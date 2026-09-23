"""토스증권 Open API의 인증 및 읽기 전용 계좌 조회 클라이언트."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from decimal import Decimal

from .settings import settings
from .models import LiveHolding, LivePortfolio, Stock


class TossApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, error_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


@dataclass(frozen=True)
class TossConnectionResult:
    connected: bool
    account_count: int
    message: str


class TossClient:
    def __init__(self, *, base_url: str | None = None, client_id: str | None = None,
                 client_secret: str | None = None, timeout: float = 10) -> None:
        self.base_url = (base_url or settings.toss_api_base_url).rstrip("/")
        self.client_id = settings.toss_client_id if client_id is None else client_id
        self.client_secret = settings.toss_client_secret if client_secret is None else client_secret
        self.timeout = timeout
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._token_lock = Lock()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _json_request(self, request: Request) -> dict:
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                payload = {}
            code = payload.get("code") or payload.get("error")
            detail = payload.get("message") or payload.get("error_description")
            message = f"토스 API 요청 실패({exc.code})"
            if code:
                message += f": {code}"
            if detail:
                message += f" - {detail}"
            raise TossApiError(message, status_code=exc.code, error_code=code) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TossApiError("토스 API 서버에 연결하지 못했습니다.") from exc

    def access_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._access_token and self._token_expires_at and now < self._token_expires_at:
            return self._access_token
        if not self.configured:
            raise TossApiError("TOSS_CLIENT_ID와 TOSS_CLIENT_SECRET을 먼저 설정하세요.")
        with self._token_lock:
            now = datetime.now(timezone.utc)
            if self._access_token and self._token_expires_at and now < self._token_expires_at:
                return self._access_token
            body = urlencode({"grant_type": "client_credentials", "client_id": self.client_id,
                              "client_secret": self.client_secret}).encode("utf-8")
            payload = self._json_request(Request(
                f"{self.base_url}/oauth2/token", data=body, method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            ))
            token = payload.get("access_token")
            if not token:
                raise TossApiError("토스 API 토큰 응답에 access_token이 없습니다.")
            expires_in = max(60, int(payload.get("expires_in", 3600)))
            self._access_token = token
            self._token_expires_at = now + timedelta(seconds=max(30, expires_in - 60))
            return token

    def accounts(self) -> list[dict]:
        payload = self._json_request(Request(
            f"{self.base_url}/api/v1/accounts", method="GET",
            headers={"Authorization": f"Bearer {self.access_token()}", "Accept": "application/json"},
        ))
        result = payload.get("result")
        if not isinstance(result, list):
            raise TossApiError("토스 API 계좌 목록 응답 형식이 예상과 다릅니다.")
        return result

    def selected_account(self) -> dict:
        accounts = self.accounts()
        if not accounts:
            raise TossApiError("연결된 토스증권 계좌가 없습니다.")
        if settings.toss_account:
            selected = next((item for item in accounts if str(item.get("accountSeq")) == settings.toss_account), None)
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
        account = self.selected_account()
        account_seq = str(account["accountSeq"])
        payload = self._json_request(Request(
            f"{self.base_url}/api/v1/holdings", method="GET",
            headers={"Authorization": f"Bearer {self.access_token()}",
                     "X-Tossinvest-Account": account_seq, "Accept": "application/json"},
        ))
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise TossApiError("토스 API 보유자산 응답 형식이 예상과 다릅니다.")
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
        return LivePortfolio(
            account_label=label,
            total_purchase_krw=self._decimal((result.get("totalPurchaseAmount") or {}).get("krw")),
            market_value=self._krw_amount((result.get("marketValue") or {}).get("amount")),
            profit_loss=self._krw_amount((result.get("profitLoss") or {}).get("amount")),
            profit_rate=self._percent((result.get("profitLoss") or {}).get("rate")),
            daily_profit_loss=self._krw_amount((result.get("dailyProfitLoss") or {}).get("amount")),
            daily_profit_rate=self._percent((result.get("dailyProfitLoss") or {}).get("rate")),
            holdings=holdings,
        )

    def test_connection(self) -> TossConnectionResult:
        accounts = self.accounts()
        return TossConnectionResult(True, len(accounts), "토큰 발급과 계좌 목록 조회에 성공했습니다.")

    def domestic_trading_amount_top(self, count: int = 10) -> tuple[list[Stock], dict[str, Decimal]]:
        query = urlencode({"type": "MARKET_TRADING_AMOUNT", "marketCountry": "KR",
                           "duration": "1d", "excludeInvestmentCaution": "true", "count": count})
        ranking_payload = self._json_request(Request(
            f"{self.base_url}/api/v1/rankings?{query}", method="GET",
            headers={"Authorization": f"Bearer {self.access_token()}", "Accept": "application/json"},
        ))
        rankings = (ranking_payload.get("result") or {}).get("rankings")
        if not isinstance(rankings, list) or not rankings:
            raise TossApiError("토스 API 거래대금 순위 응답 형식이 예상과 다릅니다.")
        rankings = sorted(rankings, key=lambda item: item.get("rank", 999))[:count]
        symbols = [str(item["symbol"]) for item in rankings]
        info_query = urlencode({"symbols": ",".join(symbols)})
        info_payload = self._json_request(Request(
            f"{self.base_url}/api/v1/stocks?{info_query}", method="GET",
            headers={"Authorization": f"Bearer {self.access_token()}", "Accept": "application/json"},
        ))
        info = info_payload.get("result")
        if not isinstance(info, list):
            raise TossApiError("토스 API 종목 정보 응답 형식이 예상과 다릅니다.")
        names = {str(item["symbol"]): str(item["name"]) for item in info}
        stocks = [Stock(symbol=symbol, name=names.get(symbol, symbol)) for symbol in symbols]
        prices = {str(item["symbol"]): self._decimal(
            (item.get("price") or {}).get("lastPrice") if isinstance(item.get("price"), dict)
            else item.get("price")) for item in rankings}
        return stocks, prices

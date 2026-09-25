"""토스증권 Open API의 인증 및 읽기 전용 계좌 조회 클라이언트."""

import gzip
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from decimal import Decimal

from .settings import settings
from .models import (LiveBuyingPower, LiveCandidateList, LiveFavoriteStock, LiveHolding,
                     LivePortfolio, LiveStockCandle, LiveStockCandidate, LiveStockDetail, LiveStockSearchPage,
                     LiveStockSearchResult, Stock)


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
        self._accounts_cache: list[dict] | None = None
        self._accounts_expires_at = 0.0
        self._accounts_lock = Lock()
        self._portfolio_cache: LivePortfolio | None = None
        self._portfolio_expires_at = 0.0
        self._portfolio_lock = Lock()
        self._kr_market_day_cache: tuple[str, bool] | None = None
        self._kr_market_day_expires_at = 0.0
        self._kr_market_day_lock = Lock()
        self._buying_power_cache: LiveBuyingPower | None = None
        self._buying_power_expires_at = 0.0
        self._buying_power_lock = Lock()
        self._candidate_cache: LiveCandidateList | None = None
        self._candidate_expires_at = 0.0
        self._candidate_lock = Lock()
        self._domestic_rankings_cache: tuple[str | None, list[dict]] | None = None
        self._domestic_rankings_expires_at = 0.0
        self._domestic_rankings_lock = Lock()
        self._stock_universe_cache: list[dict] | None = None
        self._stock_universe_expires_at = 0.0
        self._stock_universe_lock = Lock()
        self._stock_info_cache: dict[str, tuple[float, dict]] = {}
        self._stock_info_lock = Lock()
        self._candle_cache: dict[
            tuple[str, str], tuple[float, int, list[LiveStockCandle]]
        ] = {}
        self._candle_lock = Lock()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @staticmethod
    def _read_json(response) -> dict:
        raw = response.read()
        if raw[:2] == b"\x1f\x8b" or response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))

    @staticmethod
    def _error_details(payload: dict) -> tuple[str | None, str | None]:
        error = payload.get("error")
        if isinstance(error, dict):
            return error.get("code"), error.get("message")
        code = payload.get("code") or (error if isinstance(error, str) else None)
        detail = payload.get("message") or payload.get("error_description")
        return code, detail

    def _json_request(self, request: Request) -> dict:
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return self._read_json(response)
        except HTTPError as exc:
            try:
                payload = self._read_json(exc)
            except (OSError, ValueError, UnicodeDecodeError):
                payload = {}
            code, detail = self._error_details(payload)
            message = f"토스 API 요청 실패({exc.code})"
            if code:
                message += f": {code}"
            if detail:
                message += f" - {detail}"
            raise TossApiError(message, status_code=exc.code, error_code=code) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TossApiError("토스 API 서버에 연결하지 못했습니다.") from exc

    def _invalidate_access_token(self, rejected_token: str) -> None:
        with self._token_lock:
            if self._access_token == rejected_token:
                self._access_token = None
                self._token_expires_at = None

    def _authorized_json_request(self, url: str, *, headers: dict[str, str] | None = None) -> dict:
        token = self.access_token()

        def request(access_token: str) -> Request:
            request_headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
            request_headers.update(headers or {})
            return Request(url, method="GET", headers=request_headers)

        try:
            return self._json_request(request(token))
        except TossApiError as exc:
            recoverable_codes = {"invalid-token", "expired-token", "token-revoked"}
            if exc.status_code != 401 or exc.error_code not in recoverable_codes:
                raise
            self._invalidate_access_token(token)
            return self._json_request(request(self.access_token()))

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

    def kr_market_open_today(self) -> tuple[str, bool]:
        """KST 기준 오늘 국내 통합장이 열리는 날인지 조회한다."""
        kst = timezone(timedelta(hours=9))
        today = datetime.now(kst).date().isoformat()
        now = monotonic()
        if (self._kr_market_day_cache is not None
                and self._kr_market_day_cache[0] == today
                and now < self._kr_market_day_expires_at):
            return self._kr_market_day_cache
        with self._kr_market_day_lock:
            now = monotonic()
            if (self._kr_market_day_cache is not None
                    and self._kr_market_day_cache[0] == today
                    and now < self._kr_market_day_expires_at):
                return self._kr_market_day_cache
            query = urlencode({"date": today})
            payload = self._authorized_json_request(
                f"{self.base_url}/api/v1/market-calendar/KR?{query}"
            )
            result = payload.get("result")
            market_day = result.get("today") if isinstance(result, dict) else None
            if not isinstance(market_day, dict):
                raise TossApiError("토스 API 국내 장 운영 응답 형식이 예상과 다릅니다.")
            market_open_today = isinstance(market_day.get("integrated"), dict)
            previous_day = result.get("previousBusinessDay")
            previous_date = previous_day.get("date") if isinstance(previous_day, dict) else None
            reference_date = str(
                (market_day.get("date") or today)
                if market_open_today else (previous_date or market_day.get("date") or today)
            )
            status = (reference_date, market_open_today)
            self._kr_market_day_cache = status
            self._kr_market_day_expires_at = monotonic() + 300
            return status

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
            self._portfolio_expires_at = monotonic() + 1
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
            self._buying_power_expires_at = monotonic() + 2
            return buying_power

    def affordable_domestic_candidates(self, count: int = 5) -> LiveCandidateList:
        now = monotonic()
        if self._candidate_cache is not None and now < self._candidate_expires_at:
            return self._candidate_cache
        with self._candidate_lock:
            now = monotonic()
            if self._candidate_cache is not None and now < self._candidate_expires_at:
                return self._candidate_cache
            cash = self.buying_power().krw_cash_buying_power
            ranked_at, rankings = self._domestic_trading_amount_rankings()
            affordable = []
            for item in sorted(rankings, key=lambda row: row.get("rank", 999)):
                price_data = item.get("price") or {}
                price = self._decimal(price_data.get("lastPrice"))
                if price > 0 and price <= cash:
                    affordable.append((item, price, price_data))
            symbols = [str(item[0].get("symbol", "")) for item in affordable]
            details: dict[str, dict] = {}
            if symbols:
                info_query = urlencode({"symbols": ",".join(symbols)})
                info_payload = self._authorized_json_request(
                    f"{self.base_url}/api/v1/stocks?{info_query}"
                )
                info = info_payload.get("result")
                if not isinstance(info, list):
                    raise TossApiError("토스 API 종목 정보 응답 형식이 예상과 다릅니다.")
                details = {str(item.get("symbol", "")): item for item in info}
            candidates = []
            for item, price, price_data in affordable:
                symbol = str(item.get("symbol", ""))
                detail = details.get(symbol) or {}
                market_detail = detail.get("koreanMarketDetail") or {}
                if (detail.get("securityType") != "STOCK"
                        or not detail.get("isCommonShare")
                        or detail.get("status") != "ACTIVE"
                        or market_detail.get("liquidationTrading")
                        or market_detail.get("krxTradingSuspended")):
                    continue
                rank = int(item.get("rank", 0))
                candidates.append(LiveStockCandidate(
                    rank=rank,
                    symbol=symbol,
                    name=str(detail.get("name") or symbol),
                    price=price,
                    change_rate_percent=self._percent(price_data.get("changeRate")),
                    max_quantity=int(cash // price),
                    reason=f"국내 일일 거래대금 {rank}위 · 주문 가능 조건 충족",
                ))
                if len(candidates) >= count:
                    break
            candidate_list = LiveCandidateList(
                available_cash_krw=cash,
                ranked_at=ranked_at,
                basis="거래대금과 주문 가능 조건을 바탕으로 투자유의 종목을 제외한 탐색 결과",
                disclaimer="투자 권유가 아닌 탐색 후보입니다. 수익을 보장하지 않으며 가격과 주문 가능 금액은 변할 수 있습니다.",
                candidates=candidates,
            )
            self._candidate_cache = candidate_list
            self._candidate_expires_at = monotonic() + 30
            return candidate_list

    def _domestic_trading_amount_rankings(self) -> tuple[str | None, list[dict]]:
        now = monotonic()
        if self._domestic_rankings_cache is not None and now < self._domestic_rankings_expires_at:
            return self._domestic_rankings_cache
        with self._domestic_rankings_lock:
            now = monotonic()
            if self._domestic_rankings_cache is not None and now < self._domestic_rankings_expires_at:
                return self._domestic_rankings_cache
            query = urlencode({
                "type": "MARKET_TRADING_AMOUNT",
                "marketCountry": "KR",
                "duration": "1d",
                "excludeInvestmentCaution": "true",
                "count": 100,
            })
            payload = self._authorized_json_request(f"{self.base_url}/api/v1/rankings?{query}")
            result = payload.get("result")
            rankings = result.get("rankings") if isinstance(result, dict) else None
            if not isinstance(rankings, list):
                raise TossApiError("토스 API 국내 거래대금 순위 응답 형식이 예상과 다릅니다.")
            cached = (
                result.get("rankedAt") if isinstance(result, dict) else None,
                sorted(rankings, key=lambda item: item.get("rank", 999)),
            )
            self._domestic_rankings_cache = cached
            self._domestic_rankings_expires_at = monotonic() + 30
            return cached

    def _domestic_stock_universe(self) -> list[dict]:
        now = monotonic()
        if self._stock_universe_cache is not None and now < self._stock_universe_expires_at:
            return self._stock_universe_cache
        with self._stock_universe_lock:
            now = monotonic()
            if self._stock_universe_cache is not None and now < self._stock_universe_expires_at:
                return self._stock_universe_cache
            universe = []
            for index, market in enumerate(("KOSPI", "KOSDAQ")):
                if index:
                    sleep(1.05)
                query = urlencode({"market": market, "status": "ACTIVE"})
                payload = self._authorized_json_request(f"{self.base_url}/api/v1/stocks/all?{query}")
                result = payload.get("result")
                if not isinstance(result, list):
                    raise TossApiError("토스 API 국내 종목 목록 응답 형식이 예상과 다릅니다.")
                universe.extend({**item, "market": market} for item in result)
            self._stock_universe_cache = universe
            self._stock_universe_expires_at = monotonic() + 43200
            return universe

    def warm_domestic_stock_universe(self) -> int:
        """서버 시작 시 종목 목록을 미리 적재해 첫 화면 대기를 줄인다."""
        return len(self._domestic_stock_universe())

    def search_domestic_stocks(self, query: str, page: int = 1,
                               page_size: int = 8) -> LiveStockSearchPage:
        return self.list_domestic_stocks(
            query=query, page=page, page_size=page_size, sort="POPULAR"
        )

    def list_domestic_stocks(self, *, query: str = "", market: str = "ALL",
                             security_type: str = "ALL", sort: str = "POPULAR",
                             page: int = 1, page_size: int = 20) -> LiveStockSearchPage:
        normalized = query.strip().casefold()
        matches = [
            item for item in self._domestic_stock_universe()
            if (not normalized
                or normalized in str(item.get("name", "")).casefold()
                or normalized in str(item.get("symbol", "")).casefold())
            and (market == "ALL" or str(item.get("market", "")) == market)
            and (
                security_type == "ALL"
                or (security_type == "COMMON" and bool(item.get("isCommonShare", False)))
                or str(item.get("securityType", "")) == security_type
            )
        ]
        _, rankings = self._domestic_trading_amount_rankings()
        ranking_by_symbol = {str(item.get("symbol", "")): item for item in rankings}
        if sort == "NAME":
            matches.sort(key=lambda item: (str(item.get("name", "")).casefold(),
                                           str(item.get("symbol", ""))))
        elif sort == "CODE":
            matches.sort(key=lambda item: str(item.get("symbol", "")))
        else:
            matches.sort(key=lambda item: (
                int((ranking_by_symbol.get(str(item.get("symbol", ""))) or {}).get("rank", 1_000_000)),
                str(item.get("name", "")).casefold(),
                str(item.get("symbol", "")),
            ))
        total = len(matches)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        matches = matches[start:start + page_size]
        prices = {}
        details = {}
        if matches:
            symbols = [str(item.get("symbol", "")) for item in matches]
            price_query = urlencode({"symbols": ",".join(symbols)})
            payload = self._authorized_json_request(f"{self.base_url}/api/v1/prices?{price_query}")
            result = payload.get("result")
            if not isinstance(result, list):
                raise TossApiError("토스 API 현재가 응답 형식이 예상과 다릅니다.")
            prices = {str(item.get("symbol", "")): item for item in result}
            detail_payload = self._authorized_json_request(
                f"{self.base_url}/api/v1/stocks?{price_query}"
            )
            detail_rows = detail_payload.get("result")
            if not isinstance(detail_rows, list):
                raise TossApiError("토스 API 종목 기본정보 응답 형식이 예상과 다릅니다.")
            details = {str(item.get("symbol", "")): item for item in detail_rows}
            with self._stock_info_lock:
                expires_at = monotonic() + 43200
                for symbol, detail in details.items():
                    self._stock_info_cache[symbol] = (expires_at, detail)
        results = []
        for item in matches:
            symbol = str(item.get("symbol", ""))
            ranking = ranking_by_symbol.get(symbol)
            current_price = (self._decimal(prices[symbol].get("lastPrice"))
                             if symbol in prices else None)
            shares = self._decimal((details.get(symbol) or {}).get("sharesOutstanding"))
            results.append(LiveStockSearchResult(
                symbol=symbol,
                name=str(item.get("name", "")),
                market=str(item.get("market", "")),
                security_type=str(item.get("securityType", "")),
                is_common_share=bool(item.get("isCommonShare", False)),
                currency=str((prices.get(symbol) or {}).get("currency", "KRW")),
                price=current_price,
                change_rate_percent=(self._percent(((ranking or {}).get("price") or {}).get("changeRate"))
                                     if ranking else None),
                trading_amount_rank=(int(ranking.get("rank")) if ranking else None),
                trading_amount=(self._decimal(ranking.get("tradingAmount")) if ranking else None),
                market_cap=(current_price * shares if current_price is not None and shares else None),
            ))
        return LiveStockSearchPage(
            query=query,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
            results=results,
        )

    def domestic_stock(self, symbol: str) -> dict | None:
        normalized = symbol.strip().upper()
        return next(
            (item for item in self._domestic_stock_universe()
             if str(item.get("symbol", "")).upper() == normalized),
            None,
        )

    def _domestic_stock_info(self, symbol: str) -> dict:
        normalized = symbol.strip().upper()
        now = monotonic()
        cached = self._stock_info_cache.get(normalized)
        if cached is not None and now < cached[0]:
            return cached[1]
        query = urlencode({"symbols": normalized})
        payload = self._authorized_json_request(f"{self.base_url}/api/v1/stocks?{query}")
        rows = payload.get("result")
        if not isinstance(rows, list):
            raise TossApiError("토스 API 종목 기본정보 응답 형식이 예상과 다릅니다.")
        detail = rows[0] if rows else {}
        with self._stock_info_lock:
            self._stock_info_cache[normalized] = (monotonic() + 43200, detail)
        return detail

    @staticmethod
    def _chart_period(period: str, *, detailed: bool = False) -> tuple[str, int]:
        periods = {
            "1D": ("1m", 390 if detailed else 200),
            "1W": (("1m", 1950) if detailed else ("1d", 7)),
            "1M": (("1m", 8580) if detailed else ("1d", 22)),
            "3M": ("1d", 60),
            "1Y": ("1d", 200),
        }
        return periods.get(period.upper(), periods["1M"])

    def _domestic_candles(self, symbol: str, interval: str, count: int) -> list[LiveStockCandle]:
        normalized = symbol.strip().upper()
        cache_key = (normalized, interval)
        now = monotonic()
        cached = self._candle_cache.get(cache_key)
        if cached is not None and now < cached[0] and cached[1] >= count:
            return cached[2][-count:]
        with self._candle_lock:
            now = monotonic()
            cached = self._candle_cache.get(cache_key)
            if cached is not None and now < cached[0] and cached[1] >= count:
                return cached[2][-count:]
        # 네트워크 호출 중에는 전체 차트 캐시 잠금을 잡지 않는다. 서로 다른 종목은
        # 동시에 받아올 수 있어 목록의 미니 차트가 훨씬 빨리 채워진다.
        raw_rows: list[dict] = []
        before: str | None = None
        seen_cursors: set[str] = set()
        max_pages = max(1, (count + 199) // 200)
        for page_index in range(max_pages):
            query_values = {"symbol": normalized, "interval": interval}
            if before:
                query_values["before"] = before
            query = urlencode(query_values)
            payload = self._authorized_json_request(f"{self.base_url}/api/v1/candles?{query}")
            result = payload.get("result")
            rows = result.get("candles") if isinstance(result, dict) else None
            if not isinstance(rows, list):
                raise TossApiError("토스 API 차트 응답 형식이 예상과 다릅니다.")
            raw_rows.extend(rows)
            if len(raw_rows) >= count:
                break
            next_before = result.get("nextBefore") if isinstance(result, dict) else None
            if not next_before or next_before in seen_cursors:
                break
            seen_cursors.add(str(next_before))
            before = str(next_before)
            if page_index + 1 < max_pages:
                sleep(0.08)
        candles = [LiveStockCandle(
            timestamp=item.get("timestamp"),
            open_price=self._decimal(item.get("openPrice")),
            high_price=self._decimal(item.get("highPrice")),
            low_price=self._decimal(item.get("lowPrice")),
            close_price=self._decimal(item.get("closePrice")),
            volume=self._decimal(item.get("volume")),
        ) for item in reversed(raw_rows)]
        cache_seconds = 1800 if interval == "1m" and count > 2000 else 300
        with self._candle_lock:
            self._candle_cache[cache_key] = (monotonic() + cache_seconds, count, candles)
        return candles[-count:]

    def domestic_sparklines(
        self, symbols: list[str], count: int | None = None, period: str = "1D"
    ) -> dict[str, list[Decimal]]:
        interval, period_count = self._chart_period(period)
        candle_count = count or period_count
        lines: dict[str, list[Decimal]] = {}
        for index, symbol in enumerate(symbols):
            if index:
                sleep(0.06)
            try:
                lines[symbol] = [
                    item.close_price for item in self._domestic_candles(symbol, interval, candle_count)
                ]
            except TossApiError as exc:
                if exc.status_code not in {400, 404}:
                    raise
                lines[symbol] = []
        return lines

    def domestic_stock_detail(self, symbol: str, period: str = "1D") -> LiveStockDetail:
        stock = self.domestic_stock(symbol)
        if not stock:
            raise TossApiError("국내 종목을 찾지 못했습니다.", status_code=404,
                               error_code="stock-not-found")
        normalized = str(stock.get("symbol", "")).upper()
        price_query = urlencode({"symbols": normalized})
        price_payload = self._authorized_json_request(f"{self.base_url}/api/v1/prices?{price_query}")
        price_rows = price_payload.get("result")
        if not isinstance(price_rows, list):
            raise TossApiError("토스 API 현재가 응답 형식이 예상과 다릅니다.")
        price = price_rows[0] if price_rows else {}
        stock_info = self._domestic_stock_info(normalized)
        interval, candle_count = self._chart_period(period, detailed=True)
        candles = self._domestic_candles(normalized, interval, candle_count)
        current_price = self._decimal(price.get("lastPrice")) if price.get("lastPrice") is not None else None
        _, rankings = self._domestic_trading_amount_rankings()
        ranking = next((item for item in rankings if str(item.get("symbol", "")) == normalized), None)
        ranking_change_rate = ((ranking or {}).get("price") or {}).get("changeRate")
        change_rate = (self._percent(ranking_change_rate)
                       if ranking_change_rate is not None else None)
        if change_rate is None:
            daily_candles = (candles if interval == "1d"
                             else self._domestic_candles(normalized, "1d", 2))
            if len(daily_candles) >= 2 and daily_candles[-2].close_price:
                latest = current_price if current_price is not None else daily_candles[-1].close_price
                change_rate = (latest / daily_candles[-2].close_price - Decimal("1")) * Decimal("100")
        shares_outstanding = (self._decimal(stock_info.get("sharesOutstanding"))
                              if stock_info.get("sharesOutstanding") is not None else None)
        return LiveStockDetail(
            symbol=normalized,
            name=str(stock.get("name", "")),
            market=str(stock.get("market", "")),
            security_type=str(stock.get("securityType", "")),
            is_common_share=bool(stock.get("isCommonShare", False)),
            currency=str(price.get("currency", "KRW")),
            price=current_price,
            change_rate_percent=change_rate,
            trading_amount_rank=(int(ranking.get("rank")) if ranking else None),
            market_cap=(current_price * shares_outstanding
                        if current_price is not None and shares_outstanding else None),
            shares_outstanding=shares_outstanding,
            trading_amount=(self._decimal(ranking.get("tradingAmount")) if ranking else None),
            trading_volume=(self._decimal(ranking.get("tradingVolume")) if ranking else None),
            candles=candles,
        )

    def favorite_stock_snapshots(self, favorites: list[dict]) -> list[LiveFavoriteStock]:
        if not favorites:
            return []
        symbols = [str(item["symbol"]) for item in favorites]
        price_query = urlencode({"symbols": ",".join(symbols)})
        payload = self._authorized_json_request(f"{self.base_url}/api/v1/prices?{price_query}")
        price_result = payload.get("result")
        if not isinstance(price_result, list):
            raise TossApiError("토스 API 관심 종목 현재가 응답 형식이 예상과 다릅니다.")
        prices = {str(item.get("symbol", "")): item for item in price_result}
        _, rankings = self._domestic_trading_amount_rankings()
        ranking_by_symbol = {str(item.get("symbol", "")): item for item in rankings}
        snapshots = []
        for favorite in favorites:
            symbol = str(favorite["symbol"])
            price = prices.get(symbol)
            ranking = ranking_by_symbol.get(symbol)
            ranking_price = (ranking or {}).get("price") or {}
            snapshots.append(LiveFavoriteStock(
                symbol=symbol,
                name=str(favorite["name"]),
                market=str(favorite["market"]),
                security_type=str(favorite["security_type"]),
                is_common_share=bool(favorite["is_common_share"]),
                created_at=favorite["created_at"],
                currency=str((price or {}).get("currency", "KRW")),
                price=(self._decimal(price.get("lastPrice")) if price else None),
                change_rate_percent=(self._percent(ranking_price.get("changeRate"))
                                     if ranking else None),
                trading_amount_rank=(int(ranking.get("rank")) if ranking else None),
            ))
        return snapshots

    def test_connection(self) -> TossConnectionResult:
        accounts = self.accounts()
        return TossConnectionResult(True, len(accounts), "토큰 발급과 계좌 목록 조회에 성공했습니다.")

    def domestic_trading_amount_top(self, count: int = 10) -> tuple[list[Stock], dict[str, Decimal]]:
        query = urlencode({"type": "MARKET_TRADING_AMOUNT", "marketCountry": "KR",
                           "duration": "1d", "excludeInvestmentCaution": "true", "count": count})
        ranking_payload = self._authorized_json_request(f"{self.base_url}/api/v1/rankings?{query}")
        rankings = (ranking_payload.get("result") or {}).get("rankings")
        if not isinstance(rankings, list) or not rankings:
            raise TossApiError("토스 API 거래대금 순위 응답 형식이 예상과 다릅니다.")
        rankings = sorted(rankings, key=lambda item: item.get("rank", 999))[:count]
        symbols = [str(item["symbol"]) for item in rankings]
        info_query = urlencode({"symbols": ",".join(symbols)})
        info_payload = self._authorized_json_request(f"{self.base_url}/api/v1/stocks?{info_query}")
        info = info_payload.get("result")
        if not isinstance(info, list):
            raise TossApiError("토스 API 종목 정보 응답 형식이 예상과 다릅니다.")
        names = {str(item["symbol"]): str(item["name"]) for item in info}
        stocks = [Stock(symbol=symbol, name=names.get(symbol, symbol)) for symbol in symbols]
        prices = {str(item["symbol"]): self._decimal(
            (item.get("price") or {}).get("lastPrice") if isinstance(item.get("price"), dict)
            else item.get("price")) for item in rankings}
        return stocks, prices

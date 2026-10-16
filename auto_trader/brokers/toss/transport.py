"""Toss transport methods. The facade owns shared locks and caches."""

import gzip
import json
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from .types import TossApiError


class TossTransport:
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


    @staticmethod
    def _request_group(request: Request) -> str:
        path = urlparse(request.full_url).path
        method = request.get_method()
        if path == "/oauth2/token":
            return "AUTH"
        if path == "/api/v1/accounts":
            return "ACCOUNT"
        if path == "/api/v1/holdings":
            return "ASSET"
        if path in {"/api/v1/buying-power", "/api/v1/sellable-quantity", "/api/v1/commissions"}:
            return "ORDER_INFO"
        if path == "/api/v1/orders" and method == "GET" or (path.startswith("/api/v1/orders/") and method == "GET"):
            return "ORDER_HISTORY"
        if path.startswith("/api/v1/orders"):
            return "ORDER"
        if path == "/api/v1/stocks/all":
            return "STOCK_ALL"
        if path == "/api/v1/stocks" or path.startswith("/api/v1/stocks/"):
            return "STOCK"
        if path.startswith("/api/v1/market-calendar/") or path == "/api/v1/exchange-rate":
            return "MARKET_INFO"
        if path == "/api/v1/candles":
            return "MARKET_DATA_CHART"
        if path == "/api/v1/market-indicators/prices":
            return "MARKET_INDICATOR"
        if path.startswith("/api/v1/market-indicators/") and path.endswith("/candles"):
            return "MARKET_INDICATOR_CHART"
        if path in {"/api/v1/prices", "/api/v1/orderbook", "/api/v1/trades", "/api/v1/price-limits"}:
            return "MARKET_DATA"
        if path == "/api/v1/rankings":
            return "RANKING"
        return "OTHER"


    def _group_lock(self, group: str) -> Lock:
        with self._group_locks_lock:
            return self._group_locks.setdefault(group, Lock())


    def _json_request(self, request: Request) -> dict:
        # 조회만 제한적으로 재시도한다. 주문 생성/취소 요청은 재전송하지 않는다.
        group = self._request_group(request)
        deadline = monotonic() + 20
        for attempt in range(3):
            try:
                return self._json_request_once(request)
            except TossApiError as exc:
                if exc.status_code != 429 or request.get_method() != "GET" or attempt == 2:
                    raise
                remaining = max(0.1, self._group_rate_limit_until.get(group, 0) - monotonic())
                if monotonic() + remaining > deadline:
                    raise
                self._request_waiter.wait(remaining)


    def _json_request_once(self, request: Request) -> dict:
        group = self._request_group(request)
        with self._group_lock(group):
            remaining = self._group_rate_limit_until.get(group, 0.0) - monotonic()
            if remaining > 0:
                raise TossApiError(
                    f"토스 API {group} 그룹 요청 한도 대기 중입니다. 약 {max(1, round(remaining))}초 후 다시 시도해 주세요.",
                    status_code=429, error_code="rate-limit-exceeded",
                )
            request_delay = self._group_intervals.get(group, 0.1) - (
                monotonic() - self._group_last_request_at.get(group, 0.0)
            )
            if request_delay > 0:
                self._request_waiter.wait(request_delay)
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = self._read_json(response)
                    headers = response.headers
                self._group_last_request_at[group] = monotonic()
                try:
                    limit = float(headers.get("X-RateLimit-Limit", "0"))
                    if limit > 0:
                        self._group_intervals[group] = max(self._group_intervals.get(group, 0.1), 1 / limit + 0.02)
                except (TypeError, ValueError):
                    pass
                return payload
            except HTTPError as exc:
                self._group_last_request_at[group] = monotonic()
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
                if exc.code == 429:
                    try:
                        retry_after = float(exc.headers.get("Retry-After", "0"))
                    except (TypeError, ValueError):
                        retry_after = 0
                    try:
                        reset_after = float(exc.headers.get("X-RateLimit-Reset", "0"))
                    except (TypeError, ValueError):
                        reset_after = 0
                    # Reset은 상대 초 또는 Unix 시각으로 전달될 수 있다.
                    if reset_after > 1_000_000_000:
                        reset_after = max(0, reset_after - datetime.now(timezone.utc).timestamp())
                    retry_after = max(1.0, retry_after, reset_after) + 0.1
                    self._group_intervals[group] = min(2.0, self._group_intervals.get(group, 0.1) * 1.5)
                    now = monotonic()
                    self._group_rate_limit_until[group] = max(
                        self._group_rate_limit_until.get(group, 0.0), now + retry_after
                    )
                raise TossApiError(message, status_code=exc.code, error_code=code) from exc
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                self._last_request_at = monotonic()
                raise TossApiError("토스 API 서버에 연결하지 못했습니다.") from exc


    def _invalidate_access_token(self, rejected_token: str) -> None:
        with self._token_lock:
            if self._access_token == rejected_token:
                self._access_token = None
                self._token_expires_at = None


    def _authorized_json_request(
        self, url: str, *, headers: dict[str, str] | None = None,
        method: str = "GET", body: dict | None = None,
    ) -> dict:
        token = self.access_token()

        def request(access_token: str) -> Request:
            request_headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
            request_headers.update(headers or {})
            data = None
            if body is not None:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                request_headers["Content-Type"] = "application/json"
            return Request(url, data=data, method=method, headers=request_headers)

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

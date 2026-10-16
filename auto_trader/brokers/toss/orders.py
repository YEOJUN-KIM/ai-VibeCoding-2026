"""Toss orders methods. The facade owns shared locks and caches."""

import re
from urllib.parse import urlencode
from decimal import Decimal
from .types import TossApiError


class TossOrders:
    @staticmethod
    def _order_decimal(value: Decimal) -> str:
        return format(value, "f")


    def _require_live_trading(self) -> None:
        if not self.live_trading_enabled:
            raise TossApiError(
                "실제 주문 안전 잠금이 켜져 있습니다. LIVE_TRADING_ENABLED=true로 명시적으로 활성화해야 합니다."
            )


    def create_limit_order(
        self, *, symbol: str, side: str, quantity: Decimal, price: Decimal,
        client_order_id: str,
    ) -> dict:
        """안전 범위를 국내 주식 정수 수량·DAY 지정가 주문으로 제한한다."""
        self._require_live_trading()
        symbol = symbol.strip().upper()
        side = side.strip().upper()
        if not re.fullmatch(r"\d{6}", symbol):
            raise ValueError("현재 실제 주문 연결은 국내 주식 6자리 종목코드만 지원합니다.")
        if side not in {"BUY", "SELL"}:
            raise ValueError("주문 방향은 BUY 또는 SELL이어야 합니다.")
        if quantity <= 0 or quantity != quantity.to_integral_value():
            raise ValueError("현재 실제 주문 연결은 1주 이상의 정수 수량만 지원합니다.")
        if price <= 0:
            raise ValueError("지정가는 0보다 커야 합니다.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", client_order_id):
            raise ValueError("clientOrderId는 영문, 숫자, -, _ 조합으로 36자 이하여야 합니다.")
        account_seq = str(self.selected_account()["accountSeq"])
        payload = self._authorized_json_request(
            f"{self.base_url}/api/v1/orders",
            method="POST",
            headers={"X-Tossinvest-Account": account_seq},
            body={
                "symbol": symbol,
                "side": side,
                "orderType": "LIMIT",
                "quantity": self._order_decimal(quantity),
                "price": self._order_decimal(price),
                "timeInForce": "DAY",
                "clientOrderId": client_order_id,
                "confirmHighValueOrder": False,
            },
        )
        result = payload.get("result")
        if not isinstance(result, dict) or not result.get("orderId"):
            raise TossApiError("토스 API 주문 생성 응답 형식이 예상과 다릅니다.")
        return result


    def order_detail(self, order_id: str) -> dict:
        account_seq = str(self.selected_account()["accountSeq"])
        payload = self._authorized_json_request(
            f"{self.base_url}/api/v1/orders/{order_id}",
            headers={"X-Tossinvest-Account": account_seq},
        )
        result = payload.get("result")
        if not isinstance(result, dict) or not result.get("orderId"):
            raise TossApiError("토스 API 주문 상세 응답 형식이 예상과 다릅니다.")
        return result


    def orders(self, status: str, *, from_date: str | None = None, to_date: str | None = None) -> list[dict]:
        status = status.upper()
        if status not in {"OPEN", "CLOSED"}:
            raise ValueError("주문 목록 상태는 OPEN 또는 CLOSED여야 합니다.")
        account_seq = str(self.selected_account()["accountSeq"])
        params: dict[str, str | int] = {"status": status, "limit": 100}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        payload = self._authorized_json_request(
            f"{self.base_url}/api/v1/orders?{urlencode(params)}",
            headers={"X-Tossinvest-Account": account_seq},
        )
        result = payload.get("result")
        orders = result.get("orders") if isinstance(result, dict) else None
        if not isinstance(orders, list):
            raise TossApiError("토스 API 주문 목록 응답 형식이 예상과 다릅니다.")
        return orders


    def cancel_order(self, order_id: str) -> dict:
        self._require_live_trading()
        account_seq = str(self.selected_account()["accountSeq"])
        payload = self._authorized_json_request(
            f"{self.base_url}/api/v1/orders/{order_id}/cancel",
            method="POST",
            headers={"X-Tossinvest-Account": account_seq},
            body={},
        )
        result = payload.get("result")
        if not isinstance(result, dict) or not result.get("orderId"):
            raise TossApiError("토스 API 주문 취소 응답 형식이 예상과 다릅니다.")
        return result

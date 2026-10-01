"""실제 현재가와 완료된 1분봉을 읽는 모의매매 전용 입력. 주문 API는 사용하지 않는다."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal

from .toss import TossApiError, TossClient


class PaperPriceFeed:
    def __init__(self, client: TossClient):
        self.client = client
        self._bars_cache = {}
        self.unavailable = {}

    def read(self, symbols: list[str], valuation_symbols: list[str], period: int, now: datetime):
        prices = self.client.current_prices(list(dict.fromkeys(symbols + valuation_symbols)))
        def read_bars(symbol):
            price = prices.get(symbol)
            if price is None or not price.is_finite() or price <= 0:
                raise TossApiError(f"{symbol}: 유효한 현재가가 없습니다.")
            cached = self._bars_cache.get(symbol, [])
            latest_completed = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
            if len(cached) >= period and cached[-1].timestamp >= latest_completed:
                rows = cached
            else:
                rows = self.client._domestic_candles(symbol, "1m", period + 2, cache_seconds=5)
            completed = {}
            for row in rows:
                timestamp = row.timestamp
                if timestamp.tzinfo is None:
                    raise TossApiError(f"{symbol}: 봉 시각에 시간대 정보가 없습니다.")
                if timestamp + timedelta(minutes=1) <= now:
                    if not row.close_price.is_finite() or row.close_price <= Decimal(0):
                        raise TossApiError(f"{symbol}: 유효하지 않은 봉 가격입니다.")
                    completed[timestamp] = row
            bars = sorted(completed.values(), key=lambda row: row.timestamp)
            if not bars or now - bars[-1].timestamp > timedelta(minutes=3):
                raise TossApiError(f"{symbol}: 최신 완료 봉이 없습니다. 휴장·시세 지연 여부를 확인하세요.")
            if not any(row.volume > 0 for row in bars[-3:]):
                raise TossApiError(f"{symbol}: 최근 거래량이 없어 모의 체결을 대기합니다.")
            self._bars_cache[symbol] = bars
            return symbol, bars[-period:]

        with ThreadPoolExecutor(max_workers=min(4, max(1, len(symbols)))) as executor:
            futures = {symbol: executor.submit(read_bars, symbol) for symbol in symbols}
            candles, unavailable = {}, {}
            for symbol, future in futures.items():
                try:
                    _, bars = future.result()
                    candles[symbol] = bars
                except TossApiError as exc:
                    unavailable[symbol] = str(exc)
            self.unavailable = unavailable
        return prices, candles

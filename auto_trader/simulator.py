"""토스증권 API 연결 전 사용하는 가상 국내주식 시세."""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from random import Random

from .models import Quote, Stock


SAMPLE_STOCKS = (
    Stock(symbol="005930", name="삼성전자"),
    Stock(symbol="000660", name="SK하이닉스"),
    Stock(symbol="005380", name="현대차"),
    Stock(symbol="035420", name="NAVER"),
    Stock(symbol="035720", name="카카오"),
    Stock(symbol="005490", name="POSCO홀딩스"),
    Stock(symbol="051910", name="LG화학"),
    Stock(symbol="006400", name="삼성SDI"),
    Stock(symbol="105560", name="KB금융"),
    Stock(symbol="055550", name="신한지주"),
)

INITIAL_PRICES = {
    "005930": Decimal("70000"),
    "000660": Decimal("180000"),
    "005380": Decimal("240000"),
    "035420": Decimal("210000"),
    "035720": Decimal("45000"),
    "005490": Decimal("350000"),
    "051910": Decimal("400000"),
    "006400": Decimal("350000"),
    "105560": Decimal("80000"),
    "055550": Decimal("50000"),
}


class MarketSimulator:
    def __init__(self, seed: int = 2026, symbols: tuple[str, ...] | None = None) -> None:
        self._random = Random(seed)
        selected = symbols or tuple(stock.symbol for stock in SAMPLE_STOCKS)
        known_stocks = {stock.symbol: stock for stock in SAMPLE_STOCKS}
        unknown = set(selected) - set(known_stocks)
        if unknown:
            raise ValueError(f"지원하지 않는 가상 종목 코드입니다: {', '.join(sorted(unknown))}")
        self._stocks = {symbol: known_stocks[symbol] for symbol in selected}
        self._prices = {symbol: INITIAL_PRICES[symbol] for symbol in selected}

    def stocks(self) -> list[Stock]:
        return list(self._stocks.values())

    def replace_stocks(self, stocks: list[Stock], prices: dict[str, Decimal]) -> None:
        if not stocks or any(stock.symbol not in prices for stock in stocks):
            raise ValueError("가상시장 종목과 초기 가격이 필요합니다.")
        self._stocks = {stock.symbol: stock for stock in stocks}
        self._prices = {stock.symbol: Decimal(prices[stock.symbol]) for stock in stocks}

    def has_symbol(self, symbol: str) -> bool:
        return symbol in self._stocks

    def quote(self, symbol: str, *, move: bool = False) -> Quote:
        if symbol not in self._stocks:
            raise KeyError(symbol)

        if move:
            change_rate = Decimal(str(self._random.uniform(-0.005, 0.005)))
            next_price = self._prices[symbol] * (Decimal("1") + change_rate)
            self._prices[symbol] = next_price.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        stock = self._stocks[symbol]
        return Quote(
            symbol=stock.symbol,
            name=stock.name,
            price=self._prices[symbol],
            timestamp=datetime.now().astimezone(),
        )

    def quotes(self, *, move: bool = False) -> list[Quote]:
        return [self.quote(symbol, move=move) for symbol in self._stocks]

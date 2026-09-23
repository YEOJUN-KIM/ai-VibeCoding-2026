"""서버 프로세스가 살아 있는 동안만 유지되는 모의 계좌."""

from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from threading import RLock
from uuid import uuid4

from .database import connect, initialize
from .models import Account, Order, OrderRequest, OrderSide, OrderStatus, Position
from .simulator import MarketSimulator, SAMPLE_STOCKS


class PaperBroker:
    def __init__(self, market: MarketSimulator, initial_cash=Decimal("10000000"),
                 account_name="paper-default", fee_rate=Decimal("0"),
                 sell_tax_rate=Decimal("0")):
        for rate in (fee_rate, sell_tax_rate):
            if not rate.is_finite() or not Decimal(0) <= rate <= Decimal(1):
                raise ValueError("비용률은 0부터 1 사이의 유한한 값이어야 합니다.")
        if fee_rate + sell_tax_rate > 1:
            raise ValueError("수수료와 매도 세금 비율의 합은 1 이하여야 합니다.")
        self.fee_rate = fee_rate
        self.sell_tax_rate = sell_tax_rate
        self.market = market
        self.initial_cash = Decimal(initial_cash)
        self.account_name = account_name
        self.account_id = None
        self.risk_manager = None
        self._lock = RLock()
        self._reset_memory()

    def _reset_memory(self) -> None:
        with self._lock:
            self._cash = self.initial_cash
            self._positions: dict[str, dict[str, Decimal | int]] = {}
            self._orders: list[Order] = []
            self._requests: dict[str, Order] = {}
            self._next_order_id = 1
            self._total_fees = Decimal(0)
            self._total_taxes = Decimal(0)

    def initialize(self):
        """영구 설정의 계좌 키만 보장하고 PAPER 자산은 항상 새로 시작한다."""
        initialize()
        with connect() as conn:
            for stock in SAMPLE_STOCKS:
                conn.execute("INSERT INTO stocks VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                             (stock.symbol, stock.name, stock.market))
            conn.execute("""INSERT INTO accounts(name,mode,initial_cash,cash)
                VALUES (%s,'PAPER',%s,%s) ON CONFLICT(name) DO UPDATE
                SET initial_cash=EXCLUDED.initial_cash,cash=EXCLUDED.cash""",
                (self.account_name, self.initial_cash, self.initial_cash))
            self.account_id = conn.execute("SELECT id FROM accounts WHERE name=%s",
                                           (self.account_name,)).fetchone()["id"]
        self._reset_memory()

    def set_risk_manager(self, risk_manager):
        self.risk_manager = risk_manager
        risk_manager.initialize(self.account_id, self)

    def submit(self, request: OrderRequest, *, signal_id=None) -> Order:
        request_id = request.request_id or str(uuid4())
        with self._lock:
            prior = self._requests.get(request_id)
            if prior:
                if (prior.symbol, prior.side, prior.quantity) != (request.symbol, request.side, request.quantity):
                    raise ValueError("같은 request_id를 다른 주문에 사용할 수 없습니다.")
                return prior
            quote = self.market.quote(request.symbol)
            holding = self._positions.get(request.symbol)
            quantity = int(holding["quantity"]) if holding else 0
            average = Decimal(holding["average_price"]) if holding else Decimal(0)
            amount = quote.price * request.quantity
            fee = (amount * self.fee_rate).quantize(Decimal("1"), rounding=ROUND_DOWN)
            tax = ((amount * self.sell_tax_rate).quantize(Decimal("1"), rounding=ROUND_DOWN)
                   if request.side == OrderSide.SELL else Decimal(0))
            status, message = OrderStatus.FILLED, "가상 체결 완료"
            risk_message = (self.risk_manager.check_buy(symbol=request.symbol, amount=amount, fee=fee)
                            if request.side == OrderSide.BUY and self.risk_manager else None)
            if risk_message:
                status, message = OrderStatus.REJECTED, "위험 한도: " + risk_message
            elif request.side == OrderSide.BUY and amount + fee > self._cash:
                status, message = OrderStatus.REJECTED, "가상 계좌의 주문 가능 금액이 부족합니다."
            elif request.side == OrderSide.SELL and request.quantity > quantity:
                status, message = OrderStatus.REJECTED, "가상 계좌의 보유 수량이 부족합니다."
            order = Order(id=self._next_order_id, symbol=request.symbol, side=request.side,
                          quantity=request.quantity, price=quote.price, status=status,
                          message=message, created_at=datetime.now().astimezone())
            self._next_order_id += 1
            self._orders.append(order)
            self._requests[request_id] = order
            if status == OrderStatus.FILLED:
                buying = request.side == OrderSide.BUY
                remaining = quantity + request.quantity if buying else quantity - request.quantity
                new_average = (average * quantity + amount + fee) / remaining if buying else average
                self._cash += -amount - fee if buying else amount - fee - tax
                if remaining:
                    self._positions[request.symbol] = {"quantity": remaining, "average_price": new_average}
                else:
                    self._positions.pop(request.symbol, None)
                self._total_fees += fee
                self._total_taxes += tax
            return order

    def orders(self):
        with self._lock:
            return list(reversed(self._orders))

    def holding_quantity(self, symbol):
        with self._lock:
            holding = self._positions.get(symbol)
            return int(holding["quantity"]) if holding else 0

    def position_value(self, symbol):
        return self.market.quote(symbol).price * self.holding_quantity(symbol)

    def account(self):
        with self._lock:
            positions = []
            for symbol, holding in self._positions.items():
                quote = self.market.quote(symbol)
                quantity = int(holding["quantity"])
                average = Decimal(holding["average_price"])
                positions.append(Position(symbol=symbol, name=quote.name, quantity=quantity,
                    average_price=average, current_price=quote.price,
                    market_value=quote.price * quantity,
                    unrealized_profit=(quote.price - average) * quantity))
            total_asset = self._cash + sum((p.market_value for p in positions), Decimal(0))
            unrealized = sum((p.unrealized_profit for p in positions), Decimal(0))
            total_profit = total_asset - self.initial_cash
            return Account(cash=self._cash, positions=positions, total_asset=total_asset,
                           initial_cash=self.initial_cash, total_profit=total_profit,
                           unrealized_profit=unrealized, realized_profit=total_profit-unrealized,
                           return_percent=total_profit / self.initial_cash * 100 if self.initial_cash else None,
                           total_fees=self._total_fees, total_taxes=self._total_taxes,
                           fee_rate=self.fee_rate, sell_tax_rate=self.sell_tax_rate)

    def reset_practice(self):
        self._reset_memory()
        if self.risk_manager:
            self.risk_manager.reset_daily_baseline()
        return self.account()

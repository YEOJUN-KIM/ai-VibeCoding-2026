"""서버 프로세스가 살아 있는 동안만 유지되는 모의 계좌."""

from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .database import connect, initialize
from .models import Account, Order, OrderRequest, OrderSide, OrderStatus, Position
from .simulator import MarketSimulator, SAMPLE_STOCKS


class PaperBroker:
    def __init__(self, market: MarketSimulator, initial_cash=Decimal("10000000"),
                 account_name="paper-default", fee_rate=Decimal("0"),
                 sell_tax_rate=Decimal("0"), ignore_min_cash_ratio=False,
                 ignore_daily_order_limit=False, slippage_rate=Decimal("0"),
                 journal_path: Path | None = None):
        for rate in (fee_rate, sell_tax_rate, slippage_rate):
            if not rate.is_finite() or not Decimal(0) <= rate <= Decimal(1):
                raise ValueError("비용률은 0부터 1 사이의 유한한 값이어야 합니다.")
        if fee_rate + sell_tax_rate > 1:
            raise ValueError("수수료와 매도 세금 비율의 합은 1 이하여야 합니다.")
        if slippage_rate >= 1:
            raise ValueError("슬리피지 비율은 1보다 작아야 합니다.")
        self.fee_rate = fee_rate
        self.sell_tax_rate = sell_tax_rate
        self.slippage_rate = slippage_rate
        self.journal_path = journal_path
        self.ignore_min_cash_ratio = bool(ignore_min_cash_ratio)
        self.ignore_daily_order_limit = bool(ignore_daily_order_limit)
        self.market = market
        self.initial_cash = Decimal(initial_cash)
        self.account_name = account_name
        self.account_id = None
        self.risk_manager = None
        self._lock = RLock()
        self._seed_cash = self.initial_cash
        self._seed_positions: dict[str, dict[str, Decimal | int]] = {}
        self._reset_memory()

    def _reset_memory(self) -> None:
        with self._lock:
            self._cash = self._seed_cash
            self._positions = {symbol: dict(position) for symbol, position in self._seed_positions.items()}
            self._orders: list[Order] = []
            self._requests: dict[str, Order] = {}
            self._next_order_id = 1
            self._total_fees = Decimal(0)
            self._total_taxes = Decimal(0)
            self._strategy_positions = {}
            self.run_id = str(uuid4())

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

    def begin_strategy(self, symbols: list[str]) -> Decimal:
        """선택 시점의 보유분을 평가액으로 인수하고 이후 수동 거래와 분리한다."""
        with self._lock:
            run_id = str(uuid4())
            positions = {
                symbol: {"quantity": self.holding_quantity(symbol),
                         "average_price": self.market.quote(symbol).price}
                for symbol in symbols if self.holding_quantity(symbol)
            }
            baseline = sum((p["quantity"] * p["average_price"]
                            for p in positions.values()), Decimal(0))
            self._journal({"event": "STRATEGY_BASELINE", "run_id": run_id,
                           "created_at": datetime.now().astimezone().isoformat(),
                           "positions": {s: {"quantity": p["quantity"], "average_price": str(p["average_price"])}
                                         for s, p in positions.items()},
                           "baseline": str(baseline)})
            self.run_id = run_id
            self._strategy_positions = positions
            return baseline

    def _journal(self, payload: dict) -> None:
        if self.journal_path is not None:
            import json
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.journal_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def strategy_quantity(self, symbol: str) -> int:
        with self._lock:
            return int(self._strategy_positions.get(symbol, {}).get("quantity", 0))

    def strategy_average(self, symbol: str) -> Decimal | None:
        with self._lock:
            return self._strategy_positions.get(symbol, {}).get("average_price")

    def strategy_value(self) -> Decimal:
        with self._lock:
            return sum((self.market.quote(s).price * p["quantity"]
                        for s, p in self._strategy_positions.items()), Decimal(0))

    def exit_proceeds(self, symbol: str, quantity: int, price: Decimal) -> Decimal:
        amount = price * (1 - self.slippage_rate) * quantity
        return amount - (amount * self.fee_rate).quantize(Decimal("1"), rounding=ROUND_DOWN) - (
            amount * self.sell_tax_rate).quantize(Decimal("1"), rounding=ROUND_DOWN)

    def submit(self, request: OrderRequest, *, signal_id=None, source="MANUAL", reason="") -> Order:
        request_id = request.request_id or str(uuid4())
        with self._lock:
            prior = self._requests.get(request_id)
            if prior:
                if (prior.symbol, prior.side, prior.quantity, prior.source) != (request.symbol, request.side, request.quantity, source):
                    raise ValueError("같은 request_id를 다른 주문에 사용할 수 없습니다.")
                return prior
            quote = self.market.quote(request.symbol)
            holding = self._positions.get(request.symbol)
            quantity = int(holding["quantity"]) if holding else 0
            average = Decimal(holding["average_price"]) if holding else Decimal(0)
            execution_price = quote.price * (1 + self.slippage_rate if request.side == OrderSide.BUY
                                             else 1 - self.slippage_rate)
            amount = execution_price * request.quantity
            fee = (amount * self.fee_rate).quantize(Decimal("1"), rounding=ROUND_DOWN)
            tax = ((amount * self.sell_tax_rate).quantize(Decimal("1"), rounding=ROUND_DOWN)
                   if request.side == OrderSide.SELL else Decimal(0))
            status, message = OrderStatus.FILLED, "가상 체결 완료"
            risk_message = (self.risk_manager.check_buy(symbol=request.symbol, amount=amount, fee=fee,
                            ignore_min_cash_ratio=self.ignore_min_cash_ratio,
                            ignore_daily_order_limit=self.ignore_daily_order_limit)
                            if request.side == OrderSide.BUY and self.risk_manager else None)
            if risk_message:
                status, message = OrderStatus.REJECTED, "위험 한도: " + risk_message
            elif request.side == OrderSide.BUY and amount + fee > self._cash:
                status, message = OrderStatus.REJECTED, "가상 계좌의 주문 가능 금액이 부족합니다."
            elif request.side == OrderSide.SELL and request.quantity > quantity:
                status, message = OrderStatus.REJECTED, "가상 계좌의 보유 수량이 부족합니다."
            if status == OrderStatus.FILLED and request.side == OrderSide.SELL and request.quantity > (
                    self.strategy_quantity(request.symbol) if source == "STRATEGY"
                    else quantity - self.strategy_quantity(request.symbol)):
                status, message = OrderStatus.REJECTED, "전략 관리 보유분과 수동 보유분은 분리해서 매도합니다."
            strategy_average = self.strategy_average(request.symbol) or Decimal(0)
            realized = (amount - fee - tax - (strategy_average if source == "STRATEGY" else average) * request.quantity
                        if request.side == OrderSide.SELL and status == OrderStatus.FILLED else None)
            order = Order(id=self._next_order_id, symbol=request.symbol, side=request.side,
                          quantity=request.quantity, price=execution_price, status=status,
                          message=message, created_at=datetime.now().astimezone(),
                          source=source, run_id=self.run_id, reason=reason,
                          fee=fee if status == OrderStatus.FILLED else Decimal(0),
                          tax=tax if status == OrderStatus.FILLED else Decimal(0),
                          slippage=abs(execution_price - quote.price) * request.quantity if status == OrderStatus.FILLED else Decimal(0),
                          realized_profit=realized)
            self._journal({"event": "ORDER", **order.model_dump(mode="json")})
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
                if source == "STRATEGY":
                    strategy_quantity = self.strategy_quantity(request.symbol)
                    strategy_remaining = strategy_quantity + request.quantity if buying else strategy_quantity - request.quantity
                    if strategy_remaining:
                        self._strategy_positions[request.symbol] = {
                            "quantity": strategy_remaining,
                            "average_price": ((strategy_average * strategy_quantity + amount + fee) / strategy_remaining
                                              if buying else strategy_average),
                        }
                    else:
                        self._strategy_positions.pop(request.symbol, None)
            return order

    def orders(self):
        with self._lock:
            return list(reversed(self._orders))

    def holding_quantity(self, symbol):
        with self._lock:
            holding = self._positions.get(symbol)
            return int(holding["quantity"]) if holding else 0

    def position_average_price(self, symbol):
        with self._lock:
            holding = self._positions.get(symbol)
            return Decimal(holding["average_price"]) if holding else None

    def position_value(self, symbol):
        return self.market.quote(symbol).price * self.holding_quantity(symbol)

    def cash_balance(self):
        with self._lock:
            return self._cash

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

    def load_snapshot(self, *, cash: Decimal, positions: list[dict]) -> Account:
        """실제 계좌 값을 복사해 이후 실제 계좌와 분리된 PAPER 기준선을 만든다."""
        if cash < 0:
            raise ValueError("모의계좌 현금은 0원 이상이어야 합니다.")
        seeded: dict[str, dict[str, Decimal | int]] = {}
        market_value = Decimal(0)
        for item in positions:
            symbol = str(item["symbol"])
            quantity = int(Decimal(str(item["quantity"])))
            average = Decimal(str(item["average_price"]))
            current = Decimal(str(item["current_price"]))
            if quantity <= 0 or average < 0 or current <= 0:
                continue
            # 복사 이전의 실계좌 손익을 PAPER의 실현/평가손익에 섞지 않는다.
            seeded[symbol] = {"quantity": quantity, "average_price": current}
            market_value += current * quantity
        with self._lock:
            self._seed_cash = Decimal(cash)
            self._seed_positions = seeded
            self.initial_cash = self._seed_cash + market_value
            self._reset_memory()
        if self.risk_manager:
            self.risk_manager.reset_daily_baseline()
        return self.account()

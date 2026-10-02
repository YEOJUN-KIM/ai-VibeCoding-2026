"""DB에 자산과 주문 상태를 저장하는 모의 계좌."""

from copy import deepcopy
from functools import wraps
import logging
from psycopg.types.json import Jsonb
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .database import connect, initialize
from .models import Account, Order, OrderRequest, OrderSide, OrderStatus, Position, Stock
from .simulator import MarketSimulator, SAMPLE_STOCKS


def persisted(method):
    """Commit account mutations before reporting success; roll back on storage failure."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            before = {key: deepcopy(value) for key, value in self.__dict__.items()
                      if key.startswith('_') and key not in {'_lock', '_pending_journal'}}
            initial_cash, metadata, run_id = self.initial_cash, deepcopy(self.snapshot_metadata), self.run_id
            self._pending_journal = []
            try:
                result = method(self, *args, **kwargs)
                self._save_state()
            except Exception:
                self.__dict__.update(before)
                self.initial_cash, self.snapshot_metadata = initial_cash, metadata
                self.run_id = run_id
                raise
            else:
                pending = self._pending_journal
                self._pending_journal = None
                for payload in pending:
                    try:
                        self._journal(payload)
                    except OSError:
                        logging.getLogger(__name__).exception('PAPER journal append failed; account state is saved in DB')
                return result
            finally:
                self._pending_journal = None
    return wrapped


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
        self.snapshot_metadata = {}
        self._last_strategy_selection = {}
        self._management_scope = "AUTO"
        self._strategy_context = {}
        self._management_symbols = []
        self._management_external_flow = Decimal(0)
        self._pending_journal = None
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
            self._auto_lots = []
            self._managed_lots = []
            self.run_id = str(uuid4())

    def initialize(self):
        """저장된 자산과 주문 상태를 복구한다."""
        initialize()
        with connect() as conn:
            for stock in SAMPLE_STOCKS:
                conn.execute("INSERT INTO stocks VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                             (stock.symbol, stock.name, stock.market))
            conn.execute("""INSERT INTO accounts(name,mode,initial_cash,cash)
                VALUES (%s,'PAPER',%s,%s) ON CONFLICT(name) DO NOTHING""",
                (self.account_name, self.initial_cash, self.initial_cash))
            self.account_id = conn.execute("SELECT id FROM accounts WHERE name=%s",
                                           (self.account_name,)).fetchone()["id"]
            saved = conn.execute("SELECT state FROM paper_account_state WHERE account_id=%s",
                                 (self.account_id,)).fetchone()
        if saved:
            self._restore_state(saved['state'])
        else:
            self._save_state()

    def _state(self):
        def positions(rows):
            return {symbol: {'quantity': int(row['quantity']), 'average_price': str(row['average_price'])}
                    for symbol, row in rows.items()}
        symbols = set(self._positions) | set(self._seed_positions) | set(self._last_strategy_selection.get("symbols", []))
        stocks = {stock.symbol: stock for stock in self.market.stocks()}
        return dict(version=1, cash=str(self._cash), initial_cash=str(self.initial_cash),
                    seed_cash=str(self._seed_cash), positions=positions(self._positions),
                    seed_positions=positions(self._seed_positions),
                    orders=[order.model_dump(mode='json') for order in self._orders],
                    requests={key: order.id for key, order in self._requests.items()},
                    next_order_id=self._next_order_id, fees=str(self._total_fees), taxes=str(self._total_taxes),
                    metadata=self.snapshot_metadata, management_scope=self._management_scope,
                    auto_lots=self._auto_lots, last_strategy_selection=self._last_strategy_selection,
                    stocks={symbol: {'stock': stocks[symbol].model_dump(mode='json'),
                                     'price': str(self.market.quote(symbol).price)} for symbol in symbols})

    def _save_state(self):
        if self.account_id is None:
            return
        with connect() as conn:
            conn.execute("""INSERT INTO paper_account_state(account_id,state) VALUES (%s,%s)
                ON CONFLICT(account_id) DO UPDATE SET state=EXCLUDED.state,updated_at=NOW()""",
                         (self.account_id, Jsonb(self._state())))
            conn.execute('UPDATE accounts SET cash=%s,initial_cash=%s WHERE id=%s',
                         (self._cash, self.initial_cash, self.account_id))

    def _restore_state(self, state):
        if state.get('version') != 1:
            raise ValueError('지원하지 않는 모의계좌 저장 형식입니다.')
        for symbol, item in state['stocks'].items():
            if not self.market.has_symbol(symbol):
                self.market.upsert_stock(Stock.model_validate(item['stock']), Decimal(item['price']))
        def positions(rows):
            return {symbol: {'quantity': int(row['quantity']), 'average_price': Decimal(row['average_price'])}
                    for symbol, row in rows.items()}
        self._cash = Decimal(state['cash'])
        self.initial_cash = Decimal(state['initial_cash'])
        self._seed_cash = Decimal(state['seed_cash'])
        self._positions = positions(state['positions'])
        self._seed_positions = positions(state['seed_positions'])
        self._orders = [Order.model_validate(row) for row in state['orders']]
        by_id = {order.id: order for order in self._orders}
        self._requests = {key: by_id[value] for key, value in state['requests'].items()}
        self._next_order_id = state['next_order_id']
        self._total_fees, self._total_taxes = Decimal(state['fees']), Decimal(state['taxes'])
        self.snapshot_metadata = state.get('metadata', {})
        self._last_strategy_selection = state.get("last_strategy_selection", {})
        # Execution remains stopped; ownership and exit rules survive restart.
        self._management_scope = state.get("management_scope", "AUTO")
        self._auto_lots = state.get("auto_lots", [])
        self._managed_lots = []
        self._strategy_positions = {}

    @property
    def last_strategy_selection(self):
        return deepcopy(self._last_strategy_selection)

    @persisted
    def remember_strategy(self, user_id, strategy_id, symbols):
        self._last_strategy_selection = {"user_id": user_id, "strategy_id": strategy_id,
                                         "symbols": list(symbols)}

    def set_risk_manager(self, risk_manager):
        self.risk_manager = risk_manager
        risk_manager.initialize(self.account_id, self)

    @property
    def management_scope(self):
        return self._management_scope

    def managed_lots(self):
        with self._lock:
            return deepcopy(self._managed_lots)

    def holding_management(self):
        """Return owned quantities, including allocations outside the active scope."""
        with self._lock:
            managed = {lot['id']: lot for lot in self._managed_lots}
            rows = []
            for lot in self._auto_lots:
                rows.append({**deepcopy(lot), 'managed': lot['id'] in managed})
            for lot in self._managed_lots:
                if lot['origin'] == 'MANUAL':
                    rows.append({**deepcopy(lot), 'managed': True})
            for symbol, holding in self._positions.items():
                assigned = sum(row['quantity'] for row in rows if row['symbol'] == symbol)
                if holding['quantity'] > assigned:
                    rows.append(dict(id=f'unassigned:{symbol}', symbol=symbol,
                                     quantity=holding['quantity'] - assigned, origin='MANUAL',
                                     managed=False, context={}, opened_at=None,
                                     average_price=str(holding['average_price'])))
            return rows

    def _sync_allocations(self):
        self._strategy_positions = {}
        for lot in self._managed_lots:
            if not lot['quantity']:
                continue
            row = self._strategy_positions.setdefault(lot['symbol'], {'quantity': 0, 'average_price': Decimal(0)})
            quantity = row['quantity'] + lot['quantity']
            row['average_price'] = (row['average_price'] * row['quantity'] + Decimal(lot['average_price']) * lot['quantity']) / quantity
            row['quantity'] = quantity

    @persisted
    def begin_strategy(self, symbols: list[str], *, context=None, scope=None) -> Decimal:
        if scope is not None:
            if scope not in {'CURRENT', 'AUTO', 'ALL'}:
                raise ValueError('관리 범위를 확인하세요.')
            self._management_scope = scope
        self._strategy_context = deepcopy(context or {})
        self._management_symbols = list(symbols)
        self._management_external_flow = Decimal(0)
        self._managed_lots = []
        for lot in self._auto_lots:
            if self._management_scope != 'CURRENT' or lot['symbol'] in symbols:
                self._managed_lots.append(lot)
        if self._management_scope != 'AUTO':
            for symbol, holding in self._positions.items():
                if self._management_scope == 'CURRENT' and symbol not in symbols:
                    continue
                auto_quantity = sum(lot['quantity'] for lot in self._auto_lots if lot['symbol'] == symbol)
                quantity = holding['quantity'] - auto_quantity
                if quantity:
                    self._managed_lots.append(dict(id=str(uuid4()), symbol=symbol, quantity=quantity,
                        average_price=str(self.market.quote(symbol).price), origin='MANUAL',
                        opened_at=self._strategy_context.get('adopted_at', datetime.now().astimezone().isoformat()), context=deepcopy(self._strategy_context)))
        self._sync_allocations()
        baseline = self.strategy_value()
        self.run_id = str(uuid4())
        self._journal({'event': 'STRATEGY_BASELINE', 'run_id': self.run_id,
                       'management_scope': self._management_scope, 'baseline': str(baseline),
                       'created_at': datetime.now().astimezone().isoformat()})
        return baseline

    def _journal(self, payload: dict) -> None:
        if self._pending_journal is not None:
            self._pending_journal.append(payload)
            return
        if self.journal_path is not None:
            import json
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.journal_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"account_name": self.account_name, **payload}, ensure_ascii=False) + "\n")

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

    @persisted
    def submit(self, request: OrderRequest, *, signal_id=None, source="MANUAL", reason="", lot_id=None, acquired_at=None) -> Order:
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
            selected_lot = next((lot for lot in self._managed_lots if lot['id'] == lot_id and lot['symbol'] == request.symbol), None) if lot_id else None
            if lot_id and (selected_lot is None or request.quantity > selected_lot['quantity']):
                status, message = OrderStatus.REJECTED, '관리 보유분 수량을 확인하세요.'
            strategy_average = Decimal(selected_lot['average_price']) if selected_lot else self.strategy_average(request.symbol) or Decimal(0)
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
                if source == 'STRATEGY' and buying:
                    lot = dict(id=str(uuid4()), symbol=request.symbol, quantity=request.quantity,
                               average_price=str((amount + fee) / request.quantity), origin='AUTO',
                               opened_at=(acquired_at or order.created_at).isoformat(), context=deepcopy(self._strategy_context))
                    self._auto_lots.append(lot)
                    self._managed_lots.append(lot)
                elif buying and source == 'MANUAL' and self._strategy_context and (
                        self._management_scope == 'ALL' or self._management_scope == 'CURRENT' and request.symbol in self._management_symbols):
                    self._management_external_flow += amount + fee
                    self._managed_lots.append(dict(id=str(uuid4()), symbol=request.symbol, quantity=request.quantity,
                        average_price=str((amount + fee) / request.quantity), origin='MANUAL',
                        opened_at=order.created_at.isoformat(), context=deepcopy(self._strategy_context)))
                elif not buying:
                    remaining_sale = request.quantity
                    if source == 'STRATEGY':
                        candidates = [selected_lot] if selected_lot else [lot for lot in self._managed_lots if lot['symbol'] == request.symbol]
                    else:
                        managed_ids = {lot['id'] for lot in self._managed_lots}
                        unmanaged = [lot for lot in self._auto_lots if lot['symbol'] == request.symbol and lot['id'] not in managed_ids]
                        manual_quantity = quantity - sum(lot['quantity'] for lot in self._auto_lots if lot['symbol'] == request.symbol)
                        remaining_sale = max(0, remaining_sale - manual_quantity)
                        candidates = unmanaged
                    for lot in candidates:
                        sold = min(remaining_sale, lot['quantity'])
                        lot['quantity'] -= sold
                        for owned in self._auto_lots:
                            if owned['id'] == lot['id']:
                                owned['quantity'] = lot['quantity']
                        remaining_sale -= sold
                        if not remaining_sale:
                            break
                    self._auto_lots = [lot for lot in self._auto_lots if lot['quantity']]
                    self._managed_lots = [lot for lot in self._managed_lots if lot['quantity']]
                self._sync_allocations()
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

    @persisted
    def reset_practice(self):
        self._reset_memory()
        if self.risk_manager:
            self.risk_manager.reset_daily_baseline()
        return self.account()

    @persisted
    def load_snapshot(self, *, cash: Decimal, positions: list[dict], metadata: dict | None = None) -> Account:
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
            self.snapshot_metadata = metadata or {}
            self._seed_cash = Decimal(cash)
            self._seed_positions = seeded
            self.initial_cash = self._seed_cash + market_value
            self._reset_memory()
        if self.risk_manager:
            self.risk_manager.reset_daily_baseline()
        return self.account()

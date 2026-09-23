"""API 요청과 응답에 사용하는 데이터 모델."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    FILLED = "FILLED"
    REJECTED = "REJECTED"


class Stock(BaseModel):
    symbol: str
    name: str
    market: str = "KRX"


class Quote(BaseModel):
    symbol: str
    name: str
    price: Decimal
    timestamp: datetime


class OrderRequest(BaseModel):
    request_id: str | None = Field(default=None, min_length=1, max_length=100)
    symbol: str
    side: OrderSide
    quantity: int = Field(gt=0, le=1000)


class Order(BaseModel):
    id: int
    symbol: str
    side: OrderSide
    quantity: int
    price: Decimal
    status: OrderStatus
    message: str
    created_at: datetime


class Position(BaseModel):
    symbol: str
    name: str
    quantity: int
    average_price: Decimal
    current_price: Decimal
    market_value: Decimal
    unrealized_profit: Decimal


class Account(BaseModel):
    initial_cash: Decimal
    total_profit: Decimal
    realized_profit: Decimal
    unrealized_profit: Decimal
    return_percent: Decimal | None
    total_fees: Decimal
    total_taxes: Decimal
    fee_rate: Decimal
    sell_tax_rate: Decimal
    mode: str = "PAPER"
    cash: Decimal
    total_asset: Decimal
    positions: list[Position]


class StrategySnapshot(BaseModel):
    symbol: str
    name: str
    price: Decimal
    collected_prices: int
    short_average: Decimal | None = None
    long_average: Decimal | None = None
    trend: str = "COLLECTING"


class SignalEvent(BaseModel):
    symbol: str
    side: OrderSide
    reason: str
    created_at: datetime


class StrategyStatus(BaseModel):
    running: bool
    emergency_stopped: bool
    tick_count: int
    interval_seconds: int
    short_period: int
    long_period: int
    order_quantity: int
    snapshots: list[StrategySnapshot]
    recent_signals: list[SignalEvent]


class StrategySettingsUpdate(BaseModel):
    interval_seconds: int = Field(ge=1, le=60)
    short_period: int = Field(ge=2, le=100)
    long_period: int = Field(ge=3, le=300)
    order_quantity: int = Field(ge=1, le=1000)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class LivePinRequest(BaseModel):
    pin: str = Field(pattern=r"^\d{6}$")


class LivePinStatus(BaseModel):
    configured: bool
    authorized: bool
    authorized_until: datetime | None = None


class TossConnectionStatus(BaseModel):
    configured: bool
    connected: bool
    account_count: int = 0
    message: str


class LiveHolding(BaseModel):
    symbol: str
    name: str
    market_country: str
    currency: str
    quantity: Decimal
    average_purchase_price: Decimal
    last_price: Decimal
    purchase_amount: Decimal
    market_value: Decimal
    profit_loss: Decimal
    profit_rate: Decimal
    daily_profit_loss: Decimal


class LivePortfolio(BaseModel):
    account_label: str
    total_purchase_krw: Decimal
    market_value: Decimal
    profit_loss: Decimal
    profit_rate: Decimal
    daily_profit_loss: Decimal
    daily_profit_rate: Decimal
    holdings: list[LiveHolding]


class LiveBuyingPower(BaseModel):
    account_label: str
    krw_cash_buying_power: Decimal
    usd_cash_buying_power: Decimal


class LiveStockCandidate(BaseModel):
    rank: int
    symbol: str
    name: str
    price: Decimal
    change_rate_percent: Decimal
    max_quantity: int
    reason: str


class LiveCandidateList(BaseModel):
    available_cash_krw: Decimal
    ranked_at: datetime | None = None
    basis: str
    disclaimer: str
    candidates: list[LiveStockCandidate]


class LiveStockSearchResult(BaseModel):
    symbol: str
    name: str
    market: str
    security_type: str
    is_common_share: bool
    currency: str
    price: Decimal | None = None
    change_rate_percent: Decimal | None = None
    trading_amount_rank: int | None = None
    trading_amount: Decimal | None = None
    is_favorite: bool = False


class LiveStockSearchPage(BaseModel):
    query: str
    page: int
    page_size: int
    total: int
    total_pages: int
    results: list[LiveStockSearchResult]


class FavoriteStockCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)


class LiveFavoriteStock(BaseModel):
    symbol: str
    name: str
    market: str
    security_type: str
    is_common_share: bool
    created_at: datetime
    currency: str = "KRW"
    price: Decimal | None = None
    change_rate_percent: Decimal | None = None
    trading_amount_rank: int | None = None


class SessionInfo(BaseModel):
    username: str
    csrf_token: str
    expires_at: datetime


class RiskPreset(StrEnum):
    CONSERVATIVE = "CONSERVATIVE"
    DEFAULT = "DEFAULT"
    CUSTOM = "CUSTOM"


class RiskSettings(BaseModel):
    preset: RiskPreset
    max_order_amount: Decimal = Field(gt=0)
    max_symbol_amount: Decimal = Field(gt=0)
    max_total_investment: Decimal = Field(gt=0)
    min_cash_ratio: Decimal = Field(ge=0, le=100)
    daily_loss_limit: Decimal = Field(gt=0)
    daily_order_limit: int = Field(gt=0, le=10000)
    profit_target: Decimal = Field(gt=0)
    updated_at: datetime | None = None


class RiskSettingsUpdate(BaseModel):
    preset: RiskPreset
    max_order_amount: Decimal | None = Field(default=None, gt=0)
    max_symbol_amount: Decimal | None = Field(default=None, gt=0)
    max_total_investment: Decimal | None = Field(default=None, gt=0)
    min_cash_ratio: Decimal | None = Field(default=None, ge=0, le=100)
    daily_loss_limit: Decimal | None = Field(default=None, gt=0)
    daily_order_limit: int | None = Field(default=None, gt=0, le=10000)
    profit_target: Decimal | None = Field(default=None, gt=0)


class RiskStatus(BaseModel):
    settings: RiskSettings
    invested_amount: Decimal
    cash_ratio: Decimal
    daily_profit: Decimal
    daily_orders: int
    new_buys_allowed: bool
    block_reason: str | None = None

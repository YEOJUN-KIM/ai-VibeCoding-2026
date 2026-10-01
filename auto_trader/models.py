"""API 요청과 응답에 사용하는 데이터 모델."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

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
    source: str = "MANUAL"
    run_id: str | None = None
    reason: str = ""
    fee: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    realized_profit: Decimal | None = None


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


class PaperManagementUpdate(BaseModel):
    scope: Literal["CURRENT", "AUTO", "ALL"]


class PaperWorkspaceStatus(BaseModel):
    account_mode: str = "LIVE_COPY"
    account: Account
    snapshot_ready: bool = False
    snapshot_at: datetime | None = None
    source_account_label: str | None = None
    management_scope: str = "AUTO"
    managed_holdings: list[dict] = Field(default_factory=list)
    holding_management: list[dict] = Field(default_factory=list)
    background_runs: list[dict] = Field(default_factory=list)
    selected_strategy_id: int | None = None
    selected_strategy_name: str | None = None
    selected_symbol: str | None = None
    selected_symbols: list[str] = Field(default_factory=list)


class StrategySnapshot(BaseModel):
    symbol: str
    name: str
    price: Decimal
    collected_prices: int
    data_at: datetime | None = None
    short_average: Decimal | None = None
    long_average: Decimal | None = None
    trend: str = "COLLECTING"
    decision: str = "가격 수집 중"


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
    sizing_mode: str = "QUANTITY"
    order_amount: Decimal = Decimal("100000")
    strategy_basis_amount: Decimal = Decimal("0")
    strategy_profit: Decimal = Decimal("0")
    strategy_return_percent: Decimal | None = None
    data_source: str = "SIMULATED"
    bar_interval: str = "1m"
    last_data_at: datetime | None = None
    data_message: str = "시세 수신 대기"
    realized_profit: Decimal = Decimal("0")
    trading_costs: Decimal = Decimal("0")
    completed_trades: int = 0
    winning_trades: int = 0
    max_drawdown_percent: Decimal = Decimal("0")
    run_id: str | None = None
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


class LiveOrderPreviewRequest(BaseModel):
    symbol: str = Field(pattern=r"^[A-Za-z0-9.\-]{1,12}$")
    side: OrderSide
    mode: str = Field(pattern=r"^(STANDARD|SINGLE)$")
    order_type: str = Field(pattern=r"^(LIMIT|MARKET)$")
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    order_price: Decimal | None = Field(default=None, gt=0)
    trigger_price: Decimal | None = Field(default=None, gt=0)
    expire_date: date | None = None


class LiveOrderPreviewCheck(BaseModel):
    warning: bool = False
    name: str
    passed: bool
    message: str


class LiveOrderPreview(BaseModel):
    approved: bool
    dry_run: bool = True
    symbol: str
    name: str
    side: OrderSide
    mode: str
    order_type: str
    quantity: Decimal
    reference_price: Decimal
    estimated_amount: Decimal
    checks: list[LiveOrderPreviewCheck]
    message: str


class LiveDryRunConfirmRequest(LiveOrderPreviewRequest):
    accept_financial_warnings: bool = False
    client_order_id: str = Field(min_length=8, max_length=36, pattern=r"^[a-zA-Z0-9\-_]+$")


class LiveDryRunOrder(BaseModel):
    id: int
    user_id: int = Field(exclude=True)
    client_order_id: str
    account_label: str
    symbol: str
    stock_name: str
    side: OrderSide
    mode: str
    order_type: str
    quantity: Decimal
    order_price: Decimal | None = None
    trigger_price: Decimal | None = None
    expire_date: date | None = None
    reference_price: Decimal
    estimated_amount: Decimal
    status: str
    dry_run: bool
    external_order_id: str | None = None
    broker_status: str | None = None
    order_source: str = "MANUAL"
    broker_snapshot: dict = Field(default_factory=dict)
    filled_quantity: Decimal = Decimal(0)
    average_filled_price: Decimal | None = None
    filled_amount: Decimal | None = None
    commission: Decimal | None = None
    tax: Decimal | None = None
    reconciliation_status: str = "PENDING"
    last_synced_at: datetime | None = None
    validation_snapshot: dict = Field(default_factory=dict)
    created_at: datetime


class LiveRealOrderConfirmRequest(LiveDryRunConfirmRequest):
    confirmation: str = Field(pattern=r"^실제 주문$")


class DeletedOrderCount(BaseModel):
    deleted: int


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
    auto_managed_quantity: Decimal = Decimal(0)
    existing_quantity: Decimal = Decimal(0)


class LivePortfolio(BaseModel):
    account_label: str
    total_purchase_krw: Decimal
    market_value: Decimal
    profit_loss: Decimal
    profit_rate: Decimal
    daily_profit_loss: Decimal
    daily_profit_rate: Decimal
    daily_profit_reference_date: str
    market_open_today: bool | None = None
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
    previous_close: Decimal | None = None
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
    market_cap: Decimal | None = None
    is_favorite: bool = False


class LiveStockSearchPage(BaseModel):
    query: str
    page: int
    page_size: int
    total: int
    total_pages: int
    results: list[LiveStockSearchResult]


class LiveStockCandle(BaseModel):
    timestamp: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal


class LiveStockDetail(BaseModel):
    candle_interval: str | None = None
    previous_close: Decimal | None = None
    symbol: str
    name: str
    market: str
    security_type: str
    is_common_share: bool
    currency: str
    price: Decimal | None = None
    change_rate_percent: Decimal | None = None
    trading_amount_rank: int | None = None
    market_cap: Decimal | None = None
    shares_outstanding: Decimal | None = None
    trading_amount: Decimal | None = None
    trading_volume: Decimal | None = None
    english_name: str | None = None
    isin_code: str | None = None
    list_date: date | None = None
    listing_status: str | None = None
    nxt_supported: bool | None = None
    krx_trading_suspended: bool | None = None
    nxt_trading_suspended: bool | None = None
    candles: list[LiveStockCandle]


class CompanyMetric(BaseModel):
    label: str
    value: str
    previous_value: str | None = None
    change_rate_percent: Decimal | None = None


class CompanyDisclosure(BaseModel):
    title: str
    receipt_no: str
    receipt_date: str
    submitter: str | None = None


class CompanyFinancialYear(BaseModel):
    year: str
    revenue: Decimal | None = None
    operating_income: Decimal | None = None
    net_income: Decimal | None = None


class LiveCompanyProfile(BaseModel):
    configured: bool
    available: bool
    message: str
    fiscal_year: str | None = None
    corporation_name: str | None = None
    ceo_name: str | None = None
    industry_code: str | None = None
    industry_name: str | None = None
    established_date: str | None = None
    address: str | None = None
    homepage: str | None = None
    fiscal_month: str | None = None
    financials: list[CompanyMetric] = Field(default_factory=list)
    financial_history: list[CompanyFinancialYear] = Field(default_factory=list)
    dividends: list[CompanyMetric] = Field(default_factory=list)
    disclosures: list[CompanyDisclosure] = Field(default_factory=list)


class LongTermFactor(BaseModel):
    key: str
    label: str
    score: int | None = None
    max_score: int
    status: str
    value: str
    detail: str


class LongTermAnalysis(BaseModel):
    symbol: str
    name: str
    market: str
    price: Decimal | None = None
    market_cap: Decimal | None = None
    industry_name: str | None = None
    fiscal_year: str | None = None
    overall_score: int | None = None
    rank: str | None = None
    grade: str
    summary: str
    factors: list[LongTermFactor] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    financial_history: list[CompanyFinancialYear] = Field(default_factory=list)
    per: Decimal | None = None
    pbr: Decimal | None = None
    revenue_growth_percent: Decimal | None = None
    operating_margin_percent: Decimal | None = None
    debt_ratio_percent: Decimal | None = None
    dividend_yield_percent: Decimal | None = None
    price_return_1y_percent: Decimal | None = None
    max_drawdown_1y_percent: Decimal | None = None
    candles: list[LiveStockCandle] = Field(default_factory=list)
    data_message: str
    generated_at: datetime


class LongTermWatchCandidate(BaseModel):
    symbol: str
    name: str
    market: str
    is_favorite: bool = False
    added_manually: bool = False
    created_at: datetime
    analysis: LongTermAnalysis | None = None


class LiveTradingReadiness(BaseModel):
    configured: bool
    enabled: bool
    supported_order: str
    message: str


class LiveStrategyWrite(BaseModel):
    name: str = Field(min_length=2, max_length=50)
    symbol: str = Field(pattern=r"^[A-Za-z0-9.\-]{1,12}$")
    enabled: bool = False
    execution_mode: str = Field(default="DRY_RUN", pattern=r"^(DRY_RUN|LIVE)$")
    short_period: int = Field(default=5, ge=2, le=120)
    long_period: int = Field(default=20, ge=3, le=240)
    order_quantity: int = Field(default=1, ge=1, le=1000)
    sizing_mode: str = Field(default="QUANTITY", pattern=r"^(QUANTITY|AMOUNT)$")
    order_amount: Decimal = Field(default=Decimal("100000"), gt=0, le=1000000000)
    take_profit_rate: Decimal = Field(default=Decimal("5"), gt=0, le=100)
    stop_loss_rate: Decimal = Field(default=Decimal("3"), gt=0, le=100)
    max_holding_days: int = Field(default=20, ge=1, le=3650)
    trading_start: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    trading_end: str = Field(default="15:20", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    daily_order_limit: int = Field(default=0, ge=0, le=100)
    cooldown_minutes: int = Field(default=30, ge=0, le=10080)
    symbols: list[str] = Field(default_factory=list, max_length=20)


class LiveStrategyTarget(BaseModel):
    symbol: str
    stock_name: str


class LiveStrategy(LiveStrategyWrite):
    id: int
    stock_name: str
    created_at: datetime
    updated_at: datetime
    targets: list[LiveStrategyTarget] = Field(default_factory=list)


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


class NewsArticle(BaseModel):
    title: str
    url: str
    source: str
    published_at: datetime


class NewsIssue(BaseModel):
    title: str
    article_count: int
    source_count: int
    latest_at: datetime
    key_topics: list[str]
    articles: list[NewsArticle]


class AiNewsBriefing(BaseModel):
    summary: str
    key_points: list[str]
    opportunity_factors: list[str]
    risk_factors: list[str]
    related_entities: list[str]
    generated_at: datetime
    model: str


class NewsDigest(BaseModel):
    query: str
    generated_at: datetime
    source_name: str
    overview: str
    key_topics: list[str]
    article_count: int
    articles: list[NewsArticle]
    issue_count: int = 0
    issues: list[NewsIssue] = Field(default_factory=list)
    ai_status: str = "not_configured"
    ai_briefing: AiNewsBriefing | None = None


class SessionInfo(BaseModel):
    username: str
    csrf_token: str
    expires_at: datetime
    locked: bool = False


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
    daily_order_limit: int = Field(ge=0, le=10000)
    profit_target: Decimal = Field(gt=0)
    updated_at: datetime | None = None


class RiskSettingsUpdate(BaseModel):
    preset: RiskPreset
    max_order_amount: Decimal | None = Field(default=None, gt=0)
    max_symbol_amount: Decimal | None = Field(default=None, gt=0)
    max_total_investment: Decimal | None = Field(default=None, gt=0)
    min_cash_ratio: Decimal | None = Field(default=None, ge=0, le=100)
    daily_loss_limit: Decimal | None = Field(default=None, gt=0)
    daily_order_limit: int | None = Field(default=None, ge=0, le=10000)
    profit_target: Decimal | None = Field(default=None, gt=0)


class RiskStatus(BaseModel):
    settings: RiskSettings
    invested_amount: Decimal
    cash_ratio: Decimal
    daily_profit: Decimal
    daily_orders: int
    new_buys_allowed: bool
    block_reason: str | None = None
    effective_min_cash_ratio: Decimal = Decimal("0")
    daily_order_limit_disabled: bool = False

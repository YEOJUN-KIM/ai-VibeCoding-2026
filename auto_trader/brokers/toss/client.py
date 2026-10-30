"""Toss client methods. The facade owns shared locks and caches."""

from threading import Event, Lock
from ...settings import settings
from .transport import TossTransport
from .accounts import TossAccounts
from .orders import TossOrders
from .market_data import TossMarketData


class TossClient(TossTransport, TossAccounts, TossOrders, TossMarketData):
    def __init__(self, *, base_url: str | None = None, client_id: str | None = None,
                 client_secret: str | None = None, timeout: float = 10,
                 live_trading_enabled: bool | None = None, account_ref: str | None = None) -> None:
        self.base_url = (base_url or settings.toss_api_base_url).rstrip("/")
        self.client_id = settings.toss_client_id if client_id is None else client_id
        self.client_secret = settings.toss_client_secret if client_secret is None else client_secret
        self.account_ref = settings.toss_account if account_ref is None else account_ref
        self.timeout = timeout
        self.live_trading_enabled = (
            settings.live_trading_enabled if live_trading_enabled is None else live_trading_enabled
        )
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._token_lock = Lock()
        self._group_locks: dict[str, Lock] = {}
        self._group_locks_lock = Lock()
        self._request_waiter = Event()
        self._group_last_request_at: dict[str, float] = {}
        self._group_rate_limit_until: dict[str, float] = {}
        self._group_intervals = {
            "AUTH": 0.21, "ACCOUNT": 1.05, "ASSET": 0.21,
            "STOCK": 0.21, "STOCK_ALL": 1.05, "MARKET_INFO": 0.35,
            "MARKET_DATA": 0.12, "MARKET_DATA_CHART": 0.12, "RANKING": 0.21,
            "MARKET_INDICATOR": 0.12, "MARKET_INDICATOR_CHART": 0.12,
            "ORDER": 0.11, "ORDER_HISTORY": 0.21, "ORDER_INFO": 0.18,
        }
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
            tuple[str, str, int | None] | tuple[str, str, int | None, str], tuple[float, int, list[LiveStockCandle]]
        ] = {}
        self._candle_lock = Lock()
        self._candle_request_locks: dict[tuple[str, str, int | None] | tuple[str, str, int | None, str], Lock] = {}

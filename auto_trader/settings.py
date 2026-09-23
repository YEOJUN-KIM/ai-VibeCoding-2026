"""환경변수와 .env 파일에서 프로그램 설정을 읽는다."""

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _integer(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _decimal(name: str, default: str) -> Decimal:
    return Decimal(os.getenv(name, default))


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_host: str
    app_port: int
    app_mode: str
    app_reload: bool
    session_minutes: int
    login_max_failures: int
    login_lock_minutes: int
    paper_fee_rate: Decimal
    paper_sell_tax_rate: Decimal
    paper_initial_cash: Decimal
    watch_symbols: tuple[str, ...]
    strategy_interval_seconds: int
    strategy_short_period: int
    strategy_long_period: int
    order_quantity: int
    toss_client_id: str
    toss_client_secret: str
    toss_account: str
    toss_allowed_ip: str
    toss_api_base_url: str
    toss_ws_url: str
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str

    @property
    def toss_api_ready(self) -> bool:
        return bool(self.toss_client_id and self.toss_client_secret)

    @property
    def postgres_ready(self) -> bool:
        return bool(self.postgres_db and self.postgres_user and self.postgres_password)


def load_settings() -> Settings:
    _load_env_file(PROJECT_ROOT / ".env")
    mode = os.getenv("APP_MODE", "PAPER").upper()
    if mode != "PAPER":
        raise ValueError("현재 버전은 안전을 위해 APP_MODE=PAPER만 지원합니다.")

    watch_symbols = tuple(
        symbol.strip()
        for symbol in os.getenv(
            "WATCH_SYMBOLS", "005930,000660,005380,035420,035720,005490,051910,006400,105560,055550"
        ).split(",")
        if symbol.strip()
    )

    settings = Settings(
        app_host=os.getenv("APP_HOST", "127.0.0.1"),
        app_port=_integer("APP_PORT", 8000),
        app_mode=mode,
        app_reload=_boolean("APP_RELOAD", False),
        session_minutes=_integer("SESSION_MINUTES", 30),
        login_max_failures=_integer("LOGIN_MAX_FAILURES", 5),
        login_lock_minutes=_integer("LOGIN_LOCK_MINUTES", 15),
        paper_fee_rate=_decimal("PAPER_FEE_RATE", "0.00015"),
        paper_sell_tax_rate=_decimal("PAPER_SELL_TAX_RATE", "0.002"),
        paper_initial_cash=_decimal("PAPER_INITIAL_CASH", "10000000"),
        watch_symbols=watch_symbols,
        strategy_interval_seconds=_integer("STRATEGY_INTERVAL_SECONDS", 2),
        strategy_short_period=_integer("STRATEGY_SHORT_PERIOD", 5),
        strategy_long_period=_integer("STRATEGY_LONG_PERIOD", 20),
        order_quantity=_integer("ORDER_QUANTITY", 1),
        toss_client_id=os.getenv("TOSS_CLIENT_ID", ""),
        toss_client_secret=os.getenv("TOSS_CLIENT_SECRET", ""),
        toss_account=os.getenv("TOSS_ACCOUNT", ""),
        toss_allowed_ip=os.getenv("TOSS_ALLOWED_IP", ""),
        toss_api_base_url=os.getenv("TOSS_API_BASE_URL", "https://openapi.tossinvest.com"),
        toss_ws_url=os.getenv("TOSS_WS_URL", "wss://openapi-ws.tossinvest.com/ws/v1"),
        postgres_host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        postgres_port=_integer("POSTGRES_PORT", 5432),
        postgres_db=os.getenv("POSTGRES_DB", "auto_trader"),
        postgres_user=os.getenv("POSTGRES_USER", "postgres"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", ""),
    )
    if settings.strategy_short_period >= settings.strategy_long_period:
        raise ValueError("STRATEGY_SHORT_PERIOD는 STRATEGY_LONG_PERIOD보다 작아야 합니다.")
    return settings


settings = load_settings()

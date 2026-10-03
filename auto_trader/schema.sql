CREATE TABLE IF NOT EXISTS stocks (
    symbol TEXT PRIMARY KEY, name TEXT NOT NULL, market TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT UNIQUE NOT NULL, mode TEXT NOT NULL CHECK (mode = 'PAPER'),
    initial_cash NUMERIC NOT NULL CHECK (initial_cash >= 0),
    cash NUMERIC NOT NULL CHECK (cash >= 0)
);
CREATE TABLE IF NOT EXISTS positions (
    account_id BIGINT REFERENCES accounts(id), symbol TEXT REFERENCES stocks(symbol),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    average_price NUMERIC NOT NULL CHECK (average_price > 0),
    PRIMARY KEY (account_id, symbol)
);
CREATE TABLE IF NOT EXISTS strategy_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    settings JSONB NOT NULL, started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ, end_reason TEXT
);
CREATE TABLE IF NOT EXISTS signals (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id BIGINT REFERENCES strategy_runs(id), symbol TEXT REFERENCES stocks(symbol),
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')), reason TEXT NOT NULL,
    outcome TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS orders (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id),
    request_id TEXT NOT NULL, signal_id BIGINT REFERENCES signals(id),
    symbol TEXT NOT NULL REFERENCES stocks(symbol), side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    quantity INTEGER NOT NULL CHECK (quantity > 0), price NUMERIC NOT NULL CHECK (price > 0),
    status TEXT NOT NULL CHECK (status IN ('FILLED','REJECTED')), message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(account_id, request_id)
);
CREATE TABLE IF NOT EXISTS executions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id), quantity INTEGER NOT NULL CHECK (quantity > 0),
    price NUMERIC NOT NULL CHECK (price > 0), fee NUMERIC NOT NULL DEFAULT 0,
    tax NUMERIC NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS order_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id), status TEXT NOT NULL,
    message TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cash_transactions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES accounts(id), order_id BIGINT REFERENCES orders(id),
    amount NUMERIC NOT NULL, balance NUMERIC NOT NULL, reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orders_account_time ON orders(account_id, created_at DESC);

CREATE TABLE IF NOT EXISTS admin_users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);
ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS live_pin_hash TEXT;
ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS pin_failed_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS pin_locked_until TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS application_owner (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    user_id BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS user_broker_connections (
    user_id BIGINT PRIMARY KEY REFERENCES admin_users(id) ON DELETE CASCADE,
    source TEXT NOT NULL CHECK(source IN ('ENV','LOCAL')),
    account_ref TEXT NOT NULL DEFAULT '',
    account_label TEXT NOT NULL DEFAULT '',
    identity_hash TEXT UNIQUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS user_id BIGINT REFERENCES admin_users(id) ON DELETE RESTRICT;
CREATE TABLE IF NOT EXISTS user_paper_preferences (
    user_id BIGINT PRIMARY KEY REFERENCES admin_users(id) ON DELETE CASCADE,
    selected_mode TEXT NOT NULL DEFAULT 'LIVE_COPY' CHECK(selected_mode IN ('LIVE_COPY','EXPERIMENT'))
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    token_hash TEXT UNIQUE NOT NULL,
    csrf_token TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS live_authorized_until TIMESTAMPTZ;
ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS auth_sessions_token ON auth_sessions(token_hash);
CREATE INDEX IF NOT EXISTS auth_sessions_expiry ON auth_sessions(expires_at);

CREATE TABLE IF NOT EXISTS favorite_stocks (
    user_id BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    market TEXT NOT NULL,
    security_type TEXT NOT NULL,
    is_common_share BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, symbol)
);
CREATE INDEX IF NOT EXISTS favorite_stocks_user_time
    ON favorite_stocks(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS live_strategies (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT false,
    execution_mode TEXT NOT NULL DEFAULT 'DRY_RUN' CHECK (execution_mode IN ('DRY_RUN','LIVE')),
    short_period INTEGER NOT NULL CHECK (short_period >= 2),
    long_period INTEGER NOT NULL CHECK (long_period > short_period),
    order_quantity INTEGER NOT NULL CHECK (order_quantity > 0),
    take_profit_rate NUMERIC NOT NULL CHECK (take_profit_rate > 0),
    stop_loss_rate NUMERIC NOT NULL CHECK (stop_loss_rate > 0),
    max_holding_days INTEGER NOT NULL CHECK (max_holding_days > 0),
    trading_start TIME NOT NULL,
    trading_end TIME NOT NULL,
    daily_order_limit INTEGER NOT NULL CHECK (daily_order_limit > 0),
    cooldown_minutes INTEGER NOT NULL CHECK (cooldown_minutes >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id,name)
);
CREATE INDEX IF NOT EXISTS live_strategies_user ON live_strategies(user_id,updated_at DESC);
CREATE TABLE IF NOT EXISTS live_strategy_symbols (
    strategy_id BIGINT NOT NULL REFERENCES live_strategies(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (strategy_id, symbol)
);
INSERT INTO live_strategy_symbols(strategy_id,symbol,stock_name,position)
SELECT id,symbol,stock_name,0 FROM live_strategies ON CONFLICT DO NOTHING;
CREATE INDEX IF NOT EXISTS live_strategy_symbols_strategy ON live_strategy_symbols(strategy_id,position);

CREATE TABLE IF NOT EXISTS risk_settings (
    account_id BIGINT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    preset TEXT NOT NULL CHECK (preset IN ('CONSERVATIVE','DEFAULT','CUSTOM')),
    max_order_amount NUMERIC NOT NULL CHECK (max_order_amount > 0),
    max_symbol_amount NUMERIC NOT NULL CHECK (max_symbol_amount > 0),
    max_total_investment NUMERIC NOT NULL CHECK (max_total_investment > 0),
    min_cash_ratio NUMERIC NOT NULL CHECK (min_cash_ratio BETWEEN 0 AND 100),
    daily_loss_limit NUMERIC NOT NULL CHECK (daily_loss_limit > 0),
    daily_order_limit INTEGER NOT NULL CHECK (daily_order_limit > 0),
    profit_target NUMERIC NOT NULL CHECK (profit_target > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 0 means unlimited; preserve positive limits and all existing rows.
ALTER TABLE risk_settings DROP CONSTRAINT IF EXISTS risk_settings_daily_order_limit_check;
ALTER TABLE risk_settings ADD CONSTRAINT risk_settings_daily_order_limit_check CHECK (daily_order_limit >= 0);
ALTER TABLE live_strategies DROP CONSTRAINT IF EXISTS live_strategies_daily_order_limit_check;
ALTER TABLE live_strategies ADD CONSTRAINT live_strategies_daily_order_limit_check CHECK (daily_order_limit >= 0);

CREATE TABLE IF NOT EXISTS risk_daily_snapshots (
    account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE,
    trade_date DATE NOT NULL,
    opening_asset NUMERIC NOT NULL CHECK (opening_asset >= 0),
    PRIMARY KEY (account_id, trade_date)
);

CREATE TABLE IF NOT EXISTS broker_accounts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    broker TEXT NOT NULL CHECK (broker IN ('TOSS')),
    external_account_ref TEXT NOT NULL,
    account_label TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, broker, external_account_ref)
);
CREATE TABLE IF NOT EXISTS live_orders (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE RESTRICT,
    broker_account_id BIGINT NOT NULL REFERENCES broker_accounts(id) ON DELETE RESTRICT,
    client_order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    mode TEXT NOT NULL CHECK (mode IN ('STANDARD','SINGLE')),
    order_type TEXT NOT NULL CHECK (order_type IN ('LIMIT','MARKET')),
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    order_price NUMERIC CHECK (order_price > 0),
    trigger_price NUMERIC CHECK (trigger_price > 0),
    expire_date DATE,
    reference_price NUMERIC NOT NULL CHECK (reference_price > 0),
    estimated_amount NUMERIC NOT NULL CHECK (estimated_amount > 0),
    status TEXT NOT NULL CHECK (status IN ('DRY_RUN_CONFIRMED','CANCELLED')),
    dry_run BOOLEAN NOT NULL DEFAULT true,
    external_order_id TEXT,
    broker_status TEXT,
    order_source TEXT NOT NULL DEFAULT 'MANUAL',
    broker_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    filled_quantity NUMERIC NOT NULL DEFAULT 0,
    average_filled_price NUMERIC,
    filled_amount NUMERIC,
    commission NUMERIC,
    tax NUMERIC,
    reconciliation_status TEXT NOT NULL DEFAULT 'PENDING',
    last_synced_at TIMESTAMPTZ,
    validation_snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, client_order_id)
);
CREATE TABLE IF NOT EXISTS live_order_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    live_order_id BIGINT NOT NULL REFERENCES live_orders(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS broker_accounts_user ON broker_accounts(user_id, id);
CREATE INDEX IF NOT EXISTS live_orders_user_time ON live_orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS live_orders_account_time ON live_orders(broker_account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS live_order_events_order_time ON live_order_events(live_order_id, created_at DESC);
ALTER TABLE live_orders DROP CONSTRAINT IF EXISTS live_orders_status_check;
ALTER TABLE live_orders DROP CONSTRAINT IF EXISTS live_orders_dry_run_check;
ALTER TABLE live_orders ADD COLUMN IF NOT EXISTS external_order_id TEXT;
ALTER TABLE live_orders ADD COLUMN IF NOT EXISTS broker_status TEXT;
ALTER TABLE live_orders ADD COLUMN IF NOT EXISTS order_source TEXT NOT NULL DEFAULT 'MANUAL';
ALTER TABLE live_orders ADD COLUMN IF NOT EXISTS broker_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE live_orders ADD COLUMN IF NOT EXISTS filled_quantity NUMERIC NOT NULL DEFAULT 0;
ALTER TABLE live_orders ADD COLUMN IF NOT EXISTS average_filled_price NUMERIC;
ALTER TABLE live_orders ADD COLUMN IF NOT EXISTS filled_amount NUMERIC;
ALTER TABLE live_orders ADD COLUMN IF NOT EXISTS commission NUMERIC;
ALTER TABLE live_orders ADD COLUMN IF NOT EXISTS tax NUMERIC;
ALTER TABLE live_orders ADD COLUMN IF NOT EXISTS reconciliation_status TEXT NOT NULL DEFAULT 'PENDING';
ALTER TABLE live_orders ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ;
ALTER TABLE live_orders ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE live_orders ADD CONSTRAINT live_orders_status_check
    CHECK (status IN ('DRY_RUN_CONFIRMED','SUBMITTING','SUBMITTED','FILLED','PARTIAL_FILLED',
                      'PENDING','PENDING_CANCEL','CANCELED','REJECTED','UNKNOWN','CANCELLED'));
CREATE UNIQUE INDEX IF NOT EXISTS live_orders_external_order
    ON live_orders(broker_account_id,external_order_id) WHERE external_order_id IS NOT NULL;
ALTER TABLE live_orders DROP CONSTRAINT IF EXISTS live_orders_reconciliation_status_check;
ALTER TABLE live_orders ADD CONSTRAINT live_orders_reconciliation_status_check
    CHECK (reconciliation_status IN ('PENDING','MATCHED','NEEDS_REVIEW','ERROR'));
ALTER TABLE live_orders DROP CONSTRAINT IF EXISTS live_orders_order_source_check;
ALTER TABLE live_orders ADD CONSTRAINT live_orders_order_source_check
    CHECK (order_source IN ('MANUAL','AUTO'));

CREATE TABLE IF NOT EXISTS long_term_analyses (
    symbol TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    market TEXT NOT NULL,
    overall_score INTEGER CHECK (overall_score BETWEEN 0 AND 100),
    rank TEXT CHECK (rank IN ('S','A','B','C','D')),
    grade TEXT NOT NULL,
    analysis JSONB NOT NULL,
    analyzed_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS long_term_analyses_score
    ON long_term_analyses(overall_score DESC, analyzed_at DESC);

CREATE TABLE IF NOT EXISTS long_term_recommendations (
    symbol TEXT PRIMARY KEY REFERENCES long_term_analyses(symbol) ON DELETE CASCADE,
    source_rank INTEGER NOT NULL CHECK (source_rank > 0),
    selected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS long_term_recommendations_time
    ON long_term_recommendations(selected_at DESC);

CREATE TABLE IF NOT EXISTS long_term_watchlist (
    user_id BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    market TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, symbol)
);
CREATE INDEX IF NOT EXISTS long_term_watchlist_user_time
    ON long_term_watchlist(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS long_term_watch_notes (
    user_id BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '' CHECK (char_length(note) <= 2000),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, symbol)
);

CREATE TABLE IF NOT EXISTS long_term_watch_exclusions (
    user_id BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    PRIMARY KEY (user_id, symbol)
);

ALTER TABLE live_strategies ADD COLUMN IF NOT EXISTS sizing_mode TEXT NOT NULL DEFAULT 'QUANTITY' CHECK (sizing_mode IN ('QUANTITY','AMOUNT'));
ALTER TABLE live_strategies ADD COLUMN IF NOT EXISTS order_amount NUMERIC NOT NULL DEFAULT 100000 CHECK (order_amount > 0);

CREATE TABLE IF NOT EXISTS paper_account_state (
    account_id BIGINT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    state JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS strategy_preset_market (
    snapshot_key TEXT PRIMARY KEY,
    week_start DATE NOT NULL,
    candidates JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL
);

-- Immutable source observations for reproducible machine-learning datasets.
CREATE TABLE IF NOT EXISTS ml_collection_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    interval TEXT NOT NULL,
    requested_symbols JSONB NOT NULL,
    requested_count INTEGER NOT NULL CHECK (requested_count > 0),
    status TEXT NOT NULL CHECK (status IN ('RUNNING','COMPLETED','PARTIAL','FAILED')),
    inserted_rows INTEGER NOT NULL DEFAULT 0 CHECK (inserted_rows >= 0),
    duplicate_rows INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_rows >= 0),
    errors JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ml_raw_candles (
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    event_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    open_price NUMERIC NOT NULL CHECK (open_price > 0),
    high_price NUMERIC NOT NULL CHECK (high_price > 0),
    low_price NUMERIC NOT NULL CHECK (low_price > 0),
    close_price NUMERIC NOT NULL CHECK (close_price > 0),
    volume NUMERIC NOT NULL CHECK (volume >= 0),
    collection_run_id BIGINT REFERENCES ml_collection_runs(id) ON DELETE SET NULL,
    raw_payload JSONB NOT NULL,
    PRIMARY KEY (source, symbol, interval, event_at),
    CHECK (event_at <= available_at),
    CHECK (available_at <= collected_at),
    CHECK (high_price >= open_price AND high_price >= close_price AND high_price >= low_price),
    CHECK (low_price <= open_price AND low_price <= close_price AND low_price <= high_price)
);
CREATE INDEX IF NOT EXISTS ml_raw_candles_symbol_time
    ON ml_raw_candles(symbol, interval, event_at);
CREATE INDEX IF NOT EXISTS ml_raw_candles_available
    ON ml_raw_candles(symbol, interval, available_at, event_at);

CREATE TABLE IF NOT EXISTS ml_market_indicator_candles (
    source TEXT NOT NULL,
    indicator TEXT NOT NULL,
    interval TEXT NOT NULL,
    event_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    open_price NUMERIC NOT NULL CHECK (open_price > 0),
    high_price NUMERIC NOT NULL CHECK (high_price > 0),
    low_price NUMERIC NOT NULL CHECK (low_price > 0),
    close_price NUMERIC NOT NULL CHECK (close_price > 0),
    volume NUMERIC NOT NULL CHECK (volume >= 0),
    collection_run_id BIGINT REFERENCES ml_collection_runs(id) ON DELETE SET NULL,
    raw_payload JSONB NOT NULL,
    PRIMARY KEY (source, indicator, interval, event_at),
    CHECK (event_at <= available_at),
    CHECK (available_at <= collected_at),
    CHECK (high_price >= open_price AND high_price >= close_price AND high_price >= low_price),
    CHECK (low_price <= open_price AND low_price <= close_price AND low_price <= high_price)
);
CREATE INDEX IF NOT EXISTS ml_market_indicator_candles_time
    ON ml_market_indicator_candles(indicator, interval, event_at);
CREATE INDEX IF NOT EXISTS ml_market_indicator_candles_available
    ON ml_market_indicator_candles(indicator, interval, available_at, event_at);

CREATE TABLE IF NOT EXISTS ml_macro_observations (
    source TEXT NOT NULL,
    indicator TEXT NOT NULL,
    event_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    value NUMERIC NOT NULL,
    unit TEXT NOT NULL,
    frequency TEXT NOT NULL CHECK (frequency IN ('SNAPSHOT','DAILY')),
    collection_run_id BIGINT REFERENCES ml_collection_runs(id) ON DELETE SET NULL,
    raw_payload JSONB NOT NULL,
    PRIMARY KEY (source, indicator, event_at, value),
    CHECK (event_at <= available_at),
    CHECK (available_at <= collected_at)
);
CREATE INDEX IF NOT EXISTS ml_macro_observations_indicator_time
    ON ml_macro_observations(indicator, event_at);
CREATE INDEX IF NOT EXISTS ml_macro_observations_available
    ON ml_macro_observations(indicator, available_at, event_at);

CREATE TABLE IF NOT EXISTS ml_strategy_decisions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_key TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    account_id BIGINT,
    account_name TEXT NOT NULL,
    strategy_id BIGINT,
    strategy_name TEXT,
    data_source TEXT NOT NULL CHECK (data_source IN ('TOSS','SIMULATED')),
    symbol TEXT NOT NULL,
    candle_event_at TIMESTAMPTZ,
    decision_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    action TEXT NOT NULL CHECK (
        action IN ('DATA_WAIT','WAIT','BUY_FILLED','BUY_BLOCKED','SELL_FILLED','SELL_BLOCKED')
    ),
    reason TEXT NOT NULL,
    price NUMERIC NOT NULL CHECK (price > 0),
    short_average NUMERIC,
    long_average NUMERIC,
    trend TEXT,
    previous_trend TEXT,
    history_count INTEGER NOT NULL CHECK (history_count >= 0),
    entry_armed BOOLEAN NOT NULL,
    confirmation_count INTEGER NOT NULL CHECK (confirmation_count >= 0),
    strategy_quantity INTEGER NOT NULL CHECK (strategy_quantity >= 0),
    cash NUMERIC NOT NULL CHECK (cash >= 0),
    total_asset NUMERIC NOT NULL CHECK (total_asset >= 0),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (decision_at <= available_at),
    CHECK (available_at <= collected_at)
);
ALTER TABLE ml_strategy_decisions ADD COLUMN IF NOT EXISTS decision_key TEXT;
UPDATE ml_strategy_decisions SET decision_key = 'legacy-' || id WHERE decision_key IS NULL;
ALTER TABLE ml_strategy_decisions ALTER COLUMN decision_key SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ml_strategy_decisions_key
    ON ml_strategy_decisions(decision_key);
-- Earlier development schemas used a partial index with the name above.
-- Keep it for compatibility and add an unconditional index that PostgreSQL can
-- infer for ON CONFLICT (decision_key).
CREATE UNIQUE INDEX IF NOT EXISTS ml_strategy_decisions_key_all
    ON ml_strategy_decisions(decision_key);
CREATE INDEX IF NOT EXISTS ml_strategy_decisions_symbol_time
    ON ml_strategy_decisions(symbol, decision_at);
CREATE INDEX IF NOT EXISTS ml_strategy_decisions_run_time
    ON ml_strategy_decisions(run_id, decision_at);
CREATE INDEX IF NOT EXISTS ml_strategy_decisions_action_time
    ON ml_strategy_decisions(action, decision_at);
CREATE INDEX IF NOT EXISTS ml_strategy_decisions_account_time
    ON ml_strategy_decisions(account_id, decision_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS ml_data_quality_reports (
    report_date DATE PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('PASS','WARN','FAIL')),
    expected_bars INTEGER NOT NULL CHECK (expected_bars >= 0),
    stock_summary JSONB NOT NULL,
    indicator_summary JSONB NOT NULL,
    macro_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    decision_summary JSONB NOT NULL,
    collection_summary JSONB NOT NULL,
    issues JSONB NOT NULL DEFAULT '[]'::jsonb,
    generated_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE ml_data_quality_reports
    ADD COLUMN IF NOT EXISTS macro_summary JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS ml_data_quality_reports_generated
    ON ml_data_quality_reports(generated_at DESC);

CREATE TABLE IF NOT EXISTS live_asset_history (
    user_id BIGINT NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    account_label TEXT NOT NULL,
    observed_minute TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    market_value NUMERIC NOT NULL,
    cash NUMERIC NOT NULL,
    total_assets NUMERIC NOT NULL,
    PRIMARY KEY (user_id, account_label, observed_minute)
);

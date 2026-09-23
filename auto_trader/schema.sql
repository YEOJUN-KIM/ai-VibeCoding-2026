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
CREATE TABLE IF NOT EXISTS risk_daily_snapshots (
    account_id BIGINT REFERENCES accounts(id) ON DELETE CASCADE,
    trade_date DATE NOT NULL,
    opening_asset NUMERIC NOT NULL CHECK (opening_asset >= 0),
    PRIMARY KEY (account_id, trade_date)
);

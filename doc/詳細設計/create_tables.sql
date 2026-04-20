-- stock_quant DB: 21 tables
-- Execution order respects FK dependencies

-- =====================================================
-- Master
-- =====================================================

CREATE TABLE IF NOT EXISTS tickers (
    id         SERIAL PRIMARY KEY,
    code       VARCHAR(20)  NOT NULL UNIQUE,
    name       VARCHAR(100),
    market     VARCHAR(20),
    sector     VARCHAR(50),
    is_active  BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS strategies (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    id          SERIAL PRIMARY KEY,
    strategy_id INTEGER      NOT NULL REFERENCES strategies(id),
    version     VARCHAR(20)  NOT NULL,
    git_hash    VARCHAR(40)  NOT NULL,
    changelog   TEXT,
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    UNIQUE (strategy_id, git_hash)
);

CREATE TABLE IF NOT EXISTS watchlists (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS watchlist_tickers (
    id           SERIAL PRIMARY KEY,
    watchlist_id INTEGER   NOT NULL REFERENCES watchlists(id),
    ticker_id    INTEGER   NOT NULL REFERENCES tickers(id),
    added_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (watchlist_id, ticker_id)
);

-- =====================================================
-- Price data
-- =====================================================

CREATE TABLE IF NOT EXISTS ohlcv (
    id        SERIAL PRIMARY KEY,
    ticker_id INTEGER        NOT NULL REFERENCES tickers(id),
    date      DATE           NOT NULL,
    open      NUMERIC(12,2)  NOT NULL,
    high      NUMERIC(12,2)  NOT NULL,
    low       NUMERIC(12,2)  NOT NULL,
    close     NUMERIC(12,2)  NOT NULL,
    volume    BIGINT         NOT NULL,
    UNIQUE (ticker_id, date)
);
CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_date ON ohlcv (ticker_id, date);

CREATE TABLE IF NOT EXISTS fetch_logs (
    id           SERIAL PRIMARY KEY,
    ticker_id    INTEGER     NOT NULL REFERENCES tickers(id),
    fetched_from DATE        NOT NULL,
    fetched_to   DATE        NOT NULL,
    status       VARCHAR(20) NOT NULL,
    error_msg    TEXT,
    fetched_at   TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fetch_logs_ticker_id ON fetch_logs (ticker_id);

-- =====================================================
-- Indicator cache
-- =====================================================

CREATE TABLE IF NOT EXISTS indicator_params (
    id          SERIAL PRIMARY KEY,
    params_hash VARCHAR(64) NOT NULL UNIQUE,
    params      JSONB       NOT NULL,
    created_at  TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS indicators (
    id                 SERIAL PRIMARY KEY,
    ticker_id          INTEGER       NOT NULL REFERENCES tickers(id),
    date               DATE          NOT NULL,
    name               VARCHAR(50)   NOT NULL,
    value              NUMERIC(12,4) NOT NULL,
    indicator_param_id INTEGER       NOT NULL REFERENCES indicator_params(id),
    UNIQUE (ticker_id, date, name, indicator_param_id)
);
CREATE INDEX IF NOT EXISTS idx_indicators_ticker_date ON indicators (ticker_id, date);

-- =====================================================
-- Backtest
-- =====================================================

CREATE TABLE IF NOT EXISTS runs (
    id            SERIAL PRIMARY KEY,
    strategy_id   INTEGER   NOT NULL REFERENCES strategies(id),
    version_id    INTEGER   NOT NULL REFERENCES strategy_versions(id),
    watchlist_id  INTEGER   REFERENCES watchlists(id),
    days          INTEGER   NOT NULL,
    tickers_count INTEGER   NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS run_params (
    id     SERIAL PRIMARY KEY,
    run_id INTEGER      NOT NULL REFERENCES runs(id),
    key    VARCHAR(100) NOT NULL,
    value  VARCHAR(100) NOT NULL,
    UNIQUE (run_id, key)
);

CREATE TABLE IF NOT EXISTS trades (
    id           SERIAL PRIMARY KEY,
    run_id       INTEGER       NOT NULL REFERENCES runs(id),
    ticker_id    INTEGER       NOT NULL REFERENCES tickers(id),
    entry_date   DATE          NOT NULL,
    exit_date    DATE,
    direction    VARCHAR(10)   NOT NULL,
    entry_price  NUMERIC(12,2) NOT NULL,
    exit_price   NUMERIC(12,2),
    pnl_pct      NUMERIC(8,4),
    result       VARCHAR(20),
    hold_days    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_trades_run_id    ON trades (run_id);
CREATE INDEX IF NOT EXISTS idx_trades_ticker_id ON trades (ticker_id);

CREATE TABLE IF NOT EXISTS summaries (
    id            SERIAL PRIMARY KEY,
    run_id        INTEGER       NOT NULL UNIQUE REFERENCES runs(id),
    trade_count   INTEGER       NOT NULL,
    win_rate      NUMERIC(5,2),
    avg_pnl       NUMERIC(8,4),
    profit_factor NUMERIC(8,4),
    max_drawdown  NUMERIC(8,4),
    total_return  NUMERIC(8,4)
);

-- =====================================================
-- Grid search
-- =====================================================

CREATE TABLE IF NOT EXISTS optimize_jobs (
    id            SERIAL PRIMARY KEY,
    strategy_id   INTEGER   NOT NULL REFERENCES strategies(id),
    version_id    INTEGER   NOT NULL REFERENCES strategy_versions(id),
    watchlist_id  INTEGER   REFERENCES watchlists(id),
    days          INTEGER   NOT NULL,
    tickers_count INTEGER   NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS optimize_job_grid (
    id     SERIAL PRIMARY KEY,
    job_id INTEGER      NOT NULL REFERENCES optimize_jobs(id),
    key    VARCHAR(100) NOT NULL,
    values TEXT         NOT NULL,
    UNIQUE (job_id, key)
);

CREATE TABLE IF NOT EXISTS optimize_results (
    id            SERIAL PRIMARY KEY,
    job_id        INTEGER      NOT NULL REFERENCES optimize_jobs(id),
    trade_count   INTEGER      NOT NULL,
    win_rate      NUMERIC(5,2),
    avg_pnl       NUMERIC(8,4),
    profit_factor NUMERIC(8,4),
    max_drawdown  NUMERIC(8,4)
);

CREATE TABLE IF NOT EXISTS optimize_result_params (
    id        SERIAL PRIMARY KEY,
    result_id INTEGER      NOT NULL REFERENCES optimize_results(id),
    key       VARCHAR(100) NOT NULL,
    value     VARCHAR(100) NOT NULL,
    UNIQUE (result_id, key)
);

-- =====================================================
-- Signals
-- =====================================================

CREATE TABLE IF NOT EXISTS signals (
    id          SERIAL PRIMARY KEY,
    strategy_id INTEGER       NOT NULL REFERENCES strategies(id),
    version_id  INTEGER       NOT NULL REFERENCES strategy_versions(id),
    ticker_id   INTEGER       NOT NULL REFERENCES tickers(id),
    date        DATE          NOT NULL,
    direction   VARCHAR(10)   NOT NULL,
    status      VARCHAR(20)   NOT NULL,
    price       NUMERIC(12,2) NOT NULL,
    UNIQUE (strategy_id, version_id, ticker_id, date)
);
CREATE INDEX IF NOT EXISTS idx_signals_date ON signals (date);

CREATE TABLE IF NOT EXISTS signal_indicators (
    id        SERIAL PRIMARY KEY,
    signal_id INTEGER       NOT NULL REFERENCES signals(id),
    key       VARCHAR(50)   NOT NULL,
    value     NUMERIC(12,4) NOT NULL,
    UNIQUE (signal_id, key)
);

-- =====================================================
-- Live trades
-- =====================================================

CREATE TABLE IF NOT EXISTS live_trades (
    id                SERIAL PRIMARY KEY,
    signal_id         INTEGER       NOT NULL REFERENCES signals(id),
    entry_date        DATE          NOT NULL,
    exit_date         DATE,
    entry_price       NUMERIC(12,2) NOT NULL,
    exit_price        NUMERIC(12,2),
    lot               INTEGER       NOT NULL,
    stop_loss_price   NUMERIC(12,2),
    take_profit_price NUMERIC(12,2),
    pnl_pct           NUMERIC(8,4),
    pnl_jpy           NUMERIC(12,2),
    result            VARCHAR(20)
);

-- =====================================================
-- Logs
-- =====================================================

CREATE TABLE IF NOT EXISTS system_logs (
    id        SERIAL PRIMARY KEY,
    level     VARCHAR(10) NOT NULL,
    component VARCHAR(50) NOT NULL,
    message   TEXT        NOT NULL,
    ticker_id INTEGER     REFERENCES tickers(id),
    run_id    INTEGER     REFERENCES runs(id),
    job_id    INTEGER     REFERENCES optimize_jobs(id),
    created_at TIMESTAMP  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_system_logs_created_at ON system_logs (created_at);

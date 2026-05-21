SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    reason TEXT NOT NULL,
    price REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    approved INTEGER NOT NULL,
    reason TEXT NOT NULL,
    order_id TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    signal_id TEXT,
    created_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    reference_price REAL,
    reason TEXT NOT NULL,
    risk_checked INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL NOT NULL,
    slippage REAL NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS balance_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    equity REAL NOT NULL,
    cash REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_state_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    exchange TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    symbol TEXT NOT NULL,
    bar_timestamp TEXT,
    close REAL,
    signal TEXT,
    risk_decision TEXT,
    order_status TEXT NOT NULL,
    cash REAL NOT NULL,
    position_quantity REAL NOT NULL,
    position_avg_price REAL NOT NULL,
    equity REAL NOT NULL,
    reason TEXT,
    skip_reason TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS processed_bars (
    source TEXT NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    bar_timestamp TEXT NOT NULL,
    run_id TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    processed_at TEXT NOT NULL,
    PRIMARY KEY (source, exchange, symbol, timeframe, bar_timestamp)
);

CREATE TABLE IF NOT EXISTS daily_summaries (
    day TEXT PRIMARY KEY,
    trade_count INTEGER NOT NULL,
    total_fees REAL NOT NULL,
    starting_equity REAL NOT NULL,
    ending_equity REAL NOT NULL,
    pnl REAL NOT NULL
);
"""

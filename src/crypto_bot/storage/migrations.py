from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from crypto_bot.storage.schema import SCHEMA_SQL

SCHEMA_VERSION = 5

MIGRATIONS: dict[int, str] = {
    1: SCHEMA_SQL,
    2: """
        ALTER TABLE paper_state_snapshots
            ADD COLUMN account_realized_pnl REAL NOT NULL DEFAULT 0;
        ALTER TABLE paper_state_snapshots
            ADD COLUMN position_realized_pnl REAL NOT NULL DEFAULT 0;
        ALTER TABLE paper_state_snapshots
            ADD COLUMN peak_equity REAL NOT NULL DEFAULT 0;
    """,
    3: """
        CREATE TABLE runtime_controls (
            name TEXT PRIMARY KEY,
            engaged INTEGER NOT NULL,
            reason TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """,
    4: """
        CREATE TABLE shadow_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at TEXT NOT NULL,
            exchange TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            symbol TEXT NOT NULL,
            bar_timestamp TEXT NOT NULL,
            strategy_name TEXT NOT NULL,
            signal_id TEXT NOT NULL,
            signal_side TEXT NOT NULL,
            signal_reason TEXT NOT NULL,
            risk_approved INTEGER NOT NULL,
            risk_reason TEXT NOT NULL,
            proposed_order_side TEXT,
            proposed_quantity REAL,
            proposed_notional REAL,
            UNIQUE(exchange, timeframe, symbol, bar_timestamp, strategy_name)
        );
    """,
    5: """
        CREATE TABLE runtime_heartbeats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            component TEXT NOT NULL,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            detail TEXT
        );
        CREATE INDEX idx_runtime_heartbeats_component_timestamp
            ON runtime_heartbeats(component, timestamp);
    """,
}


def initialize_schema(connection: sqlite3.Connection) -> int:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    connection.commit()

    current = schema_version(connection)
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {current} is newer than supported version {SCHEMA_VERSION}"
        )

    for version in range(current + 1, SCHEMA_VERSION + 1):
        script = MIGRATIONS.get(version)
        if script is None:
            raise RuntimeError(f"missing database migration for version {version}")
        _apply_migration(connection, version, script)

    return schema_version(connection)


def schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(row[0]) if row else 0


def _apply_migration(connection: sqlite3.Connection, version: int, script: str) -> None:
    applied_at = datetime.now(timezone.utc).isoformat().replace("'", "''")
    migration_script = (
        "BEGIN IMMEDIATE;\n"
        f"{script}\n"
        "INSERT INTO schema_migrations (version, applied_at) "
        f"VALUES ({version}, '{applied_at}');\n"
        "COMMIT;"
    )
    try:
        connection.executescript(migration_script)
    except Exception:
        connection.rollback()
        raise

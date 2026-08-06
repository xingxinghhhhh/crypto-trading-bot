import sqlite3

import pytest

from crypto_bot.storage.migrations import SCHEMA_VERSION, initialize_schema, schema_version
from crypto_bot.storage.repositories import SQLiteStorage


def test_new_database_is_migrated_to_current_version(tmp_path):
    database = tmp_path / "new.db"
    storage = SQLiteStorage(f"sqlite:///{database}")

    try:
        assert schema_version(storage.connection) == SCHEMA_VERSION
        tables = {
            row[0]
            for row in storage.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "signals" in tables
        assert "schema_migrations" in tables
    finally:
        storage.close()


def test_legacy_database_is_adopted_without_losing_data(tmp_path):
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE signals (id TEXT PRIMARY KEY, timestamp TEXT, symbol TEXT, side TEXT, reason TEXT, price REAL)")
    connection.execute(
        "INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?)",
        ("signal-1", "2026-01-01T00:00:00+00:00", "BTC/USDT", "hold", "legacy", 100.0),
    )
    connection.commit()

    initialize_schema(connection)

    assert schema_version(connection) == SCHEMA_VERSION
    assert connection.execute("SELECT reason FROM signals WHERE id = 'signal-1'").fetchone()[0] == "legacy"
    connection.close()


def test_database_newer_than_application_is_rejected(tmp_path):
    database = tmp_path / "future.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION + 1, "2026-01-01T00:00:00+00:00"),
    )
    connection.commit()

    with pytest.raises(RuntimeError, match="newer than supported"):
        initialize_schema(connection)

    connection.close()


def test_migration_two_adds_complete_paper_account_state(tmp_path):
    database = tmp_path / "state.db"
    storage = SQLiteStorage(f"sqlite:///{database}")

    try:
        columns = {
            row[1]
            for row in storage.connection.execute(
                "PRAGMA table_info(paper_state_snapshots)"
            ).fetchall()
        }
    finally:
        storage.close()

    assert {
        "account_realized_pnl",
        "position_realized_pnl",
        "peak_equity",
    }.issubset(columns)


def test_migration_three_adds_persistent_runtime_controls(tmp_path):
    database = tmp_path / "controls.db"
    storage = SQLiteStorage(f"sqlite:///{database}")

    try:
        assert storage.kill_switch_status()["engaged"] is False
        engaged = storage.set_kill_switch(True, "operator_test")
        assert engaged["engaged"] is True
        assert engaged["reason"] == "operator_test"
    finally:
        storage.close()

    reopened = SQLiteStorage(f"sqlite:///{database}")
    try:
        assert reopened.kill_switch_status()["engaged"] is True
        released = reopened.set_kill_switch(False, "manual_recovery")
        assert released["engaged"] is False
    finally:
        reopened.close()

from __future__ import annotations

from datetime import date
from typing import Any

from crypto_bot.execution.models import Fill, OrderIntent
from crypto_bot.logging import redact_secret_text
from crypto_bot.risk.manager import RiskDecision
from crypto_bot.storage.db import connect_sqlite
from crypto_bot.storage.schema import SCHEMA_SQL
from crypto_bot.strategy.signals import Signal


class SQLiteStorage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.connection = connect_sqlite(url)
        self.initialize()

    def initialize(self) -> None:
        self.connection.executescript(SCHEMA_SQL)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def record_signal(self, signal: Signal, price: float) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO signals (id, timestamp, symbol, side, reason, price)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (signal.id, signal.timestamp.isoformat(), signal.symbol, signal.side.value, signal.reason, price),
        )
        self.connection.commit()

    def record_risk_event(self, signal: Signal, decision: RiskDecision) -> None:
        self.connection.execute(
            """
            INSERT INTO risk_events (timestamp, signal_id, approved, reason, order_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                signal.timestamp.isoformat(),
                signal.id,
                int(decision.approved),
                decision.reason,
                decision.order.id if decision.order else None,
            ),
        )
        self.connection.commit()

    def record_order(self, order: OrderIntent) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO orders
            (id, signal_id, created_at, symbol, side, quantity, reference_price, reason, risk_checked)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order.id,
                order.signal_id,
                order.created_at.isoformat(),
                order.symbol,
                order.side.value,
                order.quantity,
                order.reference_price,
                order.reason,
                int(order.risk_checked),
            ),
        )
        self.connection.commit()

    def record_fill(self, fill: Fill) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO fills
            (id, order_id, timestamp, symbol, side, quantity, price, fee, slippage, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill.id,
                fill.order_id,
                fill.timestamp.isoformat(),
                fill.symbol,
                fill.side.value,
                fill.quantity,
                fill.price,
                fill.fee,
                fill.slippage,
                fill.reason,
            ),
        )
        self.connection.commit()

    def record_balance_snapshot(self, equity: float, cash: float, timestamp) -> None:
        self.connection.execute(
            "INSERT INTO balance_snapshots (timestamp, equity, cash) VALUES (?, ?, ?)",
            (timestamp.isoformat(), equity, cash),
        )
        self.connection.commit()

    def record_paper_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        sanitized = dict(snapshot)
        if sanitized.get("error_message"):
            sanitized["error_message"] = redact_secret_text(str(sanitized["error_message"]))
        self.connection.execute(
            """
            INSERT INTO paper_state_snapshots
            (
                run_id, iteration, timestamp, source, exchange, timeframe, symbol,
                bar_timestamp, close, signal, risk_decision, order_status,
                cash, position_quantity, position_avg_price, equity,
                reason, skip_reason, error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sanitized["run_id"],
                sanitized["iteration"],
                sanitized["timestamp"],
                sanitized["source"],
                sanitized["exchange"],
                sanitized["timeframe"],
                sanitized["symbol"],
                sanitized.get("bar_timestamp"),
                sanitized.get("close"),
                sanitized.get("signal"),
                sanitized.get("risk_decision"),
                sanitized["order_status"],
                sanitized["cash"],
                sanitized["position_quantity"],
                sanitized["position_avg_price"],
                sanitized["equity"],
                sanitized.get("reason"),
                sanitized.get("skip_reason"),
                sanitized.get("error_message"),
            ),
        )
        self.connection.commit()

    def has_processed_bar(self, source: str, exchange: str, symbol: str, timeframe: str, bar_timestamp: str) -> bool:
        row = self.connection.execute(
            """
            SELECT 1 FROM processed_bars
            WHERE source = ? AND exchange = ? AND symbol = ? AND timeframe = ? AND bar_timestamp = ?
            """,
            (source, exchange, symbol, timeframe, bar_timestamp),
        ).fetchone()
        return row is not None

    def record_processed_bar(
        self,
        source: str,
        exchange: str,
        symbol: str,
        timeframe: str,
        bar_timestamp: str,
        run_id: str,
        iteration: int,
        processed_at: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO processed_bars
            (source, exchange, symbol, timeframe, bar_timestamp, run_id, iteration, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source, exchange, symbol, timeframe, bar_timestamp, run_id, iteration, processed_at),
        )
        self.connection.commit()

    def create_daily_summary(self, day: date) -> dict[str, float | int | str]:
        prefix = day.isoformat()
        fill_row = self.connection.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(fee), 0)
            FROM fills
            WHERE timestamp LIKE ?
            """,
            (f"{prefix}%",),
        ).fetchone()
        equity_rows = self.connection.execute(
            """
            SELECT equity FROM balance_snapshots
            WHERE timestamp LIKE ?
            ORDER BY timestamp ASC
            """,
            (f"{prefix}%",),
        ).fetchall()
        trade_count = int(fill_row[0])
        total_fees = float(fill_row[1])
        starting_equity = float(equity_rows[0][0]) if equity_rows else 0.0
        ending_equity = float(equity_rows[-1][0]) if equity_rows else 0.0
        pnl = ending_equity - starting_equity
        self.connection.execute(
            """
            INSERT OR REPLACE INTO daily_summaries
            (day, trade_count, total_fees, starting_equity, ending_equity, pnl)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (prefix, trade_count, total_fees, starting_equity, ending_equity, pnl),
        )
        self.connection.commit()
        return {
            "day": prefix,
            "trade_count": trade_count,
            "total_fees": total_fees,
            "starting_equity": starting_equity,
            "ending_equity": ending_equity,
            "pnl": pnl,
        }

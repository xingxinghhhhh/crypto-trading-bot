from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from crypto_bot.execution.models import Fill, OrderIntent
from crypto_bot.logging import redact_secret_text
from crypto_bot.portfolio.account import Account
from crypto_bot.portfolio.position import Position
from crypto_bot.risk.manager import RiskDecision
from crypto_bot.storage.db import connect_sqlite
from crypto_bot.storage.migrations import initialize_schema
from crypto_bot.strategy.signals import Signal


class SQLiteStorage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.connection = connect_sqlite(url)
        self.initialize()

    def initialize(self) -> None:
        initialize_schema(self.connection)

    def close(self) -> None:
        self.connection.close()

    def begin_paper_iteration(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")

    def commit_paper_iteration(self) -> None:
        self.connection.commit()

    def rollback_paper_iteration(self) -> None:
        if self.connection.in_transaction:
            self.connection.rollback()

    def restore_paper_account(self, initial_cash: float, symbol: str) -> Account:
        row = self.connection.execute(
            """
            SELECT
                cash,
                position_quantity,
                position_avg_price,
                account_realized_pnl,
                position_realized_pnl,
                peak_equity
            FROM paper_state_snapshots
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        if row is None:
            return Account(initial_cash=initial_cash)

        position = Position(
            symbol=symbol,
            quantity=float(row[1]),
            avg_price=float(row[2]),
            realized_pnl=float(row[4]),
        )
        return Account(
            initial_cash=initial_cash,
            cash=float(row[0]),
            positions={symbol: position},
            realized_pnl=float(row[3]),
            peak_equity=max(float(row[5]), initial_cash),
        )

    def kill_switch_status(self) -> dict[str, bool | str | None]:
        row = self.connection.execute(
            """
            SELECT engaged, reason, updated_at
            FROM runtime_controls
            WHERE name = 'paper_kill_switch'
            """
        ).fetchone()
        if row is None:
            return {"engaged": False, "reason": None, "updated_at": None}
        return {
            "engaged": bool(row[0]),
            "reason": str(row[1]),
            "updated_at": str(row[2]),
        }

    def set_kill_switch(self, engaged: bool, reason: str) -> dict[str, bool | str | None]:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("kill switch reason must not be empty")
        updated_at = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """
            INSERT INTO runtime_controls (name, engaged, reason, updated_at)
            VALUES ('paper_kill_switch', ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                engaged = excluded.engaged,
                reason = excluded.reason,
                updated_at = excluded.updated_at
            """,
            (int(engaged), normalized_reason, updated_at),
        )
        self.connection.commit()
        return self.kill_switch_status()

    def has_shadow_observation(
        self,
        exchange: str,
        timeframe: str,
        symbol: str,
        bar_timestamp: str,
        strategy_name: str,
    ) -> bool:
        row = self.connection.execute(
            """
            SELECT 1 FROM shadow_observations
            WHERE exchange = ? AND timeframe = ? AND symbol = ?
              AND bar_timestamp = ? AND strategy_name = ?
            """,
            (exchange, timeframe, symbol, bar_timestamp, strategy_name),
        ).fetchone()
        return row is not None

    def record_shadow_observation(self, observation: dict[str, Any]) -> None:
        sanitized = {
            key: redact_secret_text(str(value)) if value is not None else None
            for key, value in observation.items()
        }
        self.connection.execute(
            """
            INSERT INTO shadow_observations
            (
                observed_at, exchange, timeframe, symbol, bar_timestamp,
                strategy_name, signal_id, signal_side, signal_reason,
                risk_approved, risk_reason, proposed_order_side,
                proposed_quantity, proposed_notional
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sanitized["observed_at"],
                sanitized["exchange"],
                sanitized["timeframe"],
                sanitized["symbol"],
                sanitized["bar_timestamp"],
                sanitized["strategy_name"],
                sanitized["signal_id"],
                sanitized["signal_side"],
                sanitized["signal_reason"],
                int(observation["risk_approved"]),
                sanitized["risk_reason"],
                sanitized["proposed_order_side"],
                observation.get("proposed_quantity"),
                observation.get("proposed_notional"),
            ),
        )
        self.connection.commit()

    def record_runtime_heartbeat(
        self,
        component: str,
        run_id: str,
        status: str,
        *,
        detail: str | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        heartbeat_time = timestamp or datetime.now(timezone.utc)
        self.connection.execute(
            """
            INSERT INTO runtime_heartbeats
            (component, run_id, status, timestamp, detail)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                component,
                run_id,
                status,
                heartbeat_time.isoformat(),
                redact_secret_text(detail) if detail else None,
            ),
        )
        self.connection.commit()

    def latest_runtime_heartbeat(self, component: str) -> dict[str, str | None] | None:
        row = self.connection.execute(
            """
            SELECT run_id, status, timestamp, detail
            FROM runtime_heartbeats
            WHERE component = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (component,),
        ).fetchone()
        if row is None:
            return None
        return {
            "run_id": str(row[0]),
            "status": str(row[1]),
            "timestamp": str(row[2]),
            "detail": None if row[3] is None else str(row[3]),
        }

    def health_snapshot(self) -> dict[str, Any]:
        return {
            "database_integrity": str(
                self.connection.execute("PRAGMA integrity_check").fetchone()[0]
            ),
            "kill_switch": self.kill_switch_status(),
            "paper_heartbeat": self.latest_runtime_heartbeat("paper"),
            "shadow_heartbeat": self.latest_runtime_heartbeat("shadow"),
        }

    def record_signal(self, signal: Signal, price: float, *, commit: bool = True) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO signals (id, timestamp, symbol, side, reason, price)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (signal.id, signal.timestamp.isoformat(), signal.symbol, signal.side.value, signal.reason, price),
        )
        if commit:
            self.connection.commit()

    def record_risk_event(self, signal: Signal, decision: RiskDecision, *, commit: bool = True) -> None:
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
        if commit:
            self.connection.commit()

    def record_order(self, order: OrderIntent, *, commit: bool = True) -> None:
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
        if commit:
            self.connection.commit()

    def record_fill(self, fill: Fill, *, commit: bool = True) -> None:
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
        if commit:
            self.connection.commit()

    def record_balance_snapshot(
        self,
        equity: float,
        cash: float,
        timestamp,
        *,
        commit: bool = True,
    ) -> None:
        self.connection.execute(
            "INSERT INTO balance_snapshots (timestamp, equity, cash) VALUES (?, ?, ?)",
            (timestamp.isoformat(), equity, cash),
        )
        if commit:
            self.connection.commit()

    def record_paper_state_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        commit: bool = True,
    ) -> None:
        sanitized = dict(snapshot)
        if sanitized.get("error_message"):
            sanitized["error_message"] = redact_secret_text(str(sanitized["error_message"]))
        self.connection.execute(
            """
            INSERT INTO paper_state_snapshots
            (
                run_id, iteration, timestamp, source, exchange, timeframe, symbol,
                bar_timestamp, close, signal, risk_decision, order_status,
                cash, position_quantity, position_avg_price,
                account_realized_pnl, position_realized_pnl, peak_equity, equity,
                reason, skip_reason, error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                sanitized["account_realized_pnl"],
                sanitized["position_realized_pnl"],
                sanitized["peak_equity"],
                sanitized["equity"],
                sanitized.get("reason"),
                sanitized.get("skip_reason"),
                sanitized.get("error_message"),
            ),
        )
        if commit:
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
        *,
        commit: bool = True,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO processed_bars
            (source, exchange, symbol, timeframe, bar_timestamp, run_id, iteration, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source, exchange, symbol, timeframe, bar_timestamp, run_id, iteration, processed_at),
        )
        if commit:
            self.connection.commit()

    def count_fills_for_day(self, day: date) -> int:
        prefix = day.isoformat()
        row = self.connection.execute(
            "SELECT COUNT(*) FROM fills WHERE timestamp LIKE ?",
            (f"{prefix}%",),
        ).fetchone()
        return int(row[0]) if row else 0

    def starting_equity_for_day(self, day: date, fallback: float) -> float:
        day_start = f"{day.isoformat()}T00:00:00"
        previous = self.connection.execute(
            """
            SELECT equity FROM balance_snapshots
            WHERE timestamp < ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (day_start,),
        ).fetchone()
        if previous is not None:
            return float(previous[0])

        first = self.connection.execute(
            """
            SELECT equity FROM balance_snapshots
            WHERE timestamp LIKE ?
            ORDER BY timestamp ASC
            LIMIT 1
            """,
            (f"{day.isoformat()}%",),
        ).fetchone()
        return float(first[0]) if first is not None else float(fallback)

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

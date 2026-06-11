"""SQLite persistence for simulated trades and reviews.

This module is deliberately local-only: it never touches real exchange APIs and
only stores simulated trading records, review records, and read-only summaries.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DATABASE_DIR = BASE_DIR / "database"
DB_PATH = DATABASE_DIR / "trading.db"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _connect() -> sqlite3.Connection:
    init_database()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_database() -> None:
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sim_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id TEXT UNIQUE,
                open_time TEXT,
                close_time TEXT,
                symbol TEXT,
                side TEXT,
                entry_price REAL,
                exit_price REAL,
                position_size REAL,
                take_profit REAL,
                stop_loss REAL,
                pnl REAL DEFAULT 0,
                pnl_percent REAL DEFAULT 0,
                holding_minutes REAL DEFAULT 0,
                strategy TEXT,
                ai_score REAL,
                market_structure TEXT,
                status TEXT CHECK(status IN ('OPEN', 'CLOSED')) DEFAULT 'OPEN',
                open_reason TEXT,
                close_reason TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER,
                position_id TEXT,
                symbol TEXT,
                side TEXT,
                open_time TEXT,
                close_time TEXT,
                open_reason TEXT,
                market_structure TEXT,
                ai_score REAL,
                trade_logic TEXT,
                holding_minutes REAL,
                close_reason TEXT,
                pnl REAL,
                pnl_percent REAL,
                created_at TEXT,
                FOREIGN KEY(trade_id) REFERENCES sim_trades(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sim_trades_symbol ON sim_trades(symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sim_trades_status ON sim_trades(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sim_trades_open_time ON sim_trades(open_time)")
        conn.commit()
    finally:
        conn.close()


def record_sim_open(position: dict[str, Any]) -> None:
    """Insert a simulated opened position into SQLite."""
    snapshot = position.get("committee_snapshot") or {}
    local_snapshot = position.get("local_strategy_snapshot") or {}
    strategy = local_snapshot.get("strategy_name") or snapshot.get("strategy_name") or snapshot.get("source") or "AI交易委员会"
    market_structure = (
        snapshot.get("current_market_state")
        or snapshot.get("market_structure")
        or snapshot.get("opportunity_status")
        or ""
    )
    take_profit = _to_float(position.get("take_profit_2"), 0) or _to_float(position.get("take_profit_1"), 0)
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO sim_trades (
                position_id, open_time, symbol, side, entry_price, position_size,
                take_profit, stop_loss, strategy, ai_score, market_structure,
                status, open_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
            """,
            (
                position.get("position_id"),
                position.get("open_time") or _now(),
                position.get("symbol"),
                position.get("direction"),
                _to_float(position.get("entry_price"), 0),
                _to_float(position.get("notional_usdt"), 0),
                take_profit,
                _to_float(position.get("stop_loss"), 0),
                strategy,
                _to_float(position.get("committee_confidence"), 0),
                market_structure,
                position.get("open_reason") or snapshot.get("chairman_summary") or "",
                _now(),
                _now(),
            ),
        )
        conn.commit()


def record_sim_close(position: dict[str, Any], exit_price: float, pnl: float, reason: str) -> None:
    """Update a simulated position as closed and generate an automatic review."""
    position_id = str(position.get("position_id") or "")
    if not position_id:
        return
    entry = _to_float(position.get("entry_price"), 0)
    original_notional = _to_float(position.get("notional_usdt"), 0) + _to_float(position.get("realized_pnl"), 0) * 0
    # The JSON engine reduces notional before full close; SQLite keeps the latest
    # available notional but computes percent from entry quantity when possible.
    qty = _to_float(position.get("quantity"), 0)
    if original_notional <= 0 and entry > 0 and qty > 0:
        original_notional = entry * qty
    stored = get_sim_trade_by_position(position_id)
    if stored:
        original_notional = _to_float(stored.get("position_size"), original_notional)
    pnl_pct = pnl / original_notional * 100 if original_notional else 0.0
    holding_minutes = max(0.0, (_to_float(time.time(), 0) - _to_float(position.get("open_ts"), time.time())) / 60)
    close_time = _now()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE sim_trades
            SET close_time=?, exit_price=?, pnl=?, pnl_percent=?, holding_minutes=?,
                close_reason=?, status='CLOSED', updated_at=?
            WHERE position_id=?
            """,
            (close_time, exit_price, pnl, pnl_pct, holding_minutes, reason, _now(), position_id),
        )
        row = conn.execute("SELECT * FROM sim_trades WHERE position_id=?", (position_id,)).fetchone()
        if row:
            _insert_review_record(conn, dict(row))
        conn.commit()


def _insert_review_record(conn: sqlite3.Connection, trade: dict[str, Any]) -> None:
    exists = conn.execute("SELECT id FROM review_records WHERE position_id=?", (trade.get("position_id"),)).fetchone()
    if exists:
        return
    trade_logic = "模拟交易由机会榜/AI交易委员会候选触发，按本地模拟风控执行止盈止损。"
    conn.execute(
        """
        INSERT INTO review_records (
            trade_id, position_id, symbol, side, open_time, close_time, open_reason,
            market_structure, ai_score, trade_logic, holding_minutes, close_reason,
            pnl, pnl_percent, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade.get("id"),
            trade.get("position_id"),
            trade.get("symbol"),
            trade.get("side"),
            trade.get("open_time"),
            trade.get("close_time"),
            trade.get("open_reason"),
            trade.get("market_structure"),
            trade.get("ai_score"),
            trade_logic,
            trade.get("holding_minutes"),
            trade.get("close_reason"),
            trade.get("pnl"),
            trade.get("pnl_percent"),
            _now(),
        ),
    )


def get_sim_trade_by_position(position_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sim_trades WHERE position_id=?", (position_id,)).fetchone()
        return dict(row) if row else None


def query_sim_trades(limit: int = 100, offset: int = 0, search: str = "", status: str = "") -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if search:
        clauses.append("(symbol LIKE ? OR side LIKE ? OR strategy LIKE ? OR close_reason LIKE ?)")
        keyword = f"%{search.strip().upper()}%"
        params.extend([keyword, keyword, keyword, keyword])
    if status in {"OPEN", "CLOSED"}:
        clauses.append("status=?")
        params.append(status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM sim_trades{where} ORDER BY COALESCE(close_time, open_time) DESC, id DESC LIMIT ? OFFSET ?",
            (*params, int(limit), int(offset)),
        ).fetchall()
        return [dict(row) for row in rows]


def query_review_records(limit: int = 100) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM review_records ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(row) for row in rows]


def get_sim_trade_stats() -> dict[str, Any]:
    with _connect() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM sim_trades ORDER BY COALESCE(close_time, open_time), id").fetchall()]
    closed = [row for row in rows if row.get("status") == "CLOSED"]
    wins = [row for row in closed if _to_float(row.get("pnl"), 0) > 0]
    losses = [row for row in closed if _to_float(row.get("pnl"), 0) < 0]
    pnl_values = [_to_float(row.get("pnl"), 0) for row in closed]
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    current_win_streak = current_loss_streak = 0
    max_win_streak = max_loss_streak = 0
    for pnl in pnl_values:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        if pnl > 0:
            current_win_streak += 1
            current_loss_streak = 0
        elif pnl < 0:
            current_loss_streak += 1
            current_win_streak = 0
        else:
            current_win_streak = 0
            current_loss_streak = 0
        max_win_streak = max(max_win_streak, current_win_streak)
        max_loss_streak = max(max_loss_streak, current_loss_streak)
    total_pnl = sum(pnl_values)
    return {
        "total_trades": len(closed),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": len(wins) / len(closed) * 100 if closed else 0,
        "total_pnl": total_pnl,
        "average_pnl": total_pnl / len(closed) if closed else 0,
        "max_profit": max(pnl_values) if pnl_values else 0,
        "max_loss": min(pnl_values) if pnl_values else 0,
        "max_drawdown": max_drawdown,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "current_open_positions": sum(1 for row in rows if row.get("status") == "OPEN"),
        "cumulative_opens": len(rows),
        "database_path": str(DB_PATH),
    }


def export_trades_json(limit: int = 100) -> str:
    return json.dumps(query_sim_trades(limit=limit), ensure_ascii=False, indent=2)

"""Observation records for rejected auto-simulation candidates.

This module tracks near-miss signals without changing simulated opening rules.
It lets the system learn whether rejected candidates would have won after
30/60/120 minutes.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OBSERVATION_PATH = DATA_DIR / "sim_observation_signals.json"
MAX_OBSERVATION_ROWS = 1500
DEDUP_SECONDS = 30 * 60
HORIZONS = (30, 60, 120)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ts() -> int:
    return int(time.time())


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    return int(round(_to_float(value, default)))


def _read_json(path: Path, default: Any) -> Any:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            _write_json(path, default)
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _reason_signature(reasons: list[str]) -> str:
    text = "|".join(str(item).strip() for item in reasons if str(item).strip())
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12] if text else "none"


def _observation_key(symbol: str, direction: str, entry_state: str, action_gate: str, reasons: list[str]) -> str:
    return f"{symbol}:{direction}:{entry_state}:{action_gate}:{_reason_signature(reasons)}"


def load_observation_signals(limit: int | None = None) -> list[dict[str, Any]]:
    rows = _read_json(OBSERVATION_PATH, [])
    if not isinstance(rows, list):
        return []
    return rows[:limit] if limit else rows


def save_observation_signals(rows: list[dict[str, Any]]) -> None:
    _write_json(OBSERVATION_PATH, rows[:MAX_OBSERVATION_ROWS])


def get_pending_observation_symbols() -> list[str]:
    symbols: list[str] = []
    now_ts = _ts()
    for row in load_observation_signals():
        if row.get("status") == "completed":
            continue
        if now_ts > _to_int(row.get("created_ts")) + 125 * 60:
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def should_observe_candidate(
    *,
    symbol: str,
    direction: str,
    entry_price: float,
    rank: int,
    professional_score: int,
    simulation_score: int,
) -> bool:
    if not symbol or direction not in {"long", "short"} or entry_price <= 0:
        return False
    return rank <= 3 or professional_score >= 60 or simulation_score >= 60


def record_observation_signal(
    *,
    symbol: str,
    direction: str,
    entry_price: float,
    rank: int,
    reasons: list[str],
    row: dict[str, Any],
    precheck: dict[str, Any],
    ev_check: dict[str, Any] | None = None,
    scores: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    scores = scores or {}
    ev_check = ev_check or {}
    symbol = str(symbol or "").upper().strip()
    direction = str(direction or "").lower().strip()
    professional_score = _to_int(scores.get("professional_trade_score"))
    simulation_score = _to_int(scores.get("simulation_score"))
    if not should_observe_candidate(
        symbol=symbol,
        direction=direction,
        entry_price=entry_price,
        rank=rank,
        professional_score=professional_score,
        simulation_score=simulation_score,
    ):
        return None

    entry_state = str(row.get("entry_state") or precheck.get("entry_state") or "")
    action_gate = str(row.get("action_gate") or precheck.get("action_gate") or "")
    reasons = [str(reason) for reason in reasons if str(reason).strip()] or ["未通过自动模拟采样。"]
    key = _observation_key(symbol, direction, entry_state, action_gate, reasons)
    now_ts = _ts()
    rows = load_observation_signals()
    for existing in rows:
        if existing.get("observation_key") != key:
            continue
        if now_ts - _to_int(existing.get("created_ts")) <= DEDUP_SECONDS:
            return existing

    record = {
        "id": f"obs_{now_ts}_{symbol}_{direction}_{key[-6:]}",
        "time": _now(),
        "created_ts": now_ts,
        "source": "auto_sim_rejected_candidate",
        "status": "observing",
        "symbol": symbol,
        "direction": direction,
        "entry_price": float(entry_price),
        "rank": rank,
        "reasons": reasons,
        "observation_key": key,
        "professional_trade_score": professional_score,
        "simulation_score": simulation_score,
        "base_quality_score": _to_int(scores.get("base_quality_score")),
        "liquidity_quality_score": _to_int(scores.get("liquidity_quality_score")),
        "portfolio_fit_score": _to_int(scores.get("portfolio_fit_score")),
        "risk_score": _to_int(scores.get("risk_score")),
        "entry_state": entry_state,
        "action_gate": action_gate,
        "tradable_now": row.get("tradable_now"),
        "consensus_count": _to_int(row.get("consensus_support_count"), _to_int(precheck.get("consensus_support_count"))),
        "kline_confirming": (row.get("kline_signal") or {}).get("confirming"),
        "whale_confirming": (row.get("whale_signal") or {}).get("confirming"),
        "orderbook_confirming": (row.get("orderbook_signal") or {}).get("confirming"),
        "market_regime": row.get("market_regime") or precheck.get("market_regime"),
        "liquidity_quality": _to_int(scores.get("liquidity_quality_score")),
        "base_quality": _to_int(scores.get("base_quality_score")),
        "historical_ev": ev_check.get("ev"),
        "historical_ev_sample_size": ev_check.get("sample_size"),
        "historical_ev_win_rate": ev_check.get("win_rate"),
        "historical_ev_reason": ev_check.get("reason"),
        "last_price": float(entry_price),
        "last_update_time": _now(),
        "last_update_ts": now_ts,
    }
    rows.insert(0, record)
    save_observation_signals(rows)
    return record


def _performance(entry_price: float, current_price: float, direction: str) -> dict[str, Any]:
    if entry_price <= 0 or current_price <= 0:
        return {}
    if direction == "short":
        pct = (entry_price - current_price) / entry_price * 100
    else:
        pct = (current_price - entry_price) / entry_price * 100
    if pct > 0.05:
        outcome = "win"
    elif pct < -0.05:
        outcome = "loss"
    else:
        outcome = "flat"
    return {"price": current_price, "pct": round(pct, 4), "outcome": outcome}


def update_observation_signals(price_map: dict[str, float]) -> dict[str, int]:
    rows = load_observation_signals()
    if not rows:
        return {"updated": 0, "completed": 0, "total": 0}
    now_ts = _ts()
    updated = 0
    completed = 0
    for row in rows:
        if row.get("status") == "completed":
            continue
        symbol = str(row.get("symbol") or "").upper()
        price = _to_float(price_map.get(symbol))
        if price <= 0:
            continue
        updated += 1
        row["last_price"] = price
        row["last_update_time"] = _now()
        row["last_update_ts"] = now_ts
        created_ts = _to_int(row.get("created_ts"))
        age_seconds = max(0, now_ts - created_ts)
        entry_price = _to_float(row.get("entry_price"))
        direction = str(row.get("direction") or "")
        for minutes in HORIZONS:
            key = f"result_{minutes}m"
            if key in row or age_seconds < minutes * 60:
                continue
            result = _performance(entry_price, price, direction)
            if result:
                row[key] = {**result, "time": _now(), "age_minutes": round(age_seconds / 60, 1)}
        if all(f"result_{minutes}m" in row for minutes in HORIZONS):
            row["status"] = "completed"
            row["completed_time"] = _now()
            completed += 1
    save_observation_signals(rows)
    return {"updated": updated, "completed": completed, "total": len(rows)}

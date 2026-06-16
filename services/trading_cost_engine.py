"""Configurable fee, slippage and net-PnL helpers for simulated trading."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "trading_cost_config.json"
DEFAULT_COST_CONFIG: dict[str, Any] = {
    "maker_fee_rate": 0.0002,
    "taker_fee_rate": 0.0005,
    "default_fee_mode": "taker",
    "default_slippage_rate": 0.0005,
    "high_liquidity_slippage_rate": 0.0002,
    "mid_liquidity_slippage_rate": 0.0005,
    "low_liquidity_slippage_rate": 0.0015,
    "extreme_slippage_rate": 0.003,
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def load_trading_cost_config() -> dict[str, Any]:
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        raw = raw if isinstance(raw, dict) else {}
    except Exception:
        raw = {}
    config = DEFAULT_COST_CONFIG.copy()
    config.update(raw)
    return config


def get_fee_rate(mode: str | None = None, config: dict[str, Any] | None = None) -> float:
    config = config or load_trading_cost_config()
    fee_mode = str(mode or config.get("default_fee_mode") or "taker").lower()
    key = "maker_fee_rate" if fee_mode == "maker" else "taker_fee_rate"
    return max(0.0, _to_float(config.get(key), DEFAULT_COST_CONFIG[key]))


def calculate_fee(notional_usdt: Any, fee_rate: Any | None = None, fee_mode: str | None = None) -> float:
    rate = get_fee_rate(fee_mode) if fee_rate is None else max(0.0, _to_float(fee_rate, 0.0))
    return round(max(0.0, _to_float(notional_usdt, 0.0)) * rate, 8)


def estimate_slippage(symbol: str = "", notional_usdt: Any = 0, liquidity_level: str | None = None, config: dict[str, Any] | None = None) -> float:
    config = config or load_trading_cost_config()
    level = str(liquidity_level or "").lower()
    if level in {"high", "high_liquidity", "高", "高流动性"}:
        return _to_float(config.get("high_liquidity_slippage_rate"), DEFAULT_COST_CONFIG["high_liquidity_slippage_rate"])
    if level in {"low", "low_liquidity", "低", "低流动性"}:
        return _to_float(config.get("low_liquidity_slippage_rate"), DEFAULT_COST_CONFIG["low_liquidity_slippage_rate"])
    if level in {"extreme", "极端"}:
        return _to_float(config.get("extreme_slippage_rate"), DEFAULT_COST_CONFIG["extreme_slippage_rate"])
    notional = _to_float(notional_usdt, 0.0)
    major = str(symbol or "").upper() in {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"}
    if major and notional <= 10000:
        return _to_float(config.get("high_liquidity_slippage_rate"), DEFAULT_COST_CONFIG["high_liquidity_slippage_rate"])
    if notional >= 50000:
        return _to_float(config.get("low_liquidity_slippage_rate"), DEFAULT_COST_CONFIG["low_liquidity_slippage_rate"])
    return _to_float(config.get("mid_liquidity_slippage_rate") or config.get("default_slippage_rate"), DEFAULT_COST_CONFIG["default_slippage_rate"])


def apply_entry_slippage(price: Any, direction: str, slippage_rate: Any) -> float:
    value = _to_float(price, 0.0)
    rate = max(0.0, _to_float(slippage_rate, 0.0))
    if value <= 0:
        return 0.0
    return round(value * (1 - rate if str(direction).lower() == "short" else 1 + rate), 12)


def apply_exit_slippage(price: Any, direction: str, slippage_rate: Any) -> float:
    value = _to_float(price, 0.0)
    rate = max(0.0, _to_float(slippage_rate, 0.0))
    if value <= 0:
        return 0.0
    return round(value * (1 + rate if str(direction).lower() == "short" else 1 - rate), 12)


def _gross_pnl(entry_price: float, exit_price: float, quantity: float, direction: str) -> float:
    if str(direction).lower() == "short":
        return (entry_price - exit_price) * quantity
    return (exit_price - entry_price) * quantity


def calculate_trade_cost(
    *,
    theoretical_entry_price: Any,
    theoretical_exit_price: Any | None = None,
    actual_entry_price: Any | None = None,
    actual_exit_price: Any | None = None,
    quantity: Any,
    direction: str,
    fee_mode: str | None = None,
    entry_slippage_rate: Any | None = None,
    exit_slippage_rate: Any | None = None,
    symbol: str = "",
    notional_usdt: Any = 0,
) -> dict[str, Any]:
    qty = max(0.0, _to_float(quantity, 0.0))
    entry_theoretical = _to_float(theoretical_entry_price, 0.0)
    exit_theoretical = _to_float(theoretical_exit_price, 0.0) if theoretical_exit_price is not None else 0.0
    entry_rate = estimate_slippage(symbol, notional_usdt) if entry_slippage_rate is None else max(0.0, _to_float(entry_slippage_rate, 0.0))
    exit_rate = estimate_slippage(symbol, notional_usdt) if exit_slippage_rate is None else max(0.0, _to_float(exit_slippage_rate, 0.0))
    entry_actual = _to_float(actual_entry_price, 0.0) or apply_entry_slippage(entry_theoretical, direction, entry_rate)
    exit_actual = _to_float(actual_exit_price, 0.0) or (apply_exit_slippage(exit_theoretical, direction, exit_rate) if exit_theoretical > 0 else 0.0)
    fee_rate = get_fee_rate(fee_mode)
    open_notional = abs(entry_actual * qty)
    close_notional = abs(exit_actual * qty) if exit_actual > 0 else 0.0
    open_fee = calculate_fee(open_notional, fee_rate)
    close_fee = calculate_fee(close_notional, fee_rate) if close_notional > 0 else 0.0
    entry_slippage_cost = abs(entry_actual - entry_theoretical) * qty if entry_theoretical > 0 else 0.0
    exit_slippage_cost = abs(exit_actual - exit_theoretical) * qty if exit_theoretical > 0 and exit_actual > 0 else 0.0
    return {
        "theoretical_entry_price": entry_theoretical,
        "actual_entry_price": entry_actual,
        "entry_slippage_rate": entry_rate,
        "entry_slippage_cost": round(entry_slippage_cost, 8),
        "open_fee_rate": fee_rate,
        "open_fee_usdt": round(open_fee, 8),
        "theoretical_exit_price": exit_theoretical,
        "actual_exit_price": exit_actual,
        "exit_slippage_rate": exit_rate,
        "exit_slippage_cost": round(exit_slippage_cost, 8),
        "close_fee_rate": fee_rate,
        "close_fee_usdt": round(close_fee, 8),
        "total_fee_usdt": round(open_fee + close_fee, 8),
        "total_slippage_cost_usdt": round(entry_slippage_cost + exit_slippage_cost, 8),
    }


def calculate_net_pnl(
    *,
    theoretical_entry_price: Any,
    theoretical_exit_price: Any,
    quantity: Any,
    direction: str,
    actual_entry_price: Any | None = None,
    actual_exit_price: Any | None = None,
    fee_mode: str | None = None,
    entry_slippage_rate: Any | None = None,
    exit_slippage_rate: Any | None = None,
    symbol: str = "",
    notional_usdt: Any = 0,
    margin_usdt: Any = 0,
) -> dict[str, Any]:
    qty = max(0.0, _to_float(quantity, 0.0))
    entry_theoretical = _to_float(theoretical_entry_price, 0.0)
    exit_theoretical = _to_float(theoretical_exit_price, 0.0)
    costs = calculate_trade_cost(
        theoretical_entry_price=entry_theoretical,
        theoretical_exit_price=exit_theoretical,
        actual_entry_price=actual_entry_price,
        actual_exit_price=actual_exit_price,
        quantity=qty,
        direction=direction,
        fee_mode=fee_mode,
        entry_slippage_rate=entry_slippage_rate,
        exit_slippage_rate=exit_slippage_rate,
        symbol=symbol,
        notional_usdt=notional_usdt,
    )
    gross = _gross_pnl(entry_theoretical, exit_theoretical, qty, direction)
    actual_pnl = _gross_pnl(costs["actual_entry_price"], costs["actual_exit_price"], qty, direction)
    net = actual_pnl - _to_float(costs.get("total_fee_usdt"), 0.0)
    base_notional = _to_float(notional_usdt, 0.0) or abs(entry_theoretical * qty)
    margin = _to_float(margin_usdt, 0.0)
    return {
        **costs,
        "gross_pnl_usdt": round(gross, 8),
        "gross_pnl_pct": round(gross / base_notional * 100, 6) if base_notional else 0.0,
        "net_pnl_usdt": round(net, 8),
        "net_pnl_pct": round(net / base_notional * 100, 6) if base_notional else 0.0,
        "net_pnl_pct_on_margin": round(net / margin * 100, 6) if margin else 0.0,
        "actual_price_pnl_usdt": round(actual_pnl, 8),
        "cost_after_still_profitable": bool(gross > 0 and net > 0),
        "cost_invalid_profit": bool(gross > 0 and net <= 0),
    }


def round_trip_cost_rate(config: dict[str, Any] | None = None, fee_mode: str | None = None, slippage_rate: Any | None = None) -> float:
    config = config or load_trading_cost_config()
    fee = get_fee_rate(fee_mode, config)
    slip = _to_float(slippage_rate, _to_float(config.get("default_slippage_rate"), DEFAULT_COST_CONFIG["default_slippage_rate"]))
    return max(0.0, fee * 2 + slip * 2)

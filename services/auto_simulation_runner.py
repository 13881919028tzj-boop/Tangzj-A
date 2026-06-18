"""Background auto simulation runner.

It converts opportunity-board candidates into local simulated signals only when
the simulated account is running and simulation mode is set to auto. No real
exchange order API is used here.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from services import market_cache
from services.binance_public import get_24hr_ticker
from services.fast_opportunity_engine import collect_top10_opportunities, run_committee_top10_precheck
from services.sim_trade_engine import (
    get_open_positions,
    get_pending_orders,
    load_settings,
    load_sim_account,
    log_sim_event,
    update_simulation,
)


_LAST_RUN_AT = 0.0
_MIN_INTERVAL_SECONDS = 3.0
LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
POSITION_PRICE_LOG = LOG_DIR / "position_price_debug.log"
_LAST_PRICE_STATUS: dict[str, str] = {}


def _debug_log(message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with POSITION_PRICE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    return int(round(_to_float(value, default)))


def _direction(row: dict[str, Any], precheck: dict[str, Any] | None = None) -> str:
    text = " ".join(
        str(value)
        for value in [
            (precheck or {}).get("direction"),
            row.get("direction"),
            row.get("advice"),
            row.get("opportunity_status"),
            row.get("current_market_state"),
        ]
        if value
    )
    lowered = text.lower()
    if "short" in lowered or "空" in text:
        return "short"
    return "long"


def _price(row: dict[str, Any]) -> float:
    symbol = str(row.get("symbol") or "").upper()
    ticker = market_cache.get_ticker(symbol) or {}
    return (
        _to_float(ticker.get("last_price"), 0)
        or _to_float(row.get("current_price"), 0)
        or _to_float(row.get("last_price"), 0)
        or _to_float(row.get("price"), 0)
    )


def _fetch_live_price(symbol: str) -> float:
    try:
        ticker = get_24hr_ticker(symbol)
        market_cache.set_ticker(symbol, ticker)
        return _to_float(ticker.get("last_price"), 0)
    except Exception as exc:
        _debug_log(f"direct_price_fetch_failed symbol={symbol} error={repr(exc)}")
        return 0.0


def _build_price_map(opportunities: list[dict[str, Any]]) -> dict[str, float]:
    symbols = {str(item.get("symbol") or "").upper() for item in opportunities}
    symbols.update(str(item.get("symbol") or "").upper() for item in get_open_positions())
    symbols.update(str(item.get("symbol") or "").upper() for item in get_pending_orders())
    price_map: dict[str, float] = {}
    statuses: dict[str, str] = {}
    by_symbol = {str(item.get("symbol") or "").upper(): item for item in opportunities}
    for symbol in symbols:
        if not symbol:
            continue
        ticker = market_cache.get_ticker(symbol) or {}
        price = _to_float(ticker.get("last_price"), 0)
        status = "live" if price > 0 else "missing"
        if price <= 0:
            price = _fetch_live_price(symbol)
            status = "live" if price > 0 else "missing"
        if price <= 0:
            price = _price(by_symbol.get(symbol, {}))
            status = "ranking" if price > 0 else "missing"
        if price <= 0:
            for position in get_open_positions():
                if str(position.get("symbol") or "").upper() == symbol:
                    position_price = _to_float(position.get("current_price"))
                    if position_price > 0:
                        price = position_price
                        status = "stale"
                    break
        price_map[symbol] = max(price, 0)
        statuses[symbol] = status
    global _LAST_PRICE_STATUS
    _LAST_PRICE_STATUS = statuses
    live_count = len([s for s in statuses.values() if s in {"live", "ranking"}])
    missing_count = len([s for s in statuses.values() if s == "missing"])
    stale_count = len([s for s in statuses.values() if s == "stale"])
    _debug_log(f"build_price_map symbols={len(price_map)} live={live_count} stale={stale_count} missing={missing_count}")
    return price_map


def _risk_reward_prices(price: float, direction: str) -> tuple[float, float, float]:
    stop_pct = 0.0125
    tp1_pct = stop_pct * 1.4
    tp2_pct = stop_pct * 2.8
    if direction == "short":
        return price * (1 + stop_pct), price * (1 - tp1_pct), price * (1 - tp2_pct)
    return price * (1 - stop_pct), price * (1 + tp1_pct), price * (1 + tp2_pct)


def _signal_from_precheck(precheck: dict[str, Any]) -> dict[str, Any] | None:
    row = precheck.get("opportunity") or {}
    symbol = str(precheck.get("symbol") or row.get("symbol") or "").upper()
    price = _price(row)
    score = _to_int(row.get("final_opportunity_score", row.get("opportunity_score")), _to_int(precheck.get("score"), 0))
    risk = _to_int(row.get("risk_score"), _to_int(precheck.get("risk_score"), 50))
    if not symbol or price <= 0 or score < 75 or risk >= 85 or not precheck.get("allowed_candidate"):
        return None
    direction = _direction(row, precheck)
    stop, tp1, tp2 = _risk_reward_prices(price, direction)
    action = "顺势做多" if direction == "long" and score >= 88 else "轻仓试多" if direction == "long" else "顺势做空" if score >= 88 else "轻仓试空"
    rank = int(precheck.get("rank", 0) or 0)
    return {
        "symbol": symbol,
        "direction": direction,
        "action": action,
        "trade_permission": "approved",
        "approved_for_simulation": True,
        "veto_members": [],
        "committee_confidence": max(60, min(95, score)),
        "risk_score": risk,
        "position_suggestion": "1%-3%" if risk >= 60 else "3%-5%",
        "system_position_suggestion": "1%-3%" if risk >= 60 else "3%-5%",
        "entry_zone": {"low": price * 0.999, "high": price * 1.001},
        "stop_loss": {"price": stop},
        "take_profit_1": {"price": tp1},
        "take_profit_2": {"price": tp2},
        "risk_reward_ratio": 2.8,
        "invalid_condition": "机会榜信号失效、风险升高或委员会后续否决。",
        "chairman_summary": f"后台自动模拟：机会榜TOP{rank or '-'}候选，评分{score}，风险{risk}。仅执行本地模拟订单。",
        "source_opportunity_id": row.get("opportunity_id") or precheck.get("opportunity_id") or f"{symbol}_{direction}",
        "source_board_rank": rank,
        "current_market_state": row.get("current_market_state") or row.get("opportunity_status") or "机会榜自动模拟候选",
        "opportunity_status": row.get("opportunity_status"),
    }


def run_auto_simulation_cycle(rankings: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Run one background simulation cycle.

    Existing positions and pending simulated orders are updated even when auto
    mode is disabled. New simulated orders require account=running and mode=auto.
    """
    global _LAST_RUN_AT
    now = time.time()
    if now - _LAST_RUN_AT < _MIN_INTERVAL_SECONDS:
        return {"ok": True, "skipped": True, "reason": "自动模拟刷新冷却中。"}
    _LAST_RUN_AT = now
    opportunities = collect_top10_opportunities(rankings, limit=10)
    price_map = _build_price_map(opportunities)
    settings = load_settings()
    account = load_sim_account()
    signals: list[dict[str, Any]] = []
    if account.get("status") == "running" and settings.get("mode") == "auto":
        prechecks = run_committee_top10_precheck(rankings, limit=10)
        for precheck in prechecks:
            signal = _signal_from_precheck(precheck)
            if signal:
                signals.append(signal)
    summary = update_simulation(price_map, signals, _LAST_PRICE_STATUS)
    _debug_log(f"update_simulation signals={len(signals)} prices={len(price_map)}")
    if signals:
        log_sim_event("后台自动模拟扫描", content=f"本轮候选 {len(signals)} 个，已交给模拟风控执行。")
    return {"ok": True, "signals": len(signals), "prices": len(price_map), "summary": summary}

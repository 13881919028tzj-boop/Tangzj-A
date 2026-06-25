"""Grid trading recommendation engine.

This module scores symbols for range/grid trading, independently from the
trend-following simulation trade selector.
"""

from __future__ import annotations

from typing import Any

from services import market_cache
from services.binance_public import get_all_24hr_tickers
from services.grid_trade_engine import create_grid_bot, load_grid_bots, save_grid_bots, validate_grid_config


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> int:
    return int(round(max(low, min(high, value))))


def _candidate_rows(limit: int = 80) -> list[dict[str, Any]]:
    snapshot = market_cache.snapshot()
    rows: dict[str, dict[str, Any]] = {}
    for ticker in (snapshot.get("tickers") or {}).values():
        symbol = str((ticker or {}).get("symbol") or "").upper()
        if symbol.endswith("USDT"):
            rows[symbol] = dict(ticker or {})
    rankings = market_cache.get_rankings() or {}
    for group in rankings.values():
        if not isinstance(group, list):
            continue
        for item in group:
            symbol = str((item or {}).get("symbol") or "").upper()
            if not symbol.endswith("USDT"):
                continue
            merged = {**rows.get(symbol, {}), **dict(item or {})}
            rows[symbol] = merged
    for symbol, ticker in (snapshot.get("tickers") or {}).items():
        normalized = str(symbol or (ticker or {}).get("symbol") or "").upper()
        if normalized.endswith("USDT"):
            rows[normalized] = {**rows.get(normalized, {}), **dict(ticker or {})}
    if not rows:
        try:
            for ticker in get_all_24hr_tickers()[: max(limit, 80)]:
                symbol = str((ticker or {}).get("symbol") or "").upper()
                if not symbol.endswith("USDT"):
                    continue
                market_cache.set_ticker(symbol, ticker)
                rows[symbol] = dict(ticker or {})
        except Exception as exc:
            market_cache.set_error(f"网格推荐24h行情兜底失败：{exc!r}")
    return sorted(rows.values(), key=lambda item: _to_float(item.get("quote_volume")), reverse=True)[:limit]


def _range_stats(symbol: str, ticker: dict[str, Any] | None = None) -> dict[str, float]:
    ticker = ticker or {}
    rows = market_cache.get_klines(symbol, "1m")
    if len(rows) < 30:
        rows = market_cache.get_klines(symbol, market_cache.get_kline_interval())
    window = rows[-120:] if len(rows) >= 20 else rows
    closes = [_to_float(row.get("close")) for row in window if _to_float(row.get("close")) > 0]
    highs = [_to_float(row.get("high")) for row in window if _to_float(row.get("high")) > 0]
    lows = [_to_float(row.get("low")) for row in window if _to_float(row.get("low")) > 0]
    if not closes:
        price = _to_float(ticker.get("last_price"), _to_float(ticker.get("current_price")))
        recent_high = _to_float(ticker.get("high_price"), price * 1.04)
        recent_low = _to_float(ticker.get("low_price"), price * 0.96)
        if price <= 0 or recent_high <= 0 or recent_low <= 0 or recent_high <= recent_low:
            return {"samples": 0}
        range_pct = (recent_high - recent_low) / price * 100 if price else 0.0
        trend_pct = _to_float(ticker.get("price_change_percent"))
        atr_pct = max(0.05, min(2.5, range_pct / 24.0))
        close_position = (price - recent_low) / (recent_high - recent_low) if recent_high > recent_low else 0.5
        mid_reversion = 1.0 - abs(close_position - 0.5) * 2.0
        return {
            "samples": 1,
            "source": "ticker_24h_fallback",
            "price": price,
            "recent_high": recent_high,
            "recent_low": recent_low,
            "trend_pct": trend_pct,
            "range_pct": range_pct,
            "atr_pct": atr_pct,
            "close_position": close_position,
            "mid_reversion": max(0.0, min(1.0, mid_reversion)),
        }
    price = closes[-1]
    recent_high = max(highs) if highs else price
    recent_low = min(lows) if lows else price
    ranges = [max(0.0, _to_float(row.get("high")) - _to_float(row.get("low"))) for row in window]
    atr = sum(ranges[-60:]) / max(len(ranges[-60:]), 1) if ranges else 0.0
    ref = closes[-60] if len(closes) >= 60 else closes[0]
    trend_pct = (price - ref) / ref * 100 if ref else 0.0
    range_pct = (recent_high - recent_low) / price * 100 if price else 0.0
    atr_pct = atr / price * 100 if price else 0.0
    close_position = (price - recent_low) / (recent_high - recent_low) if recent_high > recent_low else 0.5
    mid_reversion = 1.0 - abs(close_position - 0.5) * 2.0
    return {
        "samples": len(closes),
        "price": price,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "trend_pct": trend_pct,
        "range_pct": range_pct,
        "atr_pct": atr_pct,
        "close_position": close_position,
        "mid_reversion": max(0.0, min(1.0, mid_reversion)),
    }


def _direction_for(trend_pct: float) -> str:
    if trend_pct > 1.5:
        return "long_spot"
    if trend_pct < -1.5:
        return "short_contract"
    return "neutral_contract"


def build_grid_recommendations(limit: int = 12) -> list[dict[str, Any]]:
    """Return symbols ranked for grid trading suitability."""
    result: list[dict[str, Any]] = []
    for row in _candidate_rows(100):
        symbol = str(row.get("symbol") or "").upper()
        price = _to_float(row.get("last_price"), _to_float(row.get("current_price")))
        volume = _to_float(row.get("quote_volume"))
        change = _to_float(row.get("price_change_percent"))
        if price <= 0 or volume <= 0:
            continue
        stats = _range_stats(symbol, row)
        if int(stats.get("samples", 0)) <= 0:
            continue
        trend_abs = abs(_to_float(stats.get("trend_pct")))
        atr_pct = _to_float(stats.get("atr_pct"))
        range_pct = _to_float(stats.get("range_pct"))
        mid_reversion = _to_float(stats.get("mid_reversion"))
        liquidity_score = _clamp(35 + min(45, volume / 1_000_000 * 8))
        range_score = _clamp(100 - abs(range_pct - 7.5) * 8)
        volatility_score = _clamp(100 - abs(atr_pct - 0.35) * 90)
        trend_score = _clamp(100 - trend_abs * 16)
        overheat_score = _clamp(100 - max(0.0, abs(change) - 8.0) * 4)
        position_score = _clamp(mid_reversion * 100)
        fee_score = _clamp((range_pct / max(atr_pct, 0.05)) * 8)
        score = _clamp(
            liquidity_score * 0.18
            + range_score * 0.22
            + volatility_score * 0.18
            + trend_score * 0.18
            + overheat_score * 0.10
            + position_score * 0.08
            + fee_score * 0.06
        )
        direction = _direction_for(_to_float(stats.get("trend_pct")))
        reasons = [
            f"区间宽度 {range_pct:.2f}%",
            f"ATR {atr_pct:.2f}%",
            f"趋势 {stats.get('trend_pct', 0):+.2f}%",
            f"24h涨跌 {change:+.2f}%",
        ]
        if stats.get("source") == "ticker_24h_fallback":
            reasons.append("K线不足，使用24h高低点兜底")
        if score < 60:
            quality = "不优先"
        elif score < 75:
            quality = "可观察"
        else:
            quality = "适合网格"
        result.append(
            {
                "symbol": symbol,
                "grid_score": score,
                "quality": quality,
                "suggested_direction": direction,
                "last_price": price,
                "quote_volume": volume,
                "price_change_percent": change,
                "lower_price": stats.get("recent_low"),
                "upper_price": stats.get("recent_high"),
                "range_pct": range_pct,
                "atr_pct": atr_pct,
                "trend_pct": stats.get("trend_pct"),
                "reasons": reasons,
                "liquidity_score": liquidity_score,
                "range_score": range_score,
                "volatility_score": volatility_score,
                "trend_score": trend_score,
            }
        )
    return sorted(result, key=lambda item: item["grid_score"], reverse=True)[:limit]


def _active_grid_symbols() -> set[str]:
    return {
        str(bot.get("symbol") or "").upper()
        for bot in load_grid_bots()
        if bot.get("status") in {"running", "paused"}
    }


def auto_open_recommended_grids(
    max_bots: int = 1,
    min_score: int = 70,
    investment_usdt: float = 100.0,
    grid_count: int = 20,
    fee_rate: float = 0.0004,
) -> dict[str, Any]:
    """Open simulated grid bots from the independent grid recommendation list."""
    opened: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    active_symbols = _active_grid_symbols()
    for item in build_grid_recommendations(24):
        if len(opened) >= max(1, int(max_bots)):
            break
        symbol = str(item.get("symbol") or "").upper()
        score = int(_to_float(item.get("grid_score")))
        if score < min_score:
            skipped.append({"symbol": symbol, "reason": f"评分低于 {min_score}", "score": score})
            continue
        if symbol in active_symbols:
            skipped.append({"symbol": symbol, "reason": "已有运行/暂停网格", "score": score})
            continue
        current_price = _to_float(item.get("last_price"))
        lower = _to_float(item.get("lower_price"))
        upper = _to_float(item.get("upper_price"))
        direction = str(item.get("suggested_direction") or "long_spot")
        ok, reasons = validate_grid_config(symbol, lower, upper, grid_count, investment_usdt, current_price, direction)
        if not ok:
            skipped.append({"symbol": symbol, "reason": "；".join(reasons), "score": score})
            continue
        try:
            bot = create_grid_bot(symbol, lower, upper, grid_count, investment_usdt, current_price, fee_rate, direction)
            bot["auto_opened"] = True
            bot["grid_recommendation_score"] = score
            bot["grid_recommendation_reason"] = "；".join(str(x) for x in item.get("reasons", []))
            bots = load_grid_bots()
            if bots and bots[0].get("bot_id") == bot.get("bot_id"):
                bots[0].update(
                    {
                        "auto_opened": True,
                        "grid_recommendation_score": score,
                        "grid_recommendation_reason": bot["grid_recommendation_reason"],
                    }
                )
                save_grid_bots(bots)
            opened.append(bot)
            active_symbols.add(symbol)
        except Exception as exc:
            skipped.append({"symbol": symbol, "reason": f"创建失败：{exc!r}", "score": score})
    return {"opened": opened, "skipped": skipped, "opened_count": len(opened)}

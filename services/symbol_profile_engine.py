"""Symbol profiling helpers for future experience-library grouping."""

from __future__ import annotations

from typing import Any


MAJOR_SYMBOLS = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"}
MEME_HINTS = ("DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF", "MEME", "TRUMP")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _quote_volume(ticker: dict[str, Any] | None) -> float:
    ticker = ticker or {}
    for key in ("quote_volume", "quoteVolume", "volume_quote"):
        if ticker.get(key) is not None:
            return _to_float(ticker.get(key), 0.0)
    return 0.0


def _change_abs(ticker: dict[str, Any] | None) -> float:
    ticker = ticker or {}
    for key in ("price_change_percent", "priceChangePercent", "change_24h", "change_percent"):
        if ticker.get(key) is not None:
            return abs(_to_float(ticker.get(key), 0.0))
    return 0.0


def _tier(value: float, high: float, mid: float) -> str:
    if value >= high:
        return "HIGH"
    if value >= mid:
        return "MID"
    return "LOW"


def _experience_group_candidates(symbol: str, symbol_group: str) -> list[str]:
    """Map local symbol profiles to the first factory experience taxonomy."""
    candidates = [symbol_group]
    if symbol in {"BTCUSDT", "ETHUSDT"}:
        candidates.append("majors")
    elif symbol in {"BNBUSDT", "SOLUSDT", "XRPUSDT"}:
        candidates.append("large_alt")
    elif symbol_group == "MAJOR_HIGH_LIQUIDITY":
        candidates.extend(["majors", "large_alt"])
    elif symbol_group in {"HIGH_VOLUME_ALT", "MID_VOLUME_ALT", "MEME_OR_HYPE", "LOW_LIQUIDITY_HIGH_VOL", "UNKNOWN"}:
        candidates.append("large_alt")
    return [item for index, item in enumerate(candidates) if item and item not in candidates[:index]]


def build_symbol_profile(symbol: str, ticker: dict[str, Any] | None = None, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a conservative symbol group without external market-cap data."""
    clean_symbol = str(symbol or "").upper().strip()
    quote_volume = _quote_volume(ticker)
    volatility = _change_abs(ticker)
    if volatility <= 0 and rows:
        closes = [_to_float(row.get("close"), 0.0) for row in rows[-30:] if isinstance(row, dict)]
        closes = [value for value in closes if value > 0]
        if len(closes) >= 2:
            volatility = abs(closes[-1] - closes[0]) / closes[0] * 100

    volume_tier = _tier(quote_volume, 100_000_000, 10_000_000)
    liquidity_tier = volume_tier
    if volatility >= 18:
        volatility_tier = "EXTREME"
    elif volatility >= 8:
        volatility_tier = "HIGH"
    elif volatility >= 3:
        volatility_tier = "MID"
    else:
        volatility_tier = "LOW"

    listing_age_tier = "UNKNOWN"
    profile_confidence = 45
    reason_parts: list[str] = []
    if quote_volume > 0:
        profile_confidence += 25
        reason_parts.append(f"24小时成交额约{quote_volume:.0f}，流动性分层为{liquidity_tier}。")
    else:
        reason_parts.append("缺少成交额，币种画像保持保守。")
    if volatility > 0:
        profile_confidence += 15
        reason_parts.append(f"波动率参考值约{volatility:.2f}%，波动分层为{volatility_tier}。")
    if clean_symbol in MAJOR_SYMBOLS:
        symbol_group = "MAJOR_HIGH_LIQUIDITY"
        profile_confidence += 10
        reason_parts.append("属于主流高流动性交易对。")
    elif any(hint in clean_symbol for hint in MEME_HINTS):
        symbol_group = "MEME_OR_HYPE"
        reason_parts.append("名称命中MEME或热点交易对特征。")
    elif volume_tier == "HIGH":
        symbol_group = "HIGH_VOLUME_ALT"
    elif volume_tier == "MID":
        symbol_group = "MID_VOLUME_ALT"
    elif volume_tier == "LOW" and volatility_tier in {"HIGH", "EXTREME"}:
        symbol_group = "LOW_LIQUIDITY_HIGH_VOL"
    elif not clean_symbol:
        symbol_group = "UNKNOWN"
        profile_confidence = 0
    else:
        symbol_group = "UNKNOWN" if quote_volume <= 0 else "MID_VOLUME_ALT"

    experience_candidates = _experience_group_candidates(clean_symbol, symbol_group)
    return {
        "symbol": clean_symbol,
        "symbol_group": symbol_group,
        "experience_symbol_group": experience_candidates[1] if len(experience_candidates) > 1 else symbol_group,
        "experience_symbol_group_candidates": experience_candidates,
        "liquidity_tier": liquidity_tier,
        "volatility_tier": volatility_tier,
        "volume_tier": volume_tier,
        "listing_age_tier": listing_age_tier,
        "profile_confidence": max(0, min(100, profile_confidence)),
        "reason": " ".join(reason_parts) or "缺少画像输入，暂归为UNKNOWN。",
    }

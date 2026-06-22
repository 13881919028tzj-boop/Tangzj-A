"""Formatting and numeric helpers shared by Streamlit pages."""

from __future__ import annotations

from typing import Any


def safe_number(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_score(value: Any, default: float | None = None) -> float | None:
    return safe_number(value, default)


def format_score(score: Any, loading: str = "计算中") -> str:
    number = safe_score(score)
    if number is None:
        return loading
    return str(int(number)) if float(number).is_integer() else f"{number:.1f}"


def format_price(value: Any) -> str:
    number = safe_number(value)
    if number is None:
        return "正在获取"
    if number >= 1000:
        return f"{number:,.2f}"
    if number >= 1:
        return f"{number:,.4f}"
    return f"{number:,.8f}".rstrip("0").rstrip(".")


def format_waiting_price(value: Any) -> str:
    number = safe_number(value)
    if number is None or number <= 0:
        return "等待价格刷新"
    return format_price(number)


def valid_price(value: Any) -> float | None:
    number = safe_number(value)
    return number if number is not None and number > 0 else None


def format_percent(value: Any) -> str:
    number = safe_number(value)
    if number is None:
        return "正在获取"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"


def format_compact(value: Any) -> str:
    number = safe_number(value)
    if number is None:
        return "正在获取"
    if abs(number) >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if abs(number) >= 1_000:
        return f"{number / 1_000:.2f}K"
    return f"{number:.2f}"


def money_text(value: Any) -> str:
    return f"{float(value or 0):,.2f} USDT"


def pct_text(value: Any) -> str:
    return f"{float(value or 0):+.2f}%"


def direction_text(value: Any) -> str:
    return "空单" if str(value) == "short" else "多单"


def seconds_text(value: Any) -> str:
    seconds = max(0, int(float(value or 0)))
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}分钟"
    return f"{minutes // 60}小时{minutes % 60}分钟"

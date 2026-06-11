"""Binance USDT-M Futures 衍生品公共数据服务。

接入 OI、Funding 和多空比。只读取公开数据，不包含任何账户、下单或交易接口。
"""

from __future__ import annotations

from typing import Any

import requests


BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"
REQUEST_TIMEOUT = 10


def _request_futures(path: str, params: dict[str, Any] | None = None) -> Any:
    """请求 Binance Futures 公共接口，失败时打印真实错误并抛出。"""
    url = f"{BINANCE_FUTURES_BASE_URL}{path}"
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        print(f"[Binance衍生品] 请求失败 url={url} params={params} error={repr(exc)}")
        raise


def _to_float(value: Any, default: float = 0.0) -> float:
    """安全转换为浮点数。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _change_percent(values: list[float], lookback: int) -> float | None:
    """计算指定回看距离的百分比变化。"""
    if len(values) <= lookback:
        return None
    previous = values[-lookback - 1]
    current = values[-1]
    if previous == 0:
        return None
    return (current - previous) / previous * 100


def _oi_status(change_1h: float | None) -> tuple[str, str]:
    """根据 1小时 OI 变化判断状态。"""
    if change_1h is None:
        return "等待数据", "OI历史数据不足，暂无法判断资金变化。"
    if change_1h >= 8:
        return "OI快速增加", "持仓量快速上升，说明新增资金正在明显进入市场。"
    if change_1h >= 2:
        return "OI持续增加", "持仓量稳步上升，说明市场参与度正在提高。"
    if change_1h <= -8:
        return "OI快速下降", "持仓量快速下降，说明资金正在明显离场或大规模平仓。"
    if change_1h <= -2:
        return "OI持续下降", "持仓量持续下降，说明市场参与度减弱。"
    return "OI基本稳定", "持仓量变化不大，资金暂时没有明显单边进出。"


def _funding_status(rate: float | None) -> tuple[str, str]:
    """判断 Funding 状态。"""
    if rate is None:
        return "等待数据", "Funding数据不足，暂无法判断多空拥挤程度。"
    percent = rate * 100
    if percent >= 0.08:
        return "Funding过高", "多头严重拥挤，存在反向波动或多头挤仓风险。"
    if percent >= 0.03:
        return "Funding偏高", "市场多头情绪较强，但需要警惕追多拥挤。"
    if percent <= -0.08:
        return "Funding过低", "空头严重拥挤，存在空头回补风险。"
    if percent <= -0.03:
        return "Funding偏低", "市场空头情绪较强，但需要警惕反弹。"
    return "Funding正常", "资金费率处于相对均衡区间，多空情绪未明显极端。"


def _ratio_status(ratio: float | None) -> tuple[str, str]:
    """判断多空比状态。"""
    if ratio is None:
        return "等待数据", "多空比数据不足，暂无法判断交易者站队情况。"
    if ratio >= 2:
        return "极度偏多", "多数交易者已经站在多头方向，若行情转弱容易出现多头踩踏。"
    if ratio >= 1.5:
        return "偏多", "交易者整体偏向多头，市场情绪较积极。"
    if ratio <= 0.5:
        return "极度偏空", "多数交易者已经站在空头方向，若行情转强容易出现空头回补。"
    if ratio <= 0.7:
        return "偏空", "交易者整体偏向空头，市场情绪较谨慎。"
    return "均衡", "多空比例相对均衡，暂无明显单边拥挤。"


def get_open_interest(symbol: str) -> dict[str, Any]:
    """获取当前 OI 和历史变化率。"""
    normalized = str(symbol or "").upper().strip()
    current_raw = _request_futures("/fapi/v1/openInterest", {"symbol": normalized})
    current_oi = _to_float(current_raw.get("openInterest"))
    hist_raw = _request_futures(
        "/futures/data/openInterestHist",
        {"symbol": normalized, "period": "5m", "limit": 288},
    )
    values = [_to_float(item.get("sumOpenInterest")) for item in hist_raw if _to_float(item.get("sumOpenInterest")) > 0]
    if not values:
        values = [current_oi] if current_oi else []
    changes = {
        "5m": _change_percent(values, 1),
        "15m": _change_percent(values, 3),
        "1h": _change_percent(values, 12),
        "4h": _change_percent(values, 48),
        "24h": _change_percent(values, 287),
    }
    status, explanation = _oi_status(changes["1h"])
    return {
        "symbol": normalized,
        "current_oi": current_oi,
        "changes": changes,
        "status": status,
        "explanation": explanation,
    }


def get_funding_rate(symbol: str) -> dict[str, Any]:
    """获取最近 Funding 资金费率。"""
    normalized = str(symbol or "").upper().strip()
    raw = _request_futures("/fapi/v1/fundingRate", {"symbol": normalized, "limit": 8})
    if not isinstance(raw, list) or not raw:
        return {"symbol": normalized, "rate": None, "previous_rate": None, "status": "等待数据", "explanation": "Funding数据不足。"}
    latest = raw[-1]
    previous = raw[-2] if len(raw) >= 2 else latest
    rate = _to_float(latest.get("fundingRate"))
    previous_rate = _to_float(previous.get("fundingRate"))
    if rate > previous_rate:
        trend = "上升"
    elif rate < previous_rate:
        trend = "下降"
    else:
        trend = "稳定"
    status, explanation = _funding_status(rate)
    return {
        "symbol": normalized,
        "rate": rate,
        "previous_rate": previous_rate,
        "trend": trend,
        "status": status,
        "explanation": explanation,
        "funding_time": latest.get("fundingTime"),
    }


def get_long_short_ratio(symbol: str) -> dict[str, Any]:
    """获取 Top Trader 账户多空比和持仓多空比。"""
    normalized = str(symbol or "").upper().strip()
    account_raw = _request_futures(
        "/futures/data/topLongShortAccountRatio",
        {"symbol": normalized, "period": "5m", "limit": 1},
    )
    position_raw = _request_futures(
        "/futures/data/topLongShortPositionRatio",
        {"symbol": normalized, "period": "5m", "limit": 1},
    )
    account_item = account_raw[-1] if isinstance(account_raw, list) and account_raw else {}
    position_item = position_raw[-1] if isinstance(position_raw, list) and position_raw else {}
    account_ratio = _to_float(account_item.get("longShortRatio"), None)
    position_ratio = _to_float(position_item.get("longShortRatio"), None)
    status, explanation = _ratio_status(account_ratio)
    return {
        "symbol": normalized,
        "account_ratio": account_ratio,
        "position_ratio": position_ratio,
        "status": status,
        "explanation": explanation,
        "timestamp": account_item.get("timestamp") or position_item.get("timestamp"),
    }


def get_derivatives_snapshot(symbol: str) -> dict[str, Any]:
    """获取指定交易对象的衍生品快照。"""
    normalized = str(symbol or "").upper().strip()
    return {
        "symbol": normalized,
        "oi": get_open_interest(normalized),
        "funding": get_funding_rate(normalized),
        "long_short": get_long_short_ratio(normalized),
    }

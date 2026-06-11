"""本地市场结构与交易信号分析引擎。

本模块只基于已缓存的公共行情、K线和盘口数据计算信号，不访问外部 API，
用于保持信号页在行情刷新时轻量、稳定、可解释。
"""

from __future__ import annotations

from statistics import mean
from typing import Any


def _to_float(value: Any, default: float = 0.0) -> float:
    """安全转换为浮点数。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_price(value: float | None) -> str:
    """简洁价格格式。"""
    if value is None:
        return "待确认"
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    if abs(value) >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _sma(values: list[float], window: int) -> list[float | None]:
    """简单移动平均。"""
    result: list[float | None] = []
    for index in range(len(values)):
        if index + 1 < window:
            result.append(None)
        else:
            result.append(sum(values[index + 1 - window : index + 1]) / window)
    return result


def _ema(values: list[float], window: int) -> list[float]:
    """指数移动平均。"""
    if not values:
        return []
    alpha = 2 / (window + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(value * alpha + result[-1] * (1 - alpha))
    return result


def _rsi(values: list[float], window: int = 14) -> float | None:
    """计算 RSI。"""
    if len(values) <= window:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values[-window - 1 : -1], values[-window:]):
        change = current - previous
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    avg_gain = mean(gains) if gains else 0
    avg_loss = mean(losses) if losses else 0
    if avg_loss == 0:
        return 100.0
    rs_value = avg_gain / avg_loss
    return 100 - (100 / (1 + rs_value))


def _macd_signal(values: list[float]) -> tuple[float | None, str]:
    """计算 MACD 柱体并判断金叉/死叉状态。"""
    if len(values) < 35:
        return None, "中性"
    ema12 = _ema(values, 12)
    ema26 = _ema(values, 26)
    dif = [a - b for a, b in zip(ema12[-len(ema26) :], ema26)]
    dea = _ema(dif, 9)
    if not dif or not dea:
        return None, "中性"
    hist = dif[-1] - dea[-1]
    previous_hist = dif[-2] - dea[-2] if len(dif) >= 2 and len(dea) >= 2 else hist
    if previous_hist <= 0 < hist:
        state = "金叉"
    elif previous_hist >= 0 > hist:
        state = "死叉"
    elif hist > 0:
        state = "多头延续"
    elif hist < 0:
        state = "空头延续"
    else:
        state = "中性"
    return hist, state


def _support_resistance(rows: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """用最近 K线估算邻近支撑与压力。"""
    if len(rows) < 20:
        return None, None
    recent = rows[-120:]
    current = _to_float(recent[-1].get("close"))
    lows = sorted({_to_float(row.get("low")) for row in recent if _to_float(row.get("low")) < current})
    highs = sorted({_to_float(row.get("high")) for row in recent if _to_float(row.get("high")) > current})
    support = lows[-1] if lows else min(_to_float(row.get("low")) for row in recent)
    resistance = highs[0] if highs else max(_to_float(row.get("high")) for row in recent)
    return support, resistance


def _recent_volume_change(rows: list[dict[str, Any]]) -> float:
    """估算最近成交量相对前段成交量变化。"""
    if len(rows) < 40:
        return 0.0
    recent = [_to_float(row.get("volume")) for row in rows[-10:]]
    base = [_to_float(row.get("volume")) for row in rows[-40:-10]]
    base_avg = mean(base) if base else 0
    if base_avg <= 0:
        return 0.0
    return (mean(recent) - base_avg) / base_avg * 100


def _atr(rows: list[dict[str, Any]], window: int = 14) -> float:
    """估算平均真实波幅。"""
    if len(rows) < window + 1:
        return 0.0
    ranges: list[float] = []
    sample = rows[-window:]
    previous_close = _to_float(rows[-window - 1].get("close"))
    for row in sample:
        high = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = _to_float(row.get("close"))
    return mean(ranges) if ranges else 0.0


def _trend_level(score: int) -> str:
    if score >= 80:
        return "强多"
    if score >= 60:
        return "偏多"
    if score >= 40:
        return "中性"
    if score >= 20:
        return "偏空"
    return "强空"


def _risk_level(score: int) -> str:
    if score < 30:
        return "低风险"
    if score < 60:
        return "中等风险"
    if score < 80:
        return "高风险"
    return "极高风险"


def _market_structure(
    rows: list[dict[str, Any]],
    closes: list[float],
    ma20: float | None,
    ma60: float | None,
    volume_change: float,
) -> tuple[str, str]:
    """识别市场结构。"""
    current = closes[-1]
    previous_close = closes[-2]
    lookback = rows[-50:-1] if len(rows) >= 51 else rows[:-1]
    previous_high = max(_to_float(row.get("high")) for row in lookback) if lookback else current
    previous_low = min(_to_float(row.get("low")) for row in lookback) if lookback else current
    recent_change = (current - closes[-8]) / closes[-8] * 100 if len(closes) >= 8 and closes[-8] else 0

    if previous_close > previous_high and current < previous_high:
        return "假突破", "价格短暂突破前高后回落，说明上方抛压仍在，需要等待重新站稳。"
    if current > previous_high:
        return "突破", "价格突破近期高点，若成交量继续配合，趋势可能进入新的上攻段。"
    if current < previous_low:
        return "跌破", "价格跌破近期低点，空头力量占优，短线需要防守风险。"
    if ma20 and ma60 and current > ma20 > ma60 and abs(current - ma20) / current < 0.012:
        return "回踩确认", "价格位于多头结构中并回落接近MA20，若支撑有效，可能继续上涨。"
    if ma20 and ma60 and current > ma20 > ma60 and recent_change > 2.5 and volume_change > 20:
        return "加速上涨", "价格沿均线上方快速上行且成交量放大，短线动能较强，但追高风险增加。"
    if ma20 and ma60 and current < ma20 < ma60 and recent_change < -2.5 and volume_change > 20:
        return "加速下跌", "价格沿均线下方快速下行且成交量放大，短线空头动能较强。"
    if ma20 and ma60 and current > ma20 > ma60:
        return "上升趋势", "价格站上MA20和MA60，短期趋势强于中期趋势。"
    if ma20 and ma60 and current < ma20 < ma60:
        return "下降趋势", "价格位于MA20和MA60下方，短期趋势仍偏弱。"
    return "横盘震荡", "价格围绕均线来回波动，多空暂未形成明确方向。"


def build_signal_analysis(
    ticker: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    orderbook_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成市场结构、评分、交易建议与信号解释。"""
    if not ticker or len(rows) < 80:
        return {
            "ready": False,
            "message": "K线数据积累不足，暂无法完成完整信号分析。",
            "market_structure": "等待数据",
            "structure_explanation": "等待更多K线与盘口数据同步。",
            "trend_score": 0,
            "trend_level": "中性",
            "trend_explanation": "数据不足，暂不判断趋势。",
            "risk_score": 0,
            "risk_level": "待评估",
            "risk_explanation": "数据不足，暂不评估风险。",
            "suggestion": "观望",
            "entry_zone": "待确认",
            "stop_loss": "待确认",
            "take_profit_1": "待确认",
            "take_profit_2": "待确认",
            "risk_reward": "待确认",
            "reasons": ["等待行情、K线和盘口数据同步。"],
            "risks": ["数据不足时不建议开仓。"],
            "rsi": None,
            "macd_signal": "中性",
            "ma20": None,
            "ma60": None,
            "support": None,
            "resistance": None,
        }

    closes = [_to_float(row.get("close")) for row in rows]
    highs = [_to_float(row.get("high")) for row in rows]
    lows = [_to_float(row.get("low")) for row in rows]
    current = _to_float(ticker.get("last_price"), closes[-1])
    if current <= 0:
        current = closes[-1]

    ma20_series = _sma(closes, 20)
    ma60_series = _sma(closes, 60)
    ma20 = ma20_series[-1]
    ma60 = ma60_series[-1]
    ma20_previous = ma20_series[-8] if len(ma20_series) >= 8 else ma20
    rsi_value = _rsi(closes)
    macd_hist, macd_state = _macd_signal(closes)
    support, resistance = _support_resistance(rows)
    volume_change = _recent_volume_change(rows)
    atr_value = _atr(rows)
    structure, structure_text = _market_structure(rows, closes, ma20, ma60, volume_change)

    bullish_reasons: list[str] = []
    risk_reasons: list[str] = []
    trend_score = 50

    if ma20 and current > ma20:
        trend_score += 10
        bullish_reasons.append("价格站上MA20，短线趋势保持强势。")
    elif ma20:
        trend_score -= 10
        risk_reasons.append("价格位于MA20下方，短线承压。")

    if ma60 and current > ma60:
        trend_score += 10
        bullish_reasons.append("价格站上MA60，中期趋势没有明显走弱。")
    elif ma60:
        trend_score -= 10
        risk_reasons.append("价格位于MA60下方，中期趋势偏弱。")

    if ma20 and ma60 and ma20 > ma60:
        trend_score += 15
        bullish_reasons.append("MA20位于MA60上方，均线结构偏多。")
    elif ma20 and ma60:
        trend_score -= 15
        risk_reasons.append("MA20位于MA60下方，均线结构偏空。")

    if ma20 and ma20_previous and ma20 > ma20_previous:
        trend_score += 6
        bullish_reasons.append("MA20斜率向上，短期趋势仍在抬升。")
    elif ma20 and ma20_previous:
        trend_score -= 6

    if macd_state in {"金叉", "多头延续"}:
        trend_score += 10
        bullish_reasons.append(f"MACD处于{macd_state}状态，动能偏多。")
    elif macd_state in {"死叉", "空头延续"}:
        trend_score -= 10
        risk_reasons.append(f"MACD处于{macd_state}状态，动能偏空。")

    if rsi_value is not None:
        if 50 <= rsi_value <= 70:
            trend_score += 8
            bullish_reasons.append("RSI处于健康偏强区域，未明显过热。")
        elif rsi_value > 75:
            trend_score += 2
            risk_reasons.append("RSI进入过热区，短线追高风险上升。")
        elif rsi_value < 35:
            trend_score -= 8
            risk_reasons.append("RSI偏弱，反弹确认不足。")

    if len(highs) >= 20 and highs[-1] > max(highs[-20:-1]) and lows[-1] > min(lows[-20:-1]):
        trend_score += 8
        bullish_reasons.append("近期高低点抬升，结构偏多。")
    elif len(lows) >= 20 and lows[-1] < min(lows[-20:-1]):
        trend_score -= 8
        risk_reasons.append("近期低点被跌破，结构偏弱。")

    if volume_change > 20:
        trend_score += 6 if current >= closes[-2] else -6
        if current >= closes[-2]:
            bullish_reasons.append("成交量放大且价格上行，买盘承接较好。")
        else:
            risk_reasons.append("成交量放大但价格下行，抛压增强。")

    trend_score = max(0, min(100, int(round(trend_score))))
    trend_level = _trend_level(trend_score)

    risk_score = 25
    if rsi_value is not None and (rsi_value > 75 or rsi_value < 25):
        risk_score += 18
    if ma20:
        distance_ma20 = abs(current - ma20) / current * 100
        if distance_ma20 > 8:
            risk_score += 25
            risk_reasons.append("价格明显远离MA20，容易出现回撤修正。")
        elif distance_ma20 > 5:
            risk_score += 15
            risk_reasons.append("价格距离MA20偏远，短线波动风险增加。")
    if atr_value and current:
        volatility = atr_value / current * 100
        if volatility > 2.5:
            risk_score += 18
            risk_reasons.append("近期波动率偏高，止损空间需要放宽。")
        elif volatility > 1.2:
            risk_score += 8
    if structure == "假突破":
        risk_score += 25
        risk_reasons.append("出现假突破结构，容易诱多后回落。")
    if resistance and abs(resistance - current) / current * 100 < 1:
        risk_score += 10
        risk_reasons.append("现价接近上方压力位，突破前不宜重仓追多。")
    if support and abs(current - support) / current * 100 < 1:
        risk_score += 8
        risk_reasons.append("现价接近下方支撑位，跌破后可能加速。")

    ob = orderbook_analysis or {}
    buy_ratio = _to_float(ob.get("buy_ratio"))
    sell_ratio = _to_float(ob.get("sell_ratio"))
    if max(buy_ratio, sell_ratio) >= 68:
        risk_score += 12
        side = "买盘" if buy_ratio > sell_ratio else "卖盘"
        risk_reasons.append(f"盘口{side}占比过高，短线可能出现快速波动。")
    elif buy_ratio >= 55:
        bullish_reasons.append("盘口买盘略强，多头承接占优。")
    elif sell_ratio >= 55:
        risk_reasons.append("盘口卖盘略强，上方抛压需要观察。")

    risk_score = max(0, min(100, int(round(risk_score))))
    risk_level = _risk_level(risk_score)

    bullish_structure = structure in {"上升趋势", "突破", "回踩确认", "加速上涨"}
    bearish_structure = structure in {"下降趋势", "跌破", "加速下跌"}
    if risk_score >= 75:
        suggestion = "不建议追多" if trend_score >= 55 else "不建议追空" if trend_score <= 45 else "观望"
    elif trend_score >= 75 and bullish_structure:
        suggestion = "顺势做多"
    elif trend_score >= 60 and bullish_structure:
        suggestion = "轻仓试多"
    elif trend_score <= 25 and bearish_structure:
        suggestion = "顺势做空"
    elif trend_score <= 40 and bearish_structure:
        suggestion = "轻仓试空"
    else:
        suggestion = "观望"

    entry_zone = stop_loss = take_profit_1 = take_profit_2 = risk_reward = "当前不适合开仓，建议等待更明确结构。"
    if suggestion in {"轻仓试多", "顺势做多"}:
        stop = min(support or current - atr_value * 1.2, current - max(atr_value * 1.1, current * 0.006))
        tp1 = max(resistance or current + atr_value * 1.6, current + max(atr_value * 1.4, current * 0.012))
        tp2 = max(tp1 + max(atr_value, current * 0.01), current + max(atr_value * 2.6, current * 0.022))
        entry_zone = f"{_fmt_price(current * 0.995)} - {_fmt_price(current * 1.002)}"
        stop_loss = _fmt_price(stop)
        take_profit_1 = _fmt_price(tp1)
        take_profit_2 = _fmt_price(tp2)
        risk = max(current - stop, current * 0.001)
        reward = max(tp1 - current, current * 0.001)
        risk_reward = f"约1:{reward / risk:.1f}"
    elif suggestion in {"轻仓试空", "顺势做空"}:
        stop = max(resistance or current + atr_value * 1.2, current + max(atr_value * 1.1, current * 0.006))
        tp1 = min(support or current - atr_value * 1.6, current - max(atr_value * 1.4, current * 0.012))
        tp2 = min(tp1 - max(atr_value, current * 0.01), current - max(atr_value * 2.6, current * 0.022))
        entry_zone = f"{_fmt_price(current * 0.998)} - {_fmt_price(current * 1.005)}"
        stop_loss = _fmt_price(stop)
        take_profit_1 = _fmt_price(tp1)
        take_profit_2 = _fmt_price(tp2)
        risk = max(stop - current, current * 0.001)
        reward = max(current - tp1, current * 0.001)
        risk_reward = f"约1:{reward / risk:.1f}"

    if not bullish_reasons:
        bullish_reasons = ["当前没有形成高确定性的单边趋势，建议等待结构确认。"]
    if not risk_reasons:
        risk_reasons = ["暂未发现明显极端风险，但仍需控制仓位。"]

    trend_explanation = "；".join(bullish_reasons[:3]) if trend_score >= 50 else "；".join(risk_reasons[:3])
    risk_explanation = "；".join(risk_reasons[:3])

    return {
        "ready": True,
        "message": "",
        "market_structure": structure,
        "structure_explanation": structure_text,
        "trend_score": trend_score,
        "trend_level": trend_level,
        "trend_explanation": trend_explanation,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "risk_explanation": risk_explanation,
        "suggestion": suggestion,
        "entry_zone": entry_zone,
        "stop_loss": stop_loss,
        "take_profit_1": take_profit_1,
        "take_profit_2": take_profit_2,
        "risk_reward": risk_reward,
        "reasons": bullish_reasons[:5],
        "risks": risk_reasons[:5],
        "rsi": rsi_value,
        "macd_signal": macd_state,
        "macd_hist": macd_hist,
        "ma20": ma20,
        "ma60": ma60,
        "support": support,
        "resistance": resistance,
        "volume_change": volume_change,
    }

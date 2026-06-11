"""本地策略引擎统一出口。

本模块只读取本地缓存和各分析模块结果，不调用 DeepSeek/GPT/Gemini，
用于生成稳定、可解释、可复盘的策略决策。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, minimum: int = 0, maximum: int = 100) -> int:
    return max(minimum, min(maximum, int(round(value))))


def _fmt_price(value: Any) -> str:
    number = _to_float(value)
    if number <= 0:
        return "待确认"
    if abs(number) >= 1000:
        return f"{number:,.2f}"
    if abs(number) >= 1:
        return f"{number:.4f}".rstrip("0").rstrip(".")
    return f"{number:.8f}".rstrip("0").rstrip(".")


def _atr(rows: list[dict[str, Any]], window: int = 14) -> float:
    if len(rows) < window + 1:
        return 0.0
    ranges: list[float] = []
    previous_close = _to_float(rows[-window - 1].get("close"))
    for row in rows[-window:]:
        high = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = _to_float(row.get("close"))
    return mean(ranges) if ranges else 0.0


def _latest_ma(rows: list[dict[str, Any]], window: int) -> float | None:
    if len(rows) < window:
        return None
    closes = [_to_float(row.get("close")) for row in rows[-window:]]
    return mean(closes) if closes else None


def _volatility_percent(rows: list[dict[str, Any]]) -> float:
    if len(rows) < 20:
        return 0.0
    values = []
    for row in rows[-20:]:
        close = _to_float(row.get("close"))
        if close > 0:
            values.append((_to_float(row.get("high")) - _to_float(row.get("low"))) / close * 100)
    return mean(values) if values else 0.0


def _grade_data_quality(*, ticker: dict[str, Any] | None, rows: list[dict[str, Any]], orderbook: dict[str, Any] | None, derivatives: dict[str, Any] | None, whale: dict[str, Any] | None, radar: dict[str, Any] | None) -> dict[str, Any]:
    missing: list[str] = []
    if not ticker:
        missing.append("基础行情")
    if len(rows) < 80:
        missing.append("K线数据")
    if not orderbook:
        missing.append("盘口数据")
    if not derivatives:
        missing.append("衍生品数据")
    if not whale:
        missing.append("大单数据")
    if not radar:
        missing.append("风险雷达")
    if "基础行情" in missing or "K线数据" in missing or len(missing) >= 4:
        level = "poor"
    elif missing:
        level = "partial"
    else:
        level = "good"
    return {"level": level, "missing_fields": missing}


def _higher_timeframe_bias(analysis: dict[str, Any], capital: dict[str, Any] | None, radar: dict[str, Any] | None) -> str:
    trend_score = int(_to_float(analysis.get("trend_score"), 50))
    capital_score = int(_to_float((capital or {}).get("score"), 50))
    risk_score = int(_to_float((radar or {}).get("overall_score"), analysis.get("risk_score", 50)))
    if trend_score >= 65 and capital_score >= 55 and risk_score < 75:
        return "多头"
    if trend_score <= 35 and risk_score < 80:
        return "空头"
    return "中性"


def _direction_scores(
    analysis: dict[str, Any],
    orderbook: dict[str, Any] | None,
    derivatives: dict[str, Any] | None,
    capital: dict[str, Any] | None,
    liquidation: dict[str, Any] | None,
    whale: dict[str, Any] | None,
    dealer: dict[str, Any] | None,
    radar: dict[str, Any] | None,
) -> tuple[int, int, list[str], list[str]]:
    reasons: list[str] = []
    risks: list[str] = []
    structure = str(analysis.get("market_structure", "等待数据"))
    macd = str(analysis.get("macd_signal", "中性"))
    rsi = analysis.get("rsi")
    trend_score = int(_to_float(analysis.get("trend_score"), 50))
    buy_ratio = _to_float((orderbook or {}).get("buy_ratio"), 50)
    sell_ratio = _to_float((orderbook or {}).get("sell_ratio"), 50)
    oi_change = _to_float((((derivatives or {}).get("oi") or {}).get("changes") or {}).get("1h"))
    funding = _to_float(((derivatives or {}).get("funding") or {}).get("rate")) * 100
    long_short = _to_float(((derivatives or {}).get("long_short") or {}).get("account_ratio"), 1)
    whale_stats = (whale or {}).get("stats") or {}
    whale_5m = whale_stats.get("5m") or {}
    whale_net = _to_float(whale_5m.get("net_amount"))
    dealer_state = str((dealer or {}).get("state", "无明显行为"))
    radar_score = int(_to_float((radar or {}).get("overall_score"), analysis.get("risk_score", 50)))

    long_score = trend_score
    short_score = 100 - trend_score

    if structure in {"上升趋势", "突破", "回踩确认", "加速上涨"}:
        long_score += 14
        reasons.append(f"市场结构为{structure}，多头结构更清晰。")
    if structure in {"下降趋势", "跌破", "加速下跌"}:
        short_score += 14
        reasons.append(f"市场结构为{structure}，空头结构更清晰。")
    if structure == "横盘震荡":
        risks.append("市场处于横盘震荡，区间中部不适合重仓开仓。")

    if macd in {"金叉", "多头延续"}:
        long_score += 8
        reasons.append("动能指标偏多，说明短线仍有上攻基础。")
    if macd in {"死叉", "空头延续"}:
        short_score += 8
        reasons.append("动能指标偏空，说明短线抛压仍在。")

    if rsi is not None:
        rsi_value = _to_float(rsi)
        if trend_score >= 60:
            if 40 <= rsi_value <= 70:
                long_score += 8
                reasons.append("上升趋势中 RSI 处于健康区间，不属于极端追高。")
            elif rsi_value > 80:
                long_score -= 18
                risks.append("RSI 已明显过热，不建议追多。")
        elif trend_score <= 40:
            if 30 <= rsi_value <= 55:
                short_score += 7
                reasons.append("下跌趋势中 RSI 仍处弱势区，空头延续概率较高。")
            elif rsi_value < 20:
                short_score -= 15
                risks.append("RSI 过度超跌，继续追空容易遇到反弹。")

    if oi_change > 1 and funding < 0.05 and 0.6 <= long_short <= 1.8:
        if trend_score >= 50:
            long_score += 10
            reasons.append("价格与资金结构配合，OI增加且Funding未过热，趋势可信度提高。")
        else:
            short_score += 10
            reasons.append("下跌中 OI 增加，说明空头主动加仓概率上升。")
    if oi_change < -1:
        long_score -= 15
        short_score -= 8
        risks.append("OI下降，行情可能来自平仓或去杠杆，持续性需要观察。")
    if funding >= 0.08 or long_short >= 2:
        long_score -= 20
        risks.append("Funding或多空比过热，多头拥挤，不建议追多。")
    if funding <= -0.08 or (0 < long_short <= 0.5):
        short_score -= 20
        risks.append("空头过度拥挤，继续追空容易遇到空头回补反弹。")

    if buy_ratio >= 58:
        long_score += 8
        reasons.append("盘口买盘强于卖盘，短线承接较好。")
    if sell_ratio >= 58:
        short_score += 8
        reasons.append("盘口卖盘强于买盘，短线抛压较重。")
    if whale_net > 200_000:
        long_score += 8
        reasons.append("最近大单净流入，资金有主动进场迹象。")
    if whale_net < -200_000:
        short_score += 8
        risks.append("最近大单净流出，短线卖压增强。")

    if dealer_state in {"疑似吸筹", "疑似拉升"}:
        long_score += 7
        reasons.append(f"庄家行为初判为{dealer_state}，偏向多头观察。")
    if dealer_state in {"疑似派发", "高风险诱多"}:
        long_score -= 12
        risks.append(f"庄家行为初判为{dealer_state}，追多风险提高。")
    if dealer_state in {"高风险诱空"}:
        short_score -= 12
        risks.append("存在诱空风险，不建议盲目追空。")

    if radar_score >= 75:
        long_score -= 12
        short_score -= 12
        risks.append("综合风险偏高，任何方向都应降低仓位或等待确认。")

    return _clamp(long_score), _clamp(short_score), reasons, risks


def _strategy_name(direction: str, analysis: dict[str, Any], liquidation: dict[str, Any] | None, whale: dict[str, Any] | None, radar: dict[str, Any] | None) -> str:
    structure = str(analysis.get("market_structure", "等待数据"))
    squeeze = str((liquidation or {}).get("squeeze_state", "正常"))
    whale_level = str((whale or {}).get("level", "大单中性"))
    market_state = str((radar or {}).get("market_state", "等待数据"))
    if direction == "neutral":
        if structure == "横盘震荡":
            return "震荡区间"
        return "无有效策略"
    if structure in {"回踩确认"}:
        return "回踩确认"
    if structure in {"假突破"}:
        return "假突破反打"
    if "挤压" in squeeze or "踩踏" in squeeze:
        return "清算猎杀"
    if whale_level in {"大单活跃", "大单极强"}:
        return "大单跟随"
    if structure in {"上升趋势", "下降趋势", "突破", "跌破", "加速上涨", "加速下跌"} or "健康" in market_state:
        return "趋势跟随"
    return "无有效策略"


def _risk_gate(
    direction: str,
    analysis: dict[str, Any],
    radar: dict[str, Any] | None,
    liquidation: dict[str, Any] | None,
    derivatives: dict[str, Any] | None,
    orderbook: dict[str, Any] | None,
    whale: dict[str, Any] | None,
    data_quality: dict[str, Any],
    rr_value: float | None,
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    radar_score = int(_to_float((radar or {}).get("overall_score"), analysis.get("risk_score", 50)))
    liq_level = str((liquidation or {}).get("risk_level", "中等风险"))
    squeeze = str((liquidation or {}).get("squeeze_state", "正常"))
    funding = _to_float(((derivatives or {}).get("funding") or {}).get("rate")) * 100
    long_short = _to_float(((derivatives or {}).get("long_short") or {}).get("account_ratio"), 1)
    buy_ratio = _to_float((orderbook or {}).get("buy_ratio"), 50)
    sell_ratio = _to_float((orderbook or {}).get("sell_ratio"), 50)
    whale_net = _to_float((((whale or {}).get("stats") or {}).get("5m") or {}).get("net_amount"))
    structure = str(analysis.get("market_structure", "等待数据"))

    if data_quality.get("level") == "poor":
        warnings.append("关键数据质量不足，禁止开仓。")
    if radar_score >= 85:
        warnings.append("综合风险评分过高，禁止新增仓位。")
    if liq_level == "极高风险":
        warnings.append("清算风险极高，容易出现快速扫损。")
    if abs(funding) >= 0.08:
        warnings.append("Funding处于极端区间，拥挤风险较高。")
    if direction == "long" and _to_float((((derivatives or {}).get("oi") or {}).get("changes") or {}).get("1h")) < -2:
        warnings.append("上涨伴随OI下降，可能是空头回补，不适合重仓追多。")
    if long_short >= 2 or (0 < long_short <= 0.5):
        warnings.append("多空比过度倾斜，容易出现反向收割。")
    if direction == "long" and sell_ratio >= 65:
        warnings.append("盘口卖盘明显强于买盘，与做多方向冲突。")
    if direction == "short" and buy_ratio >= 65:
        warnings.append("盘口买盘明显强于卖盘，与做空方向冲突。")
    if direction == "long" and whale_net < -300_000:
        warnings.append("大单持续净流出，与做多方向冲突。")
    if direction == "short" and whale_net > 300_000:
        warnings.append("大单持续净流入，与做空方向冲突。")
    if direction == "long" and "诱多" in squeeze:
        warnings.append("存在诱多风险，禁止追多。")
    if direction == "short" and "诱空" in squeeze:
        warnings.append("存在诱空风险，禁止追空。")
    if structure == "横盘震荡" and int(_to_float(analysis.get("trend_score"), 50)) < 55:
        warnings.append("震荡环境趋势评分不足，区间中部不建议开仓。")
    if rr_value is not None and rr_value < 1.2:
        warnings.append("风险收益比不足 1:1.2，不具备高质量交易条件。")

    if warnings:
        return "blocked", warnings
    if data_quality.get("level") == "partial" or radar_score >= 65:
        return "cautious", ["部分数据缺失或风险偏高，仅允许轻仓观察。"]
    return "allowed", []


def _price_plan(direction: str, price: float, rows: list[dict[str, Any]], analysis: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], str | None, str]:
    if direction == "neutral" or price <= 0 or len(rows) < 20:
        empty = {"price": None, "reason": "无有效入场，不设置。"}
        return {"low": None, "high": None, "text": "当前不适合开仓"}, empty, empty, empty, None, "信号不足，等待结构确认。"

    atr = _atr(rows)
    atr = atr if atr > 0 else max(price * 0.004, 0.00000001)
    support = _to_float(analysis.get("support"))
    resistance = _to_float(analysis.get("resistance"))
    ma20 = _to_float(analysis.get("ma20"))
    recent_low = min(_to_float(row.get("low")) for row in rows[-20:])
    recent_high = max(_to_float(row.get("high")) for row in rows[-20:])

    if direction == "long":
        entry_low = max(price - atr * 0.45, 0)
        entry_high = price + atr * 0.15
        stop_price = min(x for x in [recent_low - atr * 0.25, support - atr * 0.2 if support > 0 else price - atr * 1.2, ma20 - atr * 0.7 if ma20 > 0 else price - atr * 1.2] if x > 0)
        risk = max(price - stop_price, atr * 0.8)
        tp1 = max(resistance, price + risk * 1.2) if resistance > price else price + risk * 1.2
        tp2 = price + risk * 2.1
        invalid = f"跌破 {_fmt_price(stop_price)} 后，多头结构失效。"
    else:
        entry_low = price - atr * 0.15
        entry_high = price + atr * 0.45
        stop_price = max(recent_high + atr * 0.25, resistance + atr * 0.2 if resistance > 0 else price + atr * 1.2, ma20 + atr * 0.7 if ma20 > 0 else price + atr * 1.2)
        risk = max(stop_price - price, atr * 0.8)
        tp1 = min(support, price - risk * 1.2) if support > 0 and support < price else price - risk * 1.2
        tp2 = price - risk * 2.1
        invalid = f"突破 {_fmt_price(stop_price)} 后，空头结构失效。"

    reward = abs(tp1 - price)
    rr = reward / risk if risk > 0 else None
    return (
        {"low": entry_low, "high": entry_high, "text": f"{_fmt_price(entry_low)} - {_fmt_price(entry_high)}"},
        {"price": stop_price, "reason": "参考近期结构位、MA20 与 ATR 波动空间。"},
        {"price": tp1, "reason": "参考第一目标位、压力/支撑位与约1R空间。"},
        {"price": tp2, "reason": "参考第二目标位与约2R空间。"},
        f"1:{rr:.1f}" if rr is not None else None,
        invalid,
    )


def _position_suggestion(permission: str, confidence: int, risk_score: int, data_quality: str) -> str:
    if permission == "blocked" or data_quality == "poor":
        return "0%"
    if risk_score >= 80:
        return "0%"
    if risk_score >= 65:
        return "1%-3%"
    if data_quality == "partial":
        return "3%-5%" if confidence >= 65 else "1%-3%"
    if confidence >= 82 and risk_score <= 40:
        return "10%-15%"
    if confidence >= 70 and risk_score <= 55:
        return "5%-10%"
    return "3%-5%"


def _parse_rr_value(risk_reward_ratio: str | None) -> float | None:
    if not risk_reward_ratio or ":" not in risk_reward_ratio:
        return None
    return _to_float(risk_reward_ratio.split(":", 1)[1], 0.0)


def _vote_grade(score: int) -> str:
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def _vote_decision(score: int) -> str:
    if score >= 80:
        return "支持交易"
    if score >= 70:
        return "轻仓支持"
    if score >= 60:
        return "只观察"
    return "反对交易"


def _local_vote_score(
    *,
    opportunity_score: int,
    confidence: int,
    risk_score: int,
    raw_scores: dict[str, int],
    permission: str,
    data_quality: dict[str, Any],
    risk_reward_ratio: str | None,
    warnings: list[str],
    direction: str,
    higher_bias: str,
) -> dict[str, Any]:
    """生成本地策略委员投票结果，供后续AI交易委员会使用。"""
    trend = int(raw_scores.get("trend_score", 0) or 0)
    capital = int(raw_scores.get("capital_score", 0) or 0)
    risk_control = 100 - int(risk_score or 0)
    base_score = (
        opportunity_score * 0.35
        + confidence * 0.25
        + trend * 0.15
        + capital * 0.10
        + risk_control * 0.15
    )
    score = _clamp(base_score)
    caps: list[tuple[int, str]] = []
    rr_value = _parse_rr_value(risk_reward_ratio)

    if permission == "blocked":
        caps.append((59, "风控门禁已阻止开仓，本地委员不能投支持票。"))
    if data_quality.get("level") == "poor":
        caps.append((49, "关键数据质量不足，只能反对交易。"))
    if risk_score >= 85:
        caps.append((49, "综合风险极高，投票分强制降级。"))
    if rr_value is not None and rr_value < 1.2:
        caps.append((59, "风险收益比不足，不能进入支持交易区间。"))
    if direction == "long" and higher_bias == "空头":
        caps.append((69, "短线偏多但大方向偏空，多周期存在冲突。"))
    if direction == "short" and higher_bias == "多头":
        caps.append((69, "短线偏空但大方向偏多，多周期存在冲突。"))
    if any("冲突" in str(item) for item in warnings):
        caps.append((69, "盘口或大单方向存在冲突，投票只允许观察。"))

    if caps:
        cap_value = min(item[0] for item in caps)
        score = min(score, cap_value)
        reason = "；".join(item[1] for item in caps[:3])
    elif score >= 80:
        reason = "趋势、机会、置信度和风险控制形成较好共振，本地委员投支持票。"
    elif score >= 70:
        reason = "信号具备一定质量，但仍需控制仓位，本地委员轻仓支持。"
    elif score >= 60:
        reason = "信号未形成高胜率共振，本地委员建议只观察。"
    else:
        reason = "机会质量或风险控制不足，本地委员反对交易。"

    grade = _vote_grade(score)
    return {
        "local_vote_score": score,
        "local_vote_grade": grade,
        "local_vote_decision": _vote_decision(score),
        "local_vote_reason": reason,
    }


def _classify_explanations(direction: str, reasons: list[str], risks: list[str], warnings: list[str], permission: str, data_quality: dict[str, Any]) -> dict[str, list[str]]:
    """把策略解释拆成页面可读的中文分区。"""
    long_words = ("多头", "上升", "突破", "金叉", "买盘", "承接", "净流入", "吸筹", "拉升", "做多", "偏多")
    short_words = ("空头", "下跌", "跌破", "死叉", "卖盘", "抛压", "净流出", "派发", "做空", "偏空")
    conflict_words = ("冲突", "过热", "拥挤", "诱多", "诱空", "风险收益比", "方向")

    long_reasons = [item for item in reasons if any(word in item for word in long_words)]
    short_reasons = [item for item in reasons + risks if any(word in item for word in short_words)]
    conflicts = [item for item in warnings if any(word in item for word in conflict_words)]
    blocked_reasons = warnings[:] if permission == "blocked" else []

    if direction == "long" and not long_reasons:
        long_reasons.append("当前偏多信号主要来自综合评分，但仍需要等待价格结构继续确认。")
    if direction == "short" and not short_reasons:
        short_reasons.append("当前偏空信号主要来自综合评分，但仍需要等待价格结构继续确认。")
    if direction == "neutral":
        long_reasons.append("当前多头条件不足，暂未形成高质量做多信号。")
        short_reasons.append("当前空头条件不足，暂未形成高质量做空信号。")
    if not conflicts:
        conflicts.append("暂未发现明确的方向冲突，但仍需观察K线、盘口和资金是否继续同步。")
    if not blocked_reasons and permission != "blocked":
        blocked_reasons.append("当前未触发硬性禁止开仓条件，但仍需按建议仓位控制风险。")
    if data_quality.get("level") == "poor" and "当前数据质量不足，不建议开仓。" not in blocked_reasons:
        blocked_reasons.append("当前数据质量不足，不建议开仓。")

    return {
        "long_reasons": long_reasons[:6],
        "short_reasons": short_reasons[:6],
        "current_risks": (risks[:] or ["暂无极端风险信号，但仍需等待价格结构确认。"])[:6],
        "signal_conflicts": conflicts[:6],
        "blocked_reasons": blocked_reasons[:6],
    }


def _score_level(score: int, *, risk: bool = False) -> str:
    if risk:
        if score >= 80:
            return "极高"
        if score >= 60:
            return "高"
        if score >= 40:
            return "中"
        return "低"
    if score >= 80:
        return "强"
    if score >= 60:
        return "偏强"
    if score >= 40:
        return "中性"
    if score >= 20:
        return "偏弱"
    return "弱"


def _score_breakdown(raw_scores: dict[str, int], opportunity_score: int, risk_score: int, direction: str) -> list[dict[str, Any]]:
    """生成本地策略评分拆解，全部使用中文解释。"""
    orderbook_side = "买盘" if direction == "long" else "卖盘" if direction == "short" else "盘口主导一侧"
    items = [
        {
            "name": "趋势评分",
            "score": int(raw_scores.get("trend_score", 0)),
            "level": _score_level(int(raw_scores.get("trend_score", 0))),
            "explanation": "来自均线位置、市场结构、动能变化和近期高低点变化，用于判断当前方向是否顺势。",
        },
        {
            "name": "资金结构评分",
            "score": int(raw_scores.get("capital_score", 0)),
            "level": _score_level(int(raw_scores.get("capital_score", 0))),
            "explanation": "来自OI、Funding、多空比和资金状态，用于判断新增资金是否支持当前行情。",
        },
        {
            "name": "盘口评分",
            "score": int(raw_scores.get("orderbook_score", 0)),
            "level": _score_level(int(raw_scores.get("orderbook_score", 0))),
            "explanation": f"来自买卖盘口深度和买卖力量比例，当前更关注{orderbook_side}是否支持交易方向。",
        },
        {
            "name": "清算风险评分",
            "score": int(raw_scores.get("liquidation_score", 0)),
            "level": _score_level(int(raw_scores.get("liquidation_score", 0)), risk=True),
            "explanation": "来自上方/下方清算区、挤仓风险和止损猎杀概率；分数越高代表爆仓风险越大。",
        },
        {
            "name": "大单评分",
            "score": int(raw_scores.get("whale_score", 0)),
            "level": _score_level(int(raw_scores.get("whale_score", 0))),
            "explanation": "来自主动买入/卖出大单、大单净流入和大单连续性，用于观察大资金是否参与。",
        },
        {
            "name": "多周期评分",
            "score": int(raw_scores.get("multi_timeframe_score", 0)),
            "level": _score_level(int(raw_scores.get("multi_timeframe_score", 0))),
            "explanation": "来自当前周期和大方向的一致性；分数越高代表多周期共振越清晰。",
        },
        {
            "name": "综合风险评分",
            "score": int(raw_scores.get("risk_radar_score", risk_score)),
            "level": _score_level(int(raw_scores.get("risk_radar_score", risk_score)), risk=True),
            "explanation": "来自趋势、资金、盘口、清算、大单和波动率综合风险；分数越高越需要降低仓位。",
        },
        {
            "name": "机会评分",
            "score": int(opportunity_score),
            "level": _score_level(int(opportunity_score)),
            "explanation": "综合趋势、资金、盘口、大单和风险控制后的机会质量，用于判断是否值得进入委员会投票。",
        },
    ]
    return items


def _conservative_handling(data_quality: dict[str, Any]) -> str:
    level = str(data_quality.get("level", "poor"))
    if level == "good":
        return "核心数据较完整，按正常策略规则输出，但仍不代表确定性收益。"
    if level == "partial":
        return "部分数据缺失，策略已自动降低仓位上限，并采用更保守的风险判断。"
    return "当前数据质量不足，不建议开仓。策略只保留观察结论，禁止输出积极交易建议。"


def build_local_strategy(
    *,
    symbol: str,
    ticker: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    signal_analysis: dict[str, Any],
    orderbook_analysis: dict[str, Any] | None,
    derivatives: dict[str, Any] | None,
    capital: dict[str, Any] | None,
    liquidation: dict[str, Any] | None,
    whale: dict[str, Any] | None,
    dealer: dict[str, Any] | None,
    radar: dict[str, Any] | None,
    primary_timeframe: str = "15m",
) -> dict[str, Any]:
    """生成统一本地策略结果。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    price = _to_float((ticker or {}).get("last_price"))
    data_quality = _grade_data_quality(ticker=ticker, rows=rows, orderbook=orderbook_analysis, derivatives=derivatives, whale=whale, radar=radar)
    long_score, short_score, reasons, risks = _direction_scores(signal_analysis, orderbook_analysis, derivatives, capital, liquidation, whale, dealer, radar)
    score_gap = abs(long_score - short_score)
    if data_quality["level"] == "poor" or score_gap < 10:
        direction = "neutral"
    elif long_score > short_score:
        direction = "long"
    else:
        direction = "short"

    entry_zone, stop_loss, tp1, tp2, rr, invalid_condition = _price_plan(direction, price, rows, signal_analysis)
    rr_value = None
    if rr and ":" in rr:
        rr_value = _to_float(rr.split(":", 1)[1])
    permission, gate_warnings = _risk_gate(direction, signal_analysis, radar, liquidation, derivatives, orderbook_analysis, whale, data_quality, rr_value)
    warnings = gate_warnings[:]
    if data_quality["level"] == "partial":
        warnings.append("部分策略数据暂不可用，已基于现有数据进行保守分析。")
    if data_quality["level"] == "poor":
        warnings.append("当前数据质量不足，不建议开仓。")

    if permission == "blocked":
        action = "禁止开仓"
        direction = "neutral" if direction == "neutral" else direction
    elif direction == "long":
        action = "顺势做多" if long_score >= 76 else "轻仓试多"
    elif direction == "short":
        action = "顺势做空" if short_score >= 76 else "轻仓试空"
    else:
        action = "观望"

    risk_score = int(_to_float((radar or {}).get("overall_score"), signal_analysis.get("risk_score", 50)))
    opportunity_score = max(long_score, short_score) if direction != "neutral" else min(max(long_score, short_score), 60)
    confidence = _clamp(opportunity_score - max(0, risk_score - 55) * 0.35 - (12 if data_quality["level"] == "partial" else 0) - (35 if data_quality["level"] == "poor" else 0))
    if permission == "blocked":
        confidence = min(confidence, 55)
    position = _position_suggestion(permission, confidence, risk_score, data_quality["level"])
    strategy_name = _strategy_name(direction, signal_analysis, liquidation, whale, radar)
    higher_bias = _higher_timeframe_bias(signal_analysis, capital, radar)

    if not reasons:
        reasons.append("当前多空信号不够统一，策略引擎以保守观察为主。")
    if not risks:
        risks.append("暂无极端风险信号，但仍需等待价格结构确认。")
    if direction == "neutral" and "多周期方向冲突或信号不足，建议观望。" not in risks:
        risks.append("多周期方向冲突或信号不足，建议观望。")

    raw_scores = {
        "trend_score": int(_to_float(signal_analysis.get("trend_score"), 0)),
        "capital_score": int(_to_float((capital or {}).get("score"), 0)),
        "orderbook_score": int(_to_float((orderbook_analysis or {}).get("buy_ratio" if direction == "long" else "sell_ratio"), 0)),
        "liquidation_score": int(_to_float((liquidation or {}).get("risk_score"), 0)),
        "whale_score": int(_to_float((whale or {}).get("score"), 0)),
        "risk_radar_score": risk_score,
        "multi_timeframe_score": _clamp((long_score if direction == "long" else short_score if direction == "short" else 50)),
    }
    analysis_sections = _classify_explanations(direction, reasons, risks, warnings, permission, data_quality)
    score_breakdown = _score_breakdown(raw_scores, opportunity_score, risk_score, direction)
    vote = _local_vote_score(
        opportunity_score=opportunity_score,
        confidence=confidence,
        risk_score=risk_score,
        raw_scores=raw_scores,
        permission=permission,
        data_quality=data_quality,
        risk_reward_ratio=rr,
        warnings=warnings,
        direction=direction,
        higher_bias=higher_bias,
    )

    result = {
        "symbol": symbol,
        "timestamp": now,
        "primary_timeframe": primary_timeframe,
        "higher_timeframe_bias": higher_bias,
        "direction": direction,
        "action": action,
        "confidence": confidence,
        "risk_score": risk_score,
        "opportunity_score": opportunity_score,
        "strategy_name": strategy_name,
        "trade_permission": permission,
        "position_suggestion": position,
        "entry_zone": entry_zone,
        "stop_loss": stop_loss,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "risk_reward_ratio": rr,
        "invalid_condition": invalid_condition,
        "reasons": reasons[:8],
        "risks": risks[:8],
        "warnings": warnings[:8],
        "data_quality": data_quality,
        "data_quality_handling": _conservative_handling(data_quality),
        "analysis_sections": analysis_sections,
        "score_breakdown": score_breakdown,
        "raw_scores": raw_scores,
        **vote,
    }
    return result


def get_local_strategy_summary(result: dict[str, Any]) -> dict[str, Any]:
    """为 AI交易委员会、模拟交易和机会榜提供精简策略摘要。"""
    return {
        "symbol": result.get("symbol"),
        "direction": result.get("direction"),
        "action": result.get("action"),
        "confidence": result.get("confidence"),
        "local_vote_score": result.get("local_vote_score"),
        "local_vote_grade": result.get("local_vote_grade"),
        "local_vote_decision": result.get("local_vote_decision"),
        "risk_score": result.get("risk_score"),
        "strategy_name": result.get("strategy_name"),
        "trade_permission": result.get("trade_permission"),
        "position_suggestion": result.get("position_suggestion"),
        "main_reasons": list(result.get("reasons") or [])[:3],
        "main_risks": list(result.get("risks") or [])[:3],
        "invalid_condition": result.get("invalid_condition", ""),
    }


def append_strategy_log(result: dict[str, Any], log_path: str = "local_strategy_log.json") -> None:
    """写入轻量策略日志，供后续复盘学习中心使用。"""
    try:
        path = Path(log_path)
        rows: list[dict[str, Any]] = []
        if path.exists():
            rows = json.loads(path.read_text(encoding="utf-8") or "[]")
            if not isinstance(rows, list):
                rows = []
        rows.append(
            {
                "时间": result.get("timestamp"),
                "交易对象": result.get("symbol"),
                "方向": result.get("direction"),
                "建议": result.get("action"),
                "置信度": result.get("confidence"),
                "本地投票分": result.get("local_vote_score"),
                "投票评级": result.get("local_vote_grade"),
                "投票决议": result.get("local_vote_decision"),
                "风险评分": result.get("risk_score"),
                "策略类型": result.get("strategy_name"),
                "建议仓位": result.get("position_suggestion"),
                "主要理由": "；".join(list(result.get("reasons") or [])[:3]),
                "主要风险": "；".join(list(result.get("risks") or [])[:3]),
                "数据质量": (result.get("data_quality") or {}).get("level"),
            }
        )
        path.write_text(json.dumps(rows[-300:], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[本地策略日志] 写入失败 error={repr(exc)}")

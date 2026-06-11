"""市场风险雷达与综合风险评分引擎。

整合趋势、资金、盘口、清算、大单、庄家行为与波动率数据，输出统一风险评分。
本模块只做本地计算，不访问外部 API。
"""

from __future__ import annotations

from statistics import mean
from typing import Any


def _to_float(value: Any, default: float = 0.0) -> float:
    """安全转换为浮点数。"""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _risk_level(score: int) -> str:
    if score <= 20:
        return "低风险"
    if score <= 40:
        return "偏低风险"
    if score <= 60:
        return "中等风险"
    if score <= 80:
        return "高风险"
    return "极高风险"


def _sub_level(score: int) -> str:
    if score <= 30:
        return "低"
    if score <= 60:
        return "中"
    if score <= 80:
        return "高"
    return "极高"


def _volatility_score(rows: list[dict[str, Any]]) -> tuple[int, str]:
    """根据近期平均波幅估算波动率风险。"""
    if len(rows) < 20:
        return 50, "K线数据不足，波动率按中性处理。"
    ranges = []
    for row in rows[-20:]:
        high = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        close = _to_float(row.get("close"))
        if close > 0:
            ranges.append((high - low) / close * 100)
    avg_range = mean(ranges) if ranges else 0
    if avg_range >= 3:
        return 85, "近期K线波动率极高，容易出现快速扫损。"
    if avg_range >= 1.8:
        return 70, "近期波动率偏高，仓位需要降低。"
    if avg_range >= 0.9:
        return 50, "近期波动率中等，注意止损宽度。"
    return 25, "近期波动率较低，短线风险相对可控。"


def _component(score: int, title: str, reason: str) -> dict[str, Any]:
    """生成风险分项。"""
    clean_score = max(0, min(100, int(round(score))))
    return {
        "name": title,
        "score": clean_score,
        "level": _sub_level(clean_score),
        "reason": reason,
    }


def _position_advice(overall_score: int, trend_score: int) -> tuple[str, str, str]:
    """根据风险与趋势给出安全等级和仓位建议。"""
    if overall_score >= 85:
        return "禁止开仓", "0%", "综合风险极高，优先保护本金，不建议新增仓位。"
    if overall_score >= 70:
        return "不建议开仓", "1%-3%", "风险处于高位，只适合极轻仓观察，不适合追涨杀跌。"
    if overall_score >= 55:
        return "谨慎交易", "3%-5%", "风险中等偏高，如需参与应降低仓位并设置严格止损。"
    if overall_score >= 35:
        return "轻仓可试", "5%-10%", "风险相对可控，但仍需等待结构确认。"
    if trend_score >= 65:
        return "可交易", "10%-15%", "风险较低且趋势较明确，可在规则内小仓位顺势观察。"
    return "轻仓可试", "5%-10%", "风险较低，但趋势不够明确，建议轻仓或等待。"


def analyze_market_risk_radar(
    ticker: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    derivatives: dict[str, Any] | None,
    capital: dict[str, Any] | None,
    liquidation: dict[str, Any] | None,
    whale: dict[str, Any] | None,
    dealer: dict[str, Any] | None,
    orderbook_analysis: dict[str, Any] | None,
    signal_analysis: dict[str, Any] | None,
) -> dict[str, Any]:
    """输出市场综合风险评分、分项拆解和交易安全建议。"""
    signal_analysis = signal_analysis or {}
    derivatives = derivatives or {}
    capital = capital or {}
    liquidation = liquidation or {}
    whale = whale or {}
    dealer = dealer or {}
    orderbook_analysis = orderbook_analysis or {}

    if not ticker and not rows:
        return {
            "ready": False,
            "overall_score": 0,
            "risk_level": "待评估",
            "market_state": "等待数据",
            "market_explanation": "市场风险雷达暂不可用，请稍后重试。",
            "trade_safety": "等待数据",
            "position_size": "0%",
            "position_explanation": "等待行情数据同步。",
            "components": [],
            "alerts": ["市场风险雷达暂不可用，请稍后重试。"],
        }

    trend_score = int(_to_float(signal_analysis.get("trend_score"), 50))
    signal_risk = int(_to_float(signal_analysis.get("risk_score"), 50))
    structure = str(signal_analysis.get("market_structure", "等待数据"))
    capital_score = int(_to_float(capital.get("score"), 50))
    capital_state = str(capital.get("state", "资金观望"))
    capital_market_state = str(capital.get("market_state", "资金观望"))
    liq_score = int(_to_float(liquidation.get("risk_score"), 50))
    liq_state = str(liquidation.get("squeeze_state", "正常"))
    whale_score = int(_to_float(whale.get("score"), 0))
    whale_stats = whale.get("stats") or {}
    whale_5m = whale_stats.get("5m") or {}
    whale_net = _to_float(whale_5m.get("net_amount"))
    dealer_state = str(dealer.get("state", "无明显行为"))
    buy_ratio = _to_float(orderbook_analysis.get("buy_ratio"))
    sell_ratio = _to_float(orderbook_analysis.get("sell_ratio"))
    funding_rate = _to_float((derivatives.get("funding") or {}).get("rate")) * 100
    long_short_ratio = _to_float((derivatives.get("long_short") or {}).get("account_ratio"), 1.0)
    oi_change = _to_float(((derivatives.get("oi") or {}).get("changes") or {}).get("1h"))

    trend_risk = max(0, min(100, signal_risk + (20 if trend_score <= 30 else 0) - (10 if trend_score >= 75 else 0)))
    capital_risk = max(0, min(100, 100 - capital_score + (18 if "过热" in capital_state else 0)))
    if abs(funding_rate) >= 0.08 or long_short_ratio >= 2 or (0 < long_short_ratio <= 0.5):
        capital_risk = max(capital_risk, 85)
    elif abs(funding_rate) >= 0.03 or long_short_ratio >= 1.5 or (0 < long_short_ratio <= 0.7):
        capital_risk = max(capital_risk, 65)
    orderbook_risk = 35
    if max(buy_ratio, sell_ratio) >= 70:
        orderbook_risk = 80
    elif max(buy_ratio, sell_ratio) >= 60:
        orderbook_risk = 62
    liquidation_risk = liq_score
    whale_risk = 35
    if whale_score >= 80:
        whale_risk = 70
    elif whale_score >= 60:
        whale_risk = 58
    if whale_net < -500_000:
        whale_risk += 18
    whale_risk = max(0, min(100, whale_risk))
    dealer_risk = 30
    if dealer_state in {"高风险诱多", "高风险诱空"}:
        dealer_risk = 88
    elif dealer_state in {"疑似派发", "疑似洗盘"}:
        dealer_risk = 72
    elif dealer_state in {"疑似拉升", "疑似吸筹"}:
        dealer_risk = 45
    volatility_risk, volatility_reason = _volatility_score(rows)

    components = [
        _component(trend_risk, "趋势风险", f"当前趋势评分{trend_score}，结构为{structure}。"),
        _component(capital_risk, "资金风险", f"Funding约{funding_rate:+.4f}%，多空比{long_short_ratio:.2f}，OI 1小时变化{oi_change:+.2f}%。"),
        _component(orderbook_risk, "盘口风险", f"买盘{buy_ratio:.1f}%，卖盘{sell_ratio:.1f}%，盘口失衡越大短线波动越强。"),
        _component(liquidation_risk, "清算风险", str(liquidation.get("explanation", "清算风险数据正在同步。"))),
        _component(whale_risk, "大单风险", f"大单强度{whale_score}，5分钟净流入{whale_net:,.0f} USDT。"),
        _component(dealer_risk, "庄家行为风险", f"当前状态：{dealer_state}。"),
        _component(volatility_risk, "波动率风险", volatility_reason),
    ]
    weighted = (
        trend_risk * 0.16
        + capital_risk * 0.18
        + orderbook_risk * 0.12
        + liquidation_risk * 0.20
        + whale_risk * 0.12
        + dealer_risk * 0.12
        + volatility_risk * 0.10
    )
    overall_score = max(0, min(100, int(round(weighted))))
    risk_level = _risk_level(overall_score)

    alerts: list[str] = []
    if funding_rate >= 0.08:
        alerts.append("高风险提醒：Funding过高，多头过度拥挤，若跌破短线支撑可能触发多头踩踏。")
    elif funding_rate <= -0.08:
        alerts.append("高风险提醒：Funding过低，空头过度拥挤，若突破压力可能触发空头回补。")
    if long_short_ratio >= 2 or (0 < long_short_ratio <= 0.5):
        alerts.append("警报：多空比极端，市场站队过于一致，容易被反向收割。")
    if oi_change >= 8 and abs(_to_float((ticker or {}).get("price_change_percent"))) < 1:
        alerts.append("警报：OI快速增加但价格停滞，可能存在堆仓后扫损风险。")
    if liq_score >= 80:
        alerts.append("警报：清算风险极高，价格接近强平密集区。")
    if whale_net < -500_000:
        alerts.append("警报：大单持续净流出，短线卖压增强。")
    if dealer_state in {"高风险诱多", "高风险诱空"}:
        alerts.append(f"警报：{dealer_state}，不宜追单。")
    if liq_state == "高风险双向震荡":
        alerts.append("警报：上下方清算压力都较强，存在多空双杀风险。")
    if not alerts:
        alerts.append("当前未触发极端风险警报，但仍需按计划控制仓位。")

    if overall_score >= 88:
        market_state = "极端风险"
        market_explanation = "多个风险源同时升高，优先等待市场冷却。"
    elif liq_state == "高风险双向震荡":
        market_state = "多空双杀风险"
        market_explanation = "上下方清算区都较近，价格可能反复扫损。"
    elif dealer_state == "高风险诱多":
        market_state = "疑似诱多"
        market_explanation = "上涨过程中情绪或资金过热，存在冲高回落风险。"
    elif dealer_state == "高风险诱空":
        market_state = "疑似诱空"
        market_explanation = "下跌过程中空头过度拥挤，存在急速反抽风险。"
    elif "危险上涨" in capital_market_state or (trend_score >= 65 and overall_score >= 60):
        market_state = "高风险上涨"
        market_explanation = "趋势偏强但风险源较多，不适合盲目追多。"
    elif trend_score <= 35 and overall_score >= 60:
        market_state = "高风险下跌"
        market_explanation = "趋势偏弱且风险较高，反弹和下杀都可能加剧。"
    elif funding_rate >= 0.03 or long_short_ratio >= 1.5:
        market_state = "多头拥挤"
        market_explanation = "多头情绪偏强，需防止一致性过高后的反向波动。"
    elif funding_rate <= -0.03 or (0 < long_short_ratio <= 0.7):
        market_state = "空头拥挤"
        market_explanation = "空头情绪偏强，需防止空头回补。"
    elif trend_score >= 65 and overall_score < 55:
        market_state = "健康上涨"
        market_explanation = "趋势偏强且综合风险未过热。"
    elif trend_score <= 35 and overall_score < 55:
        market_state = "健康下跌"
        market_explanation = "趋势偏弱但风险未极端，顺势观察更合理。"
    else:
        market_state = "震荡观望"
        market_explanation = "风险与趋势暂未形成强共振，适合等待更明确结构。"

    trade_safety, position_size, position_explanation = _position_advice(overall_score, trend_score)
    missing_parts = []
    if not derivatives:
        missing_parts.append("衍生品")
    if not whale:
        missing_parts.append("大单")
    if not liquidation:
        missing_parts.append("清算")
    if missing_parts:
        alerts.insert(0, f"部分风险数据暂不可用：{'、'.join(missing_parts)}，已基于现有数据进行分析。")

    return {
        "ready": True,
        "overall_score": overall_score,
        "risk_level": risk_level,
        "market_state": market_state,
        "market_explanation": market_explanation,
        "trade_safety": trade_safety,
        "position_size": position_size,
        "position_explanation": position_explanation,
        "components": components,
        "alerts": alerts,
    }

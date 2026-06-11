"""资金结构与衍生品市场状态分析引擎。"""

from __future__ import annotations

from typing import Any


def _to_float(value: Any, default: float = 0.0) -> float:
    """安全转换为浮点数。"""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _score_level(score: int) -> str:
    if score <= 20:
        return "极弱"
    if score <= 40:
        return "偏弱"
    if score <= 60:
        return "中性"
    if score <= 80:
        return "偏强"
    return "极强"


def _capital_state(score: int, funding_rate: float, long_short_ratio: float, oi_change_1h: float) -> str:
    """判断资金结构状态。"""
    funding_percent = funding_rate * 100
    if abs(funding_percent) >= 0.08 or long_short_ratio >= 2.0 or (0 < long_short_ratio <= 0.5):
        return "资金过热"
    if oi_change_1h >= 2 and 45 <= score <= 85:
        return "资金流入"
    if oi_change_1h <= -2:
        return "资金流出"
    if score <= 30:
        return "资金恐慌"
    return "资金观望"


def analyze_capital_structure(
    derivatives: dict[str, Any] | None,
    ticker: dict[str, Any] | None,
    signal_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """综合 OI、Funding、多空比与价格趋势，生成资金结构评分。"""
    if not derivatives:
        return {
            "ready": False,
            "score": 0,
            "level": "待评估",
            "state": "等待衍生品数据",
            "market_state": "等待数据",
            "explanation": "衍生品数据尚未同步，暂不进行资金结构判断。",
            "market_explanation": "等待 OI、Funding 和多空比数据。",
        }

    oi = derivatives.get("oi") or {}
    funding = derivatives.get("funding") or {}
    long_short = derivatives.get("long_short") or {}
    oi_change_1h = _to_float((oi.get("changes") or {}).get("1h"))
    oi_change_24h = _to_float((oi.get("changes") or {}).get("24h"))
    funding_rate = _to_float(funding.get("rate"))
    account_ratio = _to_float(long_short.get("account_ratio"), 1.0)
    price_change = _to_float((ticker or {}).get("price_change_percent"))
    trend_score = int(_to_float((signal_analysis or {}).get("trend_score"), 50))
    risk_score = int(_to_float((signal_analysis or {}).get("risk_score"), 50))

    score = 50
    reasons: list[str] = []
    risks: list[str] = []

    if oi_change_1h >= 8:
        score += 20
        reasons.append("OI快速增加，新增资金明显进入市场。")
    elif oi_change_1h >= 2:
        score += 12
        reasons.append("OI持续增加，市场参与度正在提高。")
    elif oi_change_1h <= -8:
        score -= 20
        risks.append("OI快速下降，资金明显离场或大规模平仓。")
    elif oi_change_1h <= -2:
        score -= 12
        risks.append("OI持续下降，市场参与度减弱。")
    else:
        reasons.append("OI变化不大，资金暂时没有明显单边进出。")

    funding_percent = funding_rate * 100
    if abs(funding_percent) <= 0.03:
        score += 10
        reasons.append("Funding处于正常区间，多空情绪未明显极端。")
    elif 0.03 < funding_percent < 0.08:
        score += 2
        risks.append("Funding偏高，多头情绪较强但未到极端。")
    elif -0.08 < funding_percent < -0.03:
        score += 2
        risks.append("Funding偏低，空头情绪较强但未到极端。")
    else:
        score -= 18
        risks.append("Funding处于极端区间，拥挤交易风险上升。")

    if 0.8 <= account_ratio <= 1.4:
        score += 10
        reasons.append("多空比相对均衡，暂未出现明显拥挤。")
    elif 1.4 < account_ratio < 2.0:
        score += 4
        risks.append("多空比偏多，需要警惕追多拥挤。")
    elif 0.5 < account_ratio < 0.8:
        score += 4
        risks.append("多空比偏空，需要警惕空头回补。")
    else:
        score -= 18
        risks.append("多空比极端，市场站队过于一致。")

    if price_change > 0 and oi_change_1h > 0 and risk_score < 65:
        score += 8
        reasons.append("价格上涨同时OI增加，资金与价格方向较一致。")
    if price_change < 0 and oi_change_1h > 0:
        score -= 6
        risks.append("价格下跌同时OI增加，空头加仓或多头被套风险增加。")
    if trend_score >= 70 and risk_score < 60:
        score += 8
        reasons.append("趋势评分较高且风险未过热，资金结构更健康。")
    if risk_score >= 75:
        score -= 10
        risks.append("风险评分偏高，资金结构需要降级观察。")

    score = max(0, min(100, int(round(score))))
    level = _score_level(score)
    state = _capital_state(score, funding_rate, account_ratio, oi_change_1h)

    if state == "资金过热" and price_change > 0:
        market_state = "危险上涨"
        market_explanation = "价格上涨但 Funding 或多空比偏拥挤，存在挤仓和回撤风险。"
    elif state == "资金过热" and price_change < 0:
        market_state = "恐慌下跌"
        market_explanation = "价格下跌且情绪拥挤，短线容易出现快速波动。"
    elif oi_change_1h > 2 and price_change > 0 and abs(funding_percent) <= 0.03:
        market_state = "健康上涨"
        market_explanation = "价格上涨、OI增加且 Funding 未过热，资金推动较健康。"
    elif oi_change_1h > 2 and price_change < 0:
        market_state = "健康下跌" if funding_percent <= 0.03 else "多头拥挤"
        market_explanation = "价格走弱时OI仍增加，说明空头力量或多头被套压力正在增强。"
    elif funding_percent < -0.03 and price_change > 0:
        market_state = "空头回补"
        market_explanation = "Funding偏低但价格上涨，可能有空头回补推动。"
    elif abs(price_change) < 1 and abs(oi_change_1h) < 1:
        market_state = "资金观望"
        market_explanation = "价格和OI变化都不大，市场资金暂时观望。"
    else:
        market_state = "高风险震荡" if risk_score >= 65 else "资金观望"
        market_explanation = "衍生品数据未形成单边共振，建议等待更明确结构。"

    explanation_parts = (reasons[:3] + risks[:3]) or ["衍生品数据已同步，但暂无明显资金结构信号。"]
    return {
        "ready": True,
        "score": score,
        "level": level,
        "state": state,
        "market_state": market_state,
        "explanation": "；".join(explanation_parts),
        "market_explanation": market_explanation,
        "reasons": reasons,
        "risks": risks,
        "oi_change_1h": oi_change_1h,
        "oi_change_24h": oi_change_24h,
        "funding_percent": funding_percent,
        "account_ratio": account_ratio,
    }

"""Six-dimensional market state language engine for AI_MODEL 9.2."""

from __future__ import annotations

from typing import Any

from services.demand_engine import analyze_demand, clamp


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _missing_fields(**values: Any) -> list[str]:
    missing = []
    for key, value in values.items():
        if value is None or value == {} or value == []:
            missing.append(key)
    return missing


def _integrity_score(missing: list[str], total: int) -> float:
    if total <= 0:
        return 0.0
    return clamp((total - len(missing)) / total * 100)


def _trend_state(direction: str, strength: float) -> str:
    if direction == "LONG":
        return "T1" if strength >= 70 else "T2"
    if direction == "SHORT":
        return "T5" if strength >= 70 else "T4"
    return "T3"


def _capital_state(score: float, risk_score: float) -> str:
    if risk_score >= 75 and score >= 65:
        return "C5"
    if score >= 75:
        return "C1"
    if score >= 58:
        return "C2"
    if score <= 35:
        return "C4"
    return "C3"


def _structure_state(signal_analysis: dict[str, Any] | None, structure_score: float, trend_direction: str) -> str:
    text = str((signal_analysis or {}).get("market_structure") or "") + " " + str((signal_analysis or {}).get("suggestion") or "")
    if any(word in text for word in ("假突破", "诱多", "诱空")):
        return "S4"
    if any(word in text for word in ("跌破", "破位")):
        return "S5"
    if any(word in text for word in ("突破", "强势")) and trend_direction == "LONG":
        return "S1"
    if any(word in text for word in ("支撑", "回踩")):
        return "S2"
    if structure_score <= 35 and trend_direction == "SHORT":
        return "S5"
    return "S3"


def _behavior_state(behavior_score: float, demand: dict[str, Any]) -> str:
    net = _to_float(demand.get("net_demand_score"), 0.0)
    trap = _to_float(demand.get("trap_risk_score"), 0.0)
    if trap >= 75:
        return "B5"
    if net >= 45:
        return "B1"
    if net >= 15:
        return "B2"
    if net <= -45:
        return "B4"
    return "B3" if behavior_score >= 35 else "B5"


def _risk_state(score: float) -> str:
    if score >= 85:
        return "R5"
    if score >= 70:
        return "R4"
    if score >= 50:
        return "R3"
    if score >= 30:
        return "R2"
    return "R1"


def _demand_state(net: float) -> str:
    if net >= 50:
        return "D1"
    if net >= 20:
        return "D2"
    if net <= -50:
        return "D5"
    if net <= -20:
        return "D4"
    return "D3"


def build_market_state(
    *,
    ticker: dict[str, Any] | None = None,
    rows: list[dict[str, Any]] | None = None,
    derivatives: dict[str, Any] | None = None,
    orderbook_analysis: dict[str, Any] | None = None,
    whale: dict[str, Any] | list[Any] | None = None,
    signal_analysis: dict[str, Any] | None = None,
    local_strategy: dict[str, Any] | None = None,
    demand_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    demand = demand_result or analyze_demand(
        ticker=ticker,
        rows=rows,
        derivatives=derivatives,
        orderbook_analysis=orderbook_analysis,
        whale=whale,
        signal_analysis=signal_analysis,
        local_strategy=local_strategy,
    )
    missing = _missing_fields(
        ticker=ticker,
        rows=rows,
        orderbook_analysis=orderbook_analysis,
        signal_analysis=signal_analysis,
        local_strategy=local_strategy,
    )
    data_integrity_score = _integrity_score(missing, 5)

    signal = signal_analysis or {}
    strategy = local_strategy or {}
    base_trend_score = clamp(signal.get("trend_score"), 50)
    risk_score = clamp(max(_to_float(signal.get("risk_score"), 50), _to_float(demand.get("trap_risk_score"), 0)))
    capital_score = clamp(
        _to_float((strategy.get("capital") or {}).get("score"), 0)
        or _to_float(signal.get("capital_score"), 0)
        or _to_float(demand.get("sustainability_score"), 50)
    )
    structure_score = clamp(
        _to_float(signal.get("structure_score"), 0)
        or (100 - _to_float(risk_score, 50) * 0.35 + base_trend_score * 0.35 + _to_float(demand.get("demand_score"), 50) * 0.30)
    )
    behavior_score = clamp((_to_float(demand.get("buy_demand_score"), 50) + (100 - _to_float(demand.get("sell_supply_score"), 50))) / 2)

    direction = str(demand.get("demand_direction") or "NEUTRAL")
    if direction == "NEUTRAL":
        raw_direction = str(strategy.get("direction") or "").lower()
        if raw_direction == "long":
            direction = "LONG"
        elif raw_direction == "short":
            direction = "SHORT"
    trend_strength = clamp(abs(_to_float(demand.get("net_demand_score"), 0)) * 0.55 + abs(base_trend_score - 50) * 0.9)
    trend_quality_score = clamp(base_trend_score * 0.55 + trend_strength * 0.45)

    trend_state = _trend_state(direction, trend_strength)
    capital_state = _capital_state(capital_score, risk_score)
    structure_state = _structure_state(signal_analysis, structure_score, direction)
    behavior_state = _behavior_state(behavior_score, demand)
    risk_state = _risk_state(risk_score)
    demand_state = _demand_state(_to_float(demand.get("net_demand_score"), 0))
    state_code = f"{trend_state}-{capital_state}-{structure_state}-{behavior_state}-{risk_state}-{demand_state}"
    confidence = clamp(data_integrity_score * 0.55 + (100 - abs(_to_float(demand.get("net_demand_score"), 0))) * 0.15 + trend_strength * 0.30)

    return {
        "state_code": state_code,
        "trend_state": trend_state,
        "capital_state": capital_state,
        "structure_state": structure_state,
        "behavior_state": behavior_state,
        "risk_state": risk_state,
        "demand_state": demand_state,
        "trend_direction": direction,
        "trend_strength": round(trend_strength, 2),
        "trend_quality_score": round(trend_quality_score, 2),
        "trend_score": round(trend_quality_score, 2),
        "capital_score": round(capital_score, 2),
        "structure_score": round(structure_score, 2),
        "behavior_score": round(behavior_score, 2),
        "risk_score": round(risk_score, 2),
        "risk_score_meaning": "risk_score 越高代表风险越高",
        "demand_score": demand.get("demand_score", 50),
        "confidence": round(confidence, 2),
        "data_integrity_score": round(data_integrity_score, 2),
        "missing_fields": missing,
        "reason": f"状态码{state_code}，方向{direction}，趋势强度{trend_strength:.1f}，风险{risk_score:.1f}。",
    }

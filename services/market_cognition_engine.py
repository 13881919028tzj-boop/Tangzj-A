"""Market cognition engine for AI_MODEL 9.2."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.demand_engine import analyze_demand, clamp
from services.market_state_engine import build_market_state


APP_VERSION = "AI模型 9.2.5 经验库显示优化与样本置信度校准版"
DATA_VERSION = "market_cognition_v1"
SCHEMA_VERSION = "experience_sample_schema_v1"
STATE_LANGUAGE_VERSION = "market_state_language_v1"
COGNITION_MODEL_VERSION = "market_cognition_rule_based_v1"
WEIGHT_CONFIG_VERSION = "market_cognition_weights_v1"
SIMILARITY_CONFIG_VERSION = "cognition_similarity_weights_v1"
PROBABILITY_TYPE = "rule_based_v1"


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _price_from_ticker(ticker: dict[str, Any] | None) -> float | None:
    ticker = ticker or {}
    for key in ("last_price", "price", "lastPrice"):
        if ticker.get(key) is not None:
            return _to_float(ticker.get(key), 0.0)
    return None


def _quote_volume(ticker: dict[str, Any] | None) -> float | None:
    ticker = ticker or {}
    for key in ("quote_volume", "quoteVolume", "volume_quote"):
        if ticker.get(key) is not None:
            return _to_float(ticker.get(key), 0.0)
    return None


def _label(score: float) -> str:
    if score >= 80:
        return "强认知优势"
    if score >= 65:
        return "认知优势"
    if score >= 50:
        return "中性观察"
    if score >= 35:
        return "认知偏弱"
    return "高不确定"


def _path_probabilities(demand: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    buy = _to_float(demand.get("buy_demand_score"), 50)
    sell = _to_float(demand.get("sell_supply_score"), 50)
    net = _to_float(demand.get("net_demand_score"), 0)
    trend = _to_float(state.get("trend_quality_score") or state.get("trend_score"), 50)
    capital = _to_float(state.get("capital_score"), 50)
    behavior = _to_float(state.get("behavior_score"), 50)
    structure = _to_float(state.get("structure_score"), 50)
    risk = _to_float(state.get("risk_score"), 50)
    sustainability = _to_float(demand.get("sustainability_score"), 50)
    integrity = _to_float(state.get("data_integrity_score"), 50)

    bullish_trend = trend if state.get("trend_direction") == "LONG" else 100 - trend
    bearish_trend = trend if state.get("trend_direction") == "SHORT" else 100 - trend
    capital_inflow = capital
    capital_outflow = 100 - capital
    bullish_behavior = behavior if net >= 0 else 100 - behavior
    bearish_behavior = behavior if net <= 0 else 100 - behavior
    bullish_structure = structure if state.get("trend_direction") == "LONG" else 100 - structure
    bearish_structure = structure if state.get("trend_direction") == "SHORT" else 100 - structure
    range_structure = 100 - abs(structure - 50) * 2
    balance_demand = 100 - min(abs(net) * 2, 100)
    low_momentum = 100 - min(_to_float(state.get("trend_strength"), 0) * 1.5, 100)
    conflicting = 60 if state.get("trend_direction") != demand.get("demand_direction") and demand.get("demand_direction") != "NEUTRAL" else 25
    uncertainty = 100 - integrity

    up_raw = buy * 0.30 + bullish_trend * 0.20 + capital_inflow * 0.16 + bullish_behavior * 0.14 + bullish_structure * 0.12 + sustainability * 0.08
    down_raw = sell * 0.30 + bearish_trend * 0.20 + capital_outflow * 0.16 + bearish_behavior * 0.14 + bearish_structure * 0.12 + risk * 0.08
    sideways_raw = range_structure * 0.30 + balance_demand * 0.25 + low_momentum * 0.20 + conflicting * 0.15 + uncertainty * 0.10
    if integrity < 60:
        sideways_raw *= 1.25
    if risk >= 80:
        up_raw = min(up_raw, 55)
        down_raw = min(down_raw, 55)
        sideways_raw *= 1.20
    if abs(net) < 20:
        sideways_raw *= 1.18
    total = max(up_raw + sideways_raw + down_raw, 1)
    path_30m = {
        "up": round(up_raw / total * 100, 2),
        "sideways": round(sideways_raw / total * 100, 2),
        "down": round(down_raw / total * 100, 2),
        "reason": "基于需求、趋势、资金、行为、结构和风险的规则概率；非历史经验统计。",
        "probability_type": PROBABILITY_TYPE,
    }
    path_30m["up_probability"] = path_30m["up"]
    path_30m["sideways_probability"] = path_30m["sideways"]
    path_30m["down_probability"] = path_30m["down"]

    urgency = _to_float(demand.get("urgency_score"), 50)
    sustainable_shift = (sustainability - 50) * 0.08
    risk_shift = max(risk - 60, 0) * 0.06
    up_60 = clamp(path_30m["up"] + sustainable_shift - risk_shift)
    down_60 = clamp(path_30m["down"] + risk_shift - sustainable_shift / 2)
    sideways_60 = clamp(100 - up_60 - down_60)
    if urgency < 35:
        sideways_60 = clamp(sideways_60 + 5)
        scale = max(up_60 + down_60, 1)
        up_60 = clamp((100 - sideways_60) * up_60 / scale)
        down_60 = clamp((100 - sideways_60) * down_60 / scale)
    path_60m = {
        "up": round(up_60, 2),
        "sideways": round(sideways_60, 2),
        "down": round(down_60, 2),
        "reason": "60分钟概率在30分钟规则概率上加入持续性、紧迫度和风险衰减。",
        "probability_type": PROBABILITY_TYPE,
    }
    path_60m["up_probability"] = path_60m["up"]
    path_60m["sideways_probability"] = path_60m["sideways"]
    path_60m["down_probability"] = path_60m["down"]
    return path_30m, path_60m


def _capital_direction(capital_score: Any) -> str:
    score = _to_float(capital_score, 50)
    if score >= 58:
        return "INFLOW"
    if score <= 42:
        return "OUTFLOW"
    return "NEUTRAL"


def _source_status(ticker: Any, rows: Any, orderbook_analysis: Any, whale: Any, derivatives: Any) -> dict[str, str]:
    return {
        "ticker": "ok" if ticker else "missing",
        "klines": "ok" if rows else "missing",
        "orderbook": "ok" if orderbook_analysis else "missing",
        "whales": "ok" if whale else "missing",
        "derivatives": "ok" if derivatives else "missing",
    }


def build_market_cognition(
    *,
    symbol: str,
    ticker: dict[str, Any] | None = None,
    rows: list[dict[str, Any]] | None = None,
    derivatives: dict[str, Any] | None = None,
    orderbook_analysis: dict[str, Any] | None = None,
    whale: dict[str, Any] | list[Any] | None = None,
    signal_analysis: dict[str, Any] | None = None,
    local_strategy: dict[str, Any] | None = None,
    committee_result: dict[str, Any] | None = None,
    interval_base: str = "1m",
) -> dict[str, Any]:
    demand = analyze_demand(
        ticker=ticker,
        rows=rows,
        derivatives=derivatives,
        orderbook_analysis=orderbook_analysis,
        whale=whale,
        signal_analysis=signal_analysis,
        local_strategy=local_strategy,
    )
    state = build_market_state(
        ticker=ticker,
        rows=rows,
        derivatives=derivatives,
        orderbook_analysis=orderbook_analysis,
        whale=whale,
        signal_analysis=signal_analysis,
        local_strategy=local_strategy,
        demand_result=demand,
    )
    risk_score = _to_float(state.get("risk_score"), 50)
    risk_safe_score = clamp(100 - risk_score)
    trend_quality_score = _to_float(state.get("trend_quality_score") or state.get("trend_score"), 50)
    market_cognition_score = clamp(
        _to_float(demand.get("demand_score"), 50) * 0.25
        + trend_quality_score * 0.18
        + _to_float(state.get("capital_score"), 50) * 0.16
        + _to_float(state.get("behavior_score"), 50) * 0.15
        + _to_float(state.get("structure_score"), 50) * 0.14
        + risk_safe_score * 0.12
    )
    path_30m, path_60m = _path_probabilities(demand, state)
    net = _to_float(demand.get("net_demand_score"), 0)
    trap = _to_float(demand.get("trap_risk_score"), 0)
    main_conflict = "供需与趋势基本一致。"
    if abs(net) < 20:
        main_conflict = "买卖需求接近，核心矛盾是方向不确定。"
    elif trap >= 65:
        main_conflict = "方向需求存在，但诱导风险偏高。"
    elif state.get("trend_direction") != demand.get("demand_direction") and demand.get("demand_direction") != "NEUTRAL":
        main_conflict = "趋势方向与即时需求存在冲突。"

    price = _price_from_ticker(ticker)
    attack_point = "等待放量突破压力位" if net >= 20 else "等待卖压释放后再确认"
    defense_point = "以最近支撑和盘口承接为防守点" if net >= 0 else "以最近压力和盘口卖墙为防守点"
    failure_point = "需求方向反转、数据完整度下降或风险裁判转为阻断"
    risk_warning = "风险可控。"
    if risk_score >= 80:
        risk_warning = "风险过高，禁止激进交易。"
    elif risk_score >= 60 or trap >= 65:
        risk_warning = "风险警戒，需要降低仓位或等待确认。"

    committee = committee_result or {}
    position_plan = (committee.get("trading_committee_v91") or {}).get("position_plan") or {}
    execution_plan = (committee.get("trading_committee_v91") or {}).get("execution_plan") or {}
    timestamp = datetime.now(timezone.utc).isoformat()
    cognition_summary = (
        f"{symbol} 当前状态 {state.get('state_code')}，{demand.get('demand_change')}，"
        f"认知评分{market_cognition_score:.1f}，30分钟路径 上涨{path_30m['up']:.1f}% / "
        f"震荡{path_30m['sideways']:.1f}% / 下跌{path_30m['down']:.1f}%。"
    )
    snapshot = {
        "symbol": str(symbol or "").upper(),
        "timestamp_utc": timestamp,
        "interval_base": interval_base,
        "price": price,
        "quote_volume": _quote_volume(ticker),
        "data_version": DATA_VERSION,
        "app_version": APP_VERSION,
        "schema_version": SCHEMA_VERSION,
        "state_language_version": STATE_LANGUAGE_VERSION,
        "cognition_model_version": COGNITION_MODEL_VERSION,
        "weight_config_version": WEIGHT_CONFIG_VERSION,
        "similarity_config_version": SIMILARITY_CONFIG_VERSION,
        **state,
        "risk_safe_score": round(risk_safe_score, 2),
        **{key: demand.get(key) for key in ("buy_demand_score", "sell_supply_score", "net_demand_score", "urgency_score", "sustainability_score", "trap_risk_score")},
        "state_vector": {
            "trend_direction": state.get("trend_direction"),
            "trend_strength": state.get("trend_strength"),
            "trend_quality_score": state.get("trend_quality_score") or state.get("trend_score"),
            "capital_score": state.get("capital_score"),
            "capital_direction": _capital_direction(state.get("capital_score")),
            "structure_score": state.get("structure_score"),
            "behavior_score": state.get("behavior_score"),
            "risk_score": state.get("risk_score"),
            "risk_safe_score": round(risk_safe_score, 2),
            "demand_score": state.get("demand_score"),
            "buy_demand_score": demand.get("buy_demand_score"),
            "sell_supply_score": demand.get("sell_supply_score"),
            "net_demand_score": demand.get("net_demand_score"),
            "urgency_score": demand.get("urgency_score"),
            "sustainability_score": demand.get("sustainability_score"),
            "trap_risk_score": demand.get("trap_risk_score"),
            "confidence": state.get("confidence"),
            "data_integrity_score": state.get("data_integrity_score"),
        },
        "experience_match_key": {
            "state_code": state.get("state_code"),
            "state_vector": "required",
            "note": "未来经验库匹配必须同时使用 state_code 与 state_vector，不能只按状态码比对。",
        },
        "market_cognition_score": round(market_cognition_score, 2),
        "market_cognition_label": _label(market_cognition_score),
        "main_conflict": main_conflict,
        "attack_point": attack_point,
        "defense_point": defense_point,
        "failure_point": failure_point,
        "demand_reason": demand.get("demand_reason"),
        "risk_warning": risk_warning,
        "cognition_summary": cognition_summary,
        "path_30m": path_30m,
        "path_60m": path_60m,
        "path_30m_up_probability": path_30m["up"],
        "path_30m_sideways_probability": path_30m["sideways"],
        "path_30m_down_probability": path_30m["down"],
        "path_60m_up_probability": path_60m["up"],
        "path_60m_sideways_probability": path_60m["sideways"],
        "path_60m_down_probability": path_60m["down"],
        "probability_type": PROBABILITY_TYPE,
        "stale_fields": [],
        "source_status": _source_status(ticker, rows, orderbook_analysis, whale, derivatives),
        "committee_final_action": committee.get("final_action"),
        "committee_trade_value_score": (committee.get("trading_committee_v91") or {}).get("trade_value_score"),
        "risk_judge_verdict": ((committee.get("trading_committee_v91") or {}).get("risk_judge") or {}).get("risk_verdict"),
        "position_plan_summary": position_plan.get("reason"),
        "execution_plan_summary": execution_plan.get("reason"),
    }
    for key in (
        "future_30m_return",
        "future_30m_mfe",
        "future_30m_mae",
        "future_30m_first_tp_hit",
        "future_30m_first_sl_hit",
        "future_60m_return",
        "future_60m_mfe",
        "future_60m_mae",
        "future_60m_first_tp_hit",
        "future_60m_first_sl_hit",
        "similar_sample_count",
        "win_rate_30m",
        "win_rate_60m",
        "avg_return_30m",
        "avg_return_60m",
        "mfe_p50",
        "mfe_p75",
        "mfe_p90",
        "mae_p50",
        "mae_p75",
        "mae_p90",
        "suggested_stop_loss",
        "suggested_take_profit_1",
        "suggested_take_profit_2",
        "experience_confidence",
    ):
        snapshot[key] = None
    return snapshot

"""AI_MODEL 9.1 trading committee compatibility layer.

This module introduces the new quantitative committee shape without removing
the legacy committee engine.  The current production path can keep using
``run_committee_meeting`` while consumers gradually migrate to
``decision["trading_committee_v91"]``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.committee_types import (
    DIRECTION_LONG,
    DIRECTION_SHORT,
    DIRECTION_WAIT,
    VOTE_ABSTAIN,
    VOTE_CAUTIOUS_SUPPORT,
    VOTE_OPPOSE,
    VOTE_SUPPORT,
    VOTE_VETO,
    abstain_member,
    clamp_score,
    member_result,
    normalize_direction,
)


BASE_WEIGHTS = {
    "experience": 50.0,
    "market": 25.0,
    "orderbook": 15.0,
    "reasoning": 10.0,
}
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
LIVE_AUTO_CONFIG_PATH = DATA_DIR / "live_auto_config.json"


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _percent_value(value: Any, default: float = 0.0) -> float:
    text = str(value or "").replace("%", "").strip()
    if "-" in text:
        text = text.split("-")[-1].strip()
    return _to_float(text, default)


def _configured_max_leverage(default: int = 3) -> int:
    try:
        if not LIVE_AUTO_CONFIG_PATH.exists():
            return default
        raw = json.loads(LIVE_AUTO_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return default
        return max(1, min(int(_to_float(raw.get("max_leverage"), default)), default))
    except Exception:
        return default


def _old_votes(decision: dict[str, Any]) -> list[dict[str, Any]]:
    return list(decision.get("member_votes") or [])


def _vote_to_v91(vote_code: str, veto: bool = False) -> str:
    if veto or vote_code == "veto":
        return VOTE_VETO
    if vote_code in {"strong_support", "support"}:
        return VOTE_SUPPORT
    if vote_code in {"weak_support", "neutral_support"}:
        return VOTE_CAUTIOUS_SUPPORT
    if vote_code in {"weak_oppose", "oppose"}:
        return VOTE_OPPOSE
    return VOTE_ABSTAIN


def _integrity_from_inputs(data: dict[str, Any], required: list[str]) -> float:
    if not required:
        return 100.0
    ok = 0
    for key in required:
        value = data.get(key)
        if isinstance(value, list):
            ok += 1 if value else 0
        elif isinstance(value, dict):
            ok += 1 if value else 0
        elif value not in {None, ""}:
            ok += 1
    return clamp_score(ok / len(required) * 100)


def _pick_votes(decision: dict[str, Any], names: set[str]) -> list[dict[str, Any]]:
    return [row for row in _old_votes(decision) if str(row.get("member_name")) in names]


def _safe_vote_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "member_name": row.get("member_name"),
        "status": row.get("status"),
        "source": row.get("source"),
        "vote": row.get("vote"),
        "vote_code": row.get("vote_code"),
        "direction": row.get("direction"),
        "direction_text": row.get("direction_text"),
        "confidence": row.get("confidence"),
        "risk_level": row.get("risk_level"),
        "veto": bool(row.get("veto")),
        "soft_veto": bool(row.get("soft_veto")),
    }


def _safe_vote_snapshots(votes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_safe_vote_snapshot(row) for row in votes]


def _aggregate_old_votes(
    *,
    name: str,
    role: str,
    votes: list[dict[str, Any]],
    data_integrity_score: float,
    fallback_reason: str,
) -> dict[str, Any]:
    if not votes or data_integrity_score <= 20:
        return abstain_member(name, role, fallback_reason, {"source_votes": _safe_vote_snapshots(votes)})

    veto_votes = [v for v in votes if v.get("veto") or str(v.get("vote_code")) == "veto"]
    if veto_votes:
        return member_result(
            name=name,
            role=role,
            vote=VOTE_VETO,
            direction=DIRECTION_WAIT,
            score=0,
            confidence=min(clamp_score(max(_to_float(v.get("confidence"), 0) for v in veto_votes)), data_integrity_score),
            data_integrity_score=data_integrity_score,
            reason="；".join(str(r) for v in veto_votes for r in list(v.get("risks") or [])[:1]) or "旧委员触发否决。",
            evidence={"source_votes": _safe_vote_snapshots(votes)},
            raw={},
        )

    long_score = 0.0
    short_score = 0.0
    oppose_score = 0.0
    total_conf = 0.0
    reason_parts: list[str] = []
    for row in votes:
        confidence = clamp_score(row.get("confidence"), 50)
        total_conf += confidence
        strength = _to_float(row.get("vote_strength"), 0)
        direction = normalize_direction(row.get("direction"))
        if strength > 0 and direction == DIRECTION_LONG:
            long_score += strength * confidence
        elif strength > 0 and direction == DIRECTION_SHORT:
            short_score += strength * confidence
        elif strength < 0:
            oppose_score += abs(strength) * confidence
        summary = str(row.get("summary") or "")
        if summary:
            reason_parts.append(summary)

    confidence = min(total_conf / max(len(votes), 1), data_integrity_score)
    score = max(long_score, short_score, oppose_score) / max(len(votes), 1)
    if oppose_score > max(long_score, short_score):
        vote = VOTE_OPPOSE
        direction = DIRECTION_WAIT
    elif max(long_score, short_score) <= 0:
        vote = VOTE_ABSTAIN
        direction = DIRECTION_WAIT
    else:
        direction = DIRECTION_LONG if long_score >= short_score else DIRECTION_SHORT
        vote = VOTE_SUPPORT if score >= 45 and confidence >= 55 else VOTE_CAUTIOUS_SUPPORT

    return member_result(
        name=name,
        role=role,
        vote=vote,
        direction=direction,
        score=score,
        confidence=confidence,
        data_integrity_score=data_integrity_score,
        reason="；".join(reason_parts[:2]) or fallback_reason,
        evidence={"long_score": round(long_score, 2), "short_score": round(short_score, 2), "oppose_score": round(oppose_score, 2)},
        raw={},
    )


def build_experience_member(data: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    cognition = data.get("market_cognition") or decision.get("market_cognition") or {}
    state_code = str(cognition.get("state_code") or "")
    reason = "经验库未接入，当前版本弃权。"
    if state_code:
        reason = f"当前状态码 {state_code} 已生成；经验库未接入，等待9.4后参与投票。"
    return {
        **abstain_member("经验委员", "experience", reason),
        "enabled": False,
        "experience_library_version": "none",
        "sample_count": 0,
        "state_code": state_code,
        "similar_sample_count": 0,
        "win_rate_30m": None,
        "win_rate_60m": None,
        "avg_return_30m": None,
        "avg_return_60m": None,
        "max_drawdown": None,
        "max_profit": None,
        "experience_score": None,
        "experience_confidence": 0,
    }


def build_market_member(data: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    votes = _pick_votes(decision, {"本地策略委员", "趋势委员", "资金委员"})
    integrity = _integrity_from_inputs(data, ["ticker", "rows", "signal_analysis", "local_strategy"])
    cognition = data.get("market_cognition") or decision.get("market_cognition") or {}
    if cognition:
        integrity = min(integrity, clamp_score(cognition.get("data_integrity_score"), integrity))
    result = _aggregate_old_votes(name="市场委员", role="market", votes=votes, data_integrity_score=integrity, fallback_reason="市场委员数据不足，当前弃权。")
    if cognition:
        reason_parts = [str(result.get("reason") or "")]
        reason_parts.append(
            f"9.2市场认知：状态{cognition.get('state_code', '-')}"
            f"，趋势质量{cognition.get('trend_quality_score', cognition.get('trend_score', '-'))}"
            f"，资金{cognition.get('capital_score', '-')}"
            f"，结构{cognition.get('structure_score', '-')}"
            f"，需求{cognition.get('demand_score', '-')}"
            f"；主要矛盾：{cognition.get('main_conflict', '-')}"
        )
        result["reason"] = "；".join(part for part in reason_parts if part)
        result["evidence"] = {
            **(result.get("evidence") or {}),
            "market_cognition": {
                "state_code": cognition.get("state_code"),
                "trend_direction": cognition.get("trend_direction"),
                "trend_strength": cognition.get("trend_strength"),
                "trend_quality_score": cognition.get("trend_quality_score") or cognition.get("trend_score"),
                "capital_score": cognition.get("capital_score"),
                "structure_score": cognition.get("structure_score"),
                "demand_score": cognition.get("demand_score"),
                "main_conflict": cognition.get("main_conflict"),
            },
        }
    return result


def build_orderbook_member(data: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    votes = _pick_votes(decision, {"盘口委员", "大单 / 庄家委员"})
    integrity = _integrity_from_inputs(data, ["orderbook_analysis", "whale"])
    return _aggregate_old_votes(name="盘口委员", role="orderbook", votes=votes, data_integrity_score=integrity, fallback_reason="盘口或大单数据不足，当前弃权。")


def build_reasoning_member(data: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    votes = _pick_votes(decision, {"DeepSeek委员", "Gemini委员"})
    available = [v for v in votes if clamp_score(v.get("confidence"), 0) > 0 and str(v.get("vote_code")) != "observe"]
    integrity = 100.0 if available else 0.0
    result = _aggregate_old_votes(name="推理委员", role="reasoning", votes=votes, data_integrity_score=integrity, fallback_reason="DeepSeek/Gemini 当前不可用或未形成有效意见，推理委员弃权。")
    result["model_used"] = "DeepSeek/Gemini" if available else "none"
    result["raw_model_available"] = bool(available)
    return result


def build_risk_judge(data: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    hard = decision.get("hard_veto_status") or {}
    votes = _pick_votes(decision, {"风险委员", "清算委员", "实盘安全委员"})
    risk_score = clamp_score(decision.get("committee_risk_score"), 80)
    cognition = data.get("market_cognition") or decision.get("market_cognition") or {}
    if cognition:
        risk_score = max(risk_score, clamp_score(cognition.get("risk_score"), 0), clamp_score(cognition.get("trap_risk_score"), 0))
    warnings = list(decision.get("final_warnings") or [])
    if cognition and clamp_score(cognition.get("trap_risk_score"), 0) >= 65:
        warnings.append(f"9.2需求引擎提示诱导风险偏高：{cognition.get('trap_risk_score')}")
    if cognition and clamp_score(cognition.get("data_integrity_score"), 100) < 45:
        warnings.append("9.2市场认知数据完整度过低，风险裁判保持保守。")
    risk_items = []
    for row in votes:
        risk_items.append(
            {
                "source": row.get("member_name"),
                "vote": row.get("vote"),
                "veto": bool(row.get("veto")),
                "risks": list(row.get("risks") or []),
            }
        )
    blocked = bool(hard.get("blocked")) or any(item.get("veto") for item in risk_items)
    if blocked:
        verdict = "BLOCK"
    elif risk_score >= 65 or warnings:
        verdict = "WARNING"
    else:
        verdict = "PASS"
    return {
        "risk_verdict": verdict,
        "blocked": blocked,
        "risk_score": risk_score,
        "block_reason": "；".join(str(x) for x in (hard.get("reasons") or [])) if blocked else "",
        "warnings": warnings,
        "risk_items": risk_items,
        "data_integrity_score": min(
            _integrity_from_inputs(data, ["ticker", "rows", "orderbook_analysis", "local_strategy"]),
            clamp_score(cognition.get("data_integrity_score"), 100) if cognition else 100,
        ),
    }


def build_weighted_vote(members: list[dict[str, Any]]) -> dict[str, Any]:
    weighted_long = weighted_short = weighted_oppose = 0.0
    effective_weight = 0.0
    rows = []
    for member in members:
        role = str(member.get("role"))
        vote = str(member.get("vote"))
        if vote == VOTE_ABSTAIN:
            rows.append({**member, "effective_weight": 0.0, "reason_weight": "ABSTAIN不参与权重。"})
            continue
        base_weight = BASE_WEIGHTS.get(role, 0.0)
        quality_factor = clamp_score(member.get("confidence")) / 100 * clamp_score(member.get("data_integrity_score")) / 100
        weight = base_weight * quality_factor
        effective_weight += weight
        direction = str(member.get("direction"))
        score = clamp_score(member.get("score")) / 100
        if vote == VOTE_VETO or vote == VOTE_OPPOSE:
            weighted_oppose += weight * max(score, 0.5)
        elif direction == DIRECTION_LONG:
            weighted_long += weight * max(score, 0.5)
        elif direction == DIRECTION_SHORT:
            weighted_short += weight * max(score, 0.5)
        rows.append({**member, "effective_weight": round(weight, 4), "base_weight": base_weight})
    total_direction = max(weighted_long, weighted_short, weighted_oppose)
    final_direction = DIRECTION_WAIT
    if total_direction > 0 and weighted_oppose < max(weighted_long, weighted_short):
        final_direction = DIRECTION_LONG if weighted_long >= weighted_short else DIRECTION_SHORT
    trade_value = total_direction / max(effective_weight, 1) * 100 if effective_weight else 0.0
    confidence = sum(_to_float(row.get("effective_weight"), 0) * clamp_score(row.get("confidence")) for row in rows) / max(sum(_to_float(row.get("effective_weight"), 0) for row in rows), 1)
    integrity = sum(_to_float(row.get("effective_weight"), 0) * clamp_score(row.get("data_integrity_score")) for row in rows) / max(sum(_to_float(row.get("effective_weight"), 0) for row in rows), 1)
    return {
        "final_direction": final_direction,
        "trade_value_score": clamp_score(trade_value),
        "final_confidence": clamp_score(confidence),
        "final_data_integrity_score": clamp_score(integrity),
        "weighted_long": round(weighted_long, 4),
        "weighted_short": round(weighted_short, 4),
        "weighted_oppose": round(weighted_oppose, 4),
        "effective_weight": round(effective_weight, 4),
        "weighted_votes": rows,
    }


def build_position_plan(decision: dict[str, Any], risk_judge: dict[str, Any], weighted: dict[str, Any]) -> dict[str, Any]:
    if risk_judge.get("blocked") or weighted.get("final_direction") == DIRECTION_WAIT:
        return {"allow_position": False, "position_size_pct": 0.0, "leverage": 1, "margin_mode": "isolated", "stop_loss_pct": 0.0, "take_profit_1_pct": 0.0, "take_profit_2_pct": 0.0, "max_loss_pct": 0.0, "partial_take_profit_plan": [], "reason": "风险裁判阻断或交易方向为WAIT，不生成仓位。"}
    risk_score = clamp_score(risk_judge.get("risk_score"))
    trade_value = clamp_score(weighted.get("trade_value_score"))
    if risk_score >= 80:
        size = 0.0
    elif risk_score >= 60:
        size = min(1.0, trade_value / 100 * 2)
    elif risk_score >= 40:
        size = min(3.0, trade_value / 100 * 4)
    else:
        size = min(5.0, trade_value / 100 * 6)
    risk_max_position = _percent_value(decision.get("risk_max_position"), size)
    system_position_high = _percent_value(decision.get("system_position_suggestion") or decision.get("position_suggestion"), size)
    if risk_max_position > 0:
        size = min(size, risk_max_position)
    if system_position_high > 0:
        size = min(size, system_position_high)
    leverage = min(3 if risk_score < 40 else 2 if risk_score < 60 else 1, _configured_max_leverage(3))
    return {
        "allow_position": size > 0,
        "position_size_pct": round(size, 2),
        "leverage": leverage,
        "margin_mode": "isolated",
        "stop_loss_pct": 1.2 if risk_score < 60 else 0.8,
        "take_profit_1_pct": 1.8 if risk_score < 60 else 1.2,
        "take_profit_2_pct": 3.2 if risk_score < 60 else 2.0,
        "max_loss_pct": round(size * (1.2 if risk_score < 60 else 0.8) / 100, 4),
        "partial_take_profit_plan": [{"level": "TP1", "close_pct": 50}, {"level": "TP2", "close_pct": 50}],
        "reason": "仓位委员会仅输出建议，建议仓位已限制在旧风控最大仓位和系统建议上限内；真实交易仍受模拟交易、实盘安全锁、最大仓位和最大杠杆限制。",
    }


def build_execution_plan(decision: dict[str, Any], risk_judge: dict[str, Any], position_plan: dict[str, Any]) -> dict[str, Any]:
    simulation_allowed = bool(decision.get("approved_for_simulation")) and bool(position_plan.get("allow_position")) and not bool(risk_judge.get("blocked"))
    execution_type = "SIMULATION" if simulation_allowed else "WAIT"
    return {
        "execution_allowed": simulation_allowed,
        "execution_type": execution_type,
        "order_plan": {},
        "entry_reason": decision.get("chairman_summary", ""),
        "exit_plan": {},
        "safety_checks": [
            {"name": "风险裁判", "ok": not bool(risk_judge.get("blocked")), "message": risk_judge.get("block_reason") or risk_judge.get("risk_verdict")},
            {"name": "实盘安全", "ok": True, "message": "9.1 不扩大实盘权限，真实交易仍由 live_trading_center 控制。"},
        ],
        "reason": "执行委员会当前只生成模拟候选状态，不直接提交实盘订单。",
    }


def build_trading_committee_v91(data: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    members = [
        build_experience_member(data, decision),
        build_market_member(data, decision),
        build_orderbook_member(data, decision),
        build_reasoning_member(data, decision),
    ]
    weighted = build_weighted_vote(members)
    risk_judge = build_risk_judge(data, decision)
    if risk_judge.get("blocked") or weighted.get("final_data_integrity_score", 0) < 40:
        final_action = "WAIT"
        final_direction = DIRECTION_WAIT
    else:
        final_direction = weighted.get("final_direction", DIRECTION_WAIT)
        final_action = final_direction if final_direction in {DIRECTION_LONG, DIRECTION_SHORT} else "WAIT"
    position_plan = build_position_plan(decision, risk_judge, {**weighted, "final_direction": final_direction})
    execution_plan = build_execution_plan(decision, risk_judge, position_plan)
    abstain = [m for m in members if m.get("vote") == VOTE_ABSTAIN]
    conflicts = []
    dirs = {m.get("direction") for m in members if m.get("vote") not in {VOTE_ABSTAIN, VOTE_OPPOSE, VOTE_VETO}}
    if DIRECTION_LONG in dirs and DIRECTION_SHORT in dirs:
        conflicts.append("委员方向存在LONG/SHORT冲突。")
    return {
        "version": "AI模型 9.2",
        "symbol": decision.get("symbol") or data.get("symbol"),
        "final_action": final_action,
        "final_direction": final_direction,
        "trade_value_score": weighted.get("trade_value_score", 0),
        "final_confidence": weighted.get("final_confidence", 0),
        "final_data_integrity_score": weighted.get("final_data_integrity_score", 0),
        "members": members,
        "weighted_votes": weighted.get("weighted_votes", []),
        "final_reason": "交易委员会按有效权重汇总；ABSTAIN不参与权重，风险裁判可一票否决。",
        "conflict_summary": conflicts or ["未发现主要委员方向硬冲突。"],
        "abstain_summary": [f"{m.get('name')}：{m.get('reason')}" for m in abstain],
        "risk_judge": risk_judge,
        "position_plan": position_plan,
        "execution_plan": execution_plan,
        "shadow_members": [v for v in _old_votes(decision) if v.get("shadow") or v.get("member_type") == "shadow"],
        "legacy_summary": {
            "final_action": decision.get("final_action"),
            "trade_permission": decision.get("trade_permission"),
            "approved_for_simulation": decision.get("approved_for_simulation"),
            "committee_confidence": decision.get("committee_confidence"),
        },
    }


def attach_trading_committee_v91(data: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    decision["trading_committee_v91"] = build_trading_committee_v91(data, decision)
    return decision

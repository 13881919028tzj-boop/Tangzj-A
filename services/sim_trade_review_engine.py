"""Simulation trade review and experience feedback recorder.

This module is a sidecar for the local simulation engine. It only records
review data for simulated positions and never sends orders or changes trading
permissions.
"""

from __future__ import annotations

import csv
import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any


APP_VERSION = "AI模型 9.2.11 多经验库融合决策版"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
REVIEW_DIR = DATA_DIR / "sim_trade_reviews"
REVIEWS_JSONL_PATH = REVIEW_DIR / "sim_trade_reviews.jsonl"
FEEDBACK_SUMMARY_PATH = REVIEW_DIR / "sim_trade_feedback_summary.json"
LATEST_PATH = REVIEW_DIR / "sim_trade_review_latest.json"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ts() -> int:
    return int(time.time())


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def _pct_value(value: Any) -> float:
    number = _to_float(value, 0.0)
    if abs(number) <= 1.0:
        number *= 100.0
    return round(number, 6)


def _ensure_dir() -> None:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: Any) -> Any:
    try:
        _ensure_dir()
        if not path.exists():
            return default
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    _ensure_dir()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    _ensure_dir()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _latest_map() -> dict[str, dict[str, Any]]:
    data = _read_json(LATEST_PATH, {})
    if isinstance(data, dict):
        return {str(key): value for key, value in data.items() if isinstance(value, dict)}
    return {}


def _save_latest_map(rows: dict[str, dict[str, Any]]) -> None:
    _write_json(LATEST_PATH, rows)


def _state_vector(cognition: dict[str, Any]) -> dict[str, Any]:
    vector = cognition.get("state_vector")
    return dict(vector) if isinstance(vector, dict) else {}


def _experience_match_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    direct = snapshot.get("experience_match")
    if isinstance(direct, dict):
        return direct
    direct = snapshot.get("experience_committee")
    return dict(direct) if isinstance(direct, dict) else {}


def _market_cognition_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    cognition = snapshot.get("market_cognition")
    return dict(cognition) if isinstance(cognition, dict) else {}


def _member_summary(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for member in list(snapshot.get("member_votes") or [])[:20]:
        if not isinstance(member, dict):
            continue
        result.append(
            {
                "member_name": member.get("member_name"),
                "vote": member.get("vote"),
                "vote_code": member.get("vote_code"),
                "direction": member.get("direction"),
                "confidence": member.get("confidence"),
                "weight": member.get("weight"),
                "veto": bool(member.get("veto")),
                "summary": member.get("summary"),
                "reason": (list(member.get("reasons") or []) or list(member.get("risks") or []) or [""])[0],
            }
        )
    return result


def build_open_snapshot(position: dict[str, Any], account: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = dict(position.get("committee_snapshot") or {})
    cognition = _market_cognition_from_snapshot(snapshot)
    vector = _state_vector(cognition)
    experience = _experience_match_from_snapshot(snapshot)
    v91 = snapshot.get("trading_committee_v91") if isinstance(snapshot.get("trading_committee_v91"), dict) else {}
    risk_judge = v91.get("risk_judge") if isinstance(v91.get("risk_judge"), dict) else {}
    position_plan = v91.get("position_plan") if isinstance(v91.get("position_plan"), dict) else {}
    execution_plan = v91.get("execution_plan") if isinstance(v91.get("execution_plan"), dict) else {}
    library = snapshot.get("experience_library") if isinstance(snapshot.get("experience_library"), dict) else {}
    experience_version = (
        library.get("version")
        or snapshot.get("experience_library_version")
        or experience.get("experience_library_version")
        or experience.get("experience_version")
        or "current"
    )
    return {
        "trade_id": position.get("position_id"),
        "symbol": position.get("symbol"),
        "side": position.get("direction"),
        "entry_time": position.get("open_time"),
        "entry_price": position.get("entry_price"),
        "quantity": position.get("original_quantity") or position.get("quantity"),
        "margin": position.get("original_margin_usdt") or position.get("margin_usdt"),
        "leverage": position.get("leverage"),
        "position_size_pct": position.get("position_pct") or snapshot.get("position_suggestion"),
        "simulation_account_equity": (account or {}).get("equity"),
        "experience_library_version": experience_version,
        "app_version": APP_VERSION,
        "state_code": cognition.get("state_code"),
        "state_vector": vector,
        "trend_direction": vector.get("trend_direction"),
        "trend_strength": vector.get("trend_strength"),
        "trend_quality_score": vector.get("trend_quality_score"),
        "capital_score": vector.get("capital_score"),
        "structure_score": vector.get("structure_score"),
        "behavior_score": vector.get("behavior_score"),
        "risk_score": vector.get("risk_score"),
        "risk_safe_score": vector.get("risk_safe_score"),
        "demand_score": vector.get("demand_score"),
        "buy_demand_score": vector.get("buy_demand_score"),
        "sell_supply_score": vector.get("sell_supply_score"),
        "net_demand_score": vector.get("net_demand_score"),
        "trap_risk_score": vector.get("trap_risk_score"),
        "market_cognition_score": cognition.get("market_cognition_score") or cognition.get("confidence"),
        "experience_version_used": experience_version,
        "matched_sample_count": experience.get("matched_sample_count"),
        "exact_sample_count": experience.get("exact_sample_count"),
        "similar_state_sample_count": experience.get("similar_state_sample_count"),
        "vector_nearest_sample_count": experience.get("vector_nearest_sample_count"),
        "avg_similarity": experience.get("avg_similarity"),
        "historical_30m_up_probability": experience.get("historical_30m_up_probability"),
        "historical_60m_up_probability": experience.get("historical_60m_up_probability"),
        "historical_30m_down_probability": experience.get("historical_30m_down_probability"),
        "historical_60m_down_probability": experience.get("historical_60m_down_probability"),
        "mfe_p50": experience.get("mfe_p50"),
        "mfe_p75": experience.get("mfe_p75"),
        "mfe_p90": experience.get("mfe_p90"),
        "mae_p50": experience.get("mae_p50"),
        "mae_p75": experience.get("mae_p75"),
        "mae_p90": experience.get("mae_p90"),
        "suggested_stop_loss": experience.get("suggested_stop_loss") or position.get("stop_loss"),
        "suggested_take_profit_1": experience.get("suggested_take_profit_1") or position.get("take_profit_1"),
        "suggested_take_profit_2": experience.get("suggested_take_profit_2") or position.get("take_profit_2"),
        "experience_vote": experience.get("vote"),
        "experience_score": experience.get("score"),
        "experience_confidence": experience.get("confidence"),
        "experience_reason": experience.get("reason"),
        "final_action": snapshot.get("final_action") or snapshot.get("action"),
        "final_direction": snapshot.get("final_direction") or snapshot.get("direction"),
        "trade_value_score": v91.get("trade_value_score"),
        "final_confidence": snapshot.get("committee_confidence") or v91.get("final_confidence"),
        "final_data_integrity_score": experience.get("data_integrity_score"),
        "risk_judge_verdict": risk_judge.get("risk_verdict") or (snapshot.get("hard_veto_status") or {}).get("blocked"),
        "risk_blocked": bool(risk_judge.get("blocked") or (snapshot.get("hard_veto_status") or {}).get("blocked")),
        "position_plan": position_plan,
        "execution_plan": execution_plan,
        "committee_members_summary": _member_summary(snapshot),
        "open_reason": position.get("open_reason") or snapshot.get("chairman_summary"),
    }


def _excursion(position: dict[str, Any], price: float) -> dict[str, Any]:
    entry = _to_float(position.get("entry_price"), 0)
    direction = str(position.get("direction") or "long")
    high = _to_float(position.get("highest_price_after_entry"), entry or price)
    low = _to_float(position.get("lowest_price_after_entry"), entry or price)
    high = max(high, price) if price > 0 else high
    low = min(low, price) if price > 0 else low
    if entry <= 0:
        mfe = mae = 0.0
    elif direction == "short":
        mfe = entry / low - 1 if low > 0 else 0.0
        mae = entry / high - 1 if high > 0 else 0.0
    else:
        mfe = high / entry - 1
        mae = low / entry - 1
    return {
        "highest_price_after_entry": high,
        "lowest_price_after_entry": low,
        "max_favorable_excursion": round(mfe * 100, 6),
        "max_adverse_excursion": round(mae * 100, 6),
    }


def build_position_progress(position: dict[str, Any], current_price: float | None = None) -> dict[str, Any]:
    price = _to_float(current_price if current_price is not None else position.get("current_price"), 0)
    open_ts = int(_to_float(position.get("open_ts"), _ts()))
    current_pnl_pct = _to_float(position.get("unrealized_pnl_pct"), 0)
    max_pnl_pct = max(_to_float(position.get("max_pnl_pct"), current_pnl_pct), current_pnl_pct)
    min_pnl_pct = min(_to_float(position.get("min_pnl_pct"), current_pnl_pct), current_pnl_pct)
    excursion = _excursion(position, price)
    return {
        "current_price": price,
        "current_pnl_pct": round(current_pnl_pct, 6),
        "max_favorable_excursion": excursion["max_favorable_excursion"],
        "max_adverse_excursion": excursion["max_adverse_excursion"],
        "max_pnl_pct": round(max_pnl_pct, 6),
        "min_pnl_pct": round(min_pnl_pct, 6),
        "highest_price_after_entry": excursion["highest_price_after_entry"],
        "lowest_price_after_entry": excursion["lowest_price_after_entry"],
        "last_update_time": _now(),
        "holding_minutes": round(max(0, _ts() - open_ts) / 60, 2),
        "take_profit_1_hit": bool(position.get("tp1_hit")),
        "take_profit_2_hit": bool(position.get("tp2_hit")),
        "stop_loss_hit": bool(position.get("stop_loss_hit")),
        "trailing_stop_active": bool(position.get("moved_stop_to_breakeven")),
        "partial_close_count": int(_to_float(position.get("partial_close_count"), 0)),
        "risk_state_changed": bool(position.get("risk_state_changed")),
        "committee_signal_changed": bool(position.get("committee_signal_changed")),
    }


def record_open_snapshot(position: dict[str, Any], account: dict[str, Any] | None = None) -> None:
    if not position.get("position_id"):
        return
    rows = _latest_map()
    trade_id = str(position.get("position_id"))
    review = rows.get(trade_id) or {}
    review.update(
        {
            "trade_id": trade_id,
            "status": "open",
            "open_snapshot": build_open_snapshot(position, account),
            "position_progress": build_position_progress(position, position.get("current_price")),
            "close_result": {},
            "experience_feedback": {},
            "updated_at": _now(),
        }
    )
    rows[trade_id] = review
    _save_latest_map(rows)


def record_position_progress(position: dict[str, Any], current_price: float | None = None) -> None:
    trade_id = str(position.get("position_id") or "")
    if not trade_id:
        return
    rows = _latest_map()
    review = rows.get(trade_id)
    if not review:
        review = {
            "trade_id": trade_id,
            "status": position.get("status") or "open",
            "open_snapshot": build_open_snapshot(position),
            "close_result": {},
            "experience_feedback": {},
        }
    review["position_progress"] = build_position_progress(position, current_price)
    review["status"] = position.get("status") or review.get("status") or "open"
    review["updated_at"] = _now()
    rows[trade_id] = review
    _save_latest_map(rows)


def record_partial_close(position: dict[str, Any], reason: str, price: float, pnl: float, ratio: float) -> None:
    position["partial_close_count"] = int(_to_float(position.get("partial_close_count"), 0)) + 1
    position["take_profit_1_hit"] = bool(position.get("take_profit_1_hit") or "止盈1" in reason or position.get("tp1_hit"))
    position["take_profit_2_hit"] = bool(position.get("take_profit_2_hit") or "止盈2" in reason or position.get("tp2_hit"))
    record_position_progress(position, price)
    rows = _latest_map()
    trade_id = str(position.get("position_id") or "")
    review = rows.get(trade_id)
    if review is not None:
        events = list(review.get("partial_close_events") or [])
        events.append({"time": _now(), "reason": reason, "price": price, "pnl": pnl, "ratio": ratio})
        review["partial_close_events"] = events[-20:]
        rows[trade_id] = review
        _save_latest_map(rows)


def _exit_type(reason: str) -> str:
    text = str(reason or "")
    if "止盈2" in text:
        return "TAKE_PROFIT_2"
    if "止盈1" in text:
        return "TAKE_PROFIT_1"
    if "止损" in text:
        return "STOP_LOSS"
    if "移动" in text or "追踪" in text:
        return "TRAILING_STOP"
    if "用户" in text or "手动" in text:
        return "MANUAL_CLOSE"
    if "委员会" in text:
        return "COMMITTEE_EXIT"
    if "时间" in text:
        return "TIME_EXIT"
    return "SYSTEM_EXIT"


def build_close_result(position: dict[str, Any], trade: dict[str, Any], close_price: float, reason: str, pnl: float) -> dict[str, Any]:
    total_pnl = _to_float(position.get("realized_pnl"), pnl)
    notional = _to_float(position.get("original_notional_usdt"), _to_float(trade.get("notional_usdt"), _to_float(position.get("notional_usdt"), 0)))
    open_ts = int(_to_float(position.get("open_ts"), _ts()))
    final_pnl_pct = total_pnl / notional * 100 if notional else _to_float(trade.get("pnl_pct"), 0)
    exit_type = _exit_type(reason)
    return {
        "close_time": trade.get("close_time") or _now(),
        "close_price": close_price,
        "close_reason": reason or "系统平仓",
        "final_pnl_pct": round(final_pnl_pct, 6),
        "final_pnl_usdt": round(total_pnl, 8),
        "holding_minutes": round(max(0, _ts() - open_ts) / 60, 2),
        "exit_type": exit_type,
        "exit_trigger": exit_type,
    }


def build_experience_feedback(open_snapshot: dict[str, Any], progress: dict[str, Any], close_result: dict[str, Any]) -> dict[str, Any]:
    vote = str(open_snapshot.get("experience_vote") or "ABSTAIN").upper()
    predicted_direction = str(open_snapshot.get("final_direction") or "").upper()
    if "LONG" in vote or predicted_direction in {"LONG", "多"}:
        prediction_direction = "LONG"
    elif "SHORT" in vote or predicted_direction in {"SHORT", "空"}:
        prediction_direction = "SHORT"
    else:
        prediction_direction = "WAIT"
    final_pnl = _to_float(close_result.get("final_pnl_pct"), 0)
    exit_type = str(close_result.get("exit_type") or "")
    actual_direction = "PROFIT" if final_pnl > 0 else "LOSS" if final_pnl < 0 else "FLAT"
    if prediction_direction in {"LONG", "SHORT"} and final_pnl > 0:
        validated: bool | str = True
        label = "经验验证"
        reason = "经验委员方向与最终盈利一致。"
    elif prediction_direction in {"LONG", "SHORT"} and (exit_type == "STOP_LOSS" or final_pnl < 0):
        validated = False
        label = "经验推翻"
        reason = "经验委员支持方向但最终亏损或触发止损。"
    elif vote == "ABSTAIN" and final_pnl > 0:
        validated = "unknown"
        label = "样本不足但盈利"
        reason = "经验委员弃权，但实际交易盈利，说明可能存在经验库未覆盖机会。"
    else:
        validated = "unknown"
        label = "无法判断"
        reason = "经验方向不明确或盈亏不足以判断经验有效性。"
    confidence = _to_float(open_snapshot.get("experience_confidence"), 0)
    if validated is True and confidence < 50:
        label = "低置信经验验证"
        reason += " 但开仓时 ExperienceConfidence 不高。"

    actual_mae = abs(_to_float(progress.get("max_adverse_excursion"), 0))
    actual_mfe = abs(_to_float(progress.get("max_favorable_excursion"), 0))
    suggested_stop = abs(_pct_value(open_snapshot.get("suggested_stop_loss")))
    suggested_tp1 = abs(_pct_value(open_snapshot.get("suggested_take_profit_1")))
    suggested_tp2 = abs(_pct_value(open_snapshot.get("suggested_take_profit_2")))
    stop_loss_quality = "unknown"
    if suggested_stop > 0 and actual_mae > suggested_stop * 1.35:
        stop_loss_quality = "poor"
    elif suggested_stop > 0:
        stop_loss_quality = "good"
    take_profit_quality = "unknown"
    target_tp = suggested_tp2 or suggested_tp1
    if target_tp > 0 and actual_mfe > target_tp * 1.5:
        take_profit_quality = "conservative"
    elif target_tp > 0 and actual_mfe >= target_tp * 0.75:
        take_profit_quality = "good"

    return {
        "experience_validated": validated,
        "prediction_direction": prediction_direction,
        "actual_direction": actual_direction,
        "prediction_quality": "good" if validated is True else "poor" if validated is False else "unknown",
        "risk_prediction_quality": "poor" if exit_type == "STOP_LOSS" and _to_float(open_snapshot.get("risk_score"), 0) < 50 else "normal",
        "stop_loss_quality": stop_loss_quality,
        "take_profit_quality": take_profit_quality,
        "experience_feedback_label": label,
        "feedback_reason": reason,
        "should_enter_future_feedback_library": bool(validated in {True, False} or label == "样本不足但盈利"),
    }


def record_close_result(position: dict[str, Any], trade: dict[str, Any], close_price: float, reason: str, pnl: float) -> dict[str, Any]:
    trade_id = str(position.get("position_id") or trade.get("trade_id") or "")
    if not trade_id:
        return {}
    rows = _latest_map()
    review = rows.get(trade_id) or {
        "trade_id": trade_id,
        "open_snapshot": build_open_snapshot(position),
        "position_progress": build_position_progress(position, close_price),
    }
    progress = dict(review.get("position_progress") or build_position_progress(position, close_price))
    progress["take_profit_1_hit"] = bool(progress.get("take_profit_1_hit") or trade.get("tp1_hit") or "止盈1" in reason)
    progress["take_profit_2_hit"] = bool(progress.get("take_profit_2_hit") or trade.get("tp2_hit") or "止盈2" in reason)
    progress["stop_loss_hit"] = bool(progress.get("stop_loss_hit") or "止损" in reason)
    close_result = build_close_result(position, trade, close_price, reason, pnl)
    feedback = build_experience_feedback(dict(review.get("open_snapshot") or {}), progress, close_result)
    review.update(
        {
            "status": "closed",
            "position_progress": progress,
            "close_result": close_result,
            "experience_feedback": feedback,
            "history_row": trade,
            "updated_at": _now(),
        }
    )
    rows[trade_id] = review
    _save_latest_map(rows)
    _append_jsonl(REVIEWS_JSONL_PATH, review)
    save_feedback_summary()
    return review


def load_sim_trade_reviews(limit: int = 100) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        if REVIEWS_JSONL_PATH.exists():
            with REVIEWS_JSONL_PATH.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        item = json.loads(line)
                        if isinstance(item, dict):
                            rows.append(item)
    except Exception:
        rows = []
    latest = _latest_map()
    closed_ids = {str(row.get("trade_id")) for row in rows}
    for trade_id, row in latest.items():
        if trade_id not in closed_ids:
            rows.append(row)
    rows.sort(key=lambda row: str((row.get("close_result") or {}).get("close_time") or (row.get("open_snapshot") or {}).get("entry_time") or ""), reverse=True)
    return rows[:limit]


def build_feedback_summary(reviews: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    reviews = reviews if reviews is not None else load_sim_trade_reviews(5000)
    closed = [row for row in reviews if row.get("status") == "closed"]
    profits = [row for row in closed if _to_float((row.get("close_result") or {}).get("final_pnl_usdt"), 0) > 0]
    losses = [row for row in closed if _to_float((row.get("close_result") or {}).get("final_pnl_usdt"), 0) < 0]
    def avg(field: str, source: str = "close_result") -> float:
        values = [_to_float((row.get(source) or {}).get(field), 0) for row in closed]
        return round(sum(values) / len(values), 6) if values else 0.0

    by_version: dict[str, dict[str, Any]] = {}
    for row in closed:
        version = str((row.get("open_snapshot") or {}).get("experience_library_version") or "unknown")
        bucket = by_version.setdefault(version, {"trades": 0, "wins": 0, "losses": 0, "total_pnl_usdt": 0.0, "avg_pnl_pct": 0.0})
        pnl = _to_float((row.get("close_result") or {}).get("final_pnl_usdt"), 0)
        pnl_pct = _to_float((row.get("close_result") or {}).get("final_pnl_pct"), 0)
        bucket["trades"] += 1
        bucket["wins"] += 1 if pnl > 0 else 0
        bucket["losses"] += 1 if pnl < 0 else 0
        bucket["total_pnl_usdt"] += pnl
        bucket["avg_pnl_pct"] += pnl_pct
    for bucket in by_version.values():
        trades = max(1, int(bucket.get("trades", 0)))
        bucket["win_rate"] = round(float(bucket.get("wins", 0)) / trades * 100, 2)
        bucket["total_pnl_usdt"] = round(float(bucket.get("total_pnl_usdt", 0)), 6)
        bucket["avg_pnl_pct"] = round(float(bucket.get("avg_pnl_pct", 0)) / trades, 6)

    feedback_rows = [row.get("experience_feedback") or {} for row in closed]
    return {
        "generated_at": _now(),
        "total_trades": len(closed),
        "winning_trades": len(profits),
        "losing_trades": len(losses),
        "win_rate": round(len(profits) / len(closed) * 100, 2) if closed else 0.0,
        "avg_return_pct": avg("final_pnl_pct"),
        "avg_max_favorable_excursion": avg("max_favorable_excursion", "position_progress"),
        "avg_max_adverse_excursion": avg("max_adverse_excursion", "position_progress"),
        "take_profit_count": len([row for row in closed if str((row.get("close_result") or {}).get("exit_type", "")).startswith("TAKE_PROFIT")]),
        "stop_loss_count": len([row for row in closed if (row.get("close_result") or {}).get("exit_type") == "STOP_LOSS"]),
        "by_experience_library_version": by_version,
        "experience_validated_count": len([row for row in feedback_rows if row.get("experience_validated") is True]),
        "experience_invalidated_count": len([row for row in feedback_rows if row.get("experience_validated") is False]),
        "unknown_but_profit_count": len([row for row in feedback_rows if row.get("experience_feedback_label") == "样本不足但盈利"]),
        "tight_stop_loss_count": len([row for row in feedback_rows if row.get("stop_loss_quality") == "poor"]),
        "conservative_take_profit_count": len([row for row in feedback_rows if row.get("take_profit_quality") == "conservative"]),
    }


def save_feedback_summary() -> dict[str, Any]:
    summary = build_feedback_summary()
    _write_json(FEEDBACK_SUMMARY_PATH, summary)
    return summary


def load_feedback_summary() -> dict[str, Any]:
    summary = _read_json(FEEDBACK_SUMMARY_PATH, {})
    return summary if isinstance(summary, dict) else {}


def export_review_summary(json_path: Path, csv_path: Path) -> dict[str, Any]:
    reviews = load_sim_trade_reviews(5000)
    summary = build_feedback_summary(reviews)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps({"summary": summary, "reviews": reviews}, ensure_ascii=False, indent=2), encoding="utf-8")
    fieldnames = [
        "trade_id",
        "symbol",
        "side",
        "entry_time",
        "close_time",
        "entry_price",
        "close_price",
        "final_pnl_pct",
        "final_pnl_usdt",
        "max_favorable_excursion",
        "max_adverse_excursion",
        "close_reason",
        "experience_library_version",
        "state_code",
        "experience_vote",
        "experience_validated",
        "experience_feedback_label",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for review in reviews:
            open_snapshot = review.get("open_snapshot") or {}
            progress = review.get("position_progress") or {}
            close_result = review.get("close_result") or {}
            feedback = review.get("experience_feedback") or {}
            writer.writerow(
                {
                    "trade_id": review.get("trade_id"),
                    "symbol": open_snapshot.get("symbol"),
                    "side": open_snapshot.get("side"),
                    "entry_time": open_snapshot.get("entry_time"),
                    "close_time": close_result.get("close_time"),
                    "entry_price": open_snapshot.get("entry_price"),
                    "close_price": close_result.get("close_price"),
                    "final_pnl_pct": close_result.get("final_pnl_pct"),
                    "final_pnl_usdt": close_result.get("final_pnl_usdt"),
                    "max_favorable_excursion": progress.get("max_favorable_excursion"),
                    "max_adverse_excursion": progress.get("max_adverse_excursion"),
                    "close_reason": close_result.get("close_reason"),
                    "experience_library_version": open_snapshot.get("experience_library_version"),
                    "state_code": open_snapshot.get("state_code"),
                    "experience_vote": open_snapshot.get("experience_vote"),
                    "experience_validated": feedback.get("experience_validated"),
                    "experience_feedback_label": feedback.get("experience_feedback_label"),
                }
            )
    return summary

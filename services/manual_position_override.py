"""人工仓位干预层。

用户只能在风控允许范围内选择仓位；硬否决、数据质量差和安全锁不能被绕过。
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OVERRIDE_JSON_PATH = DATA_DIR / "manual_position_override_log.json"
OVERRIDE_CSV_PATH = DATA_DIR / "manual_position_override_log.csv"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            return float(value.strip().replace("%", ""))
        return float(value)
    except (TypeError, ValueError):
        return default


def position_text_to_bounds(text: Any) -> tuple[float, float]:
    raw = str(text or "0%").replace("％", "%").strip()
    if "-" in raw:
        left, right = raw.split("-", 1)
        return _to_float(left), _to_float(right)
    value = _to_float(raw)
    return value, value


def calculate_system_position_pct(position_suggestion: Any) -> float:
    low, high = position_text_to_bounds(position_suggestion)
    if high <= 0:
        return 0.0
    return round((low + high) / 2, 2)


def calculate_risk_max_position(decision: dict[str, Any]) -> float:
    permission = str(decision.get("trade_permission", "blocked"))
    quality = (decision.get("data_quality") or {}).get("level", decision.get("data_quality", "poor"))
    risk_score = _to_float(decision.get("committee_risk_score"), 85)
    if permission == "blocked" or decision.get("veto_members") or quality == "poor":
        return 0.0
    _, suggestion_high = position_text_to_bounds(decision.get("position_suggestion", "0%"))
    max_pct = max(suggestion_high, 3.0)
    if risk_score >= 70:
        max_pct = min(max_pct, 3.0)
    elif risk_score >= 55:
        max_pct = min(max_pct, 5.0)
    else:
        max_pct = min(max_pct, 10.0)
    if quality == "partial":
        max_pct = min(max_pct, 5.0)
    return round(max_pct, 2)


def evaluate_manual_position_override(
    decision: dict[str, Any],
    user_selected_pct: float | int | None,
    *,
    confirmed: bool = False,
    confirm_text: str = "",
) -> dict[str, Any]:
    user_pct = round(_to_float(user_selected_pct), 2)
    system_pct = calculate_system_position_pct(decision.get("position_suggestion"))
    risk_max = _to_float(decision.get("risk_max_position"), calculate_risk_max_position(decision))
    action = str(decision.get("final_action", "禁止开仓"))
    permission = str(decision.get("trade_permission", "blocked"))
    quality = (decision.get("data_quality") or {}).get("level", decision.get("data_quality", "poor"))
    veto_members = list(decision.get("veto_members") or [])
    hard = decision.get("hard_veto_status") or {}
    reasons: list[str] = []
    if action == "禁止开仓" or permission == "blocked":
        reasons.append("委员会最终禁止开仓，用户不能通过人工仓位干预绕过。")
    if veto_members:
        reasons.append("硬否决已触发：" + "、".join(str(x) for x in veto_members))
    if hard.get("blocked"):
        reasons.extend(list(hard.get("reasons") or [])[:3])
    if quality == "poor":
        reasons.append("数据质量为 poor，禁止人工调整仓位。")
    if user_pct < 0:
        reasons.append("用户选择仓位不能小于 0。")
    if user_pct > risk_max:
        reasons.append("用户选择仓位超过风控允许最大值，系统已拒绝。")
    requires_confirmation = bool(user_pct > system_pct and user_pct > 0)
    confirm_ok = True
    if requires_confirmation and not confirmed:
        confirm_ok = False
        reasons.append("用户选择仓位高于系统建议仓位，必须二次确认。")
    if requires_confirmation and confirm_text and "风险" not in confirm_text:
        confirm_ok = False
        reasons.append("确认短句未包含风险承诺，本次干预未通过。")
    allowed = not reasons and confirm_ok
    return {
        "allowed": allowed,
        "reasons": reasons,
        "system_position_pct": system_pct,
        "risk_max_position_pct": risk_max,
        "user_selected_position_pct": user_pct,
        "requires_confirmation": requires_confirmation,
        "above_system_suggestion": user_pct > system_pct,
        "near_risk_limit": risk_max > 0 and user_pct >= risk_max * 0.8,
        "message": "人工仓位选择已通过风控范围检查。" if allowed else "；".join(reasons),
    }


def save_manual_position_override(decision: dict[str, Any], evaluation: dict[str, Any], mode: str = "模拟", current_price: Any = None, confirm_text: str = "") -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "time": _now(),
        "symbol": decision.get("symbol"),
        "mode": mode,
        "system_position_suggestion": decision.get("position_suggestion"),
        "system_position_pct": evaluation.get("system_position_pct"),
        "risk_max_position": evaluation.get("risk_max_position_pct"),
        "user_selected_position": evaluation.get("user_selected_position_pct"),
        "above_system_suggestion": evaluation.get("above_system_suggestion"),
        "near_risk_limit": evaluation.get("near_risk_limit"),
        "user_confirmed": bool(evaluation.get("allowed")) or not evaluation.get("requires_confirmation"),
        "committee_final_action": decision.get("final_action"),
        "risk_score": decision.get("committee_risk_score"),
        "risk_member_status": "否决" if "风险委员" in list(decision.get("veto_members") or []) else "未否决",
        "live_safety_member_status": "否决" if "实盘安全委员" in list(decision.get("veto_members") or []) else "未否决",
        "current_price": current_price,
        "confirm_text": "已记录，原文不导出敏感信息" if confirm_text else "",
        "result": "通过" if evaluation.get("allowed") else "拒绝",
        "reason": evaluation.get("message"),
    }
    history = load_manual_position_override_log(500)
    history.insert(0, row)
    OVERRIDE_JSON_PATH.write_text(json.dumps(history[:500], ensure_ascii=False, indent=2), encoding="utf-8")
    write_header = not OVERRIDE_CSV_PATH.exists()
    with OVERRIDE_CSV_PATH.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            writer.writeheader()
        writer.writerow(row)
    return row


def load_manual_position_override_log(limit: int = 100) -> list[dict[str, Any]]:
    try:
        if not OVERRIDE_JSON_PATH.exists():
            return []
        data = json.loads(OVERRIDE_JSON_PATH.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else []
        return rows[:limit]
    except Exception:
        return []

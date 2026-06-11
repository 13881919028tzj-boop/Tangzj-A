"""半自动交易审批中心。

半自动审批只生成、展示、检查和记录审批单。真实执行仍必须走既有
Live Manual、Spot Test Order、确认短句和安全锁链路。
"""

from __future__ import annotations

import csv
import json
import time
import uuid
from pathlib import Path
from typing import Any

from services.live_trading_center import (
    cancel_live_order,
    create_live_order_plan,
    preview_live_exit_order,
    run_exit_spot_test_order,
    run_live_exit_preflight,
    run_live_manual_preflight,
    run_spot_test_order,
    submit_live_exit_order,
    submit_live_spot_order,
    validate_live_order_plan,
)
from services.sim_trade_engine import create_pending_sim_order


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
APPROVAL_QUEUE_PATH = DATA_DIR / "approval_queue.json"
APPROVAL_AUDIT_JSON_PATH = DATA_DIR / "approval_audit_log.json"
APPROVAL_AUDIT_CSV_PATH = DATA_DIR / "approval_audit_log.csv"

SEMI_AUTO_MODE = "SEMI_AUTO_APPROVAL"

ENTRY_ACTIONS = {"轻仓试多", "顺势做多", "轻仓试空", "顺势做空"}
EXIT_ACTIONS = {"建议部分止盈", "建议手动平仓", "建议减仓", "信号失效"}

EXPIRY_MINUTES = {
    "entry": 5,
    "exit": 10,
    "partial_exit": 10,
    "cancel": 2,
    "risk_action": 3,
    "strategy_admission": 24 * 60,
}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _now_ts() -> float:
    return time.time()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_json(path: Path, default: Any) -> Any:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if not path.exists():
            _write_json(path, default)
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _write_json(path, default)
        return default


def _write_json(path: Path, data: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_time(value: str) -> float:
    try:
        return time.mktime(time.strptime(str(value), "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


def _expiry_for(approval_type: str) -> str:
    minutes = EXPIRY_MINUTES.get(approval_type, 5)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_now_ts() + minutes * 60))


def _history_event(action: str, reason: str = "", user: str = "system") -> dict[str, Any]:
    return {"time": _now(), "action": action, "user": user, "reason": reason}


def _approval_index(queue: list[dict[str, Any]], approval_id: str) -> int | None:
    for idx, row in enumerate(queue):
        if row.get("approval_id") == approval_id:
            return idx
    return None


def log_approval_event(event: dict[str, Any] | str) -> None:
    if isinstance(event, str):
        row = {"time": _now(), "event": event, "approval_id": "", "symbol": "", "status": "", "result": "", "reason": ""}
    else:
        row = {
            "time": _now(),
            "event": str(event.get("event", "审批事件")),
            "approval_id": str(event.get("approval_id", "")),
            "symbol": str(event.get("symbol", "")),
            "approval_type": str(event.get("approval_type", "")),
            "status": str(event.get("status", "")),
            "result": str(event.get("result", "")),
            "reason": str(event.get("reason", "")),
            "risk_level": str(event.get("risk_level", "")),
            "idempotency_key": str(event.get("idempotency_key", "")),
        }
    logs = _read_json(APPROVAL_AUDIT_JSON_PATH, [])
    logs.insert(0, row)
    _write_json(APPROVAL_AUDIT_JSON_PATH, logs[:1000])
    try:
        with APPROVAL_AUDIT_CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
            writer.writeheader()
            writer.writerows(logs[:1000])
    except Exception:
        pass


def load_approval_audit_log(limit: int = 100) -> list[dict[str, Any]]:
    data = _read_json(APPROVAL_AUDIT_JSON_PATH, [])
    return (data if isinstance(data, list) else [])[:limit]


def load_approval_queue() -> list[dict[str, Any]]:
    queue = _read_json(APPROVAL_QUEUE_PATH, [])
    if not isinstance(queue, list):
        return []
    changed = False
    for row in queue:
        if row.get("status") == "pending" and _parse_time(str(row.get("expires_at", ""))) and _parse_time(str(row.get("expires_at"))) < _now_ts():
            row["status"] = "expired"
            row["expire_reason"] = "审批单已过期，市场状态可能已经变化，请重新生成。"
            row.setdefault("approval_history", []).append(_history_event("审批过期", row["expire_reason"]))
            changed = True
    if changed:
        save_approval_queue(queue)
    return queue


def save_approval_queue(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _write_json(APPROVAL_QUEUE_PATH, queue[:1000])
    return queue


def _save_approval(approval: dict[str, Any]) -> dict[str, Any]:
    queue = load_approval_queue()
    idx = _approval_index(queue, str(approval.get("approval_id")))
    if idx is None:
        queue.insert(0, approval)
    else:
        queue[idx] = approval
    save_approval_queue(queue)
    return approval


def _normalize_direction(signal: dict[str, Any]) -> str:
    direction = str(signal.get("direction") or signal.get("final_direction") or "").lower()
    action = str(signal.get("action") or signal.get("final_action") or "")
    if direction in {"long", "多", "buy"} or "多" in action:
        return "BUY"
    if direction in {"short", "空", "sell"} or "空" in action:
        return "SELL"
    return "BUY"


def _external_ai_snapshots(source: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    external_ai = source.get("external_ai") or source.get("external_ai_snapshot") or {}
    return external_ai.get("deepseek") or {}, external_ai.get("gemini") or {}


def _risk_priority(risk_level: str, soft_veto: bool = False) -> str:
    if risk_level in {"极高", "高"} or soft_veto:
        return "高"
    if risk_level == "中":
        return "中"
    return "低"


def create_trade_approval(signal: dict[str, Any]) -> dict[str, Any]:
    action = str(signal.get("action") or signal.get("final_action") or "")
    if action in EXIT_ACTIONS:
        return create_exit_approval({}, signal)
    return create_entry_approval(signal)


def create_entry_approval(signal: dict[str, Any]) -> dict[str, Any]:
    symbol = str(signal.get("symbol", "BTCUSDT")).upper()
    price = _to_float(signal.get("current_price"), _to_float(signal.get("entry_price"), _to_float(signal.get("planned_entry_price"))))
    amount = _to_float(signal.get("user_selected_amount"), _to_float(signal.get("system_suggested_amount"), 5))
    risk_max = _to_float(signal.get("risk_max_amount"), _to_float(signal.get("risk_max_position"), 10))
    deep, gemini = _external_ai_snapshots(signal)
    soft = bool(deep.get("soft_veto") or gemini.get("soft_veto"))
    risk_level = str(signal.get("risk_level") or signal.get("risk_score") or "中")
    approval = {
        "approval_id": f"appr_{uuid.uuid4().hex[:12]}",
        "approval_type": "entry",
        "mode": str(signal.get("mode", "LIVE_MANUAL")),
        "symbol": symbol,
        "market_type": "spot",
        "side": _normalize_direction(signal),
        "order_type": str(signal.get("order_type", "LIMIT")),
        "source": str(signal.get("source", "AI交易委员会")),
        "status": "pending",
        "priority": _risk_priority(risk_level, soft),
        "created_time": _now(),
        "expires_at": _expiry_for("entry"),
        "price_at_create": price,
        "current_price": price,
        "price_drift_pct": 0,
        "system_suggested_amount": amount,
        "risk_max_amount": risk_max or 10,
        "user_selected_amount": None,
        "entry_plan": {
            "symbol": symbol,
            "side": _normalize_direction(signal),
            "order_type": str(signal.get("order_type", "LIMIT")),
            "price": price,
            "quantity": _to_float(signal.get("quantity"), amount / price if price else 0),
            "quote_amount": amount,
            "source": str(signal.get("source", "AI交易委员会")),
        },
        "exit_plan": {},
        "committee_snapshot": signal.get("committee_snapshot") or signal,
        "local_strategy_snapshot": signal.get("local_strategy_snapshot") or {},
        "risk_snapshot": signal.get("risk_snapshot") or {"risk_level": risk_level},
        "live_safety_snapshot": signal.get("live_safety_snapshot") or {},
        "deepseek_snapshot": deep,
        "gemini_snapshot": gemini,
        "preflight_result": {},
        "approval_history": [_history_event("审批单创建", "系统生成开仓审批单。")],
        "user_decision": None,
        "user_reason": "",
        "execution_result": {},
        "review_tags": ["外部AI风险提醒"] if soft else [],
        "idempotency_key": f"idem_{uuid.uuid4().hex[:16]}",
    }
    _save_approval(approval)
    log_approval_event({"event": "审批单创建", "approval_id": approval["approval_id"], "symbol": symbol, "approval_type": "entry", "status": "pending", "result": "已创建", "reason": "系统生成开仓审批单。"})
    return approval


def create_exit_approval(position: dict[str, Any], exit_plan: dict[str, Any]) -> dict[str, Any]:
    merged = {**(position or {}), **(exit_plan or {})}
    symbol = str(merged.get("symbol", "BTCUSDT")).upper()
    ratio = _to_float(merged.get("exit_ratio"), 1.0)
    price = _to_float(merged.get("price"), _to_float(merged.get("current_price")))
    deep, gemini = _external_ai_snapshots(merged)
    approval_type = "partial_exit" if 0 < ratio < 0.999 else "exit"
    approval = {
        "approval_id": f"appr_{uuid.uuid4().hex[:12]}",
        "approval_type": approval_type,
        "mode": str(merged.get("mode", "LIVE_MANUAL")),
        "symbol": symbol,
        "market_type": "spot",
        "side": "SELL",
        "order_type": str(merged.get("order_type", "LIMIT")),
        "source": str(merged.get("source", "实盘持仓管理")),
        "status": "pending",
        "priority": str(merged.get("priority", "中")),
        "created_time": _now(),
        "expires_at": _expiry_for(approval_type),
        "price_at_create": price,
        "current_price": price,
        "price_drift_pct": 0,
        "system_suggested_amount": _to_float(merged.get("estimated_value")),
        "risk_max_amount": _to_float(merged.get("risk_max_amount"), _to_float(merged.get("estimated_value"))),
        "user_selected_amount": None,
        "entry_plan": {},
        "exit_plan": exit_plan or merged,
        "committee_snapshot": merged.get("committee_snapshot") or {},
        "local_strategy_snapshot": merged.get("local_strategy_snapshot") or {},
        "risk_snapshot": merged.get("risk_snapshot") or {},
        "live_safety_snapshot": merged.get("live_safety_snapshot") or {},
        "deepseek_snapshot": deep,
        "gemini_snapshot": gemini,
        "preflight_result": {},
        "approval_history": [_history_event("审批单创建", "系统生成平仓审批单。")],
        "user_decision": None,
        "user_reason": "",
        "execution_result": {},
        "review_tags": ["部分平仓"] if approval_type == "partial_exit" else ["全部平仓"],
        "idempotency_key": f"idem_{uuid.uuid4().hex[:16]}",
    }
    _save_approval(approval)
    log_approval_event({"event": "审批单创建", "approval_id": approval["approval_id"], "symbol": symbol, "approval_type": approval_type, "status": "pending", "result": "已创建", "reason": "系统生成平仓审批单。"})
    return approval


def create_partial_exit_approval(position: dict[str, Any], ratio: float) -> dict[str, Any]:
    qty = _to_float(position.get("remaining_quantity")) * _to_float(ratio, 0.5)
    price = _to_float(position.get("current_price"), _to_float(position.get("avg_entry_price")))
    exit_plan = {
        "live_position_id": position.get("live_position_id"),
        "symbol": position.get("symbol"),
        "side": "SELL",
        "exit_ratio": ratio,
        "exit_quantity": qty,
        "quantity": qty,
        "order_type": "LIMIT",
        "price": price,
        "estimated_value": qty * price,
        "position_snapshot": position,
        "exit_reason": "风险处理审批",
    }
    return create_exit_approval(position, exit_plan)


def create_cancel_order_approval(order: dict[str, Any]) -> dict[str, Any]:
    symbol = str(order.get("symbol", "")).upper()
    approval = {
        "approval_id": f"appr_{uuid.uuid4().hex[:12]}",
        "approval_type": "cancel",
        "mode": "LIVE_MANUAL",
        "symbol": symbol,
        "market_type": "spot",
        "side": str(order.get("side", "")),
        "order_type": str(order.get("order_type", "")),
        "source": "真实订单管理",
        "status": "pending",
        "priority": "中",
        "created_time": _now(),
        "expires_at": _expiry_for("cancel"),
        "price_at_create": _to_float(order.get("price")),
        "current_price": _to_float(order.get("price")),
        "price_drift_pct": 0,
        "system_suggested_amount": _to_float(order.get("notional")),
        "risk_max_amount": _to_float(order.get("notional")),
        "user_selected_amount": None,
        "entry_plan": {},
        "exit_plan": {},
        "cancel_order": order,
        "approval_history": [_history_event("审批单创建", "系统生成撤单审批单。")],
        "user_decision": None,
        "user_reason": "",
        "execution_result": {},
        "review_tags": ["撤单审批"],
        "idempotency_key": f"idem_{uuid.uuid4().hex[:16]}",
    }
    _save_approval(approval)
    log_approval_event({"event": "审批单创建", "approval_id": approval["approval_id"], "symbol": symbol, "approval_type": "cancel", "status": "pending", "result": "已创建", "reason": "系统生成撤单审批单。"})
    return approval


def get_pending_approvals() -> list[dict[str, Any]]:
    return [row for row in load_approval_queue() if row.get("status") == "pending"]


def get_approval_detail(approval_id: str) -> dict[str, Any] | None:
    for row in load_approval_queue():
        if row.get("approval_id") == approval_id:
            return row
    return None


def check_approval_price_drift(approval: dict[str, Any], current_price: float | None = None) -> dict[str, Any]:
    price0 = _to_float(approval.get("price_at_create"))
    now_price = _to_float(current_price, _to_float(approval.get("current_price"), price0))
    drift = abs(now_price - price0) / price0 * 100 if price0 else 0.0
    status = "ok"
    message = "价格漂移在允许范围内。"
    if drift > 0.8:
        status = "blocked"
        message = "价格漂移超过 0.8%，审批单已失效，请重新生成。"
    elif drift > 0.3:
        status = "reconfirm"
        message = "价格漂移超过 0.3%，需要重新确认。"
    return {"ok": status == "ok", "status": status, "price_at_create": price0, "current_price": now_price, "price_drift_pct": drift, "message": message}


def check_approval_risk_change(approval: dict[str, Any]) -> dict[str, Any]:
    risk = approval.get("risk_snapshot") or {}
    live = approval.get("live_safety_snapshot") or {}
    hard_reasons: list[str] = []
    warnings: list[str] = []
    if risk.get("veto") or risk.get("risk_veto"):
        hard_reasons.append("风险委员已触发否决。")
    if live.get("veto") or live.get("kill_switch_enabled"):
        hard_reasons.append("实盘安全委员或安全锁阻止执行。")
    if (approval.get("deepseek_snapshot") or {}).get("soft_veto") or (approval.get("gemini_snapshot") or {}).get("soft_veto"):
        warnings.append("外部AI存在软否决或风险提醒。")
    return {"ok": not hard_reasons, "hard_reasons": hard_reasons, "warnings": warnings, "message": "风险复核通过。" if not hard_reasons else "；".join(hard_reasons)}


def approve_approval(approval_id: str, user_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    queue = load_approval_queue()
    idx = _approval_index(queue, approval_id)
    if idx is None:
        return {"ok": False, "message": "审批单不存在。"}
    approval = queue[idx]
    if approval.get("status") not in {"pending", "modified"}:
        return {"ok": False, "message": f"当前状态 {approval.get('status')} 不允许批准。"}
    if _parse_time(str(approval.get("expires_at", ""))) < _now_ts():
        approval["status"] = "expired"
        approval.setdefault("approval_history", []).append(_history_event("审批过期", "用户批准时审批单已过期。", "user"))
        queue[idx] = approval
        save_approval_queue(queue)
        return {"ok": False, "message": "该审批单已过期，市场状态可能已经变化，请重新生成。", "approval": approval}
    inputs = user_inputs or {}
    approval["status"] = "approved"
    approval["user_decision"] = "approved"
    approval["user_reason"] = str(inputs.get("reason", "用户批准。"))
    if inputs.get("user_selected_amount") not in {None, ""}:
        approval["user_selected_amount"] = _to_float(inputs.get("user_selected_amount"))
    approval.setdefault("approval_history", []).append(_history_event("用户批准", approval["user_reason"], "user"))
    queue[idx] = approval
    save_approval_queue(queue)
    log_approval_event({"event": "用户批准", "approval_id": approval_id, "symbol": approval.get("symbol"), "approval_type": approval.get("approval_type"), "status": "approved", "result": "已批准", "reason": approval["user_reason"]})
    return {"ok": True, "message": "审批单已批准，执行前仍需通过预检和确认短句。", "approval": approval}


def reject_approval(approval_id: str, reason: str) -> dict[str, Any]:
    queue = load_approval_queue()
    idx = _approval_index(queue, approval_id)
    if idx is None:
        return {"ok": False, "message": "审批单不存在。"}
    approval = queue[idx]
    approval["status"] = "rejected"
    approval["user_decision"] = "rejected"
    approval["user_reason"] = reason or "用户拒绝。"
    approval.setdefault("approval_history", []).append(_history_event("用户拒绝", approval["user_reason"], "user"))
    queue[idx] = approval
    save_approval_queue(queue)
    log_approval_event({"event": "用户拒绝", "approval_id": approval_id, "symbol": approval.get("symbol"), "approval_type": approval.get("approval_type"), "status": "rejected", "result": "已拒绝", "reason": approval["user_reason"]})
    return {"ok": True, "message": "审批单已拒绝。", "approval": approval}


def modify_approval(approval_id: str, modifications: dict[str, Any]) -> dict[str, Any]:
    queue = load_approval_queue()
    idx = _approval_index(queue, approval_id)
    if idx is None:
        return {"ok": False, "message": "审批单不存在。"}
    approval = queue[idx]
    amount = _to_float(modifications.get("user_selected_amount"), _to_float(approval.get("user_selected_amount"), _to_float(approval.get("system_suggested_amount"))))
    if amount and amount > _to_float(approval.get("risk_max_amount")):
        return {"ok": False, "message": "用户选择金额超过风控允许最大金额，系统已拒绝。"}
    for key in ["user_selected_amount", "order_type", "current_price", "priority"]:
        if key in modifications:
            approval[key] = modifications[key]
    if approval.get("approval_type") == "entry":
        plan = approval.setdefault("entry_plan", {})
        if "user_selected_amount" in modifications:
            plan["quote_amount"] = amount
        if "current_price" in modifications:
            plan["price"] = _to_float(modifications.get("current_price"))
        if "order_type" in modifications:
            plan["order_type"] = modifications.get("order_type")
    approval["status"] = "pending"
    approval.setdefault("approval_history", []).append(_history_event("审批单修改", "用户修改审批参数后回到待审批。", "user"))
    queue[idx] = approval
    save_approval_queue(queue)
    log_approval_event({"event": "审批单修改", "approval_id": approval_id, "symbol": approval.get("symbol"), "approval_type": approval.get("approval_type"), "status": "pending", "result": "已修改", "reason": "用户修改审批参数。"})
    return {"ok": True, "message": "审批单已修改，并回到待审批状态。", "approval": approval}


def expire_approval(approval_id: str, reason: str) -> dict[str, Any]:
    approval = get_approval_detail(approval_id)
    if not approval:
        return {"ok": False, "message": "审批单不存在。"}
    approval["status"] = "expired"
    approval["expire_reason"] = reason or "审批单已过期。"
    approval.setdefault("approval_history", []).append(_history_event("审批过期", approval["expire_reason"]))
    _save_approval(approval)
    log_approval_event({"event": "审批过期", "approval_id": approval_id, "symbol": approval.get("symbol"), "approval_type": approval.get("approval_type"), "status": "expired", "result": "已过期", "reason": approval["expire_reason"]})
    return {"ok": True, "message": approval["expire_reason"], "approval": approval}


def run_approval_preflight(approval: dict[str, Any], current_price: float | None = None, test_order_result: dict[str, Any] | None = None, confirmation_phrase: str = "") -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    expired = _parse_time(str(approval.get("expires_at", ""))) < _now_ts()
    drift = check_approval_price_drift(approval, current_price)
    risk = check_approval_risk_change(approval)
    checks.append({"name": "审批单未过期", "ok": not expired, "message": "通过" if not expired else "审批单已过期。"})
    checks.append({"name": "价格漂移检查", "ok": drift.get("status") != "blocked", "message": drift.get("message")})
    checks.append({"name": "风险变化检查", "ok": risk.get("ok"), "message": risk.get("message")})
    if approval.get("status") != "approved":
        checks.append({"name": "用户已批准", "ok": False, "message": "审批单尚未处于 approved 状态。"})

    extra: dict[str, Any] = {}
    approval_type = str(approval.get("approval_type"))
    mode = str(approval.get("mode", "LIVE_MANUAL"))
    if mode == "LIVE_MANUAL" and approval_type == "entry":
        plan = create_live_order_plan(approval.get("committee_snapshot") or {}, approval.get("entry_plan") or {})
        plan["quote_amount"] = _to_float(approval.get("user_selected_amount"), _to_float(plan.get("quote_amount")))
        extra = run_live_manual_preflight(plan, test_order_result or {}, True, confirmation_phrase)
        checks.extend({"name": item.get("name"), "ok": item.get("status") == "通过", "message": item.get("message")} for item in extra.get("checklist", []))
    elif mode == "LIVE_MANUAL" and approval_type in {"exit", "partial_exit"}:
        extra = run_live_exit_preflight(approval.get("exit_plan") or {}, test_order_result or {}, True, confirmation_phrase)
        checks.extend({"name": item.get("name"), "ok": item.get("status") == "通过", "message": item.get("message")} for item in extra.get("checklist", []))
    elif approval_type == "cancel":
        required = "我确认撤销该真实订单"
        checks.append({"name": "撤单确认短句", "ok": str(confirmation_phrase).strip() == required, "message": f"请输入：{required}"})
    else:
        checks.append({"name": "模拟审批", "ok": True, "message": "模拟执行不涉及真实资金。"})
    ok = all(bool(item.get("ok")) for item in checks)
    result = {"ok": ok, "checks": checks, "price_drift": drift, "risk_change": risk, "external_preflight": extra, "message": "审批执行前检查通过。" if ok else "审批执行前检查未通过。"}
    approval["preflight_result"] = result
    _save_approval(approval)
    log_approval_event({"event": "执行前检查", "approval_id": approval.get("approval_id"), "symbol": approval.get("symbol"), "approval_type": approval.get("approval_type"), "status": approval.get("status"), "result": "通过" if ok else "失败", "reason": result["message"]})
    return result


def execute_approved_approval(approval: dict[str, Any], test_order_result: dict[str, Any] | None = None, confirmation_phrase: str = "", current_price: float | None = None) -> dict[str, Any]:
    approval = get_approval_detail(str(approval.get("approval_id"))) or approval
    if approval.get("execution_result", {}).get("execution_id"):
        return {"ok": False, "message": "该审批单已存在执行记录，系统已阻止重复执行。", "approval": approval}
    preflight = run_approval_preflight(approval, current_price, test_order_result, confirmation_phrase)
    if not preflight.get("ok"):
        approval["status"] = "failed"
        approval["execution_result"] = {"ok": False, "message": preflight.get("message"), "preflight": preflight}
        _save_approval(approval)
        return {"ok": False, "message": preflight.get("message"), "approval": approval}
    execution_id = f"exec_{uuid.uuid4().hex[:12]}"
    approval["status"] = "executing"
    approval.setdefault("approval_history", []).append(_history_event("开始执行", execution_id))
    _save_approval(approval)
    try:
        approval_type = str(approval.get("approval_type"))
        mode = str(approval.get("mode", "LIVE_MANUAL"))
        if mode == "SIM" and approval_type == "entry":
            signal = approval.get("committee_snapshot") or {"symbol": approval.get("symbol")}
            price = _to_float(current_price, _to_float(approval.get("current_price")))
            result = create_pending_sim_order(signal, price)
            output = {"ok": bool(result), "message": "模拟审批已执行。" if result else "模拟审批执行失败。", "order": result}
        elif mode == "LIVE_MANUAL" and approval_type == "entry":
            plan = create_live_order_plan(approval.get("committee_snapshot") or {}, approval.get("entry_plan") or {})
            output = submit_live_spot_order(plan, test_order_result or {}, True, confirmation_phrase)
        elif mode == "LIVE_MANUAL" and approval_type in {"exit", "partial_exit"}:
            output = submit_live_exit_order(approval.get("exit_plan") or {}, test_order_result or {}, True, confirmation_phrase)
        elif mode == "LIVE_MANUAL" and approval_type == "cancel":
            order = approval.get("cancel_order") or {}
            output = cancel_live_order(str(order.get("order_id", "")), str(order.get("symbol", approval.get("symbol", ""))), str(confirmation_phrase).strip() == "我确认撤销该真实订单")
        else:
            output = {"ok": False, "message": "当前审批类型暂不支持执行，已保持安全阻止。"}
        approval["status"] = "executed" if output.get("ok") else "failed"
        approval["execution_result"] = {**output, "execution_id": execution_id, "idempotency_key": approval.get("idempotency_key")}
        approval.setdefault("approval_history", []).append(_history_event("执行完成" if output.get("ok") else "执行失败", output.get("message", "")))
        _save_approval(approval)
        log_approval_event({"event": "执行成功" if output.get("ok") else "执行失败", "approval_id": approval.get("approval_id"), "symbol": approval.get("symbol"), "approval_type": approval.get("approval_type"), "status": approval.get("status"), "result": "成功" if output.get("ok") else "失败", "reason": output.get("message", ""), "idempotency_key": approval.get("idempotency_key")})
        return {"ok": bool(output.get("ok")), "message": output.get("message"), "approval": approval, "execution_result": output}
    except Exception as exc:
        approval["status"] = "failed"
        approval["execution_result"] = {"ok": False, "message": f"审批执行失败：{exc}", "execution_id": execution_id}
        _save_approval(approval)
        return {"ok": False, "message": f"审批执行失败：{exc}", "approval": approval}


def get_approval_stats() -> dict[str, Any]:
    queue = load_approval_queue()
    total = len(queue)
    counts: dict[str, int] = {}
    for row in queue:
        counts[str(row.get("status", "unknown"))] = counts.get(str(row.get("status", "unknown")), 0) + 1
    approved = counts.get("approved", 0) + counts.get("executed", 0)
    rejected = counts.get("rejected", 0)
    expired = counts.get("expired", 0)
    executed = counts.get("executed", 0)
    failed = counts.get("failed", 0)
    return {
        "total": total,
        "pending": counts.get("pending", 0),
        "approved": approved,
        "rejected": rejected,
        "modified": counts.get("modified", 0),
        "expired": expired,
        "executed": executed,
        "failed": failed,
        "approval_accept_rate": approved / total * 100 if total else 0,
        "approval_reject_rate": rejected / total * 100 if total else 0,
        "approval_expire_rate": expired / total * 100 if total else 0,
        "approval_execution_success_rate": executed / max(executed + failed, 1) * 100,
    }


def get_approval_review_summary() -> dict[str, Any]:
    stats = get_approval_stats()
    return {
        **stats,
        "summary": "审批流复盘统计已记录审批通过、拒绝、过期和执行结果。样本不足时不得进入全自动。",
        "sample_warning": "审批样本不足，暂不建议进入自动实盘。" if stats.get("total", 0) < 30 else "审批样本达到初步观察门槛。",
    }

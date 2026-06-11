"""小资金自动实盘试运行安全版。

LIVE_AUTO_PILOT 是小资金全自动试运行层。默认关闭，强白名单、极小额度、
冷却、熔断和审计；真实 Spot / U本位合约订单必须经过对应 Test Order 与实盘安全链路。
"""

from __future__ import annotations

import csv
import json
import time
import uuid
from pathlib import Path
from typing import Any

from services import market_cache
from services.fast_opportunity_engine import collect_top10_opportunities, run_committee_top10_precheck
from services.live_trading_center import (
    LIVE_TRADING_ENABLED,
    create_live_order_plan,
    get_live_safety_status,
    load_live_order_records,
    run_futures_test_order,
    run_spot_test_order,
    submit_live_futures_order,
    submit_live_spot_order,
    validate_live_order_plan,
    validate_order_against_exchange_rules,
)
from services.sim_trade_engine import calculate_sim_performance_stats


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CONFIG_PATH = DATA_DIR / "live_auto_config.json"
AUDIT_JSON_PATH = DATA_DIR / "live_auto_audit_log.json"
AUDIT_CSV_PATH = DATA_DIR / "live_auto_audit_log.csv"
PLAN_PATH = DATA_DIR / "live_auto_order_plans.json"
POSITION_PATH = DATA_DIR / "live_auto_positions.json"

LIVE_AUTO_MODE = "LIVE_AUTO_PILOT"
_LAST_AUTO_CYCLE_AT = 0.0
_AUTO_CYCLE_SECONDS = 3.0

DEFAULT_CONFIG = {
    "mode": "OFF",
    "live_auto_pilot_enabled": False,
    "live_auto_order_enabled": False,
    "live_auto_exit_enabled": False,
    "paused": False,
    "circuit_breaker_enabled": False,
    "circuit_breaker_reason": "",
    "principal_usdt": 100.0,
    "position_pct": 5.0,
    "max_order_usdt": 5.0,
    "daily_limit_usdt": 20.0,
    "max_positions": 1,
    "allowed_symbols": ["BTCUSDT", "ETHUSDT"],
    "allow_market_order": False,
    "allow_spot": True,
    "allow_futures": True,
    "default_market_type": "futures",
    "default_leverage": 5,
    "max_leverage": 20,
    "global_cooldown_minutes": 15,
    "symbol_cooldown_minutes": 60,
    "loss_cooldown_minutes": 120,
    "last_order_time": "",
    "symbol_last_order_time": {},
    "take_profit_pct": 2.13,
    "stop_loss_pct": -1.07,
    "restart_requires_reconfirm": True,
    "last_confirm_time": "",
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


def load_live_auto_config() -> dict[str, Any]:
    raw = _read_json(CONFIG_PATH, DEFAULT_CONFIG.copy())
    config = DEFAULT_CONFIG.copy()
    if isinstance(raw, dict):
        config.update(raw)
    return config


def save_live_auto_config(config: dict[str, Any]) -> dict[str, Any]:
    merged = DEFAULT_CONFIG.copy()
    merged.update(config or {})
    merged["principal_usdt"] = min(max(_to_float(merged.get("principal_usdt"), 100), 1), 10_000_000)
    merged["position_pct"] = min(max(_to_float(merged.get("position_pct"), 5), 0.1), 40)
    merged["max_order_usdt"] = max(1.0, merged["principal_usdt"] * merged["position_pct"] / 100)
    merged["daily_limit_usdt"] = min(max(_to_float(merged.get("daily_limit_usdt"), merged["max_order_usdt"]), merged["max_order_usdt"]), merged["principal_usdt"])
    merged["max_positions"] = int(min(max(_to_float(merged.get("max_positions"), 1), 1), 5))
    merged["allowed_symbols"] = [str(x).upper().strip() for x in merged.get("allowed_symbols", []) if str(x).strip()]
    merged["allow_spot"] = bool(merged.get("allow_spot", True))
    merged["allow_futures"] = bool(merged.get("allow_futures", True))
    if not merged["allow_spot"] and not merged["allow_futures"]:
        merged["allow_spot"] = True
    requested_market = str(merged.get("default_market_type", "futures"))
    if requested_market == "futures" and merged["allow_futures"]:
        merged["default_market_type"] = "futures"
    elif merged["allow_spot"]:
        merged["default_market_type"] = "spot"
    else:
        merged["default_market_type"] = "futures"
    merged["default_leverage"] = int(min(max(_to_float(merged.get("default_leverage"), 5), 1), _to_float(merged.get("max_leverage"), 20)))
    merged["max_leverage"] = int(min(max(_to_float(merged.get("max_leverage"), 20), 1), 125))
    merged["take_profit_pct"] = min(max(_to_float(merged.get("take_profit_pct"), 2.13), 0.1), 20)
    merged["stop_loss_pct"] = -min(max(abs(_to_float(merged.get("stop_loss_pct"), -1.07)), 0.1), 20)
    _write_json(CONFIG_PATH, merged)
    log_live_auto_event({"event": "用户配置修改", "result": "已保存", "reason": "用户保存自动实盘试运行配置。"})
    return merged


def log_live_auto_event(event: dict[str, Any] | str) -> None:
    if isinstance(event, str):
        row = {"time": _now(), "event": event, "symbol": "", "result": "", "reason": "", "risk_level": ""}
    else:
        row = {
            "time": _now(),
            "event": str(event.get("event", "自动实盘事件")),
            "symbol": str(event.get("symbol", "")),
            "result": str(event.get("result", "")),
            "reason": str(event.get("reason", "")),
            "risk_level": str(event.get("risk_level", "")),
            "idempotency_key": str(event.get("idempotency_key", "")),
        }
    logs = _read_json(AUDIT_JSON_PATH, [])
    logs.insert(0, row)
    _write_json(AUDIT_JSON_PATH, logs[:1000])
    try:
        with AUDIT_CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
            writer.writeheader()
            writer.writerows(logs[:1000])
    except Exception:
        pass


def load_live_auto_audit_log(limit: int = 100) -> list[dict[str, Any]]:
    data = _read_json(AUDIT_JSON_PATH, [])
    return (data if isinstance(data, list) else [])[:limit]


def _daily_auto_notional() -> float:
    today = time.strftime("%Y-%m-%d")
    total = 0.0
    for row in load_live_auto_audit_log(1000):
        if str(row.get("time", "")).startswith(today) and row.get("event") == "自动真实下单" and row.get("result") == "成功":
            total += _to_float(row.get("notional"))
    for row in load_live_order_records(500):
        if str(row.get("time", "")).startswith(today) and str(row.get("source")) == "LIVE_AUTO_PILOT":
            total += _to_float(row.get("notional"))
    return total


def load_live_auto_positions(limit: int = 100) -> list[dict[str, Any]]:
    data = _read_json(POSITION_PATH, [])
    return (data if isinstance(data, list) else [])[:limit]


def _save_auto_position(position: dict[str, Any]) -> None:
    rows = load_live_auto_positions(1000)
    idx = next((i for i, row in enumerate(rows) if row.get("auto_position_id") == position.get("auto_position_id")), None)
    if idx is None:
        rows.insert(0, position)
    else:
        rows[idx] = {**rows[idx], **position}
    _write_json(POSITION_PATH, rows[:1000])


def enable_live_auto_pilot(user_confirm: str) -> dict[str, Any]:
    required = "我确认开启小资金自动实盘试运行"
    if str(user_confirm or "").strip() != required:
        return {"ok": False, "message": f"确认短句不匹配，请输入：{required}"}
    admission = run_live_auto_admission_check(user_confirmed=True)
    config = load_live_auto_config()
    if not admission.get("ok"):
        config["live_auto_pilot_enabled"] = False
        config["live_auto_order_enabled"] = False
        save_live_auto_config(config)
        return {"ok": False, "message": admission.get("message"), "admission": admission}
    config["mode"] = LIVE_AUTO_MODE
    config["live_auto_pilot_enabled"] = True
    config["live_auto_order_enabled"] = True
    config["paused"] = False
    config["last_confirm_time"] = _now()
    save_live_auto_config(config)
    log_live_auto_event({"event": "自动实盘开启", "result": "已开启", "reason": "用户确认开启小资金自动实盘试运行。", "risk_level": "高"})
    return {"ok": True, "message": "小资金自动实盘试运行已开启。", "admission": admission}


def disable_live_auto_pilot(reason: str) -> dict[str, Any]:
    config = load_live_auto_config()
    config["mode"] = "OFF"
    config["live_auto_pilot_enabled"] = False
    config["live_auto_order_enabled"] = False
    config["live_auto_exit_enabled"] = False
    save_live_auto_config(config)
    log_live_auto_event({"event": "自动实盘关闭", "result": "已关闭", "reason": reason or "用户关闭。"})
    return {"ok": True, "message": "自动实盘试运行已关闭。"}


def pause_live_auto_pilot(reason: str) -> dict[str, Any]:
    config = load_live_auto_config()
    config["paused"] = True
    config["live_auto_order_enabled"] = False
    save_live_auto_config(config)
    log_live_auto_event({"event": "自动实盘暂停", "result": "已暂停", "reason": reason or "用户暂停。"})
    return {"ok": True, "message": "自动实盘试运行已暂停。"}


def resume_live_auto_pilot(user_confirm: str) -> dict[str, Any]:
    required = "我确认开启小资金自动实盘试运行"
    if str(user_confirm or "").strip() != required:
        return {"ok": False, "message": f"确认短句不匹配，请输入：{required}"}
    config = load_live_auto_config()
    config["paused"] = False
    config["mode"] = LIVE_AUTO_MODE
    config["live_auto_pilot_enabled"] = True
    config["live_auto_order_enabled"] = True
    save_live_auto_config(config)
    log_live_auto_event({"event": "自动实盘恢复", "result": "已恢复", "reason": "用户确认恢复。"})
    return {"ok": True, "message": "自动实盘试运行已恢复。"}


def trigger_live_auto_circuit_breaker(reason: str) -> dict[str, Any]:
    config = load_live_auto_config()
    config["circuit_breaker_enabled"] = True
    config["circuit_breaker_reason"] = reason or "自动实盘熔断触发。"
    config["live_auto_order_enabled"] = False
    config["live_auto_exit_enabled"] = False
    save_live_auto_config(config)
    log_live_auto_event({"event": "熔断触发", "result": "已熔断", "reason": config["circuit_breaker_reason"], "risk_level": "高"})
    return {"ok": True, "message": "自动实盘熔断已触发。"}


def release_live_auto_circuit_breaker(user_confirm: str) -> dict[str, Any]:
    required = "我确认解除自动实盘熔断"
    if str(user_confirm or "").strip() != required:
        return {"ok": False, "message": f"确认短句不匹配，请输入：{required}"}
    config = load_live_auto_config()
    config["circuit_breaker_enabled"] = False
    config["circuit_breaker_reason"] = ""
    save_live_auto_config(config)
    log_live_auto_event({"event": "熔断解除", "result": "已解除", "reason": "用户确认解除自动实盘熔断。"})
    return {"ok": True, "message": "自动实盘熔断已解除。"}


def run_live_auto_admission_check(user_confirmed: bool = False) -> dict[str, Any]:
    config = load_live_auto_config()
    live_status = get_live_safety_status()
    sim_stats = calculate_sim_performance_stats()
    checks = [
        {"name": "用户明确开启", "ok": bool(user_confirmed), "message": "需要用户点击自动交易开关。"},
        {"name": "LIVE_TRADING_ENABLED", "ok": bool(LIVE_TRADING_ENABLED), "message": "LIVE_TRADING_ENABLED=false，禁止自动真实下单。"},
        {"name": "实盘安全中心正常", "ok": not (live_status.get("settings") or {}).get("kill_switch_enabled"), "message": "实盘安全锁未开启。"},
        {"name": "API权限安全", "ok": bool((live_status.get("permission") or {}).get("can_trade")) and not bool((live_status.get("permission") or {}).get("can_withdraw")), "message": "API需可交易且提现权限关闭。"},
        {"name": "自动模拟样本参考", "ok": True, "message": f"自动模拟样本 {sim_stats.get('total_trades', 0)} 笔，仅作为风控参考，不再阻断自动交易。"},
        {"name": "模拟 Profit Factor参考", "ok": True, "message": f"Profit Factor {sim_stats.get('profit_factor', 0)}，仅作为风控参考。"},
        {"name": "白名单存在", "ok": bool(config.get("allowed_symbols")), "message": "至少需要一个自动实盘白名单交易对。"},
    ]
    failed = [row for row in checks if not row["ok"]]
    return {"ok": not failed, "checks": checks, "message": "自动实盘准入通过。" if not failed else "自动实盘准入失败：样本数量不足或安全条件未满足。"}


def check_live_auto_cooldown(symbol: str | None = None) -> dict[str, Any]:
    config = load_live_auto_config()
    now = _now_ts()
    last_global = _parse_time(str(config.get("last_order_time", "")))
    if last_global and now - last_global < _to_float(config.get("global_cooldown_minutes"), 15) * 60:
        return {"ok": False, "message": "全局自动交易冷却中。"}
    if symbol:
        last_symbol = _parse_time(str((config.get("symbol_last_order_time") or {}).get(str(symbol).upper(), "")))
        if last_symbol and now - last_symbol < _to_float(config.get("symbol_cooldown_minutes"), 60) * 60:
            return {"ok": False, "message": f"{symbol} 自动交易冷却中。"}
    return {"ok": True, "message": "冷却检查通过。"}


def filter_live_auto_signal(signal: dict[str, Any]) -> tuple[bool, list[str]]:
    config = load_live_auto_config()
    reasons: list[str] = []
    symbol = str(signal.get("symbol", "")).upper()
    action = str(signal.get("action") or signal.get("final_action") or "")
    direction = str(signal.get("direction", "")).lower()
    spot_enabled = bool(config.get("allow_spot", True))
    futures_enabled = bool(config.get("allow_futures"))
    external_ai = signal.get("external_ai") or signal.get("external_ai_snapshot") or {}
    if symbol not in config.get("allowed_symbols", []):
        reasons.append("非自动实盘白名单交易对。")
    allowed_actions = {"轻仓试多", "顺势做多", "轻仓试空", "顺势做空"} if futures_enabled else {"轻仓试多", "顺势做多"}
    if action not in allowed_actions:
        reasons.append("自动实盘信号方向不符合当前市场类型设置。")
    if direction == "long" and not (spot_enabled or futures_enabled):
        reasons.append("未开启现货或合约自动交易市场。")
    if direction == "short" and not futures_enabled:
        reasons.append("Spot 自动实盘不允许做空，需开启 U本位合约。")
    if _to_float(signal.get("committee_confidence"), _to_float(signal.get("confidence"))) < 70:
        reasons.append("委员会置信度不足 70。")
    if _to_float(signal.get("risk_score"), 100) > 55:
        reasons.append("综合风险评分超过 55。")
    if str(signal.get("data_quality", "good")) != "good":
        reasons.append("数据质量不是 good。")
    if signal.get("veto") or signal.get("risk_veto") or signal.get("veto_members"):
        reasons.append("风险委员或硬否决已触发。")
    if (external_ai.get("deepseek") or {}).get("soft_veto") or (external_ai.get("gemini") or {}).get("soft_veto"):
        reasons.append("DeepSeek/Gemini 存在软否决。")
    if not check_live_auto_cooldown(symbol).get("ok"):
        reasons.append(check_live_auto_cooldown(symbol).get("message", "冷却中。"))
    if len([p for p in load_live_auto_positions() if p.get("status") == "open"]) >= int(config.get("max_positions", 1)):
        reasons.append("当前自动真实持仓数量已达上限。")
    return not reasons, reasons


def _live_auto_price(row: dict[str, Any]) -> float:
    symbol = str(row.get("symbol") or "").upper()
    ticker = market_cache.get_ticker(symbol) or {}
    return (
        _to_float(ticker.get("last_price"), 0)
        or _to_float(row.get("current_price"), 0)
        or _to_float(row.get("last_price"), 0)
        or _to_float(row.get("price"), 0)
    )


def _live_auto_signal_from_precheck(precheck: dict[str, Any]) -> dict[str, Any] | None:
    row = precheck.get("opportunity") or {}
    symbol = str(precheck.get("symbol") or row.get("symbol") or "").upper()
    price = _live_auto_price(row)
    score = _to_float(row.get("final_opportunity_score"), _to_float(row.get("opportunity_score"), _to_float(precheck.get("score"), 0)))
    risk = _to_float(row.get("risk_score"), _to_float(precheck.get("risk_score"), 100))
    if not symbol or price <= 0 or score < 80 or risk > 55 or not precheck.get("allowed_candidate"):
        return None
    direction = str(precheck.get("direction") or row.get("direction") or "long").lower()
    action = "顺势做多" if direction == "long" and score >= 88 else "轻仓试多" if direction == "long" else "顺势做空" if score >= 88 else "轻仓试空"
    return {
        "symbol": symbol,
        "direction": direction,
        "action": action,
        "final_action": action,
        "trade_permission": "candidate",
        "approved_for_simulation": True,
        "committee_confidence": max(70, min(95, score)),
        "confidence": max(70, min(95, score)),
        "risk_score": risk,
        "current_price": price,
        "entry_price": price,
        "data_quality": row.get("data_quality") or "good",
        "veto_members": [],
        "opportunity_id": row.get("opportunity_id") or precheck.get("opportunity_id"),
        "source_board_rank": int(precheck.get("rank", 0) or 0),
        "source_opportunity_id": row.get("opportunity_id") or precheck.get("opportunity_id"),
        "source_committee_result": precheck.get("fast_action") or "进入候选",
        "source_resonance_level": "中等共振",
        "source_review_time": _now(),
        "external_ai": row.get("external_ai") or {"deepseek": {}, "gemini": {}},
        "risk_snapshot": row.get("risk_breakdown") or {},
        "local_strategy_snapshot": row.get("opportunity_breakdown") or {},
    }


def run_live_auto_trading_cycle(rankings: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Run one automatic live-trading cycle from the opportunity board.

    The cycle is inert unless the user has enabled LIVE_AUTO_PILOT and automatic
    order execution. Each execution still passes API, safety, rule and test-order
    checks before a real Spot/Futures order can be submitted.
    """
    global _LAST_AUTO_CYCLE_AT
    now = time.time()
    if now - _LAST_AUTO_CYCLE_AT < _AUTO_CYCLE_SECONDS:
        return {"ok": True, "skipped": True, "reason": "自动交易刷新冷却中。"}
    _LAST_AUTO_CYCLE_AT = now

    config = load_live_auto_config()
    symbols = {str(pos.get("symbol") or "").upper() for pos in load_live_auto_positions() if pos.get("symbol")}
    prices = {symbol: _live_auto_price({"symbol": symbol}) for symbol in symbols}
    monitor_live_auto_positions(prices)

    if not config.get("live_auto_pilot_enabled") or not config.get("live_auto_order_enabled"):
        return {"ok": True, "skipped": True, "reason": "自动交易未开启。"}
    if config.get("paused") or config.get("circuit_breaker_enabled"):
        return {"ok": True, "skipped": True, "reason": "自动交易暂停或熔断中。"}
    admission = run_live_auto_admission_check(user_confirmed=True)
    if not admission.get("ok"):
        return {"ok": False, "skipped": True, "reason": admission.get("message"), "admission": admission}

    opportunities = collect_top10_opportunities(rankings, limit=10)
    if not opportunities:
        return {"ok": True, "signals": 0, "reason": "机会榜暂无候选。"}
    prechecks = run_committee_top10_precheck(rankings, limit=10)
    for precheck in prechecks:
        signal = _live_auto_signal_from_precheck(precheck)
        if not signal:
            continue
        allowed, reasons = filter_live_auto_signal(signal)
        if not allowed:
            continue
        plan = create_live_auto_order_plan(signal)
        result = execute_live_auto_spot_order(plan)
        return {"ok": bool(result.get("ok")), "signals": 1, "plan": plan, "result": result}
    return {"ok": True, "signals": 0, "reason": "本轮无符合自动实盘风控的候选。"}


def create_live_auto_order_plan(signal: dict[str, Any]) -> dict[str, Any]:
    config = load_live_auto_config()
    price = _to_float(signal.get("current_price"), _to_float(signal.get("entry_price"), _to_float(signal.get("planned_entry_price"))))
    amount = min(_to_float(signal.get("quote_amount"), _to_float(config.get("max_order_usdt"), 5)), _to_float(config.get("max_order_usdt"), 5))
    direction = str(signal.get("direction", "long")).lower()
    market_type = "futures" if bool(config.get("allow_futures")) and (str(config.get("default_market_type")) == "futures" or direction == "short") else "spot"
    if market_type == "spot" and not bool(config.get("allow_spot", True)) and bool(config.get("allow_futures")):
        market_type = "futures"
    leverage = int(config.get("default_leverage", 5) or 5) if market_type == "futures" else 1
    notional = amount * leverage if market_type == "futures" else amount
    plan = {
        "auto_plan_id": f"auto_live_{uuid.uuid4().hex[:12]}",
        "symbol": str(signal.get("symbol", "BTCUSDT")).upper(),
        "market_type": market_type,
        "side": "SELL" if market_type == "futures" and direction == "short" else "BUY",
        "order_type": "LIMIT",
        "price": price,
        "quantity": notional / price if price else 0,
        "quote_amount": amount,
        "margin_usdt": amount,
        "notional": notional,
        "leverage": leverage,
        "source": "LIVE_AUTO_PILOT",
        "committee_snapshot": signal,
        "local_strategy_snapshot": signal.get("local_strategy_snapshot") or {},
        "risk_snapshot": signal.get("risk_snapshot") or {},
        "deepseek_snapshot": (signal.get("external_ai") or {}).get("deepseek") or {},
        "gemini_snapshot": (signal.get("external_ai") or {}).get("gemini") or {},
        "preflight_result": {},
        "created_time": _now(),
        "status": "planned",
        "idempotency_key": f"auto_idem_{uuid.uuid4().hex[:16]}",
    }
    rows = _read_json(PLAN_PATH, [])
    rows.insert(0, plan)
    _write_json(PLAN_PATH, rows[:500])
    log_live_auto_event({"event": "自动订单计划生成", "symbol": plan["symbol"], "result": "已生成", "reason": f"{market_type} 计划保证金 {amount:.2f} USDT，名义 {notional:.2f} USDT，杠杆 {leverage}x。", "idempotency_key": plan["idempotency_key"]})
    return plan


def run_live_auto_preflight(order_plan: dict[str, Any]) -> dict[str, Any]:
    config = load_live_auto_config()
    market_type = str(order_plan.get("market_type", "spot"))
    if market_type == "futures":
        validation = validate_order_against_exchange_rules({**order_plan, "market_type": "futures", "source": "LIVE_AUTO_PILOT"})
        test_result = run_futures_test_order({**order_plan, "source": "LIVE_AUTO_PILOT"}) if validation.get("ok") else {"ok": False, "message": "规则校验未通过，未执行 Futures Test Order。"}
    else:
        validation = validate_live_order_plan({**order_plan, "source": "LIVE_AUTO_PILOT"})
        test_result = run_spot_test_order({**order_plan, "source": "LIVE_AUTO_PILOT"}) if validation.get("ok") else {"ok": False, "message": "规则校验未通过，未执行 Spot Test Order。"}
    checks = [
        {"name": "自动试运行开启", "ok": bool(config.get("live_auto_pilot_enabled")), "message": "LIVE_AUTO_PILOT_ENABLED 必须为 true。"},
        {"name": "自动下单开启", "ok": bool(config.get("live_auto_order_enabled")), "message": "LIVE_AUTO_ORDER_ENABLED 必须为 true。"},
        {"name": "当前模式", "ok": config.get("mode") == LIVE_AUTO_MODE, "message": "当前模式必须为 LIVE_AUTO_PILOT。"},
        {"name": "未暂停", "ok": not config.get("paused"), "message": "自动实盘已暂停。"},
        {"name": "未熔断", "ok": not config.get("circuit_breaker_enabled"), "message": config.get("circuit_breaker_reason") or "自动熔断已开启。"},
        {"name": "白名单", "ok": str(order_plan.get("symbol")) in config.get("allowed_symbols", []), "message": "交易对必须在白名单。"},
        {"name": "市场类型", "ok": (market_type == "spot" and bool(config.get("allow_spot", True))) or (market_type == "futures" and bool(config.get("allow_futures"))), "message": "当前配置未允许该市场自动交易。"},
        {"name": "合约杠杆", "ok": market_type != "futures" or 1 <= int(order_plan.get("leverage", 5) or 5) <= int(config.get("max_leverage", 20) or 20), "message": "合约杠杆超过配置上限。"},
        {"name": "单笔额度", "ok": _to_float(order_plan.get("quote_amount")) <= _to_float(config.get("max_order_usdt"), 5), "message": "单笔额度超过限制。"},
        {"name": "单日额度", "ok": _daily_auto_notional() + _to_float(order_plan.get("quote_amount")) <= _to_float(config.get("daily_limit_usdt"), 20), "message": "单日自动实盘额度将超限。"},
        {"name": "交易所规则", "ok": validation.get("ok"), "message": "；".join(validation.get("errors") or []) or "规则通过。"},
        {"name": "Test Order", "ok": test_result.get("ok"), "message": test_result.get("message", "")},
    ]
    ok = all(row["ok"] for row in checks)
    result = {"ok": ok, "checks": checks, "validation": validation, "test_order_result": test_result, "message": "自动实盘执行前检查通过。" if ok else "自动实盘执行前检查失败。"}
    log_live_auto_event({"event": "自动订单执行前检查", "symbol": order_plan.get("symbol"), "result": "通过" if ok else "失败", "reason": result["message"], "idempotency_key": order_plan.get("idempotency_key")})
    return result


def execute_live_auto_spot_order(order_plan: dict[str, Any]) -> dict[str, Any]:
    preflight = run_live_auto_preflight(order_plan)
    if not preflight.get("ok"):
        log_live_auto_event({"event": "自动订单被拒绝", "symbol": order_plan.get("symbol"), "result": "失败", "reason": preflight.get("message"), "risk_level": "高", "idempotency_key": order_plan.get("idempotency_key")})
        return {"ok": False, "message": preflight.get("message"), "preflight": preflight, "converted_to_approval": False}
    if str(order_plan.get("market_type", "spot")) == "futures":
        result = submit_live_futures_order({**order_plan, "source": "LIVE_AUTO_PILOT"}, preflight.get("test_order_result") or {}, True, "我确认执行小资金实盘订单")
    else:
        result = submit_live_spot_order({**order_plan, "source": "LIVE_AUTO_PILOT"}, preflight.get("test_order_result") or {}, True, "我确认执行小资金实盘订单")
    config = load_live_auto_config()
    if result.get("ok"):
        config["last_order_time"] = _now()
        symbols = dict(config.get("symbol_last_order_time") or {})
        symbols[str(order_plan.get("symbol")).upper()] = _now()
        config["symbol_last_order_time"] = symbols
        save_live_auto_config(config)
        order = result.get("order") or {}
        position = {
            "auto_position_id": f"auto_pos_{uuid.uuid4().hex[:12]}",
            "symbol": order_plan.get("symbol"),
            "entry_order_id": order.get("order_id", ""),
            "entry_price": order_plan.get("price"),
            "quantity": order_plan.get("quantity"),
            "quote_amount": order_plan.get("quote_amount"),
            "market_type": order_plan.get("market_type", "spot"),
            "leverage": order_plan.get("leverage", 1),
            "notional": order_plan.get("notional", order_plan.get("quote_amount")),
            "status": "open",
            "entry_time": _now(),
            "current_price": order_plan.get("price"),
            "unrealized_pnl": 0,
            "unrealized_pnl_pct": 0,
            "max_unrealized_pnl": 0,
            "max_drawdown_from_peak": 0,
            "exit_rule": {"take_profit_pct": config.get("take_profit_pct"), "stop_loss_pct": config.get("stop_loss_pct")},
            "risk_status": "normal",
            "last_review_time": _now(),
        }
        _save_auto_position(position)
        log_live_auto_event({"event": "自动真实下单", "symbol": order_plan.get("symbol"), "result": "成功", "reason": f"自动小资金 {order_plan.get('market_type', 'spot')} 订单已提交。", "idempotency_key": order_plan.get("idempotency_key")})
        log_live_auto_event({"event": "自动持仓创建", "symbol": order_plan.get("symbol"), "result": "已创建", "reason": position["auto_position_id"]})
    else:
        log_live_auto_event({"event": "自动订单失败", "symbol": order_plan.get("symbol"), "result": "失败", "reason": result.get("message", ""), "risk_level": "高", "idempotency_key": order_plan.get("idempotency_key")})
    return result


def monitor_live_auto_positions(current_prices: dict[str, float] | None = None) -> list[dict[str, Any]]:
    prices = current_prices or {}
    rows = load_live_auto_positions(1000)
    changed = False
    for pos in rows:
        if pos.get("status") != "open":
            continue
        price = _to_float(prices.get(str(pos.get("symbol", "")).upper()), _to_float(pos.get("current_price"), _to_float(pos.get("entry_price"))))
        entry = _to_float(pos.get("entry_price"))
        qty = _to_float(pos.get("quantity"))
        pnl = (price - entry) * qty if entry and qty else 0
        pct = pnl / (_to_float(pos.get("quote_amount")) or 1) * 100
        pos["current_price"] = price
        pos["unrealized_pnl"] = pnl
        pos["unrealized_pnl_pct"] = pct
        pos["max_unrealized_pnl"] = max(_to_float(pos.get("max_unrealized_pnl")), pnl)
        pos["last_review_time"] = _now()
        changed = True
    if changed:
        _write_json(POSITION_PATH, rows[:1000])
    return rows


def run_live_auto_exit_check(position: dict[str, Any]) -> dict[str, Any]:
    config = load_live_auto_config()
    pct = _to_float(position.get("unrealized_pnl_pct"))
    if not config.get("live_auto_exit_enabled"):
        return {"ok": False, "action": "alert_only", "message": "自动止盈/止损关闭，仅提醒不卖出。"}
    if pct <= _to_float(config.get("stop_loss_pct"), -1):
        return {"ok": True, "action": "stop_loss", "message": "触发自动止损试运行条件。"}
    if pct >= _to_float(config.get("take_profit_pct"), 1.5):
        return {"ok": True, "action": "take_profit", "message": "触发自动止盈试运行条件。"}
    return {"ok": False, "action": "hold", "message": "未触发自动退出条件。"}


def execute_live_auto_exit(position: dict[str, Any], reason: str) -> dict[str, Any]:
    log_live_auto_event({"event": "自动止盈/止损触发", "symbol": position.get("symbol"), "result": "提醒", "reason": reason or "本版本保守处理：生成提醒，自动卖出执行预留。"})
    return {"ok": False, "message": "自动退出执行已保守预留：本轮只记录提醒，不自动卖出。"}


def get_live_auto_review_summary() -> dict[str, Any]:
    logs = load_live_auto_audit_log(1000)
    orders = [row for row in logs if row.get("event") == "自动真实下单"]
    failures = [row for row in logs if row.get("event") in {"自动订单失败", "自动订单被拒绝"}]
    circuit = [row for row in logs if row.get("event") == "熔断触发"]
    return {
        "auto_order_count": len(orders),
        "auto_success_count": len([row for row in orders if row.get("result") == "成功"]),
        "auto_failure_count": len(failures),
        "circuit_breaker_count": len(circuit),
        "sample_warning": "自动实盘样本不足，暂不建议扩大额度或进入正式自动交易。" if len(orders) < 30 else "样本达到初步观察门槛，仍需谨慎。",
    }


def get_live_auto_status(current_prices: dict[str, float] | None = None) -> dict[str, Any]:
    config = load_live_auto_config()
    positions = monitor_live_auto_positions(current_prices)
    open_positions = [row for row in positions if row.get("status") == "open"]
    admission = run_live_auto_admission_check(user_confirmed=False)
    return {
        "config": config,
        "enabled": bool(config.get("live_auto_pilot_enabled")),
        "order_enabled": bool(config.get("live_auto_order_enabled")),
        "exit_enabled": bool(config.get("live_auto_exit_enabled")),
        "paused": bool(config.get("paused")),
        "circuit_breaker_enabled": bool(config.get("circuit_breaker_enabled")),
        "circuit_breaker_reason": config.get("circuit_breaker_reason", ""),
        "daily_used_usdt": _daily_auto_notional(),
        "open_positions": open_positions,
        "audit": load_live_auto_audit_log(50),
        "review": get_live_auto_review_summary(),
        "admission": admission,
        "mode_name": "小资金自动实盘试运行",
    }

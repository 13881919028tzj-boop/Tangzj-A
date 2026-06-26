"""Live grid trading bridge.

This module exposes a guarded bridge from local grid bots to Binance live-order
planning. It deliberately does not submit real orders automatically.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

from services.grid_trade_engine import load_grid_bots
from services.live_trading_center import (
    LIVE_TRADING_ENABLED,
    _binance_order_params,
    _signed_request,
    check_api_connection,
    check_api_key_restrictions,
    check_api_permissions,
    create_live_order_plan,
    create_live_order_preview,
    fetch_live_order_status,
    load_api_credentials_safely,
    load_exchange_rules,
    load_live_settings,
    load_live_order_records,
    log_live_audit_event,
    require_confirmation_phrase,
    run_futures_test_order,
    run_spot_test_order,
    save_live_order_record,
    submit_live_futures_order,
    validate_live_order_plan,
)


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CONFIG_PATH = DATA_DIR / "live_grid_settings.json"
AUDIT_PATH = DATA_DIR / "live_grid_audit_log.json"
RUNTIME_PATH = DATA_DIR / "live_grid_runtime_state.json"

DEFAULT_CONFIG = {
    "live_grid_interface_enabled": False,
    "mode": "review_only",
    "allow_reading": True,
    "allow_spot_long_grid": True,
    "allow_futures_grid": False,
    "allow_margin": False,
    "max_initial_orders": 2,
    "max_order_usdt": 5.0,
    "futures_leverage": 3,
    "max_futures_leverage": 20,
    "require_ip_restrict": True,
    "allow_test_orders": True,
    "allow_real_order_submit": False,
    "auto_replenish_enabled": True,
}

_LAST_RUNTIME_CYCLE_AT = 0.0
_RUNTIME_CYCLE_SECONDS = 15.0


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _floor_to_step(value: float, step: float) -> float:
    if step <= 0 or value <= 0:
        return value
    try:
        decimal_value = Decimal(str(value))
        decimal_step = Decimal(str(step))
        return float((decimal_value / decimal_step).to_integral_value(rounding=ROUND_DOWN) * decimal_step)
    except Exception:
        return value


def _ceil_to_step(value: float, step: float) -> float:
    floored = _floor_to_step(value, step)
    if step <= 0 or floored >= value:
        return floored
    return _floor_to_step(floored + step, step)


def _exchange_min_quote(symbol: str, market_type: str) -> float:
    rule = load_exchange_rules(symbol, market_type, False)
    return max(1.0, _to_float(rule.get("minNotional"), 1.0))


def _exchange_rule_blocker(symbol: str, market_type: str) -> str:
    rule = load_exchange_rules(symbol, market_type, False)
    if not rule.get("ok"):
        return str(rule.get("message") or "交易所规则读取失败，系统已阻止生成真实订单计划。")
    if rule.get("status") not in {"TRADING", "TRADING_ALLOWED", None}:
        return "交易对象当前状态不可交易，系统已阻止生成真实订单计划。"
    tick = _to_float(rule.get("tickSize"))
    step = _to_float(rule.get("stepSize"))
    min_qty = _to_float(rule.get("minQty"))
    min_notional = _to_float(rule.get("minNotional"))
    if tick <= 0 or step <= 0 or min_qty <= 0 or min_notional <= 0:
        return "交易所价格精度、数量精度或最小金额规则不完整，系统已阻止生成真实订单计划。"
    return ""


def _align_plan_to_exchange_rules(plan: dict[str, Any]) -> dict[str, Any]:
    symbol = str(plan.get("symbol") or "").upper()
    market_type = str(plan.get("market_type") or "spot")
    rule = load_exchange_rules(symbol, market_type, False)
    if not rule.get("ok"):
        aligned = dict(plan)
        aligned["exchange_rule_aligned"] = False
        aligned["exchange_rule_error"] = str(rule.get("message") or "交易所规则读取失败。")
        return aligned
    tick_size = _to_float(rule.get("tickSize"))
    step_size = _to_float(rule.get("stepSize"))
    price = _floor_to_step(_to_float(plan.get("price")), tick_size)
    min_price = _to_float(rule.get("minPrice"))
    max_price = _to_float(rule.get("maxPrice"))
    if min_price > 0 and price < min_price:
        price = _ceil_to_step(min_price, tick_size)
    if max_price > 0 and price > max_price:
        aligned = dict(plan)
        aligned["exchange_rule_aligned"] = False
        aligned["exchange_rule_error"] = "计划价格高于交易所最大价格。"
        return aligned
    quote_amount = _to_float(plan.get("quote_amount"))
    leverage = max(_to_float(plan.get("leverage"), 1), 1)
    raw_qty = (quote_amount * leverage if market_type == "futures" else quote_amount) / price if price > 0 else 0.0
    qty = _floor_to_step(raw_qty, step_size)
    min_qty = _to_float(rule.get("minQty"))
    if min_qty > 0 and qty < min_qty:
        qty = _ceil_to_step(min_qty, step_size)
    max_qty = _to_float(rule.get("maxQty"))
    if max_qty > 0 and qty > max_qty:
        aligned = dict(plan)
        aligned["exchange_rule_aligned"] = False
        aligned["exchange_rule_error"] = "计划数量高于交易所最大数量。"
        return aligned
    notional = price * qty
    min_notional = _to_float(rule.get("minNotional"))
    if min_notional > 0 and notional < min_notional and price > 0:
        target_qty = (min_notional * (1.002 if market_type == "spot" else 1.0)) / price
        qty = _ceil_to_step(target_qty, step_size)
        if max_qty > 0 and qty > max_qty:
            aligned = dict(plan)
            aligned["exchange_rule_aligned"] = False
            aligned["exchange_rule_error"] = "满足最小名义金额所需数量超过交易所最大数量。"
            return aligned
        notional = price * qty
    max_notional = _to_float(rule.get("maxNotional"))
    if max_notional > 0 and notional > max_notional:
        aligned = dict(plan)
        aligned["exchange_rule_aligned"] = False
        aligned["exchange_rule_error"] = "订单金额高于交易所最大名义金额。"
        return aligned
    if qty <= 0:
        aligned = dict(plan)
        aligned["exchange_rule_aligned"] = False
        aligned["exchange_rule_error"] = "规则对齐后数量无效。"
        return aligned
    aligned = dict(plan)
    aligned["price"] = price
    aligned["quantity"] = qty
    aligned["notional"] = notional
    aligned["quote_amount"] = notional / leverage if market_type == "futures" else notional
    aligned["margin_usdt"] = aligned["quote_amount"]
    aligned["exchange_rule_aligned"] = True
    return aligned


def _strict_plan_preview(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("exchange_rule_aligned") is False:
        reason = str(plan.get("exchange_rule_error") or "交易所规则对齐失败。")
        return {"ok": False, "risk_errors": [reason], "rule_check": {"errors": [reason]}, "plan_check": {"errors": [reason]}}
    return create_live_order_preview(plan)


def _prepare_plan_for_submission(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    aligned = _align_plan_to_exchange_rules(plan)
    preview = _strict_plan_preview(aligned)
    return aligned, preview


def _read_json(path: Path, default: Any) -> Any:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            _write_json(path, default)
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_live_grid_settings() -> dict[str, Any]:
    raw = _read_json(CONFIG_PATH, DEFAULT_CONFIG.copy())
    settings = DEFAULT_CONFIG.copy()
    if isinstance(raw, dict):
        settings.update(raw)
    settings["max_initial_orders"] = max(1, min(int(_to_float(settings.get("max_initial_orders"), 2)), 5))
    settings["max_order_usdt"] = max(1.0, min(_to_float(settings.get("max_order_usdt"), 5.0), 50.0))
    settings["max_futures_leverage"] = max(1, min(int(_to_float(settings.get("max_futures_leverage"), 20)), 125))
    settings["futures_leverage"] = max(1, min(int(_to_float(settings.get("futures_leverage"), 3)), int(settings["max_futures_leverage"])))
    settings["allow_margin"] = False
    return settings


def save_live_grid_settings(settings: dict[str, Any]) -> dict[str, Any]:
    merged = load_live_grid_settings()
    for key in DEFAULT_CONFIG:
        if key in settings:
            merged[key] = settings[key]
    merged["allow_margin"] = False
    merged["max_futures_leverage"] = max(1, min(int(_to_float(merged.get("max_futures_leverage"), 20)), 125))
    merged["futures_leverage"] = max(1, min(int(_to_float(merged.get("futures_leverage"), 3)), int(merged["max_futures_leverage"])))
    merged["last_update"] = _now()
    _write_json(CONFIG_PATH, merged)
    append_live_grid_audit("配置修改", "", "已保存", "实盘网格接口配置已保存。")
    return merged


def append_live_grid_audit(event_type: str, symbol: str = "", result: str = "", reason: str = "") -> None:
    rows = _read_json(AUDIT_PATH, [])
    if not isinstance(rows, list):
        rows = []
    rows.insert(
        0,
        {
            "time": _now(),
            "event_type": event_type,
            "symbol": symbol,
            "result": result,
            "reason": reason,
            "real_account": True,
        },
    )
    _write_json(AUDIT_PATH, rows[:500])


def load_live_grid_audit(limit: int = 100) -> list[dict[str, Any]]:
    rows = _read_json(AUDIT_PATH, [])
    return (rows if isinstance(rows, list) else [])[:limit]


def _load_runtime_state() -> dict[str, Any]:
    raw = _read_json(RUNTIME_PATH, {"processed_fills": {}, "last_cycle_time": ""})
    return raw if isinstance(raw, dict) else {"processed_fills": {}, "last_cycle_time": ""}


def _save_runtime_state(state: dict[str, Any]) -> None:
    _write_json(RUNTIME_PATH, state)


def get_live_grid_status() -> dict[str, Any]:
    settings = load_live_grid_settings()
    live_settings = load_live_settings()
    connection = check_api_connection(False, "spot")
    permission = check_api_permissions(False, "spot")
    restrictions = check_api_key_restrictions(False)
    spot_allowed = bool(settings.get("allow_spot_long_grid")) and bool(restrictions.get("enableSpotAndMarginTrading"))
    futures_allowed = bool(settings.get("allow_futures_grid")) and bool(restrictions.get("enableFutures"))
    reading_allowed = bool(settings.get("allow_reading")) and bool(restrictions.get("enableReading"))
    any_market_allowed = bool(spot_allowed or futures_allowed)
    real_submit_enabled = (
        bool(settings.get("allow_real_order_submit"))
        and bool(LIVE_TRADING_ENABLED)
        and bool(reading_allowed)
        and any_market_allowed
        and bool(permission.get("can_trade"))
        and not bool(permission.get("can_withdraw"))
        and bool(restrictions.get("ok"))
        and not bool(restrictions.get("enableWithdrawals"))
        and (not bool(settings.get("require_ip_restrict")) or bool(restrictions.get("ipRestrict")))
    )
    ready = (
        bool(settings.get("live_grid_interface_enabled"))
        and bool(connection.get("ok"))
        and bool(reading_allowed)
        and any_market_allowed
        and bool(permission.get("can_trade"))
        and not bool(permission.get("can_withdraw"))
        and bool(restrictions.get("ok"))
        and not bool(restrictions.get("enableWithdrawals"))
        and (not bool(settings.get("require_ip_restrict")) or bool(restrictions.get("ipRestrict")))
    )
    blockers: list[str] = []
    if not settings.get("live_grid_interface_enabled"):
        blockers.append("实盘网格接口未开启。")
    if not connection.get("ok"):
        blockers.append(connection.get("message", "Binance连接失败。"))
    if not reading_allowed:
        blockers.append("读取权限未开启或本地读取开关关闭。")
    if not any_market_allowed:
        blockers.append("现货/合约网格开关均未开启，或 Binance 交易权限不可用。")
    if not permission.get("can_trade"):
        blockers.append("API没有现货交易权限。")
    if permission.get("can_withdraw") or restrictions.get("enableWithdrawals"):
        blockers.append("API提现权限未关闭。")
    if settings.get("require_ip_restrict") and restrictions.get("ok") and not restrictions.get("ipRestrict"):
        blockers.append("API未开启IP白名单。")
    if not LIVE_TRADING_ENABLED:
        blockers.append("全局 LIVE_TRADING_ENABLED=false，真实提交被阻止。")
    return {
        "ready_for_review": ready,
        "real_submit_enabled": real_submit_enabled,
        "settings": settings,
        "live_settings": live_settings,
        "connection": connection,
        "permission": permission,
        "restrictions": restrictions,
        "local_permission_switches": {
            "reading": bool(settings.get("allow_reading")),
            "spot": bool(settings.get("allow_spot_long_grid")),
            "futures": bool(settings.get("allow_futures_grid")),
            "margin": False,
            "test_orders": bool(settings.get("allow_test_orders")),
            "real_submit": bool(settings.get("allow_real_order_submit")),
        },
        "exchange_permission_switches": {
            "reading": bool(restrictions.get("enableReading")),
            "spot": bool(restrictions.get("enableSpotAndMarginTrading")),
            "futures": bool(restrictions.get("enableFutures")),
            "margin": bool(restrictions.get("enableMargin")),
            "withdrawals": bool(restrictions.get("enableWithdrawals")),
        },
        "blockers": blockers,
        "message": "实盘网格接口可用于生成和测试订单。" if ready else "实盘网格接口仍有阻断项。",
    }


def _find_bot(bot_id: str) -> dict[str, Any] | None:
    for bot in load_grid_bots():
        if str(bot.get("bot_id") or "") == str(bot_id or ""):
            return bot
    return None


def _make_plan(symbol: str, market_type: str, side: str, price: float, quote_amount: float, source: str, grid_ref: dict[str, Any]) -> dict[str, Any]:
    settings = load_live_grid_settings()
    leverage = int(settings.get("futures_leverage", 3)) if market_type == "futures" else 1
    inputs = {
        "symbol": symbol,
        "market_type": market_type,
        "leverage": leverage,
        "side": side,
        "order_type": "LIMIT",
        "price": price,
        "quote_amount": quote_amount,
        "source": source,
    }
    if _to_float(grid_ref.get("quantity")) > 0:
        inputs["quantity"] = _to_float(grid_ref.get("quantity"))
    plan = create_live_order_plan(
        None,
        inputs,
    )
    plan.update(grid_ref)
    plan["live_grid_review_only"] = True
    return _align_plan_to_exchange_rules(plan)


def _normalize_investment_mode(value: Any) -> str:
    mode = str(value or "fixed_equal").strip()
    return mode if mode in {"fixed_equal", "compound_reinvest"} else "fixed_equal"


def _orders_to_plans(bot: dict[str, Any], orders: list[dict[str, Any]], source: str, allocation: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    settings = load_live_grid_settings()
    live_settings = load_live_settings()
    symbol = str(bot.get("symbol") or "").upper()
    max_plans = int(settings.get("max_initial_orders", 2))
    live_order_cap = min(_to_float(live_settings.get("max_live_notional_usdt"), 10.0), _to_float(live_settings.get("hard_max_live_notional_usdt"), 50.0))
    max_order_usdt = min(_to_float(settings.get("max_order_usdt"), 5.0), live_order_cap if live_order_cap > 0 else _to_float(settings.get("max_order_usdt"), 5.0))
    allocation = allocation or {}
    funding_mode = str(allocation.get("funding_mode") or "single_order")
    investment_mode = _normalize_investment_mode(allocation.get("investment_mode"))
    selected_orders: list[dict[str, Any]] = []
    for order in orders:
        if len(selected_orders) >= max_plans:
            break
        side = str(order.get("side"))
        position = str(order.get("position") or "long")
        price = _to_float(order.get("price"))
        original_quote_amount = min(_to_float(order.get("quote_amount")), max_order_usdt)
        if price <= 0 or original_quote_amount <= 0:
            continue
        market_type = "futures" if position == "short" else "spot"
        plan_side = "SELL" if side == "sell" else "BUY"
        if market_type == "spot" and (not settings.get("allow_spot_long_grid") or plan_side != "BUY"):
            continue
        if market_type == "futures" and not settings.get("allow_futures_grid"):
            continue
        if _exchange_rule_blocker(symbol, market_type):
            continue
        selected_orders.append({**order, "market_type": market_type, "plan_side": plan_side, "position": position, "original_quote_amount": original_quote_amount})

    if funding_mode == "total_amount" and selected_orders:
        raw_total = max(_to_float(allocation.get("total_quote_amount")), 1.0)
        min_quotes = [_exchange_min_quote(symbol, str(order.get("market_type"))) for order in selected_orders]
        min_required = max(min_quotes or [1.0])
        grid_count = max(1, int(_to_float(bot.get("grid_count"), len(selected_orders) or 1)))
        quote_amount = raw_total / grid_count
        if min_required > max_order_usdt:
            return []
        if quote_amount < min_required:
            return []
        if quote_amount > max_order_usdt:
            return []
        feasible_count = min(len(selected_orders), max_plans)
        selected_orders = selected_orders[:feasible_count]
        total_quote_amount = raw_total
        quote_amounts = [quote_amount for _ in selected_orders]
    else:
        total_quote_amount = 0.0
        quote_amounts = [order["original_quote_amount"] for order in selected_orders]

    plans: list[dict[str, Any]] = []
    for order, quote_amount in zip(selected_orders, quote_amounts):
        price = _to_float(order.get("price"))
        quote_amount = min(max(_to_float(quote_amount), 1.0), max_order_usdt)
        if quote_amount < _exchange_min_quote(symbol, str(order.get("market_type"))):
            continue
        plan = _make_plan(
            symbol,
            str(order.get("market_type")),
            str(order.get("plan_side")),
            price,
            quote_amount,
            source,
            {
                "grid_bot_id": bot.get("bot_id"),
                "grid_level_index": order.get("level_index"),
                "grid_position": order.get("position"),
                "grid_direction": bot.get("grid_direction"),
                "grid_lower_price": bot.get("lower_price"),
                "grid_upper_price": bot.get("upper_price"),
                "grid_count": bot.get("grid_count"),
                "grid_price_step": bot.get("grid_price_step"),
                "grid_funding_mode": funding_mode,
                "grid_investment_mode": investment_mode,
                "grid_profit_reinvestment": investment_mode == "compound_reinvest",
                "grid_total_investment_usdt": total_quote_amount if funding_mode == "total_amount" else quote_amount,
                "grid_allocation_note": "总投入金额自动等额分配到初始挂单" if funding_mode == "total_amount" else "按单格挂单金额生成",
            },
        )
        preview = _strict_plan_preview(plan)
        if preview.get("ok"):
            plans.append({"plan": plan, "preview": preview})
    return plans


def build_live_grid_order_plans(bot_id: str) -> dict[str, Any]:
    settings = load_live_grid_settings()
    bot = _find_bot(bot_id)
    if not bot:
        return {"ok": False, "message": "未找到网格。", "plans": []}
    symbol = str(bot.get("symbol") or "").upper()
    direction = str(bot.get("grid_direction") or "long_spot")
    if direction == "long_spot" and not settings.get("allow_spot_long_grid"):
        return {"ok": False, "message": "本地现货做多网格开关未开启。", "plans": []}
    if direction in {"short_contract", "neutral_contract"} and not settings.get("allow_futures_grid"):
        return {"ok": False, "message": "本地合约网格开关未开启。", "plans": []}
    if bot.get("status") not in {"running", "paused"}:
        return {"ok": False, "message": "只有运行中或暂停中的网格可以生成实盘接口计划。", "plans": []}
    orders = sorted(
        [
            order
            for order in bot.get("open_orders") or []
            if _to_float(order.get("price")) > 0
            and (_to_float(order.get("quote_amount")) > 0 or _to_float(order.get("quantity")) > 0)
        ],
        key=lambda item: abs(_to_float(item.get("price")) - _to_float(bot.get("last_price"))),
    )
    plans = _orders_to_plans(bot, orders, "实盘网格接口")
    append_live_grid_audit("生成实盘网格计划", symbol, "完成", f"生成 {len(plans)} 个{direction}订单计划，仅用于检查/测试。")
    return {"ok": bool(plans), "message": f"已生成 {len(plans)} 个实盘网格订单计划。", "bot": bot, "plans": plans}


def build_live_grid_recommendation_order_plans(recommendation: dict[str, Any]) -> dict[str, Any]:
    settings = load_live_grid_settings()
    symbol = str(recommendation.get("symbol") or "").upper()
    current_price = _to_float(recommendation.get("last_price"))
    lower = _to_float(recommendation.get("lower_price"))
    upper = _to_float(recommendation.get("upper_price"))
    direction = str(recommendation.get("suggested_direction") or "long_spot")
    grid_count = max(2, int(_to_float(recommendation.get("grid_count"), 20)))
    if not symbol or current_price <= 0 or lower <= 0 or upper <= 0 or lower >= upper:
        return {"ok": False, "message": "推荐对象价格或区间不可用。", "plans": []}
    levels = [lower + (upper - lower) / grid_count * idx for idx in range(grid_count + 1)]
    current_index = max(0, min(len(levels) - 1, len([level for level in levels if level <= current_price]) - 1))
    quote_amount = _to_float(settings.get("max_order_usdt"), 5.0)
    orders: list[dict[str, Any]] = []
    if direction in {"long_spot", "neutral_contract"}:
        for idx in range(current_index - 1, -1, -1):
            orders.append({"side": "buy", "position": "long", "level_index": idx, "price": levels[idx], "quote_amount": quote_amount})
    if direction in {"short_contract", "neutral_contract"}:
        for idx in range(current_index + 1, len(levels)):
            orders.append({"side": "sell", "position": "short", "level_index": idx, "price": levels[idx], "quote_amount": quote_amount})
    bot_like = {
        "bot_id": f"recommendation_{symbol}_{direction}",
        "symbol": symbol,
        "grid_direction": direction,
        "last_price": current_price,
        "lower_price": lower,
        "upper_price": upper,
        "grid_count": grid_count,
        "grid_price_step": (upper - lower) / grid_count,
    }
    plans = _orders_to_plans(bot_like, sorted(orders, key=lambda item: abs(_to_float(item.get("price")) - current_price)), "实盘网格推荐接口")
    append_live_grid_audit("生成推荐实盘网格计划", symbol, "完成", f"推荐对象生成 {len(plans)} 个{direction}订单计划。")
    return {"ok": bool(plans), "message": f"已为推荐对象生成 {len(plans)} 个实盘网格订单计划。", "recommendation": recommendation, "plans": plans}


def build_live_grid_manual_order_plans(config: dict[str, Any]) -> dict[str, Any]:
    settings = load_live_grid_settings()
    symbol = str(config.get("symbol") or "").upper().strip()
    current_price = _to_float(config.get("current_price"))
    lower = _to_float(config.get("lower_price"))
    upper = _to_float(config.get("upper_price"))
    direction = str(config.get("direction") or "long_spot")
    grid_count = max(2, min(int(_to_float(config.get("grid_count"), 20)), 200))
    quote_amount = min(max(_to_float(config.get("quote_amount"), _to_float(settings.get("max_order_usdt"), 5.0)), 1.0), _to_float(settings.get("max_order_usdt"), 5.0))
    funding_mode = str(config.get("funding_mode") or "single_order")
    investment_mode = _normalize_investment_mode(config.get("investment_mode"))
    total_quote_amount = _to_float(config.get("total_quote_amount"))
    if direction not in {"long_spot", "short_contract", "neutral_contract"}:
        return {"ok": False, "message": "网格方向无效。", "plans": []}
    if not symbol or current_price <= 0 or lower <= 0 or upper <= 0 or lower >= upper:
        return {"ok": False, "message": "交易对象、当前价或价格区间无效。", "plans": []}
    if not (lower < current_price < upper):
        return {"ok": False, "message": "当前价格必须位于网格上下限之间。", "plans": []}
    if direction == "long_spot" and not settings.get("allow_spot_long_grid"):
        return {"ok": False, "message": "本地现货网格开关未开启。", "plans": []}
    if direction in {"short_contract", "neutral_contract"} and not settings.get("allow_futures_grid"):
        return {"ok": False, "message": "本地合约网格开关未开启。", "plans": []}
    levels = [lower + (upper - lower) / grid_count * idx for idx in range(grid_count + 1)]
    current_index = max(0, min(len(levels) - 1, len([level for level in levels if level <= current_price]) - 1))
    orders: list[dict[str, Any]] = []
    if direction in {"long_spot", "neutral_contract"}:
        for idx in range(current_index, -1, -1):
            if levels[idx] < current_price:
                orders.append({"side": "buy", "position": "long", "level_index": idx, "price": levels[idx], "quote_amount": quote_amount})
    if direction in {"short_contract", "neutral_contract"}:
        for idx in range(current_index + 1, len(levels)):
            if levels[idx] > current_price:
                orders.append({"side": "sell", "position": "short", "level_index": idx, "price": levels[idx], "quote_amount": quote_amount})
    bot_like = {
        "bot_id": f"manual_live_grid_{symbol}_{direction}",
        "symbol": symbol,
        "grid_direction": direction,
        "last_price": current_price,
        "lower_price": lower,
        "upper_price": upper,
        "grid_count": grid_count,
        "grid_price_step": (upper - lower) / grid_count,
    }
    plans = _orders_to_plans(
        bot_like,
        sorted(orders, key=lambda item: abs(_to_float(item.get("price")) - current_price)),
        "真实网格手动参数",
        {"funding_mode": funding_mode, "total_quote_amount": total_quote_amount, "investment_mode": investment_mode},
    )
    append_live_grid_audit("生成手动真实网格计划", symbol, "完成", f"手动参数生成 {len(plans)} 个{direction}订单计划。")
    return {
        "ok": bool(plans),
        "message": f"已生成 {len(plans)} 个真实网格订单计划。",
        "manual_config": {
            "symbol": symbol,
            "current_price": current_price,
            "lower_price": lower,
            "upper_price": upper,
            "direction": direction,
            "grid_count": grid_count,
            "quote_amount": quote_amount,
            "funding_mode": funding_mode,
            "total_quote_amount": total_quote_amount,
            "investment_mode": investment_mode,
        },
        "plans": plans,
    }


def _summarize_test_order_result(plan: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(result.get("ok")),
        "message": str(result.get("message") or ("Test Order 通过。" if result.get("ok") else "Test Order 失败。")),
        "plan_id": plan.get("plan_id"),
        "symbol": plan.get("symbol"),
        "side": plan.get("side"),
        "market_type": plan.get("market_type"),
        "price": plan.get("price"),
        "quantity": plan.get("quantity"),
        "quote_amount": plan.get("quote_amount"),
        "leverage": plan.get("leverage"),
    }


def _preview_failure_message(preview: dict[str, Any]) -> str:
    reasons: list[str] = []
    reasons.extend(str(item) for item in (preview.get("risk_errors") or []) if str(item).strip())
    rule_check = preview.get("rule_check") or {}
    plan_check = preview.get("plan_check") or {}
    reasons.extend(str(item) for item in (rule_check.get("errors") or []) if str(item).strip())
    reasons.extend(str(item) for item in (plan_check.get("errors") or []) if str(item).strip())
    if not reasons:
        reasons.extend(str(item) for item in (rule_check.get("warnings") or []) if str(item).strip())
        reasons.extend(str(item) for item in (plan_check.get("warnings") or []) if str(item).strip())
    detail = "；".join(reasons[:5]) if reasons else "请检查价格精度、数量精度、最小名义金额、单笔上限和交易对状态。"
    return f"订单预览未通过：{detail}"


def run_live_grid_test_orders(bot_id: str) -> dict[str, Any]:
    settings = load_live_grid_settings()
    if not settings.get("allow_test_orders"):
        return {"ok": False, "message": "实盘网格 Test Order 开关未开启。", "results": []}
    status = get_live_grid_status()
    if not status.get("ready_for_review"):
        return {"ok": False, "message": "实盘网格接口未通过检查：" + "；".join(status.get("blockers") or []), "status": status, "results": []}
    built = build_live_grid_order_plans(bot_id)
    results = []
    for item in built.get("plans") or []:
        plan, preview = _prepare_plan_for_submission(item.get("plan") or {})
        if not preview.get("ok"):
            results.append({"ok": False, "message": _preview_failure_message(preview), "plan_id": plan.get("plan_id"), "symbol": plan.get("symbol"), "side": plan.get("side"), "market_type": plan.get("market_type"), "price": plan.get("price"), "quantity": plan.get("quantity"), "quote_amount": plan.get("quote_amount")})
            continue
        if str(plan.get("market_type")) == "futures":
            test_result = run_futures_test_order(plan)
        else:
            test_result = run_spot_test_order(plan)
        results.append(_summarize_test_order_result(plan, test_result))
    symbol = str((built.get("bot") or {}).get("symbol") or "")
    append_live_grid_audit("实盘网格Test Order", symbol, "完成", f"提交 {len(results)} 个 Binance Test Order；未进入真实撮合。")
    return {"ok": bool(results) and all(row.get("ok") for row in results), "message": "Test Order 已完成，未产生真实成交。", "results": results, "built": built}


def run_live_grid_plan_test_orders(plan_items: list[dict[str, Any]]) -> dict[str, Any]:
    settings = load_live_grid_settings()
    if not settings.get("allow_test_orders"):
        return {"ok": False, "message": "实盘网格 Test Order 开关未开启。", "results": []}
    status = get_live_grid_status()
    if not status.get("ready_for_review"):
        return {"ok": False, "message": "实盘网格接口未通过检查：" + "；".join(status.get("blockers") or []), "status": status, "results": []}
    results = []
    for item in plan_items or []:
        plan, preview = _prepare_plan_for_submission(item.get("plan") or {})
        if not preview.get("ok"):
            results.append({"ok": False, "message": _preview_failure_message(preview), "plan_id": plan.get("plan_id"), "symbol": plan.get("symbol"), "side": plan.get("side"), "market_type": plan.get("market_type"), "price": plan.get("price"), "quantity": plan.get("quantity"), "quote_amount": plan.get("quote_amount")})
            continue
        test_result = run_futures_test_order(plan) if str(plan.get("market_type")) == "futures" else run_spot_test_order(plan)
        results.append(_summarize_test_order_result(plan, test_result))
    append_live_grid_audit("实盘网格计划Test Order", "", "完成", f"提交 {len(results)} 个 Binance Test Order；未进入真实撮合。")
    return {"ok": bool(results) and all(row.get("ok") for row in results), "message": "Test Order 已完成，未产生真实成交。", "results": results}


def _submit_live_grid_spot_plan(plan: dict[str, Any], test_order: dict[str, Any], confirmation_phrase: str) -> dict[str, Any]:
    validation = validate_live_order_plan(plan)
    phrase = require_confirmation_phrase(plan, confirmation_phrase)
    status = get_live_grid_status()
    checklist = [
        {"name": "实盘网格真实提交开关", "status": "通过" if status.get("real_submit_enabled") else "失败", "message": "检查通过。" if status.get("real_submit_enabled") else "实盘网格真实提交未开启或接口检查未通过。"},
        {"name": "现货网格订单", "status": "通过" if str(plan.get("market_type", "spot")) == "spot" else "失败", "message": "检查通过。" if str(plan.get("market_type", "spot")) == "spot" else "当前计划不是现货订单。"},
        {"name": "交易所规则通过", "status": "通过" if validation.get("ok") else "失败", "message": "检查通过。" if validation.get("ok") else "；".join(validation.get("errors") or [])},
        {"name": "Spot Test Order 通过", "status": "通过" if test_order.get("ok") else "失败", "message": test_order.get("message", "Spot Test Order 未通过。")},
        {"name": "确认短句正确", "status": "通过" if phrase.get("ok") else "失败", "message": phrase.get("message", "")},
    ]
    failed = [item for item in checklist if item["status"] == "失败"]
    if failed:
        return {"ok": False, "message": "网格现货提交前检查未通过。", "preflight": {"ok": False, "checklist": checklist}}
    credentials = load_api_credentials_safely(False)
    if not credentials.get("configured"):
        return {"ok": False, "message": "API尚未配置，无法提交网格现货订单。", "preflight": {"ok": False, "checklist": checklist}}
    try:
        response = _signed_request("POST", "/api/v3/order", _binance_order_params(plan), credentials, "spot", False)
        order_id = str(response.get("orderId", ""))
        record = {
            "time": _now(),
            "order_id": order_id,
            "client_order_id": response.get("clientOrderId"),
            "symbol": response.get("symbol") or plan.get("symbol"),
            "market_type": "spot",
            "side": plan.get("side"),
            "order_type": plan.get("order_type"),
            "price": plan.get("price"),
            "quantity": plan.get("quantity"),
            "notional": plan.get("quote_amount"),
            "order_status": response.get("status", "SUBMITTED"),
            "executed_qty": response.get("executedQty"),
            "avg_price": "",
            "source": plan.get("source"),
            "grid_bot_id": plan.get("grid_bot_id"),
            "grid_level_index": plan.get("grid_level_index"),
            "grid_direction": plan.get("grid_direction"),
            "grid_position": plan.get("grid_position"),
            "grid_lower_price": plan.get("grid_lower_price"),
            "grid_upper_price": plan.get("grid_upper_price"),
            "grid_count": plan.get("grid_count"),
            "grid_price_step": plan.get("grid_price_step"),
            "grid_investment_mode": plan.get("grid_investment_mode"),
            "grid_funding_mode": plan.get("grid_funding_mode"),
            "confirmation_phrase_ok": True,
            "raw_status_summary": response.get("status"),
        }
        save_live_order_record(record)
        log_live_audit_event("真实网格现货订单提交", mode=str(load_live_settings().get("mode")), symbol=str(plan.get("symbol", "")), result="已提交", reason=f"真实网格 Spot 订单已提交，订单ID {order_id}", real_account=True)
        order_status = fetch_live_order_status(order_id, str(plan.get("symbol", ""))) if order_id else {"ok": False, "message": "订单ID暂不可用。"}
        return {"ok": True, "message": "真实网格 Spot 订单已提交。", "order": record, "status": order_status}
    except Exception as exc:
        msg = f"真实网格 Spot 订单提交失败：{exc}"
        log_live_audit_event("真实网格现货订单提交失败", mode=str(load_live_settings().get("mode")), symbol=str(plan.get("symbol", "")), result="失败", reason=msg, real_account=True)
        return {"ok": False, "message": msg, "preflight": {"ok": False, "checklist": checklist}}


def _is_live_grid_record(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get(key, "")) for key in ("source", "grid_bot_id", "grid_direction", "grid_funding_mode"))
    return "网格" in text or bool(row.get("grid_level_index") is not None)


def _has_grid_runtime_metadata(row: dict[str, Any]) -> bool:
    return (
        row.get("grid_level_index") is not None
        and _to_float(row.get("grid_lower_price")) > 0
        and _to_float(row.get("grid_upper_price")) > 0
        and int(_to_float(row.get("grid_count"), 0)) > 0
        and bool(str(row.get("grid_position") or "").strip())
    )


def _fetch_grid_order_status(row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").upper()
    order_id = str(row.get("order_id") or "")
    if not symbol or not order_id:
        return {"ok": False, "message": "缺少订单ID或交易对象。"}
    market_type = str(row.get("market_type") or "spot")
    if market_type == "futures":
        credentials = load_api_credentials_safely(False)
        if not credentials.get("configured"):
            return {"ok": False, "message": "API尚未配置，无法回查合约订单。"}
        try:
            data = _signed_request("GET", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id}, credentials, "futures", False)
            return {"ok": True, "message": "合约订单状态回查成功。", "order": data}
        except Exception as exc:
            return {"ok": False, "message": f"合约订单状态回查失败：{exc}"}
    return fetch_live_order_status(order_id, symbol)


def _filled_status(status_result: dict[str, Any]) -> bool:
    order = status_result.get("order") or {}
    status = str(order.get("status") or order.get("order_status") or "").upper()
    return status == "FILLED"


def _level_price(record: dict[str, Any], level_index: int) -> float:
    lower = _to_float(record.get("grid_lower_price"))
    upper = _to_float(record.get("grid_upper_price"))
    count = int(_to_float(record.get("grid_count"), 0))
    if lower <= 0 or upper <= 0 or count <= 0 or lower >= upper:
        return 0.0
    return lower + (upper - lower) / count * level_index


def _build_replenish_plan(record: dict[str, Any], status_result: dict[str, Any]) -> dict[str, Any] | None:
    order = status_result.get("order") or {}
    symbol = str(record.get("symbol") or "").upper()
    market_type = str(record.get("market_type") or "spot")
    side = str(record.get("side") or "").upper()
    position = str(record.get("grid_position") or ("short" if market_type == "futures" else "long"))
    level_index = int(_to_float(record.get("grid_level_index"), -1))
    count = int(_to_float(record.get("grid_count"), 0))
    if not symbol or level_index < 0 or count <= 0:
        return None
    if position == "long":
        next_side = "SELL" if side == "BUY" else "BUY"
        next_index = level_index + 1 if side == "BUY" else level_index - 1
    else:
        next_side = "BUY" if side == "SELL" else "SELL"
        next_index = level_index - 1 if side == "SELL" else level_index + 1
    if next_index < 0 or next_index > count:
        return None
    price = _level_price(record, next_index)
    if price <= 0:
        return None
    filled_qty = _to_float(order.get("executedQty") or order.get("executed_qty") or record.get("quantity"))
    quote_amount = _to_float(record.get("notional") or record.get("quote_amount") or record.get("margin_usdt"))
    grid_ref = {
        "grid_bot_id": record.get("grid_bot_id"),
        "grid_level_index": next_index,
        "grid_position": position,
        "grid_direction": record.get("grid_direction"),
        "grid_lower_price": record.get("grid_lower_price"),
        "grid_upper_price": record.get("grid_upper_price"),
        "grid_count": record.get("grid_count"),
        "grid_price_step": record.get("grid_price_step"),
        "grid_funding_mode": record.get("grid_funding_mode"),
        "grid_investment_mode": record.get("grid_investment_mode"),
        "grid_profit_reinvestment": bool(record.get("grid_profit_reinvestment")),
        "grid_source_order_id": record.get("order_id"),
    }
    if market_type == "spot" and next_side == "SELL" and filled_qty > 0:
        grid_ref["quantity"] = filled_qty
        quote_amount = price * filled_qty
    return _make_plan(symbol, market_type, next_side, price, quote_amount, "真实网格自动补单", grid_ref)


def run_live_grid_runtime_cycle(limit: int = 20, force: bool = False) -> dict[str, Any]:
    global _LAST_RUNTIME_CYCLE_AT
    now = time.time()
    if not force and now - _LAST_RUNTIME_CYCLE_AT < _RUNTIME_CYCLE_SECONDS:
        return {"ok": True, "message": "真实网格自动补单冷却中。", "checked": 0, "replenished": 0}
    _LAST_RUNTIME_CYCLE_AT = now
    settings = load_live_grid_settings()
    if not settings.get("auto_replenish_enabled"):
        return {"ok": True, "message": "真实网格自动补单未开启。", "checked": 0, "replenished": 0}
    status = get_live_grid_status()
    if not status.get("real_submit_enabled"):
        return {"ok": False, "message": "真实网格自动补单未通过接口检查：" + "；".join(status.get("blockers") or []), "checked": 0, "replenished": 0}
    state = _load_runtime_state()
    processed = state.setdefault("processed_fills", {})
    checked = 0
    replenished = 0
    failures: list[str] = []
    for record in load_live_order_records(500):
        if checked >= limit:
            break
        if not _is_live_grid_record(record):
            continue
        order_id = str(record.get("order_id") or "")
        if not order_id or processed.get(order_id):
            continue
        if not _has_grid_runtime_metadata(record):
            processed[order_id] = {"time": _now(), "result": "跳过", "reason": "旧网格订单缺少层级信息，不能自动补单。"}
            append_live_grid_audit("真实网格自动补单", str(record.get("symbol") or ""), "跳过", f"订单 {order_id} 缺少新版网格层级信息，已跳过。")
            continue
        checked += 1
        order_status = _fetch_grid_order_status(record)
        if not order_status.get("ok"):
            failures.append(order_status.get("message", "订单状态回查失败。"))
            continue
        if not _filled_status(order_status):
            continue
        plan = _build_replenish_plan(record, order_status)
        if not plan:
            processed[order_id] = {"time": _now(), "result": "跳过", "reason": "缺少网格区间/层级，或成交在边界层级。"}
            append_live_grid_audit("真实网格自动补单", str(record.get("symbol") or ""), "跳过", f"订单 {order_id} 缺少可补单网格信息或已到边界。")
            continue
        preview = create_live_order_preview(plan)
        if not preview.get("ok"):
            reason = _preview_failure_message(preview)
            processed[order_id] = {"time": _now(), "result": "失败", "reason": reason}
            failures.append(reason)
            append_live_grid_audit("真实网格自动补单", str(plan.get("symbol") or ""), "失败", reason)
            continue
        test_order = run_futures_test_order(plan) if str(plan.get("market_type")) == "futures" else run_spot_test_order(plan)
        if not test_order.get("ok"):
            reason = test_order.get("message", "自动补单 Test Order 未通过。")
            processed[order_id] = {"time": _now(), "result": "失败", "reason": reason}
            failures.append(reason)
            append_live_grid_audit("真实网格自动补单", str(plan.get("symbol") or ""), "失败", reason)
            continue
        result = submit_live_futures_order(plan, test_order, True, "我确认执行小资金实盘订单") if str(plan.get("market_type")) == "futures" else _submit_live_grid_spot_plan(plan, test_order, "我确认执行小资金实盘订单")
        processed[order_id] = {"time": _now(), "result": "通过" if result.get("ok") else "失败", "reason": result.get("message", ""), "replenish_plan_id": plan.get("plan_id")}
        if result.get("ok"):
            replenished += 1
            append_live_grid_audit("真实网格自动补单", str(plan.get("symbol") or ""), "通过", f"订单 {order_id} 成交后已提交反向补单 {plan.get('side')} {plan.get('grid_level_index')}。")
        else:
            failures.append(result.get("message", "自动补单提交失败。"))
            append_live_grid_audit("真实网格自动补单", str(plan.get("symbol") or ""), "失败", result.get("message", "自动补单提交失败。"))
    state["last_cycle_time"] = _now()
    _save_runtime_state(state)
    return {"ok": not failures, "message": f"检查 {checked} 个网格订单，补单 {replenished} 个。", "checked": checked, "replenished": replenished, "failures": failures[:10]}


def submit_live_grid_spot_orders(bot_id: str, confirmation_phrase: str) -> dict[str, Any]:
    settings = load_live_grid_settings()
    if not settings.get("allow_real_order_submit"):
        return {"ok": False, "message": "实盘网格真实提交开关未开启。", "results": []}
    status = get_live_grid_status()
    if not status.get("real_submit_enabled"):
        return {"ok": False, "message": "实盘网格真实提交未通过检查：" + "；".join(status.get("blockers") or []), "status": status, "results": []}
    built = build_live_grid_order_plans(bot_id)
    if not built.get("ok"):
        return {"ok": False, "message": built.get("message", "实盘网格计划生成失败。"), "built": built, "results": []}
    results: list[dict[str, Any]] = []
    for item in built.get("plans") or []:
        plan, preview = _prepare_plan_for_submission(item.get("plan") or {})
        if not preview.get("ok"):
            results.append({"ok": False, "message": _preview_failure_message(preview), "plan_id": plan.get("plan_id"), "symbol": plan.get("symbol"), "side": plan.get("side"), "market_type": plan.get("market_type"), "price": plan.get("price"), "quantity": plan.get("quantity"), "quote_amount": plan.get("quote_amount")})
            continue
        test_order = run_futures_test_order(plan) if str(plan.get("market_type")) == "futures" else run_spot_test_order(plan)
        if not test_order.get("ok"):
            results.append({"ok": False, "message": test_order.get("message", "Test Order 未通过。"), "plan_id": plan.get("plan_id")})
            continue
        if str(plan.get("market_type")) == "futures":
            results.append(submit_live_futures_order(plan, test_order, True, confirmation_phrase))
        else:
            results.append(_submit_live_grid_spot_plan(plan, test_order, confirmation_phrase))
    symbol = str((built.get("bot") or {}).get("symbol") or "")
    ok = bool(results) and all(row.get("ok") for row in results)
    append_live_grid_audit("实盘网格真实提交", symbol, "通过" if ok else "失败", f"尝试提交 {len(results)} 个实盘网格初始订单。")
    return {"ok": ok, "message": "实盘网格初始订单提交完成。" if ok else "实盘网格初始订单未全部提交成功。", "results": results, "built": built}


def submit_live_grid_plan_orders(plan_items: list[dict[str, Any]], confirmation_phrase: str) -> dict[str, Any]:
    settings = load_live_grid_settings()
    if not settings.get("allow_real_order_submit"):
        return {"ok": False, "message": "实盘网格真实提交开关未开启。", "results": []}
    status = get_live_grid_status()
    if not status.get("real_submit_enabled"):
        return {"ok": False, "message": "实盘网格真实提交未通过检查：" + "；".join(status.get("blockers") or []), "status": status, "results": []}
    results: list[dict[str, Any]] = []
    for item in plan_items or []:
        plan, preview = _prepare_plan_for_submission(item.get("plan") or {})
        if not preview.get("ok"):
            results.append({"ok": False, "message": _preview_failure_message(preview), "plan_id": plan.get("plan_id"), "symbol": plan.get("symbol"), "side": plan.get("side"), "market_type": plan.get("market_type"), "price": plan.get("price"), "quantity": plan.get("quantity"), "quote_amount": plan.get("quote_amount")})
            continue
        test_order = run_futures_test_order(plan) if str(plan.get("market_type")) == "futures" else run_spot_test_order(plan)
        if not test_order.get("ok"):
            results.append({"ok": False, "message": test_order.get("message", "Test Order 未通过。"), "plan_id": plan.get("plan_id")})
            continue
        if str(plan.get("market_type")) == "futures":
            results.append(submit_live_futures_order(plan, test_order, True, confirmation_phrase))
        else:
            results.append(_submit_live_grid_spot_plan(plan, test_order, confirmation_phrase))
    ok = bool(results) and all(row.get("ok") for row in results)
    append_live_grid_audit("实盘网格计划真实提交", "", "通过" if ok else "失败", f"尝试提交 {len(results)} 个实盘网格计划订单。")
    return {"ok": ok, "message": "实盘网格计划订单提交完成。" if ok else "实盘网格计划订单未全部提交成功。", "results": results}

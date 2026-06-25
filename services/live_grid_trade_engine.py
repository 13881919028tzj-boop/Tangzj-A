"""Live grid trading bridge.

This module exposes a guarded bridge from local grid bots to Binance live-order
planning. It deliberately does not submit real orders automatically.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from services.grid_trade_engine import load_grid_bots
from services.live_trading_center import (
    LIVE_TRADING_ENABLED,
    check_api_connection,
    check_api_key_restrictions,
    check_api_permissions,
    create_live_order_plan,
    create_live_order_preview,
    load_live_settings,
    run_futures_test_order,
    run_spot_test_order,
    submit_live_futures_order,
    submit_live_spot_order,
)


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CONFIG_PATH = DATA_DIR / "live_grid_settings.json"
AUDIT_PATH = DATA_DIR / "live_grid_audit_log.json"

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
}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


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
    plan = create_live_order_plan(
        None,
        {
            "symbol": symbol,
            "market_type": market_type,
            "leverage": leverage,
            "side": side,
            "order_type": "LIMIT",
            "price": price,
            "quote_amount": quote_amount,
            "source": source,
        },
    )
    plan.update(grid_ref)
    plan["live_grid_review_only"] = True
    return plan


def _orders_to_plans(bot: dict[str, Any], orders: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    settings = load_live_grid_settings()
    symbol = str(bot.get("symbol") or "").upper()
    plans: list[dict[str, Any]] = []
    max_plans = int(settings.get("max_initial_orders", 2))
    for order in orders:
        if len(plans) >= max_plans:
            break
        side = str(order.get("side"))
        position = str(order.get("position") or "long")
        price = _to_float(order.get("price"))
        quote_amount = min(_to_float(order.get("quote_amount")), _to_float(settings.get("max_order_usdt"), 5.0))
        if price <= 0 or quote_amount <= 0:
            continue
        market_type = "futures" if position == "short" else "spot"
        plan_side = "SELL" if side == "sell" else "BUY"
        if market_type == "spot" and (not settings.get("allow_spot_long_grid") or plan_side != "BUY"):
            continue
        if market_type == "futures" and not settings.get("allow_futures_grid"):
            continue
        plan = _make_plan(
            symbol,
            market_type,
            plan_side,
            price,
            quote_amount,
            source,
            {
                "grid_bot_id": bot.get("bot_id"),
                "grid_level_index": order.get("level_index"),
                "grid_position": position,
                "grid_direction": bot.get("grid_direction"),
            },
        )
        plans.append({"plan": plan, "preview": create_live_order_preview(plan)})
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
    }
    plans = _orders_to_plans(bot_like, sorted(orders, key=lambda item: abs(_to_float(item.get("price")) - current_price)), "真实网格手动参数")
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
        },
        "plans": plans,
    }


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
        plan = item.get("plan") or {}
        if not (item.get("preview") or {}).get("ok"):
            results.append({"ok": False, "message": "订单预览未通过。", "plan_id": plan.get("plan_id")})
            continue
        if str(plan.get("market_type")) == "futures":
            results.append(run_futures_test_order(plan))
        else:
            results.append(run_spot_test_order(plan))
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
        plan = item.get("plan") or {}
        if not (item.get("preview") or {}).get("ok"):
            results.append({"ok": False, "message": "订单预览未通过。", "plan_id": plan.get("plan_id")})
            continue
        results.append(run_futures_test_order(plan) if str(plan.get("market_type")) == "futures" else run_spot_test_order(plan))
    append_live_grid_audit("实盘网格计划Test Order", "", "完成", f"提交 {len(results)} 个 Binance Test Order；未进入真实撮合。")
    return {"ok": bool(results) and all(row.get("ok") for row in results), "message": "Test Order 已完成，未产生真实成交。", "results": results}


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
        plan = item.get("plan") or {}
        preview = item.get("preview") or {}
        if not preview.get("ok"):
            results.append({"ok": False, "message": "订单预览未通过。", "plan_id": plan.get("plan_id")})
            continue
        test_order = run_futures_test_order(plan) if str(plan.get("market_type")) == "futures" else run_spot_test_order(plan)
        if not test_order.get("ok"):
            results.append({"ok": False, "message": test_order.get("message", "Test Order 未通过。"), "plan_id": plan.get("plan_id")})
            continue
        if str(plan.get("market_type")) == "futures":
            results.append(submit_live_futures_order(plan, test_order, True, confirmation_phrase))
        else:
            results.append(submit_live_spot_order(plan, test_order, True, confirmation_phrase))
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
        plan = item.get("plan") or {}
        preview = item.get("preview") or {}
        if not preview.get("ok"):
            results.append({"ok": False, "message": "订单预览未通过。", "plan_id": plan.get("plan_id")})
            continue
        test_order = run_futures_test_order(plan) if str(plan.get("market_type")) == "futures" else run_spot_test_order(plan)
        if not test_order.get("ok"):
            results.append({"ok": False, "message": test_order.get("message", "Test Order 未通过。"), "plan_id": plan.get("plan_id")})
            continue
        if str(plan.get("market_type")) == "futures":
            results.append(submit_live_futures_order(plan, test_order, True, confirmation_phrase))
        else:
            results.append(submit_live_spot_order(plan, test_order, True, confirmation_phrase))
    ok = bool(results) and all(row.get("ok") for row in results)
    append_live_grid_audit("实盘网格计划真实提交", "", "通过" if ok else "失败", f"尝试提交 {len(results)} 个实盘网格计划订单。")
    return {"ok": ok, "message": "实盘网格计划订单提交完成。" if ok else "实盘网格计划订单未全部提交成功。", "results": results}

"""Independent local grid trading simulator.

This module is intentionally separate from committee-driven simulation trading.
It stores its own bots, fills and events, and only uses public ticker prices.
No real exchange order API is called here.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BOTS_PATH = DATA_DIR / "grid_bots.json"
TRADES_PATH = DATA_DIR / "grid_trades.json"
EVENTS_PATH = DATA_DIR / "grid_events.json"

DEFAULT_FEE_RATE = 0.0004
MIN_GRID_COUNT = 2
MAX_GRID_COUNT = 200
GRID_DIRECTIONS = {"long_spot", "short_contract", "neutral_contract"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ts() -> int:
    return int(time.time())


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
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_grid_bots() -> list[dict[str, Any]]:
    rows = _read_json(BOTS_PATH, [])
    return rows if isinstance(rows, list) else []


def save_grid_bots(rows: list[dict[str, Any]]) -> None:
    _write_json(BOTS_PATH, rows[:200])


def load_grid_trades(limit: int | None = None) -> list[dict[str, Any]]:
    rows = _read_json(TRADES_PATH, [])
    if not isinstance(rows, list):
        return []
    rows = [row for row in rows if _to_float((row or {}).get("quantity")) > 0]
    return rows[:limit] if limit else rows


def save_grid_trades(rows: list[dict[str, Any]]) -> None:
    _write_json(TRADES_PATH, rows[:2000])


def load_grid_events(limit: int | None = None) -> list[dict[str, Any]]:
    rows = _read_json(EVENTS_PATH, [])
    if not isinstance(rows, list):
        return []
    rows = [
        row
        for row in rows
        if not (
            str((row or {}).get("event_type") or "") == "网格成交"
            and " 0.00000000 " in str((row or {}).get("content") or "")
        )
    ]
    return rows[:limit] if limit else rows


def save_grid_events(rows: list[dict[str, Any]]) -> None:
    _write_json(EVENTS_PATH, rows[:1000])


def compact_grid_storage() -> dict[str, int]:
    """Remove invalid historical grid records and bad open orders from storage."""
    raw_trades = _read_json(TRADES_PATH, [])
    raw_events = _read_json(EVENTS_PATH, [])
    raw_bots = load_grid_bots()
    trades = [row for row in raw_trades if isinstance(row, dict) and _to_float(row.get("quantity")) > 0]
    events = [
        row
        for row in raw_events
        if isinstance(row, dict)
        and not (
            str(row.get("event_type") or "") == "网格成交"
            and " 0.00000000 " in str(row.get("content") or "")
        )
    ]
    removed_orders = 0
    for bot in raw_bots:
        removed_orders += _sanitize_open_orders(bot)
    save_grid_trades(trades)
    save_grid_events(events)
    save_grid_bots(raw_bots)
    return {
        "removed_trades": max(0, len(raw_trades) - len(trades)) if isinstance(raw_trades, list) else 0,
        "removed_events": max(0, len(raw_events) - len(events)) if isinstance(raw_events, list) else 0,
        "removed_orders": removed_orders,
    }


def _append_event(event_type: str, bot: dict[str, Any] | None = None, content: str = "", price: float | None = None) -> None:
    events = load_grid_events()
    events.insert(
        0,
        {
            "time": _now(),
            "event_type": event_type,
            "bot_id": (bot or {}).get("bot_id", ""),
            "symbol": (bot or {}).get("symbol", ""),
            "price": price,
            "content": content,
        },
    )
    save_grid_events(events)


def _levels(lower_price: float, upper_price: float, grid_count: int) -> list[float]:
    step = (upper_price - lower_price) / grid_count
    return [lower_price + step * index for index in range(grid_count + 1)]


def _nearest_level_index(levels: list[float], price: float) -> int:
    below = [idx for idx, level in enumerate(levels) if level <= price]
    if not below:
        return 0
    return min(max(below[-1], 0), len(levels) - 1)


def validate_grid_config(symbol: str, lower_price: float, upper_price: float, grid_count: int, investment_usdt: float, current_price: float, grid_direction: str = "long_spot") -> tuple[bool, list[str]]:
    reasons: list[str] = []
    direction = str(grid_direction or "long_spot")
    if not str(symbol or "").upper().endswith("USDT"):
        reasons.append("网格交易对象必须是 USDT 交易对。")
    if direction not in GRID_DIRECTIONS:
        reasons.append("网格方向必须是做多、做空或中性。")
    if lower_price <= 0 or upper_price <= 0 or current_price <= 0:
        reasons.append("价格必须大于0。")
    if lower_price >= upper_price:
        reasons.append("网格下限必须低于上限。")
    if grid_count < MIN_GRID_COUNT or grid_count > MAX_GRID_COUNT:
        reasons.append(f"网格数量必须在 {MIN_GRID_COUNT}-{MAX_GRID_COUNT} 之间。")
    if investment_usdt <= 0:
        reasons.append("投入资金必须大于0。")
    if current_price and (current_price <= lower_price or current_price >= upper_price):
        reasons.append("当前价格必须位于网格区间内。")
    if investment_usdt / max(grid_count, 1) < 1:
        reasons.append("单格资金低于 1 USDT，模拟结果容易失真。")
    return not reasons, reasons


def create_grid_bot(symbol: str, lower_price: float, upper_price: float, grid_count: int, investment_usdt: float, current_price: float, fee_rate: float = DEFAULT_FEE_RATE, grid_direction: str = "long_spot") -> dict[str, Any]:
    symbol = str(symbol or "").upper().strip()
    grid_direction = str(grid_direction or "long_spot")
    lower_price = _to_float(lower_price)
    upper_price = _to_float(upper_price)
    grid_count = int(_to_float(grid_count))
    investment_usdt = _to_float(investment_usdt)
    current_price = _to_float(current_price)
    fee_rate = max(0.0, _to_float(fee_rate, DEFAULT_FEE_RATE))
    ok, reasons = validate_grid_config(symbol, lower_price, upper_price, grid_count, investment_usdt, current_price, grid_direction)
    if not ok:
        raise ValueError("；".join(reasons))

    levels = _levels(lower_price, upper_price, grid_count)
    current_index = _nearest_level_index(levels, current_price)
    order_quote = investment_usdt / (grid_count + 1)
    open_orders: list[dict[str, Any]] = []
    quote_balance = investment_usdt
    base_inventory = 0.0
    short_inventory = 0.0

    for idx, level in enumerate(levels):
        if grid_direction == "long_spot":
            if idx < current_index:
                open_orders.append({"side": "buy", "position": "long", "level_index": idx, "price": level, "quote_amount": order_quote})
            elif idx > current_index:
                qty = order_quote / current_price
                cost = qty * current_price
                if quote_balance >= cost:
                    quote_balance -= cost
                    base_inventory += qty
                    open_orders.append({"side": "sell", "position": "long", "level_index": idx, "price": level, "quantity": qty, "paired_buy_price": current_price})
        elif grid_direction == "short_contract":
            if idx < current_index:
                qty = order_quote / current_price
                short_inventory += qty
                open_orders.append({"side": "buy", "position": "short", "level_index": idx, "price": level, "quantity": qty, "paired_sell_price": current_price})
            elif idx > current_index:
                open_orders.append({"side": "sell", "position": "short", "level_index": idx, "price": level, "quote_amount": order_quote})
        else:
            if idx < current_index:
                open_orders.append({"side": "buy", "position": "long", "level_index": idx, "price": level, "quote_amount": order_quote})
            elif idx > current_index:
                open_orders.append({"side": "sell", "position": "short", "level_index": idx, "price": level, "quote_amount": order_quote})

    bot = {
        "bot_id": f"grid_{int(time.time() * 1000)}",
        "symbol": symbol,
        "status": "running",
        "grid_direction": grid_direction,
        "lower_price": lower_price,
        "upper_price": upper_price,
        "grid_count": grid_count,
        "grid_step": (upper_price - lower_price) / grid_count,
        "levels": levels,
        "investment_usdt": investment_usdt,
        "initial_price": current_price,
        "last_price": current_price,
        "last_update_time": _now(),
        "created_time": _now(),
        "created_ts": _ts(),
        "fee_rate": fee_rate,
        "order_quote": order_quote,
        "quote_balance": quote_balance,
        "base_inventory": base_inventory,
        "short_inventory": short_inventory,
        "realized_profit": 0.0,
        "total_fee": 0.0,
        "filled_trades": 0,
        "open_orders": open_orders,
    }
    bots = load_grid_bots()
    bots.insert(0, bot)
    save_grid_bots(bots)
    _append_event("启动网格", bot, f"方向 {grid_direction}，区间 {lower_price:.8f}-{upper_price:.8f}，网格 {grid_count}，投入 {investment_usdt:.2f} USDT。", current_price)
    return bot


def stop_grid_bot(bot_id: str, reason: str = "用户停止网格") -> dict[str, Any] | None:
    bots = load_grid_bots()
    stopped: dict[str, Any] | None = None
    for bot in bots:
        if bot.get("bot_id") == bot_id and bot.get("status") in {"running", "paused"}:
            bot["status"] = "stopped"
            bot["stopped_time"] = _now()
            bot["stop_reason"] = reason
            stopped = bot
            _append_event("停止网格", bot, reason, _to_float(bot.get("last_price")))
    save_grid_bots(bots)
    return stopped


def pause_grid_bot(bot_id: str, reason: str = "暂停补单") -> dict[str, Any] | None:
    bots = load_grid_bots()
    paused: dict[str, Any] | None = None
    for bot in bots:
        if bot.get("bot_id") == bot_id and bot.get("status") == "running":
            bot["status"] = "paused"
            bot["paused_time"] = _now()
            bot["pause_reason"] = reason
            paused = bot
            _append_event("暂停网格", bot, reason, _to_float(bot.get("last_price")))
    save_grid_bots(bots)
    return paused


def resume_grid_bot(bot_id: str, reason: str = "恢复网格") -> dict[str, Any] | None:
    bots = load_grid_bots()
    resumed: dict[str, Any] | None = None
    for bot in bots:
        if bot.get("bot_id") == bot_id and bot.get("status") == "paused":
            bot["status"] = "running"
            bot["resumed_time"] = _now()
            bot["resume_reason"] = reason
            resumed = bot
            _append_event("恢复网格", bot, reason, _to_float(bot.get("last_price")))
    save_grid_bots(bots)
    return resumed


def cancel_grid_orders(bot_id: str, reason: str = "停止并撤销挂单") -> dict[str, Any] | None:
    bots = load_grid_bots()
    stopped: dict[str, Any] | None = None
    for bot in bots:
        if bot.get("bot_id") == bot_id and bot.get("status") in {"running", "paused"}:
            canceled = len(bot.get("open_orders") or [])
            bot["open_orders"] = []
            bot["status"] = "stopped"
            bot["stopped_time"] = _now()
            bot["stop_reason"] = reason
            bot["canceled_orders"] = int(bot.get("canceled_orders", 0) or 0) + canceled
            stopped = bot
            _append_event("撤销挂单", bot, f"{reason}，撤销 {canceled} 个模拟挂单。", _to_float(bot.get("last_price")))
    save_grid_bots(bots)
    return stopped


def close_grid_position(bot_id: str, current_price: float, reason: str = "停止并市价平仓", emergency: bool = False) -> dict[str, Any] | None:
    bots = load_grid_bots()
    closed: dict[str, Any] | None = None
    price = _to_float(current_price)
    for bot in bots:
        if bot.get("bot_id") != bot_id or bot.get("status") not in {"running", "paused"}:
            continue
        if price <= 0:
            price = _to_float(bot.get("last_price"), _to_float(bot.get("initial_price")))
        if price <= 0:
            continue
        canceled = len(bot.get("open_orders") or [])
        qty = _to_float(bot.get("base_inventory"))
        short_qty = _to_float(bot.get("short_inventory"))
        gross = qty * price
        fee = gross * _to_float(bot.get("fee_rate"), DEFAULT_FEE_RATE)
        short_fee = short_qty * price * _to_float(bot.get("fee_rate"), DEFAULT_FEE_RATE)
        short_profit = (_to_float(bot.get("initial_price"), price) - price) * short_qty - short_fee
        bot["open_orders"] = []
        bot["quote_balance"] = _to_float(bot.get("quote_balance")) + gross - fee
        bot["quote_balance"] = _to_float(bot.get("quote_balance")) + short_profit
        bot["base_inventory"] = 0.0
        bot["short_inventory"] = 0.0
        bot["total_fee"] = _to_float(bot.get("total_fee")) + fee + short_fee
        bot["realized_profit"] = _to_float(bot.get("quote_balance")) - _to_float(bot.get("investment_usdt"))
        bot["last_price"] = price
        bot["last_update_time"] = _now()
        bot["status"] = "emergency_stopped" if emergency else "stopped"
        bot["stopped_time"] = _now()
        bot["stop_reason"] = reason
        bot["canceled_orders"] = int(bot.get("canceled_orders", 0) or 0) + canceled
        trade = {
            "time": _now(),
            "bot_id": bot.get("bot_id"),
            "symbol": bot.get("symbol"),
            "side": "sell",
            "action": "emergency_close" if emergency else "market_close",
            "price": price,
            "quantity": qty + short_qty,
            "fee": fee + short_fee,
            "profit": bot["realized_profit"],
            "market_price": price,
        }
        _append_trade(trade)
        event_type = "紧急强停" if emergency else "市价平仓"
        _append_event(event_type, bot, f"{reason}，撤销 {canceled} 个挂单，按 {price:.8f} 平仓，多头 {qty:.8f} / 空头 {short_qty:.8f}，手续费 {fee + short_fee:.4f} USDT。", price)
        closed = bot
    save_grid_bots(bots)
    return closed


def _has_order(bot: dict[str, Any], side: str, level_index: int) -> bool:
    return any(order.get("side") == side and int(order.get("level_index", -1)) == level_index for order in bot.get("open_orders", []) or [])


def _is_valid_open_order(bot: dict[str, Any], order: dict[str, Any]) -> bool:
    side = str(order.get("side") or "")
    position = str(order.get("position") or "long")
    level_index = int(_to_float(order.get("level_index"), -1))
    levels = bot.get("levels") or []
    price = _to_float(order.get("price"))
    if side not in {"buy", "sell"} or price <= 0 or level_index < 0 or level_index >= len(levels):
        return False
    if side == "buy":
        if position == "short":
            return _to_float(order.get("quantity")) > 0
        return _to_float(order.get("quote_amount")) > 0
    if position == "short":
        return _to_float(order.get("quote_amount")) > 0 or _to_float(order.get("quantity")) > 0
    return _to_float(order.get("quantity")) > 0


def _sanitize_open_orders(bot: dict[str, Any]) -> int:
    cleaned: list[dict[str, Any]] = []
    removed = 0
    seen: set[tuple[str, str, int, float]] = set()
    for order in bot.get("open_orders") or []:
        if not isinstance(order, dict) or not _is_valid_open_order(bot, order):
            removed += 1
            continue
        key = (
            str(order.get("side") or ""),
            str(order.get("position") or "long"),
            int(_to_float(order.get("level_index"), -1)),
            round(_to_float(order.get("price")), 12),
        )
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        cleaned.append(order)
    if removed:
        bot["open_orders"] = cleaned
        bot["invalid_orders_removed"] = int(bot.get("invalid_orders_removed", 0) or 0) + removed
        bot["last_order_sanitize_time"] = _now()
    return removed


def _append_trade(trade: dict[str, Any]) -> None:
    rows = load_grid_trades()
    rows.insert(0, trade)
    save_grid_trades(rows)


def _fill_buy(bot: dict[str, Any], order: dict[str, Any], fill_price: float) -> dict[str, Any]:
    if str(order.get("position") or "long") == "short":
        return _fill_short_buy(bot, order, fill_price)
    quote = _to_float(order.get("quote_amount"))
    fee = quote * _to_float(bot.get("fee_rate"), DEFAULT_FEE_RATE)
    spend = quote + fee
    if _to_float(bot.get("quote_balance")) < spend:
        raise ValueError("quote_balance_not_enough")
    qty = quote / fill_price if fill_price > 0 else 0
    bot["quote_balance"] = _to_float(bot.get("quote_balance")) - spend
    bot["base_inventory"] = _to_float(bot.get("base_inventory")) + qty
    bot["total_fee"] = _to_float(bot.get("total_fee")) + fee
    idx = int(order.get("level_index", 0))
    sell_idx = min(idx + 1, int(bot.get("grid_count", 0)))
    if sell_idx > idx and not _has_order(bot, "sell", sell_idx):
        bot.setdefault("open_orders", []).append({"side": "sell", "position": "long", "level_index": sell_idx, "price": bot["levels"][sell_idx], "quantity": qty, "paired_buy_price": fill_price})
    return {"quantity": qty, "fee": fee, "profit": 0.0}


def _fill_sell(bot: dict[str, Any], order: dict[str, Any], fill_price: float) -> dict[str, Any]:
    if str(order.get("position") or "long") == "short":
        return _fill_short_sell(bot, order, fill_price)
    qty = _to_float(order.get("quantity"))
    qty = min(qty, _to_float(bot.get("base_inventory")))
    if qty <= 0:
        raise ValueError("invalid_long_sell_quantity")
    gross = qty * fill_price
    fee = gross * _to_float(bot.get("fee_rate"), DEFAULT_FEE_RATE)
    paired_buy = _to_float(order.get("paired_buy_price"), _to_float(bot.get("initial_price"), fill_price))
    profit = (fill_price - paired_buy) * qty - fee
    bot["base_inventory"] = _to_float(bot.get("base_inventory")) - qty
    bot["quote_balance"] = _to_float(bot.get("quote_balance")) + gross - fee
    bot["realized_profit"] = _to_float(bot.get("realized_profit")) + profit
    bot["total_fee"] = _to_float(bot.get("total_fee")) + fee
    idx = int(order.get("level_index", 0))
    buy_idx = max(idx - 1, 0)
    if buy_idx < idx and not _has_order(bot, "buy", buy_idx):
        bot.setdefault("open_orders", []).append({"side": "buy", "position": "long", "level_index": buy_idx, "price": bot["levels"][buy_idx], "quote_amount": _to_float(bot.get("order_quote"))})
    return {"quantity": qty, "fee": fee, "profit": profit}


def _fill_short_sell(bot: dict[str, Any], order: dict[str, Any], fill_price: float) -> dict[str, Any]:
    quote = _to_float(order.get("quote_amount"))
    qty = quote / fill_price if fill_price > 0 else 0
    if qty <= 0:
        qty = _to_float(order.get("quantity"))
        quote = qty * fill_price if fill_price > 0 else 0
    if qty <= 0 or quote <= 0:
        raise ValueError("invalid_short_sell_quantity")
    fee = quote * _to_float(bot.get("fee_rate"), DEFAULT_FEE_RATE)
    bot["quote_balance"] = _to_float(bot.get("quote_balance")) - fee
    bot["short_inventory"] = _to_float(bot.get("short_inventory")) + qty
    bot["total_fee"] = _to_float(bot.get("total_fee")) + fee
    idx = int(order.get("level_index", 0))
    buy_idx = max(idx - 1, 0)
    if buy_idx < idx and not _has_order(bot, "buy", buy_idx):
        bot.setdefault("open_orders", []).append({"side": "buy", "position": "short", "level_index": buy_idx, "price": bot["levels"][buy_idx], "quantity": qty, "paired_sell_price": fill_price})
    return {"quantity": qty, "fee": fee, "profit": 0.0}


def _fill_short_buy(bot: dict[str, Any], order: dict[str, Any], fill_price: float) -> dict[str, Any]:
    qty = min(_to_float(order.get("quantity")), _to_float(bot.get("short_inventory")))
    if qty <= 0:
        raise ValueError("invalid_short_buy_quantity")
    paired_sell = _to_float(order.get("paired_sell_price"), _to_float(bot.get("initial_price"), fill_price))
    close_notional = qty * fill_price
    fee = close_notional * _to_float(bot.get("fee_rate"), DEFAULT_FEE_RATE)
    profit = (paired_sell - fill_price) * qty - fee
    bot["quote_balance"] = _to_float(bot.get("quote_balance")) + profit
    bot["short_inventory"] = _to_float(bot.get("short_inventory")) - qty
    bot["realized_profit"] = _to_float(bot.get("realized_profit")) + profit
    bot["total_fee"] = _to_float(bot.get("total_fee")) + fee
    idx = int(order.get("level_index", 0))
    sell_idx = min(idx + 1, int(bot.get("grid_count", 0)))
    if sell_idx > idx and not _has_order(bot, "sell", sell_idx):
        bot.setdefault("open_orders", []).append({"side": "sell", "position": "short", "level_index": sell_idx, "price": bot["levels"][sell_idx], "quote_amount": _to_float(bot.get("order_quote"))})
    return {"quantity": qty, "fee": fee, "profit": profit}


def update_grid_bots(price_map: dict[str, float]) -> dict[str, Any]:
    bots = load_grid_bots()
    fills: list[dict[str, Any]] = []
    for bot in bots:
        if bot.get("status") != "running":
            continue
        removed_orders = _sanitize_open_orders(bot)
        if removed_orders:
            _append_event("清理无效挂单", bot, f"自动移除 {removed_orders} 个无效或重复网格挂单。", _to_float(bot.get("last_price")))
        symbol = str(bot.get("symbol") or "").upper()
        price = _to_float(price_map.get(symbol), 0)
        if price <= 0:
            continue
        bot["last_price"] = price
        bot["last_update_time"] = _now()
        if price <= _to_float(bot.get("lower_price")) or price >= _to_float(bot.get("upper_price")):
            bot["range_warning"] = "价格已触及或越过网格边界，建议人工检查是否停止。"
        pending_orders = list(bot.get("open_orders") or [])
        bot["open_orders"] = []
        for order in pending_orders:
            side = str(order.get("side"))
            level_price = _to_float(order.get("price"))
            should_fill = (side == "buy" and price <= level_price) or (side == "sell" and price >= level_price)
            if not should_fill:
                bot["open_orders"].append(order)
                continue
            try:
                result = _fill_buy(bot, order, level_price) if side == "buy" else _fill_sell(bot, order, level_price)
            except ValueError:
                continue
            if _to_float(result.get("quantity")) <= 0:
                continue
            bot["filled_trades"] = int(bot.get("filled_trades", 0) or 0) + 1
            trade = {
                "time": _now(),
                "bot_id": bot.get("bot_id"),
                "symbol": symbol,
                "side": side,
                "price": level_price,
                "quantity": result["quantity"],
                "fee": result["fee"],
                "profit": result["profit"],
                "market_price": price,
            }
            fills.append(trade)
            _append_trade(trade)
            side_text = "买入" if side == "buy" else "卖出"
            _append_event("网格成交", bot, f"{side_text} {result['quantity']:.8f} @ {level_price:.8f}，利润 {result['profit']:+.4f} USDT。", level_price)
    save_grid_bots(bots)
    return {"fills": fills, "fill_count": len(fills)}


def _bot_equity(bot: dict[str, Any], price: float | None = None) -> float:
    last_price = _to_float(price, _to_float(bot.get("last_price"), _to_float(bot.get("initial_price"))))
    short_unrealized = (_to_float(bot.get("initial_price"), last_price) - last_price) * _to_float(bot.get("short_inventory"))
    return _to_float(bot.get("quote_balance")) + _to_float(bot.get("base_inventory")) * last_price + short_unrealized


def get_grid_summary(price_map: dict[str, float] | None = None) -> dict[str, Any]:
    price_map = price_map or {}
    bots = load_grid_bots()
    running = [bot for bot in bots if bot.get("status") == "running"]
    total_equity = 0.0
    total_investment = 0.0
    enriched: list[dict[str, Any]] = []
    for bot in bots:
        symbol = str(bot.get("symbol") or "").upper()
        price = _to_float(price_map.get(symbol), _to_float(bot.get("last_price"), _to_float(bot.get("initial_price"))))
        equity = _bot_equity(bot, price)
        investment = _to_float(bot.get("investment_usdt"))
        total_equity += equity if bot.get("status") == "running" else 0
        total_investment += investment if bot.get("status") == "running" else 0
        enriched.append(
            {
                **bot,
                "mark_price": price,
                "equity": equity,
                "floating_pnl": equity - investment,
                "floating_pnl_pct": (equity - investment) / investment * 100 if investment else 0,
                "open_buy_orders": len([order for order in bot.get("open_orders", []) if order.get("side") == "buy"]),
                "open_sell_orders": len([order for order in bot.get("open_orders", []) if order.get("side") == "sell"]),
            }
        )
    return {
        "bots": enriched,
        "running_bots": running,
        "trades": load_grid_trades(200),
        "events": load_grid_events(100),
        "total_running_bots": len(running),
        "total_investment": total_investment,
        "total_equity": total_equity,
        "total_pnl": total_equity - total_investment,
        "total_pnl_pct": (total_equity - total_investment) / total_investment * 100 if total_investment else 0,
    }

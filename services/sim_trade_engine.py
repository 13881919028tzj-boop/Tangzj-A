"""本地自动模拟交易执行器。

安全边界：
- 只做本地模拟订单、模拟持仓、模拟盈亏。
- 不连接真实账户，不读取真实资金，不调用任何 Binance 下单接口。
- 唯一信号来源是 AI交易委员会通过的模拟候选。
"""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from services import market_cache
from services.ai_committee_engine import get_committee_approved_signals
from services.structure_level_engine import build_structure_exit_plan
from services.trading_database import record_sim_close, record_sim_open


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ACCOUNT_PATH = DATA_DIR / "sim_account.json"
SETTINGS_PATH = DATA_DIR / "sim_settings.json"
POSITIONS_PATH = DATA_DIR / "sim_positions.json"
ORDERS_PATH = DATA_DIR / "sim_orders.json"
HISTORY_JSON_PATH = DATA_DIR / "sim_trade_history.json"
HISTORY_CSV_PATH = DATA_DIR / "sim_trade_history.csv"
LOG_PATH = DATA_DIR / "sim_trade_log.json"
DIAGNOSTICS_PATH = DATA_DIR / "sim_diagnostics.json"
EQUITY_JSON_PATH = DATA_DIR / "sim_equity_curve.json"
EQUITY_CSV_PATH = DATA_DIR / "sim_equity_curve.csv"
EARLY_EXIT_SHADOW_PATH = DATA_DIR / "sim_early_exit_shadow.json"
LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
POSITION_PRICE_LOG = LOG_DIR / "position_price_debug.log"


DEFAULT_SETTINGS = {
    "initial_balance": 1000.0,
    "max_position_pct": 10.0,
    "max_risk_pct": 1.0,
    "max_positions": 0,
    "max_same_symbol_positions": 0,
    "max_same_direction_positions": 0,
    "allow_long": True,
    "allow_short": True,
    "leverage": 5,
    "market_type": "futures",
    "futures_leverage_locked": True,
    "continuous_run": True,
    "ignore_loss_limits": False,
    "entry_mode": "立即按当前价模拟开仓",
    "tp1_close_pct": 50.0,
    "move_sl_to_breakeven": True,
    "daily_loss_limit_pct": 3.0,
    "max_drawdown_limit_pct": 8.0,
    "consecutive_loss_pause": 3,
    "signal_ttl_minutes": 60,
    "cooldown_minutes": 15,
    "mode": "auto",
    "min_order_margin_usdt": 0.5,
    "dynamic_stop_loss_base_pct": 1.25,
    "dynamic_stop_loss_high_risk_pct": 0.85,
    "dynamic_stop_loss_low_risk_pct": 1.55,
    "dynamic_take_profit_1_r": 1.0,
    "dynamic_take_profit_2_r": 2.4,
    "early_exit_min_seconds": 600,
    "early_exit_adverse_r": 0.5,
    "sim_fee_rate": 0.0004,
    "sim_slippage_pct": 0.0003,
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ts() -> int:
    return int(time.time())


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _positive_float(value: Any, default: float = 0.0) -> float:
    number = _to_float(value, default)
    return number if number > 0 else default


def account_initial_balance(account: dict[str, Any] | None = None, settings: dict[str, Any] | None = None) -> float:
    """Return the canonical initial simulation capital, migrating older account fields."""
    account = account or {}
    settings = settings or load_settings()
    for value in (
        account.get("initial_balance"),
        account.get("initial_equity"),
        settings.get("initial_balance"),
        account.get("max_equity"),
        account.get("equity"),
    ):
        number = _positive_float(value, 0.0)
        if number > 0:
            return number
    return 1000.0


def _read_json(path: Path, default: Any, *, rewrite_on_error: bool = True) -> Any:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            _write_json(path, default)
            return default
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception as exc:
        print(f"[模拟交易] 读取文件失败 {path.name} error={exc!r}")
        backup = path.with_suffix(path.suffix + f".broken_{int(time.time())}") if path.exists() else None
        try:
            if backup:
                path.rename(backup)
        except Exception:
            pass
        if rewrite_on_error:
            _write_json(path, default)
        return default


def _write_json(path: Path, data: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _debug_price_log(message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with POSITION_PRICE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{_now()} {message}\n")
    except Exception:
        pass


def log_sim_event(event_type: str, symbol: str = "", direction: str = "", price: float | None = None, content: str = "", reason: str = "") -> None:
    logs = _read_json(LOG_PATH, [])
    logs.insert(
        0,
        {
            "time": _now(),
            "event_type": event_type,
            "symbol": symbol,
            "direction": direction,
            "price": price,
            "content": content,
            "reason": reason,
        },
    )
    _write_json(LOG_PATH, logs[:500])


def append_sim_diagnostic(event_type: str, symbol: str = "", status: str = "", reason: str = "", details: dict[str, Any] | None = None) -> None:
    rows = _read_json(DIAGNOSTICS_PATH, [])
    rows.insert(
        0,
        {
            "time": _now(),
            "event_type": event_type,
            "symbol": symbol,
            "status": status,
            "reason": reason,
            "details": details or {},
        },
    )
    _write_json(DIAGNOSTICS_PATH, rows[:300])


def load_sim_diagnostics(limit: int = 80) -> list[dict[str, Any]]:
    rows = _read_json(DIAGNOSTICS_PATH, [])
    if not isinstance(rows, list):
        return []
    return rows[:limit]


def load_early_exit_shadow_rows(limit: int | None = None) -> list[dict[str, Any]]:
    rows = _read_json(EARLY_EXIT_SHADOW_PATH, [])
    if not isinstance(rows, list):
        return []
    return rows[:limit] if limit else rows


def save_early_exit_shadow_rows(rows: list[dict[str, Any]]) -> None:
    _write_json(EARLY_EXIT_SHADOW_PATH, rows[:1000])


def get_pending_early_exit_shadow_symbols() -> list[str]:
    symbols: list[str] = []
    now_ts = _ts()
    for row in load_early_exit_shadow_rows():
        if row.get("status") == "completed":
            continue
        if row.get("deadline_ts") and now_ts > int(row.get("deadline_ts", 0)) + 300:
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def default_account(initial_balance: float = 1000.0) -> dict[str, Any]:
    return {
        "initial_balance": initial_balance,
        "available_balance": initial_balance,
        "used_margin": 0.0,
        "equity": initial_balance,
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "total_fee_usdt": 0.0,
        "total_slippage_cost_usdt": 0.0,
        "total_pnl": 0.0,
        "return_pct": 0.0,
        "max_equity": initial_balance,
        "max_drawdown": 0.0,
        "daily_pnl": 0.0,
        "consecutive_losses": 0,
        "status": "stopped",
        "updated_at": _now(),
    }


def load_settings() -> dict[str, Any]:
    settings = _read_json(SETTINGS_PATH, DEFAULT_SETTINGS.copy())
    merged = DEFAULT_SETTINGS.copy()
    if isinstance(settings, dict):
        merged.update(settings)
    if merged.get("futures_leverage_locked", True):
        merged["market_type"] = "futures"
        merged["leverage"] = 5
    if merged.get("continuous_run", True):
        merged["max_positions"] = max(0, int(_to_float(merged.get("max_positions"), 0)))
        merged["max_same_symbol_positions"] = max(0, int(_to_float(merged.get("max_same_symbol_positions"), 0)))
        merged["max_same_direction_positions"] = max(0, int(_to_float(merged.get("max_same_direction_positions"), 0)))
        if merged.get("mode") == "observe":
            merged["mode"] = "auto"
    return merged


def save_settings(settings: dict[str, Any]) -> None:
    merged = DEFAULT_SETTINGS.copy()
    merged.update(settings or {})
    if merged.get("futures_leverage_locked", True):
        merged["market_type"] = "futures"
        merged["leverage"] = 5
    if merged.get("continuous_run", True):
        merged["max_positions"] = max(0, int(_to_float(merged.get("max_positions"), 0)))
        merged["max_same_symbol_positions"] = max(0, int(_to_float(merged.get("max_same_symbol_positions"), 0)))
        merged["max_same_direction_positions"] = max(0, int(_to_float(merged.get("max_same_direction_positions"), 0)))
        if merged.get("mode") == "observe":
            merged["mode"] = "auto"
    _write_json(SETTINGS_PATH, merged)


def init_sim_account() -> dict[str, Any]:
    settings = load_settings()
    account = _read_json(ACCOUNT_PATH, default_account(float(settings.get("initial_balance", 1000))))
    if not isinstance(account, dict):
        account = default_account(float(settings.get("initial_balance", 1000)))
        save_sim_account(account)
    initial_balance = account_initial_balance(account, settings)
    if _positive_float(account.get("initial_balance"), 0.0) <= 0:
        account["initial_balance"] = initial_balance
        account.setdefault("initial_equity", initial_balance)
        _write_json(ACCOUNT_PATH, account)
    if settings.get("continuous_run", True) and account.get("status") != "running":
        account["status"] = "running"
        account["mode"] = "auto"
        _write_json(ACCOUNT_PATH, account)
    return account


def load_sim_account() -> dict[str, Any]:
    return init_sim_account()


def save_sim_account(account: dict[str, Any]) -> None:
    account = enrich_sim_account(account)
    account["updated_at"] = _now()
    _write_json(ACCOUNT_PATH, account)
    append_equity_curve_point(account)


def reset_sim_account(initial_balance: float | None = None) -> dict[str, Any]:
    settings = load_settings()
    balance = float(initial_balance if initial_balance is not None else settings.get("initial_balance", 1000))
    settings["initial_balance"] = balance
    save_settings(settings)
    account = default_account(balance)
    _write_json(POSITIONS_PATH, [])
    _write_json(ORDERS_PATH, [])
    _write_json(HISTORY_JSON_PATH, [])
    _write_json(LOG_PATH, [])
    _write_json(EQUITY_JSON_PATH, [])
    if HISTORY_CSV_PATH.exists():
        HISTORY_CSV_PATH.unlink()
    if EQUITY_CSV_PATH.exists():
        EQUITY_CSV_PATH.unlink()
    save_sim_account(account)
    log_sim_event("模拟账户重置", content=f"初始资金重置为 {balance:.2f} USDT")
    return account


def clear_sim_history() -> None:
    """清空模拟交易历史，保留账户、订单和持仓。"""
    _write_json(HISTORY_JSON_PATH, [])
    if HISTORY_CSV_PATH.exists():
        HISTORY_CSV_PATH.unlink()
    log_sim_event("清空模拟历史", content="用户清空模拟交易历史。")


def _positions_last_good_path() -> Path:
    return POSITIONS_PATH.with_suffix(POSITIONS_PATH.suffix + ".last_good")


def load_positions() -> list[dict[str, Any]]:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not POSITIONS_PATH.exists():
            _write_json(POSITIONS_PATH, [])
            return []
        data = json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"[模拟交易] 读取文件失败 {POSITIONS_PATH.name} error={exc!r}")
        broken = POSITIONS_PATH.with_suffix(POSITIONS_PATH.suffix + f".broken_{int(time.time())}")
        try:
            if POSITIONS_PATH.exists():
                POSITIONS_PATH.rename(broken)
        except Exception:
            pass
        try:
            backup = json.loads(_positions_last_good_path().read_text(encoding="utf-8"))
            if isinstance(backup, list):
                _write_json(POSITIONS_PATH, backup)
                append_sim_diagnostic(
                    "模拟持仓文件自动恢复",
                    status="recovered",
                    reason="sim_positions.json 损坏，已从 last_good 备份恢复，避免账户保证金被错误清零。",
                    details={"broken_file": str(broken), "restored_positions": len(backup)},
                )
                return backup
        except Exception as backup_exc:
            append_sim_diagnostic(
                "模拟持仓文件恢复失败",
                status="failed",
                reason="sim_positions.json 损坏且 last_good 备份不可用，已返回空持仓但不覆盖账户。",
                details={"error": repr(exc), "backup_error": repr(backup_exc), "broken_file": str(broken)},
            )
        return []


def save_positions(positions: list[dict[str, Any]]) -> None:
    _write_json(POSITIONS_PATH, positions)
    _write_json(_positions_last_good_path(), positions)


def load_orders() -> list[dict[str, Any]]:
    data = _read_json(ORDERS_PATH, [])
    return data if isinstance(data, list) else []


def save_orders(orders: list[dict[str, Any]]) -> None:
    _write_json(ORDERS_PATH, orders)


def get_open_positions() -> list[dict[str, Any]]:
    return [p for p in load_positions() if p.get("status") in {"open", "partially_closed"}]


def get_pending_orders() -> list[dict[str, Any]]:
    return [o for o in load_orders() if o.get("status") == "pending"]


def get_closed_trades() -> list[dict[str, Any]]:
    return load_sim_trade_history()


def calculate_used_margin(positions: list[dict[str, Any]] | None = None) -> float:
    return sum(_to_float(p.get("margin_usdt"), 0) for p in (positions or get_open_positions()))


def calculate_total_exposure(positions: list[dict[str, Any]] | None = None) -> float:
    return sum(_to_float(p.get("notional_usdt"), 0) for p in (positions or get_open_positions()))


def calculate_long_short_exposure(positions: list[dict[str, Any]] | None = None) -> tuple[float, float]:
    long_exposure = 0.0
    short_exposure = 0.0
    for position in positions or get_open_positions():
        notional = _to_float(position.get("notional_usdt"), 0)
        if position.get("direction") == "short":
            short_exposure += notional
        else:
            long_exposure += notional
    return long_exposure, short_exposure


def calculate_account_drawdown(account: dict[str, Any]) -> tuple[float, float]:
    equity = _to_float(account.get("equity"), 0)
    max_equity = max(_to_float(account.get("max_equity"), equity) or 0.0, equity)
    current = (max_equity - equity) / max_equity * 100 if max_equity else 0.0
    return current, max(_to_float(account.get("max_drawdown"), 0), current)


def calculate_available_balance(account: dict[str, Any], positions: list[dict[str, Any]] | None = None) -> float:
    used = calculate_used_margin(positions)
    equity = _to_float(account.get("equity"), 0)
    return max(0.0, equity - used)


def enrich_sim_account(account: dict[str, Any] | None, positions: list[dict[str, Any]] | None = None, orders: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    account = dict(account or default_account())
    positions = positions if positions is not None else get_open_positions()
    orders = orders if orders is not None else get_pending_orders()
    initial_balance = account_initial_balance(account)
    account["initial_balance"] = initial_balance
    account.setdefault("initial_equity", initial_balance)
    unrealized = sum(_to_float(p.get("unrealized_pnl"), 0) for p in positions)
    used_margin = calculate_used_margin(positions)
    long_exposure, short_exposure = calculate_long_short_exposure(positions)
    account["used_margin"] = used_margin
    account["total_exposure"] = long_exposure + short_exposure
    account["long_exposure"] = long_exposure
    account["short_exposure"] = short_exposure
    account["unrealized_pnl"] = unrealized
    account["total_pnl"] = _to_float(account.get("realized_pnl"), 0) + unrealized
    account["equity"] = _to_float(account.get("available_balance"), 0) + used_margin + unrealized
    account["max_equity"] = max(_to_float(account.get("max_equity"), initial_balance) or 0.0, _to_float(account.get("equity"), 0) or 0.0)
    account["current_drawdown"], account["max_drawdown"] = calculate_account_drawdown(account)
    account["return_pct"] = (account["equity"] - initial_balance) / initial_balance * 100 if initial_balance else 0
    account["open_position_count"] = len(positions)
    account["pending_order_count"] = len(orders)
    account["risk_status"] = "locked" if account.get("status") == "stopped" else "warning" if account["current_drawdown"] >= 5 or account["max_drawdown"] >= 8 else "normal"
    account["last_update"] = _now()
    return account


def load_sim_trade_history() -> list[dict[str, Any]]:
    data = _read_json(HISTORY_JSON_PATH, [])
    return data if isinstance(data, list) else []


def save_sim_trade_history(history: list[dict[str, Any]]) -> None:
    _write_json(HISTORY_JSON_PATH, history[:1000])
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if history:
        keys = sorted({key for row in history for key in row.keys()})
        with HISTORY_CSV_PATH.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=keys)
            writer.writeheader()
            writer.writerows(history)


def get_sim_equity_curve(limit: int | None = None) -> list[dict[str, Any]]:
    data = _read_json(EQUITY_JSON_PATH, [])
    rows = data if isinstance(data, list) else []
    return rows[-limit:] if limit else rows


def save_sim_equity_curve(rows: list[dict[str, Any]]) -> None:
    rows = rows[-2000:]
    _write_json(EQUITY_JSON_PATH, rows)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if rows:
        keys = ["time", "equity", "available_balance", "used_margin", "unrealized_pnl", "realized_pnl", "total_pnl", "current_drawdown", "max_drawdown"]
        with EQUITY_CSV_PATH.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)


def append_equity_curve_point(account: dict[str, Any]) -> None:
    rows = get_sim_equity_curve()
    point = {
        "time": _now(),
        "equity": round(_to_float(account.get("equity"), 0), 8),
        "available_balance": round(_to_float(account.get("available_balance"), 0), 8),
        "used_margin": round(_to_float(account.get("used_margin"), 0), 8),
        "unrealized_pnl": round(_to_float(account.get("unrealized_pnl"), 0), 8),
        "realized_pnl": round(_to_float(account.get("realized_pnl"), 0), 8),
        "total_pnl": round(_to_float(account.get("total_pnl"), 0), 8),
        "current_drawdown": round(_to_float(account.get("current_drawdown"), 0), 8),
        "max_drawdown": round(_to_float(account.get("max_drawdown"), 0), 8),
    }
    last = rows[-1] if rows else {}
    if last and last.get("time") == point["time"] and last.get("equity") == point["equity"] and last.get("total_pnl") == point["total_pnl"]:
        return
    rows.append(point)
    save_sim_equity_curve(rows)


def load_sim_events(limit: int = 50) -> list[dict[str, Any]]:
    data = _read_json(LOG_PATH, [])
    return (data if isinstance(data, list) else [])[:limit]


def get_committee_signals_for_simulation(limit: int = 20) -> list[dict[str, Any]]:
    return get_committee_approved_signals(limit)


def _rr_value(signal: dict[str, Any]) -> float:
    rr = signal.get("risk_reward_ratio")
    if isinstance(rr, str) and ":" in rr:
        return _to_float(rr.split(":", 1)[1])
    return _to_float(rr)


def _position_pct(signal: dict[str, Any], settings: dict[str, Any]) -> float:
    text = str(signal.get("position_suggestion", "0%"))
    if "1%-3%" in text:
        pct = 2.0
    elif "3%-5%" in text:
        pct = 4.0
    elif "5%-10%" in text:
        pct = 7.0
    elif "10%-15%" in text:
        pct = 10.0
    else:
        pct = 0.0
    risk_score = _to_float(signal.get("risk_score"), 50)
    if risk_score >= 70:
        pct = min(pct, 3.0)
    simulation_score = _to_float(signal.get("simulation_score"), 70)
    liquidity_quality = _to_float(signal.get("liquidity_quality_score"), 70)
    portfolio_fit = _to_float(signal.get("portfolio_fit_score"), 70)
    historical_tradability = _to_float(signal.get("historical_tradability_score"), 60)
    signal_freshness = _to_float(signal.get("signal_freshness_score"), 60)
    if simulation_score < 66:
        pct = min(pct, 2.0)
    if liquidity_quality < 55:
        pct = min(pct, 2.0)
    if portfolio_fit < 50:
        pct = min(pct, 2.0)
    if historical_tradability < 45:
        pct = min(pct, 2.0)
    if signal_freshness < 45:
        pct = min(pct, 2.0)
    return min(pct * 4, 100.0)


def _entry_zone(signal: dict[str, Any], current_price: float) -> tuple[float, float]:
    entry = signal.get("entry_zone") or {}
    low = _to_float(entry.get("low"), 0)
    high = _to_float(entry.get("high"), 0)
    if low <= 0 or high <= 0:
        return current_price, current_price
    return min(low, high), max(low, high)


def _price_field(data: dict[str, Any] | None) -> float:
    return _to_float((data or {}).get("price"), 0)


def _dynamic_exit_prices(price: float, direction: str, risk_score: float, settings: dict[str, Any]) -> tuple[float, float, float, float]:
    """Build a conservative two-target plan from entry price and risk score."""
    base_pct = _to_float(settings.get("dynamic_stop_loss_base_pct"), 1.25) / 100
    high_risk_pct = _to_float(settings.get("dynamic_stop_loss_high_risk_pct"), 0.85) / 100
    low_risk_pct = _to_float(settings.get("dynamic_stop_loss_low_risk_pct"), 1.55) / 100
    if risk_score >= 75:
        stop_pct = high_risk_pct
    elif risk_score <= 40:
        stop_pct = low_risk_pct
    else:
        stop_pct = base_pct
    tp1_r = _to_float(settings.get("dynamic_take_profit_1_r"), 1.0)
    tp2_r = _to_float(settings.get("dynamic_take_profit_2_r"), 2.4)
    if direction == "short":
        return price * (1 + stop_pct), price * (1 - stop_pct * tp1_r), price * (1 - stop_pct * tp2_r), stop_pct
    return price * (1 - stop_pct), price * (1 + stop_pct * tp1_r), price * (1 + stop_pct * tp2_r), stop_pct


def _fee_rate(settings: dict[str, Any] | None = None) -> float:
    settings = settings or load_settings()
    return max(0.0, min(0.01, _to_float(settings.get("sim_fee_rate"), DEFAULT_SETTINGS["sim_fee_rate"])))


def _slippage_pct(settings: dict[str, Any] | None = None) -> float:
    settings = settings or load_settings()
    return max(0.0, min(0.02, _to_float(settings.get("sim_slippage_pct"), DEFAULT_SETTINGS["sim_slippage_pct"])))


def apply_sim_slippage(price: float, direction: str, side: str, settings: dict[str, Any] | None = None) -> float:
    """Return the worse executable price after simulated market-order slippage."""
    price = _to_float(price, 0)
    if price <= 0:
        return 0.0
    slip = _slippage_pct(settings)
    direction = str(direction or "long")
    side = str(side or "entry")
    if side == "entry":
        return price * (1 + slip) if direction != "short" else price * (1 - slip)
    return price * (1 - slip) if direction != "short" else price * (1 + slip)


def calculate_sim_fee(notional: float, settings: dict[str, Any] | None = None) -> float:
    return abs(_to_float(notional, 0)) * _fee_rate(settings)


def _slippage_cost(reference_price: float, execution_price: float, quantity: float) -> float:
    return abs(_to_float(execution_price, 0) - _to_float(reference_price, 0)) * abs(_to_float(quantity, 0))


def _valid_exit_plan(direction: str, current_price: float, stop: float, tp1: float, tp2: float) -> bool:
    if current_price <= 0 or stop <= 0 or tp1 <= 0 or tp2 <= 0:
        return False
    if direction == "short":
        return stop > current_price and tp1 < current_price and tp2 < current_price and tp2 < tp1
    return stop < current_price and tp1 > current_price and tp2 > current_price and tp2 > tp1


def _normalize_signal_exit_plan(signal: dict[str, Any], current_price: float, settings: dict[str, Any]) -> tuple[float, float, float, float, str, dict[str, Any]]:
    direction = str(signal.get("direction", "long"))
    risk_score = _to_float(signal.get("risk_score"), _to_float(signal.get("committee_risk_score"), 50))
    dynamic_stop, dynamic_tp1, dynamic_tp2, stop_pct = _dynamic_exit_prices(current_price, direction, risk_score, settings)
    symbol = str(signal.get("symbol") or "").upper().strip()
    interval = market_cache.get_kline_interval()
    rows = market_cache.get_klines(symbol, interval) if symbol else []
    structure_plan = build_structure_exit_plan(symbol, direction, current_price, rows, risk_score)
    if structure_plan.get("valid"):
        stop = _to_float(structure_plan.get("stop_loss"))
        tp1 = _to_float(structure_plan.get("take_profit_1"))
        tp2 = _to_float(structure_plan.get("take_profit_2"))
        if _valid_exit_plan(direction, current_price, stop, tp1, tp2):
            return stop, tp1, tp2, _to_float(structure_plan.get("stop_pct"), stop_pct), "structure_levels", structure_plan

    stop = _price_field(signal.get("stop_loss")) or dynamic_stop
    tp1 = _price_field(signal.get("take_profit_1")) or dynamic_tp1
    tp2 = _price_field(signal.get("take_profit_2")) or dynamic_tp2
    if _valid_exit_plan(direction, current_price, stop, tp1, tp2):
        signal_plan = {
            "valid": True,
            "source": "signal_exit_plan",
            "structure_fallback_reason": structure_plan.get("reason"),
            "stop_loss": stop,
            "take_profit_1": tp1,
            "take_profit_2": tp2,
        }
        return stop, tp1, tp2, abs(current_price - stop) / current_price if current_price else stop_pct, "signal_exit_plan", signal_plan
    dynamic_plan = {
        "valid": True,
        "source": "dynamic_r_fallback",
        "structure_fallback_reason": structure_plan.get("reason"),
        "stop_loss": dynamic_stop,
        "take_profit_1": dynamic_tp1,
        "take_profit_2": dynamic_tp2,
    }
    return dynamic_stop, dynamic_tp1, dynamic_tp2, stop_pct, "dynamic_r_fallback", dynamic_plan


def validate_signal_for_simulation(signal: dict[str, Any], current_prices: dict[str, float] | None = None) -> tuple[bool, list[str]]:
    settings = load_settings()
    account = load_sim_account()
    positions = [p for p in load_positions() if p.get("status") in {"open", "partially_closed"}]
    reasons: list[str] = []
    symbol = str(signal.get("symbol", "")).upper()
    action = str(signal.get("action", ""))
    direction = str(signal.get("direction", ""))
    if action not in {"轻仓试多", "顺势做多", "轻仓试空", "顺势做空", "高风险轻仓模拟", "高风险轻仓试空", "顺势交易候选"}:
        reasons.append("委员会最终动作不属于可模拟开仓动作。")
    if str(signal.get("trade_permission", "")) not in {"approved", "cautious", "candidate", "simulation_or_approval", ""}:
        reasons.append("委员会交易许可未通过。")
    if signal.get("approved_for_simulation") is False:
        reasons.append("委员会未批准进入模拟候选。")
    if "tradable_now" in signal and signal.get("tradable_now") is not True:
        reasons.append("专业交易预审未通过：当前不是可立即交易位置。")
    if signal.get("action_gate") and signal.get("action_gate") != "open_now":
        reasons.append("专业交易预审要求等待确认，不允许立即开仓。")
    if signal.get("veto_members"):
        reasons.append("风险委员或其他委员已触发否决。")
    if _to_float(signal.get("committee_confidence"), 0) < 60:
        reasons.append("委员会置信度不足。")
    if _rr_value(signal) and _rr_value(signal) < 1.2:
        reasons.append("风险收益比低于1:1.2。")
    if "simulation_score" in signal and _to_float(signal.get("simulation_score"), 0) < 60:
        reasons.append("模拟适配分低于60，暂不创建模拟订单。")
    if "base_quality_score" in signal and _to_float(signal.get("base_quality_score"), 0) < 50:
        reasons.append("基础质量分低于50，暂不创建模拟订单。")
    if "liquidity_quality_score" in signal and _to_float(signal.get("liquidity_quality_score"), 0) < 40:
        reasons.append("流动性质量低于40，滑点风险过高。")
    if "portfolio_fit_score" in signal and _to_float(signal.get("portfolio_fit_score"), 0) < 25:
        reasons.append("组合适配分低于25，当前模拟敞口过于拥挤。")
    if account.get("status") != "running":
        reasons.append("模拟交易未处于运行状态。")
    if settings.get("mode") == "observe":
        reasons.append("当前为仅观察模式，不创建新订单。")
    if direction == "long" and not settings.get("allow_long", True):
        reasons.append("参数设置不允许模拟做多。")
    if direction == "short" and not settings.get("allow_short", True):
        reasons.append("参数设置不允许模拟做空。")
    max_positions = int(_to_float(settings.get("max_positions"), 0))
    max_same_symbol = int(_to_float(settings.get("max_same_symbol_positions"), 0))
    max_same_direction = int(_to_float(settings.get("max_same_direction_positions"), 0))
    if max_positions > 0 and len(positions) >= max_positions:
        reasons.append("当前持仓数量已达到上限。")
    same_symbol = [p for p in positions if p.get("symbol") == symbol]
    if max_same_symbol > 0 and len(same_symbol) >= max_same_symbol:
        reasons.append(f"当前已持有 {symbol} 模拟仓位。")
    same_direction = [
        p
        for p in positions
        if p.get("direction") == direction and max(abs(_to_float(p.get("notional_usdt"), 0)), abs(_to_float(p.get("margin_usdt"), 0))) >= 1
    ]
    if direction in {"long", "short"} and max_same_direction > 0 and len(same_direction) >= max_same_direction:
        reasons.append(f"当前 {direction} 方向持仓数量已达到上限。")
    if _to_float(account.get("available_balance"), 0) <= 0:
        reasons.append("模拟账户可用余额不足，无法继续开仓。")
    ignore_loss_limits = bool(settings.get("ignore_loss_limits", False))
    if not ignore_loss_limits:
        if _to_float(account.get("daily_pnl"), 0) <= -_to_float(account.get("initial_balance"), 1000) * _to_float(settings.get("daily_loss_limit_pct"), 3) / 100:
            reasons.append("已达到每日最大亏损限制。")
        if _to_float(account.get("max_drawdown"), 0) >= _to_float(settings.get("max_drawdown_limit_pct"), 8):
            reasons.append("已达到最大回撤限制。")
    if current_prices is not None and _to_float(current_prices.get(symbol), 0) <= 0:
        reasons.append("当前价格不可用，无法创建模拟订单。")
    return not reasons, reasons


def create_pending_sim_order(signal: dict[str, Any], current_price: float) -> dict[str, Any] | None:
    current_price = _to_float(current_price, 0)
    settings = load_settings()
    account = load_sim_account()
    current_prices = {str(signal.get("symbol", "")).upper(): current_price}
    ok, reasons = validate_signal_for_simulation(signal, current_prices)
    if not ok:
        log_sim_event("拒绝创建模拟订单", signal.get("symbol", ""), signal.get("direction", ""), current_price, reason="；".join(reasons))
        return None
    symbol = str(signal.get("symbol", "")).upper()
    direction = str(signal.get("direction", "neutral"))
    low, high = _entry_zone(signal, current_price)
    pct = _position_pct(signal, settings)
    if pct <= 0:
        pct = min(2.0, float(settings.get("max_position_pct", 10)))
    available = _to_float(account.get("available_balance"), 0)
    min_margin = _to_float(settings.get("min_order_margin_usdt"), 0.5)
    if available < min_margin:
        log_sim_event("拒绝创建模拟订单", symbol, direction, current_price, reason=f"可用余额低于最小模拟保证金 {min_margin:.2f} USDT。")
        return None
    leverage = 5 if settings.get("futures_leverage_locked", True) else max(1, min(20, int(settings.get("leverage", 5))))
    max_affordable_margin = available / (1 + leverage * _fee_rate(settings))
    if max_affordable_margin < min_margin:
        log_sim_event("拒绝创建模拟订单", symbol, direction, current_price, reason="可用余额不足以覆盖最小保证金和开仓手续费。")
        return None
    margin = min(max_affordable_margin, max(min_margin, _to_float(account.get("equity"), 0) * pct / 100))
    notional = margin * leverage
    stop, tp1, tp2, dynamic_stop_pct, exit_plan_source, exit_plan_detail = _normalize_signal_exit_plan(signal, current_price, settings)
    order = {
        "order_id": f"sim_order_{int(time.time() * 1000)}",
        "symbol": symbol,
        "direction": direction,
        "action": signal.get("action"),
        "status": "pending",
        "entry_zone_low": low,
        "entry_zone_high": high,
        "planned_entry_price": current_price if low <= current_price <= high else (low + high) / 2,
        "stop_loss": stop,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "dynamic_stop_loss_pct": round(dynamic_stop_pct * 100, 4),
        "dynamic_take_profit_1_r": settings.get("dynamic_take_profit_1_r", 1.0),
        "dynamic_take_profit_2_r": settings.get("dynamic_take_profit_2_r", 2.4),
        "exit_plan_source": exit_plan_source,
        "structure_exit_plan": exit_plan_detail,
        "position_pct": signal.get("position_suggestion", "0%"),
        "notional_usdt": notional,
        "margin_usdt": margin,
        "quantity": notional / current_price if current_price > 0 else 0,
        "sim_fee_rate": _fee_rate(settings),
        "sim_slippage_pct": _slippage_pct(settings),
        "estimated_entry_fee": calculate_sim_fee(notional, settings),
        "estimated_entry_slippage_cost": _slippage_cost(current_price, apply_sim_slippage(current_price, direction, "entry", settings), notional / current_price if current_price > 0 else 0),
        "leverage": leverage,
        "market_type": settings.get("market_type", "futures"),
        "contract_type": "USDT_PERPETUAL" if settings.get("market_type") == "futures" else "SPOT_SIM",
        "created_time": _now(),
        "created_ts": _ts(),
        "expired_time": datetime.fromtimestamp(_ts() + int(settings.get("signal_ttl_minutes", 60)) * 60).strftime("%Y-%m-%d %H:%M:%S"),
        "expired_ts": _ts() + int(settings.get("signal_ttl_minutes", 60)) * 60,
        "source": "AI交易委员会",
        "committee_snapshot": signal,
        "local_strategy_snapshot": {},
        "reason": signal.get("chairman_summary", ""),
    }
    mode = str(settings.get("entry_mode", "等待入场区"))
    if mode == "立即按当前价模拟开仓" or low <= current_price <= high:
        order["status"] = "filled"
        open_sim_position(order, current_price)
        log_sim_event("模拟开仓", symbol, direction, current_price, content=f"由委员会信号触发，仓位 {margin:.2f} USDT，动态止损 {dynamic_stop_pct * 100:.2f}%。")
    else:
        orders = load_orders()
        orders.insert(0, order)
        save_orders(orders)
        log_sim_event("创建待触发订单", symbol, direction, current_price, content=f"等待入场区 {low:.8f}-{high:.8f}")
    return order


def get_current_price(symbol: str, current_prices: dict[str, float] | None = None) -> float:
    return _to_float((current_prices or {}).get(symbol), 0)


def open_sim_position(order: dict[str, Any], price: float) -> dict[str, Any]:
    settings = load_settings()
    account = load_sim_account()
    margin = _to_float(order.get("margin_usdt"), 0)
    direction = str(order.get("direction", "long"))
    execution_price = apply_sim_slippage(price, direction, "entry", settings)
    notional = _to_float(order.get("notional_usdt"), 0)
    entry_fee = calculate_sim_fee(notional, settings)
    if margin + entry_fee > _to_float(account.get("available_balance"), 0):
        log_sim_event("拒绝模拟开仓", order.get("symbol", ""), order.get("direction", ""), price, reason="可用余额不足以覆盖保证金和开仓手续费。")
        return {}
    quantity = notional / execution_price if execution_price > 0 else 0
    entry_slippage_cost = _slippage_cost(price, execution_price, quantity)
    position = {
        "position_id": f"sim_pos_{int(time.time() * 1000)}",
        "symbol": order.get("symbol"),
        "direction": direction,
        "status": "open",
        "entry_price": execution_price,
        "entry_reference_price": price,
        "current_price": price,
        "quantity": quantity,
        "margin_usdt": margin,
        "notional_usdt": notional,
        "leverage": int(order.get("leverage", 1)),
        "market_type": order.get("market_type", "futures"),
        "contract_type": order.get("contract_type", "USDT_PERPETUAL"),
        "open_time": _now(),
        "open_ts": _ts(),
        "update_time": _now(),
        "last_price_update": _now(),
        "price_status": "live",
        "stop_loss": _to_float(order.get("stop_loss"), 0),
        "take_profit_1": _to_float(order.get("take_profit_1"), 0),
        "take_profit_2": _to_float(order.get("take_profit_2"), 0),
        "exit_plan_source": order.get("exit_plan_source", "dynamic_r_fallback"),
        "structure_exit_plan": order.get("structure_exit_plan") or {},
        "tp1_hit": False,
        "tp2_hit": False,
        "moved_stop_to_breakeven": False,
        "unrealized_pnl": 0.0,
        "unrealized_pnl_pct": 0.0,
        "realized_pnl": 0.0,
        "entry_fee": entry_fee,
        "exit_fee": 0.0,
        "total_fee": entry_fee,
        "entry_slippage_cost": entry_slippage_cost,
        "exit_slippage_cost": 0.0,
        "total_slippage_cost": entry_slippage_cost,
        "sim_fee_rate": _fee_rate(settings),
        "sim_slippage_pct": _slippage_pct(settings),
        "risk_reward_ratio": order.get("committee_snapshot", {}).get("risk_reward_ratio"),
        "committee_confidence": order.get("committee_snapshot", {}).get("committee_confidence"),
        "committee_risk_score": order.get("committee_snapshot", {}).get("risk_score"),
        "committee_action": order.get("committee_snapshot", {}).get("action"),
        "local_strategy_action": "",
        "invalid_condition": order.get("committee_snapshot", {}).get("invalid_condition"),
        "open_reason": order.get("reason"),
        "close_reason": "",
        "committee_snapshot": order.get("committee_snapshot"),
        "local_strategy_snapshot": order.get("local_strategy_snapshot"),
    }
    positions = load_positions()
    positions.insert(0, position)
    save_positions(positions)
    account["available_balance"] = _to_float(account.get("available_balance"), 0) - margin - entry_fee
    account["used_margin"] = _to_float(account.get("used_margin"), 0) + margin
    account["realized_pnl"] = _to_float(account.get("realized_pnl"), 0) - entry_fee
    account["daily_pnl"] = _to_float(account.get("daily_pnl"), 0) - entry_fee
    account["total_fee_usdt"] = _to_float(account.get("total_fee_usdt"), 0) + entry_fee
    account["total_slippage_cost_usdt"] = _to_float(account.get("total_slippage_cost_usdt"), 0) + entry_slippage_cost
    account["total_pnl"] = _to_float(account.get("realized_pnl"), 0) + _to_float(account.get("unrealized_pnl"), 0)
    account["equity"] = _to_float(account.get("available_balance"), 0) + _to_float(account.get("used_margin"), 0) + _to_float(account.get("unrealized_pnl"), 0)
    save_sim_account(account)
    try:
        record_sim_open(position)
    except Exception as exc:
        log_sim_event("模拟交易数据库写入失败", position.get("symbol", ""), position.get("direction", ""), price, reason=f"开仓记录未写入SQLite：{exc!r}")
    return position


def calculate_unrealized_pnl(position: dict[str, Any], price: float) -> float:
    entry = _to_float(position.get("entry_price"), 0)
    qty = _to_float(position.get("quantity"), 0)
    if entry <= 0 or qty <= 0:
        return 0.0
    if position.get("direction") == "short":
        return (entry - price) * qty
    return (price - entry) * qty


def calculate_position_r_multiple(position: dict[str, Any], current_price: float | None = None) -> float | None:
    entry = _to_float(position.get("entry_price"), 0)
    price = _to_float(current_price if current_price is not None else position.get("current_price"), 0)
    stop = _to_float(position.get("stop_loss"), 0)
    if entry <= 0 or price <= 0 or stop <= 0:
        return None
    if position.get("direction") == "short":
        risk_per_unit = stop - entry
        reward_per_unit = entry - price
    else:
        risk_per_unit = entry - stop
        reward_per_unit = price - entry
    if risk_per_unit <= 0:
        return None
    return reward_per_unit / risk_per_unit


def _shadow_pnl(entry_price: float, price: float, quantity: float, direction: str) -> float:
    if entry_price <= 0 or price <= 0 or quantity <= 0:
        return 0.0
    if direction == "short":
        return (entry_price - price) * quantity
    return (price - entry_price) * quantity


def _shadow_result(entry_price: float, price: float, direction: str) -> str:
    if price <= 0 or entry_price <= 0:
        return "unknown"
    if direction == "short":
        if price < entry_price:
            return "win"
        if price > entry_price:
            return "loss"
        return "flat"
    if price > entry_price:
        return "win"
    if price < entry_price:
        return "loss"
    return "flat"


def create_early_exit_shadow(position: dict[str, Any], exit_price: float, r_multiple: float | None = None) -> None:
    """Track what would have happened 30/60 minutes after early adverse exits."""
    symbol = str(position.get("symbol") or "").upper().strip()
    position_id = str(position.get("position_id") or "")
    if not symbol or not position_id:
        return
    rows = load_early_exit_shadow_rows()
    if any(row.get("position_id") == position_id for row in rows):
        return
    close_ts = _ts()
    entry_price = _to_float(position.get("entry_price"), 0)
    quantity = _to_float(position.get("quantity"), 0)
    close_pnl = _shadow_pnl(entry_price, exit_price, quantity, str(position.get("direction")))
    rows.insert(
        0,
        {
            "position_id": position_id,
            "symbol": symbol,
            "direction": position.get("direction"),
            "status": "tracking",
            "open_time": position.get("open_time"),
            "open_ts": position.get("open_ts"),
            "close_time": _now(),
            "close_ts": close_ts,
            "deadline_ts": close_ts + 3900,
            "entry_price": entry_price,
            "early_exit_price": exit_price,
            "quantity": quantity,
            "notional_usdt": position.get("notional_usdt"),
            "leverage": position.get("leverage"),
            "early_exit_pnl": close_pnl,
            "early_exit_r_multiple": r_multiple if r_multiple is not None else calculate_position_r_multiple(position, exit_price),
            "check_30m_ts": close_ts + 1800,
            "check_60m_ts": close_ts + 3600,
            "check_30m": {},
            "check_60m": {},
            "created_at": _now(),
            "updated_at": _now(),
        },
    )
    save_early_exit_shadow_rows(rows)
    append_sim_diagnostic(
        "反向复核影子跟踪",
        symbol,
        "tracking",
        "已记录反向复核退出后的30/60分钟观察任务。",
        {"position_id": position_id, "early_exit_price": exit_price, "early_exit_pnl": close_pnl},
    )


def update_early_exit_shadow_tracks(current_prices: dict[str, float], price_statuses: dict[str, str] | None = None) -> None:
    rows = load_early_exit_shadow_rows()
    if not rows:
        return
    now_ts = _ts()
    changed = False
    for row in rows:
        if row.get("status") == "completed":
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        price = get_current_price(symbol, current_prices)
        if price <= 0:
            if row.get("deadline_ts") and now_ts > int(row.get("deadline_ts", 0)):
                row["status"] = "expired"
                row["updated_at"] = _now()
                changed = True
            continue
        entry_price = _to_float(row.get("entry_price"), 0)
        quantity = _to_float(row.get("quantity"), 0)
        direction = str(row.get("direction") or "")
        status = str((price_statuses or {}).get(symbol) or "live")
        for label, target_key in (("30m", "check_30m_ts"), ("60m", "check_60m_ts")):
            result_key = f"check_{label}"
            if row.get(result_key):
                continue
            if now_ts < int(row.get(target_key, 0) or 0):
                continue
            pnl = _shadow_pnl(entry_price, price, quantity, direction)
            row[result_key] = {
                "time": _now(),
                "ts": now_ts,
                "price": price,
                "price_status": status,
                "pnl": pnl,
                "pnl_delta_vs_early_exit": pnl - _to_float(row.get("early_exit_pnl"), 0),
                "result": _shadow_result(entry_price, price, direction),
            }
            row["updated_at"] = _now()
            changed = True
            append_sim_diagnostic(
                "反向复核影子结果",
                symbol,
                str(row[result_key]["result"]),
                f"反向复核退出后{label}观察完成。",
                {
                    "position_id": row.get("position_id"),
                    "price": price,
                    "pnl": pnl,
                    "pnl_delta_vs_early_exit": row[result_key]["pnl_delta_vs_early_exit"],
                    "early_exit_pnl": row.get("early_exit_pnl"),
                },
            )
        if row.get("check_30m") and row.get("check_60m"):
            row["status"] = "completed"
            row["completed_at"] = _now()
            changed = True
        elif row.get("deadline_ts") and now_ts > int(row.get("deadline_ts", 0)):
            row["status"] = "expired"
            row["updated_at"] = _now()
            changed = True
    if changed:
        save_early_exit_shadow_rows(rows)


def calculate_position_holding_time(position: dict[str, Any]) -> dict[str, Any]:
    seconds = max(0, _ts() - int(position.get("open_ts", _ts())))
    minutes = seconds // 60
    if minutes < 60:
        text = f"{minutes}分钟"
    else:
        text = f"{minutes // 60}小时{minutes % 60}分钟"
    return {"seconds": seconds, "text": text}


def calculate_position_pnl(position: dict[str, Any], current_price: float) -> dict[str, Any]:
    settings = load_settings()
    direction = str(position.get("direction"))
    exit_price = apply_sim_slippage(current_price, direction, "exit", settings)
    qty = _to_float(position.get("quantity"), 0)
    close_notional = abs(exit_price * qty)
    gross_pnl = calculate_realized_pnl(_to_float(position.get("entry_price"), 0), exit_price, qty, direction)
    estimated_exit_fee = calculate_sim_fee(close_notional, settings)
    pnl = gross_pnl - estimated_exit_fee
    margin = _to_float(position.get("margin_usdt"), 0)
    notional = _to_float(position.get("notional_usdt"), 0)
    r_multiple = calculate_position_r_multiple(position, exit_price)
    return {
        "unrealized_pnl": pnl,
        "unrealized_gross_pnl": gross_pnl,
        "unrealized_pnl_pct": pnl / margin * 100 if margin else 0,
        "unrealized_pnl_pct_notional": pnl / notional * 100 if notional else 0,
        "r_multiple": r_multiple,
        "estimated_exit_price": exit_price,
        "estimated_exit_fee": estimated_exit_fee,
        "estimated_exit_slippage_cost": _slippage_cost(current_price, exit_price, qty),
    }


def calculate_realized_pnl(entry_price: float, exit_price: float, quantity: float, direction: str) -> float:
    if direction == "short":
        return (entry_price - exit_price) * quantity
    return (exit_price - entry_price) * quantity


def check_stop_loss(position: dict[str, Any], price: float) -> bool:
    """检查是否触发模拟止损。"""
    stop = _to_float(position.get("stop_loss"), 0)
    direction = str(position.get("direction"))
    return stop > 0 and ((direction == "long" and price <= stop) or (direction == "short" and price >= stop))


def check_take_profit(position: dict[str, Any], price: float) -> str | None:
    """检查模拟止盈层级。"""
    direction = str(position.get("direction"))
    tp1 = _to_float(position.get("take_profit_1"), 0)
    tp2 = _to_float(position.get("take_profit_2"), 0)
    if tp2 > 0 and ((direction == "long" and price >= tp2) or (direction == "short" and price <= tp2)):
        return "tp2"
    if not position.get("tp1_hit") and tp1 > 0 and ((direction == "long" and price >= tp1) or (direction == "short" and price <= tp1)):
        return "tp1"
    return None


def check_signal_invalid(position: dict[str, Any]) -> tuple[bool, str]:
    """预留本地策略/委员会信号失效检查。"""
    snapshot = position.get("committee_snapshot") or {}
    if snapshot.get("trade_permission") == "blocked":
        return True, "委员会信号已变为禁止开仓。"
    if snapshot.get("veto_members"):
        return True, "委员会存在否决委员。"
    return False, ""


def check_committee_reversal(position: dict[str, Any]) -> tuple[bool, str]:
    """预留委员会方向反转检查，本版本仅记录接口。"""
    return False, ""


def close_sim_position(position_id: str, reason: str, price: float, ratio: float = 1.0) -> dict[str, Any] | None:
    positions = load_positions()
    history = load_sim_trade_history()
    account = load_sim_account()
    ratio = max(0.0, min(1.0, ratio))
    for position in positions:
        if position.get("position_id") != position_id or position.get("status") not in {"open", "partially_closed"}:
            continue
        qty = _to_float(position.get("quantity"), 0)
        close_qty = qty * ratio
        direction = str(position.get("direction"))
        settings = load_settings()
        execution_price = apply_sim_slippage(price, direction, "exit", settings)
        close_notional = abs(execution_price * close_qty)
        gross_pnl = calculate_realized_pnl(_to_float(position.get("entry_price"), 0), execution_price, close_qty, direction)
        exit_fee = calculate_sim_fee(close_notional, settings)
        exit_slippage_cost = _slippage_cost(price, execution_price, close_qty)
        pnl = gross_pnl - exit_fee
        released_margin = _to_float(position.get("margin_usdt"), 0) * ratio
        position["realized_pnl"] = _to_float(position.get("realized_pnl"), 0) + pnl
        position["exit_fee"] = _to_float(position.get("exit_fee"), 0) + exit_fee
        position["total_fee"] = _to_float(position.get("total_fee"), _to_float(position.get("entry_fee"), 0)) + exit_fee
        position["exit_slippage_cost"] = _to_float(position.get("exit_slippage_cost"), 0) + exit_slippage_cost
        position["total_slippage_cost"] = _to_float(position.get("total_slippage_cost"), _to_float(position.get("entry_slippage_cost"), 0)) + exit_slippage_cost
        position["quantity"] = qty - close_qty
        position["margin_usdt"] = _to_float(position.get("margin_usdt"), 0) - released_margin
        position["notional_usdt"] = _to_float(position.get("notional_usdt"), 0) * (1 - ratio)
        position["current_price"] = execution_price
        position["last_reference_price"] = price
        position["update_time"] = _now()
        account["available_balance"] = _to_float(account.get("available_balance"), 0) + released_margin + pnl
        account["used_margin"] = max(0.0, _to_float(account.get("used_margin"), 0) - released_margin)
        account["realized_pnl"] = _to_float(account.get("realized_pnl"), 0) + pnl
        account["daily_pnl"] = _to_float(account.get("daily_pnl"), 0) + pnl
        account["total_fee_usdt"] = _to_float(account.get("total_fee_usdt"), 0) + exit_fee
        account["total_slippage_cost_usdt"] = _to_float(account.get("total_slippage_cost_usdt"), 0) + exit_slippage_cost
        if ratio >= 0.999 or position["quantity"] <= 0:
            position["status"] = "closed"
            position["close_reason"] = reason
            trade = _history_row(position, execution_price, pnl, reason, close_qty=close_qty, close_notional=close_notional)
            trade["reference_exit_price"] = price
            trade["gross_pnl"] = gross_pnl
            trade["exit_fee"] = exit_fee
            trade["fee_usdt"] = _to_float(position.get("entry_fee"), 0) * ratio + exit_fee
            trade["slippage_cost_usdt"] = _to_float(position.get("entry_slippage_cost"), 0) * ratio + exit_slippage_cost
            history.insert(0, trade)
            try:
                record_sim_close(position, execution_price, pnl, reason)
            except Exception as exc:
                log_sim_event("模拟交易数据库写入失败", position.get("symbol", ""), position.get("direction", ""), price, reason=f"平仓记录未写入SQLite：{exc!r}")
            if pnl < 0:
                account["consecutive_losses"] = int(account.get("consecutive_losses", 0)) + 1
            else:
                account["consecutive_losses"] = 0
        else:
            position["status"] = "partially_closed"
        account["total_pnl"] = _to_float(account.get("realized_pnl"), 0) + _to_float(account.get("unrealized_pnl"), 0)
        account["equity"] = _to_float(account.get("available_balance"), 0) + _to_float(account.get("used_margin"), 0) + _to_float(account.get("unrealized_pnl"), 0)
        save_positions(positions)
        save_sim_account(account)
        save_sim_trade_history(history)
        log_sim_event("模拟平仓" if ratio >= 0.999 else "模拟部分平仓", position.get("symbol", ""), position.get("direction", ""), execution_price, content=f"净盈亏 {pnl:.2f} USDT，手续费 {exit_fee:.4f} USDT", reason=reason)
        return position
    return None


def partial_close_sim_position(position_id: str, ratio: float, reason: str, price: float) -> dict[str, Any] | None:
    return close_sim_position(position_id, reason, price, ratio)


def _history_row(position: dict[str, Any], exit_price: float, pnl: float, reason: str, close_qty: float | None = None, close_notional: float | None = None) -> dict[str, Any]:
    entry = _to_float(position.get("entry_price"), 0)
    notional = _to_float(close_notional, 0) if close_notional is not None else _to_float(position.get("notional_usdt"), 0)
    r_multiple = calculate_position_r_multiple(position, exit_price)
    snapshot = position.get("committee_snapshot") or {}
    local_snapshot = position.get("local_strategy_snapshot") or {}
    return {
        "trade_id": position.get("position_id"),
        "symbol": position.get("symbol"),
        "direction": position.get("direction"),
        "open_time": position.get("open_time"),
        "close_time": _now(),
        "holding_seconds": max(0, _ts() - int(position.get("open_ts", _ts()))),
        "entry_price": entry,
        "exit_price": exit_price,
        "entry_fee": position.get("entry_fee", 0),
        "exit_fee": position.get("exit_fee", 0),
        "total_fee": position.get("total_fee", 0),
        "entry_slippage_cost": position.get("entry_slippage_cost", 0),
        "exit_slippage_cost": position.get("exit_slippage_cost", 0),
        "total_slippage_cost": position.get("total_slippage_cost", 0),
        "sim_fee_rate": position.get("sim_fee_rate"),
        "sim_slippage_pct": position.get("sim_slippage_pct"),
        "quantity": close_qty if close_qty is not None else position.get("quantity"),
        "notional_usdt": notional,
        "leverage": position.get("leverage"),
        "pnl": pnl,
        "pnl_pct": pnl / notional * 100 if notional else 0,
        "r_multiple": r_multiple if r_multiple is not None else "",
        "is_win": pnl > 0,
        "close_reason": reason,
        "strategy_name": local_snapshot.get("strategy_name") or snapshot.get("strategy_name") or "",
        "committee_action": position.get("committee_action"),
        "committee_confidence": position.get("committee_confidence"),
        "committee_risk_score": position.get("committee_risk_score"),
        "local_strategy_action": position.get("local_strategy_action"),
        "risk_reward_ratio": position.get("risk_reward_ratio"),
        "exit_plan_source": position.get("exit_plan_source", "unknown"),
        "structure_exit_plan": position.get("structure_exit_plan") or {},
        "chairman_summary": snapshot.get("chairman_summary"),
        "main_reasons": snapshot.get("main_reasons"),
        "main_risks": snapshot.get("main_risks"),
        "committee_snapshot": snapshot,
        "external_ai_snapshot": snapshot.get("external_ai") or snapshot.get("external_ai_snapshot") or {},
        "local_strategy_snapshot": local_snapshot,
        "tp1_hit": bool(position.get("tp1_hit")),
        "tp2_hit": bool(position.get("tp2_hit")) or "止盈2" in reason,
        "stop_loss_hit": "止损" in reason,
        "manual_close": "用户" in reason or "手动" in reason,
    }


def check_pending_orders(current_prices: dict[str, float]) -> None:
    orders = load_orders()
    remaining: list[dict[str, Any]] = []
    for order in orders:
        if order.get("status") != "pending":
            remaining.append(order)
            continue
        symbol = str(order.get("symbol", ""))
        price = get_current_price(symbol, current_prices)
        if _ts() > int(order.get("expired_ts", 0)):
            order["status"] = "expired"
            log_sim_event("订单过期", symbol, order.get("direction", ""), price, reason="超过信号有效期未触发。")
            continue
        if price <= 0:
            remaining.append(order)
            continue
        low = _to_float(order.get("entry_zone_low"), 0)
        high = _to_float(order.get("entry_zone_high"), 0)
        if low <= price <= high:
            order["status"] = "filled"
            open_sim_position(order, price)
            log_sim_event("订单触发", symbol, order.get("direction", ""), price, content="价格进入委员会入场区。")
        else:
            remaining.append(order)
    save_orders(remaining)


def update_sim_positions(current_prices: dict[str, float], price_statuses: dict[str, str] | None = None) -> None:
    positions = load_positions()
    account = load_sim_account()
    settings = load_settings()
    unrealized_total = 0.0
    external_position_change = False
    live_count = 0
    stale_count = 0
    missing_count = 0
    early_exit_seconds = max(0, int(_to_float(settings.get("early_exit_min_seconds"), 600)))
    early_exit_adverse_r = max(0.0, _to_float(settings.get("early_exit_adverse_r"), 0.5))
    for position in positions:
        if position.get("status") not in {"open", "partially_closed"}:
            continue
        symbol = str(position.get("symbol", ""))
        price = get_current_price(symbol, current_prices)
        status = str((price_statuses or {}).get(symbol) or ("live" if price > 0 else "missing"))
        if price <= 0:
            position["price_status"] = "missing"
            position["last_price_update"] = position.get("last_price_update") or ""
            missing_count += 1
            continue
        if status == "missing":
            status = "stale"
        if status == "stale":
            stale_count += 1
        else:
            live_count += 1
        pnl_info = calculate_position_pnl(position, price)
        pnl = _to_float(pnl_info.get("unrealized_pnl"), 0)
        position["current_price"] = price
        position["price_status"] = status
        position["last_price_update"] = _now()
        position["unrealized_pnl"] = pnl
        position["unrealized_pnl_pct"] = _to_float(pnl_info.get("unrealized_pnl_pct"), 0)
        position["unrealized_pnl_pct_notional"] = _to_float(pnl_info.get("unrealized_pnl_pct_notional"), 0)
        position["r_multiple"] = pnl_info.get("r_multiple")
        position["holding_seconds"] = calculate_position_holding_time(position)["seconds"]
        position["update_time"] = _now()
        unrealized_total += pnl
        direction = str(position.get("direction"))
        can_auto_exit = status in {"live", "ranking", "stale"}
        r_multiple = _to_float(position.get("r_multiple"), 0)
        if (
            can_auto_exit
            and not position.get("tp1_hit")
            and early_exit_seconds > 0
            and early_exit_adverse_r > 0
            and int(position.get("holding_seconds", 0) or 0) >= early_exit_seconds
            and r_multiple <= -early_exit_adverse_r
        ):
            _debug_price_log(f"auto_close_trigger symbol={symbol} position={position.get('position_id')} reason=early_adverse_review price={price} r={r_multiple:.4f} status={status}")
            create_early_exit_shadow(position, price, r_multiple)
            close_sim_position(position["position_id"], "反向复核提前退出", price)
            external_position_change = True
            continue
        if can_auto_exit and check_stop_loss(position, price):
            _debug_price_log(f"auto_close_trigger symbol={symbol} position={position.get('position_id')} reason=stop_loss price={price} status={status}")
            close_sim_position(position["position_id"], "触发止损", price)
            external_position_change = True
            continue
        take_profit_hit = check_take_profit(position, price)
        if can_auto_exit and take_profit_hit == "tp1":
            _debug_price_log(f"auto_close_trigger symbol={symbol} position={position.get('position_id')} reason=take_profit_1 price={price} status={status}")
            updated_position = partial_close_sim_position(position["position_id"], 0.5, "触发止盈1", price)
            if updated_position:
                saved_positions = load_positions()
                for saved_position in saved_positions:
                    if saved_position.get("position_id") != position.get("position_id"):
                        continue
                    saved_position["tp1_hit"] = True
                    if settings.get("move_sl_to_breakeven", True):
                        saved_position["stop_loss"] = _to_float(saved_position.get("entry_price"), saved_position.get("stop_loss"))
                        saved_position["moved_stop_to_breakeven"] = True
                    break
                save_positions(saved_positions)
                log_sim_event("移动止损到保本", symbol, direction, price, reason="止盈1已触发。")
            external_position_change = True
        if can_auto_exit and take_profit_hit == "tp2":
            _debug_price_log(f"auto_close_trigger symbol={symbol} position={position.get('position_id')} reason=take_profit_2 price={price} status={status}")
            position["tp2_hit"] = True
            close_sim_position(position["position_id"], "触发止盈2", price)
            external_position_change = True
    if external_position_change:
        positions = load_positions()
        account = load_sim_account()
        unrealized_total = 0.0
        for open_position in positions:
            if open_position.get("status") not in {"open", "partially_closed"}:
                continue
            price = get_current_price(str(open_position.get("symbol", "")), current_prices)
            if price > 0:
                unrealized_total += _to_float(calculate_position_pnl(open_position, price).get("unrealized_pnl"), 0)
    account["unrealized_pnl"] = unrealized_total
    account["total_pnl"] = _to_float(account.get("realized_pnl"), 0) + unrealized_total
    account["equity"] = _to_float(account.get("available_balance"), 0) + _to_float(account.get("used_margin"), 0) + unrealized_total
    initial_balance = account_initial_balance(account)
    account["initial_balance"] = initial_balance
    account.setdefault("initial_equity", initial_balance)
    account["max_equity"] = max(_to_float(account.get("max_equity"), initial_balance) or 0.0, _to_float(account.get("equity"), 0) or 0.0)
    max_equity = _to_float(account.get("max_equity"), 0)
    account["max_drawdown"] = max(_to_float(account.get("max_drawdown"), 0), (max_equity - _to_float(account.get("equity"), 0)) / max_equity * 100 if max_equity else 0)
    account["return_pct"] = (account["equity"] - initial_balance) / initial_balance * 100 if initial_balance else 0
    save_positions(positions)
    save_sim_account(account)
    _debug_price_log(f"update_sim_positions positions={len([p for p in positions if p.get('status') in {'open', 'partially_closed'}])} live={live_count} stale={stale_count} missing={missing_count}")


def process_committee_signals(current_prices: dict[str, float], signals: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    settings = load_settings()
    account = load_sim_account()
    signals = signals if signals is not None else get_committee_signals_for_simulation()
    results: list[dict[str, Any]] = []
    if account.get("status") != "running" or settings.get("mode") == "observe":
        return [{"status": "skipped", "reason": "模拟交易未运行或处于仅观察模式。"}]
    for signal in signals[:10]:
        symbol = str(signal.get("symbol", "")).upper()
        price = get_current_price(symbol, current_prices)
        ok, reasons = validate_signal_for_simulation(signal, current_prices)
        if not ok:
            reason = "；".join(reasons)
            results.append({"symbol": symbol, "status": "rejected", "reason": reason})
            append_sim_diagnostic(
                "模拟信号拒绝",
                symbol,
                "rejected",
                reason,
                {
                    "price": price,
                    "direction": signal.get("direction"),
                    "simulation_score": signal.get("simulation_score"),
                    "base_quality_score": signal.get("base_quality_score"),
                    "liquidity_quality_score": signal.get("liquidity_quality_score"),
                    "portfolio_fit_score": signal.get("portfolio_fit_score"),
                    "risk_score": signal.get("risk_score"),
                },
            )
            continue
        order = create_pending_sim_order(signal, price)
        status = "created" if order else "rejected"
        reason = "已创建模拟订单。" if order else "模拟订单创建失败。"
        results.append({"symbol": symbol, "status": status, "reason": reason})
        append_sim_diagnostic(
            "模拟信号执行",
            symbol,
            status,
            reason,
            {
                "price": price,
                "direction": signal.get("direction"),
                "simulation_score": signal.get("simulation_score"),
                "base_quality_score": signal.get("base_quality_score"),
                "liquidity_quality_score": signal.get("liquidity_quality_score"),
                "portfolio_fit_score": signal.get("portfolio_fit_score"),
                "risk_score": signal.get("risk_score"),
            },
        )
    return results


def update_simulation(current_prices: dict[str, float], signals: list[dict[str, Any]] | None = None, price_statuses: dict[str, str] | None = None) -> dict[str, Any]:
    check_pending_orders(current_prices)
    update_sim_positions(current_prices, price_statuses)
    update_early_exit_shadow_tracks(current_prices, price_statuses)
    process_results = process_committee_signals(current_prices, signals)
    return get_sim_account_summary(process_results=process_results)


def set_sim_status(status: str) -> dict[str, Any]:
    account = load_sim_account()
    account["status"] = status
    save_sim_account(account)
    log_sim_event("模拟交易状态变更", content=f"状态变更为 {status}")
    return account


def cancel_order(order_id: str, reason: str = "用户取消待触发订单") -> None:
    orders = load_orders()
    kept: list[dict[str, Any]] = []
    for order in orders:
        if order.get("order_id") == order_id:
            log_sim_event("订单取消", order.get("symbol", ""), order.get("direction", ""), None, reason=reason)
        else:
            kept.append(order)
    save_orders(kept)


def move_stop_to_breakeven(position_id: str) -> None:
    positions = load_positions()
    for position in positions:
        if position.get("position_id") == position_id and position.get("status") in {"open", "partially_closed"}:
            position["stop_loss"] = _to_float(position.get("entry_price"), position.get("stop_loss"))
            position["moved_stop_to_breakeven"] = True
            log_sim_event("移动止损到保本", position.get("symbol", ""), position.get("direction", ""), position.get("current_price"), reason="用户手动移动。")
    save_positions(positions)


def calculate_sim_stats() -> dict[str, Any]:
    history = load_sim_trade_history()
    wins = [row for row in history if row.get("is_win")]
    losses = [row for row in history if not row.get("is_win")]
    pnl_values = [_to_float(row.get("pnl"), 0) for row in history]
    total_profit = sum(v for v in pnl_values if v > 0)
    total_loss = abs(sum(v for v in pnl_values if v < 0))
    long_rows = [row for row in history if row.get("direction") == "long"]
    short_rows = [row for row in history if row.get("direction") == "short"]
    r_values = [_to_float(row.get("r_multiple"), 0) for row in history if row.get("r_multiple") not in {"", None}]
    holding_values = [_to_float(row.get("holding_seconds"), 0) for row in history]
    reason_counts: dict[str, int] = {}
    symbol_pnl: dict[str, float] = {}
    consecutive_wins = 0
    consecutive_losses = 0
    for row in history:
        reason = str(row.get("close_reason") or "未知原因")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        symbol = str(row.get("symbol") or "")
        if symbol:
            symbol_pnl[symbol] = symbol_pnl.get(symbol, 0.0) + _to_float(row.get("pnl"), 0)
    for row in history:
        if row.get("is_win"):
            consecutive_wins += 1
            if consecutive_losses == 0:
                continue
        break
    for row in history:
        if not row.get("is_win"):
            consecutive_losses += 1
            if consecutive_wins == 0:
                continue
        break
    best_symbol = max(symbol_pnl.items(), key=lambda item: item[1])[0] if symbol_pnl else "暂无"
    worst_symbol = min(symbol_pnl.items(), key=lambda item: item[1])[0] if symbol_pnl else "暂无"
    common_reason = max(reason_counts.items(), key=lambda item: item[1])[0] if reason_counts else "暂无"
    return {
        "total_trades": len(history),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(history) * 100 if history else 0,
        "total_pnl": sum(pnl_values),
        "max_win": max(pnl_values) if pnl_values else 0,
        "max_loss": min(pnl_values) if pnl_values else 0,
        "avg_win": total_profit / len(wins) if wins else 0,
        "avg_loss": -total_loss / len(losses) if losses else 0,
        "profit_factor": total_profit / total_loss if total_loss else (total_profit if total_profit else 0),
        "avg_holding_seconds": sum(holding_values) / len(holding_values) if holding_values else 0,
        "avg_r_multiple": sum(r_values) / len(r_values) if r_values else 0,
        "consecutive_wins": consecutive_wins,
        "consecutive_losses": consecutive_losses,
        "long_win_rate": len([r for r in long_rows if r.get("is_win")]) / len(long_rows) * 100 if long_rows else 0,
        "short_win_rate": len([r for r in short_rows if r.get("is_win")]) / len(short_rows) * 100 if short_rows else 0,
        "common_close_reason": common_reason,
        "best_symbol": best_symbol,
        "worst_symbol": worst_symbol,
        "recent_10": history[:10],
    }


def calculate_account_risk_summary(account: dict[str, Any] | None = None, positions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    account = account or load_sim_account()
    positions = positions if positions is not None else get_open_positions()
    equity = _positive_float(account.get("equity"), account_initial_balance(account))
    initial = account_initial_balance(account)
    total_stop_loss = 0.0
    max_single_loss = 0.0
    long_exposure, short_exposure = calculate_long_short_exposure(positions)
    total_exposure = long_exposure + short_exposure
    for position in positions:
        entry = _to_float(position.get("entry_price"), 0)
        stop = _to_float(position.get("stop_loss"), 0)
        qty = _to_float(position.get("quantity"), 0)
        if entry <= 0 or stop <= 0 or qty <= 0:
            continue
        if position.get("direction") == "short":
            loss = max(0.0, (stop - entry) * qty)
        else:
            loss = max(0.0, (entry - stop) * qty)
        total_stop_loss += loss
        max_single_loss = max(max_single_loss, loss)
    risk_pct = total_stop_loss / equity * 100 if equity else 0
    exposure_pct = total_exposure / equity * 100 if equity else 0
    margin_pct = _to_float(account.get("used_margin"), 0) / equity * 100 if equity else 0
    daily_limit = initial * _to_float(load_settings().get("daily_loss_limit_pct"), 3) / 100
    allowed = account.get("status") == "running" and account.get("risk_status") != "locked"
    if risk_pct >= 60:
        status = "高风险"
    elif risk_pct >= 40:
        status = "警戒"
    elif risk_pct >= 20:
        status = "注意"
    else:
        status = "正常"
    if not allowed:
        status = "锁定"
    explanation = (
        f"风险率=预计最大止损亏损 {total_stop_loss:.2f} / 当前权益 {equity:.2f} * 100 = {risk_pct:.2f}%。"
        f" 名义暴露 {total_exposure:.2f} USDT，占权益 {exposure_pct:.2f}%；保证金占用 {margin_pct:.2f}%。"
    )
    if status != "正常":
        explanation += " 当前模拟账户风险偏高或交易状态未运行，建议暂停新增模拟仓位。"
    else:
        explanation += " 当前模拟账户风险处于正常范围。"
    return {
        "status": status,
        "total_risk_usdt": total_stop_loss,
        "total_risk_pct": risk_pct,
        "risk_denominator": "equity",
        "risk_formula": "estimated_stop_loss_usdt / equity * 100",
        "max_single_loss": max_single_loss,
        "equity": equity,
        "initial_balance": initial,
        "total_exposure": total_exposure,
        "long_exposure": long_exposure,
        "short_exposure": short_exposure,
        "exposure_pct": exposure_pct,
        "used_margin": _to_float(account.get("used_margin"), 0),
        "margin_pct": margin_pct,
        "daily_loss_limit_usdt": daily_limit,
        "available_balance_ok": _to_float(account.get("available_balance"), 0) > 0,
        "near_drawdown_limit": _to_float(account.get("max_drawdown"), 0) >= _to_float(load_settings().get("max_drawdown_limit_pct"), 8) * 0.8,
        "allow_new_position": allowed,
        "explanation": explanation,
    }


def get_position_detail(position_id: str) -> dict[str, Any] | None:
    for position in load_positions():
        if position.get("position_id") == position_id:
            detail = dict(position)
            detail["holding_time"] = calculate_position_holding_time(position)
            detail["r_multiple"] = calculate_position_r_multiple(position)
            detail["events"] = [e for e in load_sim_events(200) if e.get("symbol") == position.get("symbol")][:20]
            return detail
    return None


def get_trade_history_summary() -> dict[str, Any]:
    return calculate_sim_stats()


def get_recent_trade_results(limit: int = 10) -> list[dict[str, Any]]:
    return load_sim_trade_history()[:limit]


def _score_bucket(value: Any) -> str:
    score = _to_float(value, 0)
    if score >= 75:
        return "高"
    if score >= 60:
        return "中"
    return "低"


def calculate_sim_score_feedback(history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Summarize how the new base scores relate to closed simulated trades."""
    rows = history if history is not None else load_sim_trade_history()
    fields = [
        ("simulation_score", "模拟适配分"),
        ("base_quality_score", "基础质量分"),
        ("liquidity_quality_score", "流动性质量"),
        ("relative_strength_score", "相对强弱"),
        ("signal_freshness_score", "信号新鲜度"),
        ("historical_tradability_score", "历史可交易性"),
        ("portfolio_fit_score", "组合适配"),
    ]
    stats: list[dict[str, Any]] = []
    suggestions: list[str] = []
    for key, label in fields:
        buckets: dict[str, dict[str, Any]] = {
            "高": {"count": 0, "wins": 0, "pnl": 0.0},
            "中": {"count": 0, "wins": 0, "pnl": 0.0},
            "低": {"count": 0, "wins": 0, "pnl": 0.0},
        }
        for row in rows:
            snapshot = row.get("committee_snapshot") or {}
            if key not in snapshot:
                continue
            bucket = _score_bucket(snapshot.get(key))
            item = buckets[bucket]
            item["count"] += 1
            item["wins"] += 1 if row.get("is_win") else 0
            item["pnl"] += _to_float(row.get("pnl"), 0)
        high = buckets["高"]
        low = buckets["低"]
        high_win_rate = high["wins"] / high["count"] * 100 if high["count"] else 0
        low_win_rate = low["wins"] / low["count"] * 100 if low["count"] else 0
        row_stats = {
            "评分项": label,
            "高分样本": high["count"],
            "高分胜率": round(high_win_rate, 2),
            "高分盈亏": round(high["pnl"], 4),
            "低分样本": low["count"],
            "低分胜率": round(low_win_rate, 2),
            "低分盈亏": round(low["pnl"], 4),
        }
        stats.append(row_stats)
        if high["count"] >= 3 and high["pnl"] < 0:
            suggestions.append(f"{label}高分样本仍亏损，建议下调该评分权重或提高风险惩罚。")
        if low["count"] >= 3 and low["pnl"] > 0:
            suggestions.append(f"{label}低分样本表现为正，建议检查该评分是否过严。")
    sample_count = len([row for row in rows if row.get("committee_snapshot")])
    return {
        "sample_count": sample_count,
        "stats": stats,
        "suggestions": suggestions or ["当前样本尚未暴露明显评分偏差，继续积累模拟交易结果。"],
        "sample_warning": "样本少于20笔，评分反馈只作观察，不自动改权重。" if sample_count < 20 else "",
    }


def calculate_sim_performance_stats() -> dict[str, Any]:
    stats = calculate_sim_stats()
    account = enrich_sim_account(load_sim_account())
    stats.update(
        {
            "current_equity": account.get("equity", 0),
            "current_drawdown": account.get("current_drawdown", 0),
            "max_drawdown": account.get("max_drawdown", 0),
            "current_unrealized_pnl": account.get("unrealized_pnl", 0),
            "open_position_count": account.get("open_position_count", 0),
            "pending_order_count": account.get("pending_order_count", 0),
            "return_pct": account.get("return_pct", 0),
            "daily_pnl": account.get("daily_pnl", 0),
        }
    )
    return stats


def get_sim_account_summary(process_results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    positions = load_positions()
    orders = load_orders()
    open_positions = [p for p in positions if p.get("status") in {"open", "partially_closed"}]
    pending_orders = [o for o in orders if o.get("status") == "pending"]
    account = enrich_sim_account(load_sim_account(), open_positions, pending_orders)
    history = load_sim_trade_history()
    stats = calculate_sim_performance_stats()
    risk_summary = calculate_account_risk_summary(account, open_positions)
    return {
        "account": account,
        "settings": load_settings(),
        "positions": positions,
        "orders": orders,
        "history": history,
        "equity_curve": get_sim_equity_curve(300),
        "risk_summary": risk_summary,
        "events": load_sim_events(30),
        "diagnostics": load_sim_diagnostics(80),
        "score_feedback": calculate_sim_score_feedback(history),
        "stats": stats,
        "process_results": process_results or [],
        "safety_notice": "当前为模拟交易，不会使用真实资金，不会执行真实订单。",
    }

"""实盘交易中心前置安全版。

本模块默认只做安全检查、只读监控、订单预览、Dry-run 与审计日志。
7.6.1 开始预留小资金 Spot 手动实盘执行口，但默认 LIVE_TRADING_ENABLED=false，
所有真实订单必须经过 Live Manual、Spot Test Order、二次确认和确认短句。
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from services.replay_learning_engine import analyze_replay_learning
from services.sim_trade_engine import calculate_sim_performance_stats
from services.strategy_factory import get_strategy_candidates, get_strategy_candidates_for_simulation


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ROOT_DIR = Path(__file__).resolve().parents[1]
SETTINGS_PATH = DATA_DIR / "live_settings.json"
AUDIT_JSON_PATH = DATA_DIR / "live_audit_log.json"
AUDIT_CSV_PATH = DATA_DIR / "live_audit_log.csv"
RULE_CACHE_DIR = DATA_DIR / "live_exchange_rules"
LIVE_ORDER_JSON_PATH = DATA_DIR / "live_order_records.json"
LIVE_ORDER_CSV_PATH = DATA_DIR / "live_order_records.csv"
LIVE_POSITION_JSON_PATH = DATA_DIR / "live_position_records.json"
LIVE_POSITION_AUDIT_JSON_PATH = DATA_DIR / "live_position_audit_log.json"
LIVE_POSITION_AUDIT_CSV_PATH = DATA_DIR / "live_position_audit_log.csv"

BINANCE_SPOT_BASE = "https://api.binance.com"
BINANCE_FUTURES_BASE = "https://fapi.binance.com"
BINANCE_SPOT_TESTNET_BASE = "https://testnet.binance.vision"
BINANCE_FUTURES_TESTNET_BASE = "https://testnet.binancefuture.com"

LIVE_TRADING_ENABLED = str(os.environ.get("LIVE_TRADING_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}


DEFAULT_SETTINGS = {
    "mode": "read_only",
    "market_type": "spot",
    "testnet_enabled": False,
    "live_manual_enabled": False,
    "kill_switch_enabled": False,
    "kill_switch_reason": "",
    "ip_whitelist_confirmed": False,
    "max_live_risk_pct": 0.5,
    "max_live_notional_usdt": 10.0,
    "hard_max_live_notional_usdt": 50.0,
    "daily_live_notional_limit_usdt": 100.0,
    "daily_live_loss_limit_usdt": 5.0,
    "system_suggested_live_amount_usdt": 5.0,
    "allowed_symbols": ["BTCUSDT", "ETHUSDT"],
    "max_leverage": 5,
    "daily_loss_limit_pct": 1.0,
    "max_drawdown_limit_pct": 3.0,
    "last_update": "",
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
    except Exception as exc:
        print(f"[实盘安全中心] 读取文件失败 {path.name} error={exc!r}")
        _write_json(path, default)
        return default


def _write_json(path: Path, data: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_live_settings() -> dict[str, Any]:
    data = _read_json(SETTINGS_PATH, DEFAULT_SETTINGS.copy())
    settings = DEFAULT_SETTINGS.copy()
    if isinstance(data, dict):
        settings.update(data)
    settings["live_trading_enabled"] = LIVE_TRADING_ENABLED
    return settings


def save_live_settings(settings: dict[str, Any]) -> dict[str, Any]:
    merged = DEFAULT_SETTINGS.copy()
    merged.update(settings or {})
    merged["live_manual_enabled"] = bool(merged.get("live_manual_enabled")) and LIVE_TRADING_ENABLED
    merged["max_live_notional_usdt"] = min(_to_float(merged.get("max_live_notional_usdt"), 10), _to_float(merged.get("hard_max_live_notional_usdt"), 50))
    merged["last_update"] = _now()
    _write_json(SETTINGS_PATH, merged)
    log_live_audit_event("配置修改", mode=str(merged.get("mode")), result="已保存", reason="用户修改实盘安全中心配置。")
    return merged


def _load_env_file() -> dict[str, str]:
    result: dict[str, str] = {}
    for path in [ROOT_DIR / ".env", Path.cwd() / ".env"]:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def load_api_credentials_safely(testnet: bool = False) -> dict[str, Any]:
    env = _load_env_file()
    key_name = "BINANCE_TESTNET_API_KEY" if testnet else "BINANCE_API_KEY"
    secret_name = "BINANCE_TESTNET_API_SECRET" if testnet else "BINANCE_API_SECRET"
    api_key = os.environ.get(key_name) or env.get(key_name) or ""
    api_secret = os.environ.get(secret_name) or env.get(secret_name) or ""
    return {
        "configured": bool(api_key and api_secret),
        "api_key": api_key,
        "api_secret": api_secret,
        "masked_api_key": mask_api_key(api_key),
        "secret_status": "已隐藏" if api_secret else "未配置",
        "source": "环境变量或本地.env" if api_key or api_secret else "未配置",
    }


def mask_api_key(api_key: str) -> str:
    if not api_key:
        return "未配置"
    if len(api_key) <= 8:
        return api_key[:2] + "****"
    return api_key[:4] + "*" * max(4, len(api_key) - 8) + api_key[-4:]


def _base_url(market_type: str, testnet: bool) -> str:
    if market_type == "futures":
        return BINANCE_FUTURES_TESTNET_BASE if testnet else BINANCE_FUTURES_BASE
    return BINANCE_SPOT_TESTNET_BASE if testnet else BINANCE_SPOT_BASE


def _signed_get(path: str, params: dict[str, Any], credentials: dict[str, Any], market_type: str, testnet: bool) -> Any:
    query = dict(params or {})
    query["timestamp"] = int(time.time() * 1000)
    query["recvWindow"] = 5000
    encoded = urlencode(query)
    signature = hmac.new(str(credentials.get("api_secret", "")).encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    url = f"{_base_url(market_type, testnet)}{path}?{encoded}&signature={signature}"
    headers = {"X-MBX-APIKEY": str(credentials.get("api_key", ""))}
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()


def _signed_request(method: str, path: str, params: dict[str, Any], credentials: dict[str, Any], market_type: str, testnet: bool) -> Any:
    query = dict(params or {})
    query["timestamp"] = int(time.time() * 1000)
    query["recvWindow"] = 5000
    encoded = urlencode(query)
    signature = hmac.new(str(credentials.get("api_secret", "")).encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    url = f"{_base_url(market_type, testnet)}{path}?{encoded}&signature={signature}"
    headers = {"X-MBX-APIKEY": str(credentials.get("api_key", ""))}
    method = method.upper()
    if method == "POST":
        response = requests.post(url, headers=headers, timeout=10)
    elif method == "DELETE":
        response = requests.delete(url, headers=headers, timeout=10)
    else:
        response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    try:
        return response.json()
    except Exception:
        return {}


def check_api_connection(testnet: bool = False, market_type: str = "spot") -> dict[str, Any]:
    url = f"{_base_url(market_type, testnet)}/api/v3/time" if market_type == "spot" else f"{_base_url(market_type, testnet)}/fapi/v1/time"
    try:
        response = requests.get(url, timeout=8)
        response.raise_for_status()
        log_live_audit_event("API连接测试", mode="testnet" if testnet else "read_only", result="通过", reason="公共时间接口连接正常。")
        return {"status": "正常", "ok": True, "message": "API公共连接正常。"}
    except Exception as exc:
        log_live_audit_event("API连接测试", mode="testnet" if testnet else "read_only", result="失败", reason=str(exc))
        return {"status": "失败", "ok": False, "message": f"API连接失败：{exc}"}


def check_api_permissions(testnet: bool = False, market_type: str = "spot") -> dict[str, Any]:
    credentials = load_api_credentials_safely(testnet)
    if not credentials["configured"]:
        return {"ok": False, "permission_status": "未配置", "can_trade": False, "can_withdraw": False, "ip_restricted": "未知", "message": "API尚未配置，请先完成安全设置。"}
    try:
        if market_type == "futures":
            data = _signed_get("/fapi/v2/account", {}, credentials, market_type, testnet)
            can_trade = True
            can_withdraw = False
            balances = data.get("assets", [])
        else:
            data = _signed_get("/api/v3/account", {}, credentials, market_type, testnet)
            can_trade = bool(data.get("canTrade"))
            can_withdraw = bool(data.get("canWithdraw"))
            balances = data.get("balances", [])
        status = "可交易" if can_trade else "只读"
        if can_withdraw:
            status = "权限异常"
        log_live_audit_event("API权限检查", mode="testnet" if testnet else "read_only", result=status, reason="已完成只读权限检查。")
        return {"ok": True, "permission_status": status, "can_trade": can_trade, "can_withdraw": can_withdraw, "ip_restricted": "请在 Binance 后台确认", "balances_preview": len(balances), "message": "API权限检查完成。"}
    except Exception as exc:
        msg = str(exc)
        ip_hint = "可能未配置IP白名单或当前IP不在白名单内。" if "-2015" in msg or "Invalid API-key" in msg else "请检查 API Key / Secret 和网络。"
        log_live_audit_event("API权限检查", mode="testnet" if testnet else "read_only", result="失败", reason=ip_hint)
        return {"ok": False, "permission_status": "检查失败", "can_trade": False, "can_withdraw": False, "ip_restricted": "未知", "message": f"API权限检查失败：{ip_hint}"}


def check_withdraw_permission_disabled(permission: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(permission.get("can_withdraw"))
    return {"ok": not enabled, "status": "高危开启" if enabled else "关闭", "message": "提现权限已开启，必须立即关闭。" if enabled else "提现权限未发现开启。"}


def check_ip_restriction_status(permission: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    confirmed = bool(settings.get("ip_whitelist_confirmed"))
    return {"ok": confirmed, "status": "已确认" if confirmed else "未确认", "message": "建议开启 Binance API IP 白名单。" if not confirmed else "用户已确认 IP 白名单或已知风险。"}


def get_live_account_snapshot(testnet: bool = False, market_type: str = "spot") -> dict[str, Any]:
    credentials = load_api_credentials_safely(testnet)
    if not credentials["configured"]:
        return {"ok": False, "message": "API尚未配置，请先完成安全设置。", "balances": [], "positions": [], "open_orders": [], "updated_time": _now()}
    try:
        if market_type == "futures":
            data = _signed_get("/fapi/v2/account", {}, credentials, market_type, testnet)
            balances = [{"asset": a.get("asset"), "free": a.get("availableBalance"), "locked": "0", "wallet": a.get("walletBalance")} for a in data.get("assets", []) if _to_float(a.get("walletBalance")) > 0 or _to_float(a.get("availableBalance")) > 0]
            positions = [p for p in data.get("positions", []) if abs(_to_float(p.get("positionAmt"))) > 0]
        else:
            data = _signed_get("/api/v3/account", {}, credentials, market_type, testnet)
            balances = [b for b in data.get("balances", []) if _to_float(b.get("free")) > 0 or _to_float(b.get("locked")) > 0]
            positions = []
        return {"ok": True, "message": "真实账户只读数据获取成功。", "balances": balances[:50], "positions": positions[:50], "open_orders": [], "updated_time": _now(), "label": "真实账户只读数据" if not testnet else "测试网账户数据"}
    except Exception as exc:
        return {"ok": False, "message": f"账户只读数据获取失败：{exc}", "balances": [], "positions": [], "open_orders": [], "updated_time": _now()}


def load_exchange_rules(symbol: str, market_type: str = "spot", testnet: bool = False) -> dict[str, Any]:
    symbol = str(symbol or "BTCUSDT").upper()
    RULE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = RULE_CACHE_DIR / f"{market_type}_{symbol}.json"
    if path.exists() and time.time() - path.stat().st_mtime < 86400:
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        if market_type == "futures":
            url = f"{_base_url(market_type, testnet)}/fapi/v1/exchangeInfo"
        else:
            url = f"{_base_url(market_type, testnet)}/api/v3/exchangeInfo"
        data = requests.get(url, timeout=10).json()
        item = next((s for s in data.get("symbols", []) if s.get("symbol") == symbol), None)
        if not item:
            return {"ok": False, "symbol": symbol, "message": "交易对象不存在或不可交易。"}
        filters = {f.get("filterType"): f for f in item.get("filters", [])}
        rule = {
            "ok": True,
            "symbol": symbol,
            "status": item.get("status"),
            "baseAsset": item.get("baseAsset"),
            "quoteAsset": item.get("quoteAsset"),
            "tickSize": _to_float((filters.get("PRICE_FILTER") or {}).get("tickSize")),
            "minPrice": _to_float((filters.get("PRICE_FILTER") or {}).get("minPrice")),
            "stepSize": _to_float((filters.get("LOT_SIZE") or {}).get("stepSize") or (filters.get("MARKET_LOT_SIZE") or {}).get("stepSize")),
            "minQty": _to_float((filters.get("LOT_SIZE") or {}).get("minQty") or (filters.get("MARKET_LOT_SIZE") or {}).get("minQty")),
            "maxQty": _to_float((filters.get("LOT_SIZE") or {}).get("maxQty") or (filters.get("MARKET_LOT_SIZE") or {}).get("maxQty")),
            "minNotional": _to_float((filters.get("MIN_NOTIONAL") or {}).get("minNotional") or (filters.get("NOTIONAL") or {}).get("minNotional")),
            "raw_filters": filters,
        }
        path.write_text(json.dumps(rule, ensure_ascii=False, indent=2), encoding="utf-8")
        return rule
    except Exception as exc:
        return {"ok": False, "symbol": symbol, "message": f"交易规则获取失败：{exc}"}


def _is_step_aligned(value: float, step: float) -> bool:
    if step <= 0:
        return True
    ratio = value / step
    return abs(ratio - round(ratio)) < 1e-8


def validate_order_against_exchange_rules(order_plan: dict[str, Any]) -> dict[str, Any]:
    rule = load_exchange_rules(str(order_plan.get("symbol", "BTCUSDT")), str(order_plan.get("market_type", "spot")), bool(order_plan.get("testnet", False)))
    errors: list[str] = []
    warnings: list[str] = []
    if not rule.get("ok"):
        return {"ok": False, "errors": [rule.get("message", "交易规则获取失败，系统已阻止订单预览。")], "warnings": [], "rule": rule}
    qty = _to_float(order_plan.get("quantity"))
    price = _to_float(order_plan.get("price"))
    notional = qty * price
    if rule.get("status") not in {"TRADING", "TRADING_ALLOWED", None}:
        errors.append("交易对象当前状态不可交易。")
    if qty < _to_float(rule.get("minQty")):
        errors.append("下单数量低于交易所最小数量。")
    if _to_float(rule.get("maxQty")) and qty > _to_float(rule.get("maxQty")):
        errors.append("下单数量高于交易所最大数量。")
    if not _is_step_aligned(qty, _to_float(rule.get("stepSize"))):
        errors.append("下单数量不符合 step size 精度要求。")
    if _to_float(rule.get("tickSize")) and not _is_step_aligned(price, _to_float(rule.get("tickSize"))):
        warnings.append("计划价格可能不完全符合 tick size，正式执行前需要按交易所精度修正。")
    if _to_float(rule.get("minNotional")) and notional < _to_float(rule.get("minNotional")):
        errors.append("订单金额低于最小名义金额。")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "rule": rule}


def _daily_live_notional() -> float:
    today = time.strftime("%Y-%m-%d")
    total = 0.0
    for row in load_live_order_records(500):
        if str(row.get("time", "")).startswith(today) and str(row.get("order_status", "")) not in {"REJECTED", "CANCELED"}:
            total += _to_float(row.get("notional"), 0)
    return total


def _normalize_side(side: Any) -> str:
    raw = str(side or "BUY").upper()
    if raw in {"BUY", "买入", "LONG", "buy"}:
        return "BUY"
    return "SELL"


def _normalize_order_type(order_type: Any) -> str:
    raw = str(order_type or "LIMIT").upper()
    return "MARKET" if raw == "MARKET" else "LIMIT"


def create_live_order_plan(signal: dict[str, Any] | None, user_inputs: dict[str, Any]) -> dict[str, Any]:
    settings = load_live_settings()
    symbol = str(user_inputs.get("symbol") or (signal or {}).get("symbol") or "BTCUSDT").upper()
    market_type = str(user_inputs.get("market_type") or settings.get("market_type") or "spot")
    leverage = max(1, min(int(_to_float(user_inputs.get("leverage"), _to_float(settings.get("max_leverage"), 5))), 125))
    price = _to_float(user_inputs.get("price"), _to_float((signal or {}).get("planned_entry_price"), 0))
    quote_amount = _to_float(user_inputs.get("quote_amount"), _to_float(user_inputs.get("user_selected_amount"), 0))
    quantity = _to_float(user_inputs.get("quantity"), 0)
    if not quantity and price > 0 and quote_amount > 0:
        quantity = (quote_amount * leverage if market_type == "futures" else quote_amount) / price
    suggested = min(_to_float(settings.get("system_suggested_live_amount_usdt"), 5), _to_float(settings.get("max_live_notional_usdt"), 10))
    risk_max = min(_to_float(settings.get("max_live_notional_usdt"), 10), _to_float(settings.get("hard_max_live_notional_usdt"), 50))
    plan = {
        "plan_id": f"live_plan_{uuid.uuid4().hex[:12]}",
        "symbol": symbol,
        "market_type": market_type,
        "side": _normalize_side(user_inputs.get("side")),
        "order_type": _normalize_order_type(user_inputs.get("order_type")),
        "price": price,
        "quantity": quantity,
        "quote_amount": quote_amount or price * quantity,
        "margin_usdt": quote_amount or (price * quantity / leverage if market_type == "futures" and leverage else price * quantity),
        "notional": price * quantity,
        "leverage": leverage if market_type == "futures" else 1,
        "time_in_force": "GTC",
        "source": user_inputs.get("source") or ((signal or {}).get("source") or "AI交易委员会"),
        "committee_snapshot": signal or {},
        "local_strategy_snapshot": (signal or {}).get("local_strategy_snapshot") or {},
        "risk_snapshot": {"max_live_risk_pct": settings.get("max_live_risk_pct"), "daily_live_notional": _daily_live_notional()},
        "live_safety_snapshot": {"mode": settings.get("mode"), "live_manual_enabled": settings.get("live_manual_enabled"), "live_trading_enabled": LIVE_TRADING_ENABLED},
        "user_selected_amount": quote_amount or price * quantity,
        "system_suggested_amount": suggested,
        "risk_max_amount": risk_max,
        "created_time": _now(),
        "status": "draft",
        "manual_override": (quote_amount or price * quantity) > suggested,
    }
    log_live_audit_event("创建订单计划", mode=str(settings.get("mode")), symbol=symbol, result="已创建", reason="用户创建小资金手动实盘订单计划。", real_account=True)
    return plan


def validate_live_order_plan(order_plan: dict[str, Any]) -> dict[str, Any]:
    settings = load_live_settings()
    errors: list[str] = []
    warnings: list[str] = []
    market_type = str(order_plan.get("market_type", "spot"))
    if market_type not in {"spot", "futures"}:
        errors.append("市场类型不支持。")
    if str(order_plan.get("order_type")) == "MARKET":
        warnings.append("市价单存在滑点风险，本版本默认不推荐，需要额外确认。")
    notional = _to_float(order_plan.get("quote_amount"), _to_float(order_plan.get("price")) * _to_float(order_plan.get("quantity")))
    risk_max = min(_to_float(settings.get("max_live_notional_usdt"), 10), _to_float(settings.get("hard_max_live_notional_usdt"), 50))
    if notional <= 0:
        errors.append("订单金额必须大于 0。")
    if notional > risk_max:
        errors.append("用户选择金额超过风控允许最大金额。")
    if _daily_live_notional() + notional > _to_float(settings.get("daily_live_notional_limit_usdt"), 100):
        errors.append("单日真实交易总额将超过上限。")
    if str(order_plan.get("symbol")) not in settings.get("allowed_symbols", ["BTCUSDT", "ETHUSDT"]):
        warnings.append("当前交易对象不在默认实盘候选白名单。")
    rule_check = validate_order_against_exchange_rules({**order_plan, "testnet": settings.get("mode") == "testnet"})
    errors.extend(rule_check.get("errors") or [])
    return {"ok": not errors, "errors": errors, "warnings": warnings + list(rule_check.get("warnings") or []), "rule_check": rule_check}


def create_live_order_preview(order_plan: dict[str, Any]) -> dict[str, Any]:
    settings = load_live_settings()
    plan = {
        "symbol": str(order_plan.get("symbol", "BTCUSDT")).upper(),
        "market_type": order_plan.get("market_type", settings.get("market_type", "spot")),
        "mode": settings.get("mode", "read_only"),
        "side": _normalize_side(order_plan.get("side", "BUY")),
        "order_type": _normalize_order_type(order_plan.get("order_type", "LIMIT")),
        "price": _to_float(order_plan.get("price")),
        "quantity": _to_float(order_plan.get("quantity")),
        "stop_loss": _to_float(order_plan.get("stop_loss")),
        "take_profit": _to_float(order_plan.get("take_profit")),
        "source": order_plan.get("source", "手动订单预览"),
        "testnet": settings.get("mode") == "testnet",
        "plan_id": order_plan.get("plan_id"),
        "quote_amount": _to_float(order_plan.get("quote_amount")),
    }
    plan["notional"] = plan["price"] * plan["quantity"]
    plan["estimated_fee"] = plan["notional"] * 0.0004
    plan["estimated_slippage"] = plan["notional"] * 0.0002
    max_loss = abs(plan["price"] - plan["stop_loss"]) * plan["quantity"] if plan["stop_loss"] else 0
    plan["max_loss"] = max_loss
    reward = abs(plan["take_profit"] - plan["price"]) * plan["quantity"] if plan["take_profit"] else 0
    plan["risk_reward_ratio"] = reward / max_loss if max_loss else 0
    rule_check = validate_order_against_exchange_rules(plan)
    risk_errors = []
    plan_check = validate_live_order_plan({**order_plan, **plan})
    if plan["notional"] > _to_float(settings.get("max_live_notional_usdt"), 10):
        risk_errors.append("订单名义金额超过实盘前置安全上限。")
    if plan["symbol"] not in settings.get("allowed_symbols", ["BTCUSDT", "ETHUSDT"]):
        risk_errors.append("当前交易对象不在默认实盘候选白名单。")
    risk_errors.extend(plan_check.get("errors") or [])
    preview = {"ok": rule_check["ok"] and not risk_errors, "plan": plan, "rule_check": rule_check, "plan_check": plan_check, "risk_errors": risk_errors, "message": "这只是订单预览，尚未执行真实订单。"}
    log_live_audit_event("订单预览生成" if preview["ok"] else "订单预览失败", mode=str(settings.get("mode")), symbol=plan["symbol"], result="通过" if preview["ok"] else "失败", reason="；".join(rule_check.get("errors", []) + risk_errors) or "订单预览生成。")
    return preview


def preview_live_order(order_plan: dict[str, Any]) -> dict[str, Any]:
    return create_live_order_preview(order_plan)


def run_live_preflight_check(order_plan: dict[str, Any], user_confirmed: bool = False) -> dict[str, Any]:
    settings = load_live_settings()
    permission = check_api_permissions(settings.get("mode") == "testnet", str(order_plan.get("market_type", settings.get("market_type", "spot"))))
    preview = create_live_order_preview(order_plan)
    sim_stats = calculate_sim_performance_stats()
    replay = analyze_replay_learning()
    checklist = [
        _check("API连接正常", check_api_connection(settings.get("mode") == "testnet", str(order_plan.get("market_type", settings.get("market_type", "spot")))).get("ok"), "API公共连接失败。"),
        _check("API权限符合当前模式", settings.get("mode") in {"read_only", "dry_run"} or permission.get("ok"), permission.get("message", "API权限不匹配。")),
        _check("提现权限关闭", not permission.get("can_withdraw"), "提现权限开启，高危阻止。"),
        _check("IP白名单建议已确认", bool(settings.get("ip_whitelist_confirmed")), "建议开启并确认 IP 白名单。", warning=True),
        _check("当前模式允许该操作", settings.get("mode") in {"dry_run", "testnet"} and not settings.get("kill_switch_enabled"), "只读模式或安全锁状态不允许执行流程。"),
        _check("交易所规则通过", preview.get("rule_check", {}).get("ok"), "交易所规则校验失败。"),
        _check("系统风控通过", not preview.get("risk_errors"), "订单预览未通过系统风控。"),
        _check("模拟交易样本达标", _to_float(sim_stats.get("total_trades")) >= 30, f"模拟交易样本不足，当前 {sim_stats.get('total_trades', 0)} 笔，最低要求30笔。"),
        _check("模拟 Profit Factor 达标", _to_float(sim_stats.get("profit_factor")) >= 1.2, "模拟 Profit Factor 未达到 1.2。"),
        _check("复盘中心未高危", replay.get("summary", {}).get("data_quality") != "poor", "复盘样本不足或质量较差。"),
        _check("用户已人工确认", user_confirmed, "尚未人工确认。"),
        _check("紧急停止未触发", not settings.get("kill_switch_enabled"), "实盘安全锁已开启。"),
    ]
    failed = [item for item in checklist if item["status"] == "失败"]
    ok = not failed
    log_live_audit_event("实盘前检查通过" if ok else "实盘前检查失败", mode=str(settings.get("mode")), symbol=str(order_plan.get("symbol", "")), result="通过" if ok else "失败", reason="；".join(item["message"] for item in failed) or "检查清单通过。")
    return {"ok": ok, "checklist": checklist, "preview": preview, "message": "执行前检查通过。" if ok else "执行前检查未通过，系统阻止后续动作。"}


def _check(name: str, ok: bool, message: str, warning: bool = False) -> dict[str, Any]:
    if ok:
        return {"name": name, "status": "通过", "message": "检查通过。"}
    return {"name": name, "status": "警告" if warning else "失败", "message": message}


def run_test_order_validation(order_plan: dict[str, Any]) -> dict[str, Any]:
    preview = create_live_order_preview(order_plan)
    if not preview.get("ok"):
        return {"ok": False, "message": "测试订单验证失败：" + "；".join(preview.get("rule_check", {}).get("errors", []) + preview.get("risk_errors", []))}
    log_live_audit_event("Test order 验证", mode="dry_run", symbol=str(order_plan.get("symbol", "")), result="通过", reason="本地 Dry-run 通过，未发送真实订单。")
    return {"ok": True, "message": "Dry-run 测试通过：未发送到 Binance，不会产生真实订单。", "preview": preview}


def _binance_order_params(order_plan: dict[str, Any]) -> dict[str, Any]:
    params = {
        "symbol": str(order_plan.get("symbol", "")).upper(),
        "side": _normalize_side(order_plan.get("side")),
        "type": _normalize_order_type(order_plan.get("order_type")),
        "quantity": f"{_to_float(order_plan.get('quantity')):.8f}".rstrip("0").rstrip("."),
    }
    if params["type"] == "LIMIT":
        params["timeInForce"] = str(order_plan.get("time_in_force", "GTC"))
        params["price"] = f"{_to_float(order_plan.get('price')):.8f}".rstrip("0").rstrip(".")
    return params


def _binance_futures_order_params(order_plan: dict[str, Any]) -> dict[str, Any]:
    params = {
        "symbol": str(order_plan.get("symbol", "")).upper(),
        "side": _normalize_side(order_plan.get("side")),
        "type": _normalize_order_type(order_plan.get("order_type")),
        "quantity": f"{_to_float(order_plan.get('quantity')):.8f}".rstrip("0").rstrip("."),
        "newOrderRespType": "RESULT",
    }
    if params["type"] == "LIMIT":
        params["timeInForce"] = str(order_plan.get("time_in_force", "GTC"))
        params["price"] = f"{_to_float(order_plan.get('price')):.8f}".rstrip("0").rstrip(".")
    return params


def set_futures_leverage(symbol: str, leverage: int, testnet: bool = False) -> dict[str, Any]:
    credentials = load_api_credentials_safely(testnet)
    if not credentials.get("configured"):
        return {"ok": False, "message": "API尚未配置，无法同步合约杠杆。"}
    leverage = max(1, min(int(leverage or 5), 125))
    try:
        data = _signed_request("POST", "/fapi/v1/leverage", {"symbol": str(symbol).upper(), "leverage": leverage}, credentials, "futures", testnet)
        log_live_audit_event("合约杠杆同步", mode="testnet" if testnet else "live", symbol=str(symbol).upper(), result="通过", reason=f"U本位合约杠杆已同步为 {leverage}x。", real_account=not testnet)
        return {"ok": True, "message": f"合约杠杆已同步为 {leverage}x。", "response": data}
    except Exception as exc:
        msg = f"合约杠杆同步失败：{exc}"
        log_live_audit_event("合约杠杆同步", mode="testnet" if testnet else "live", symbol=str(symbol).upper(), result="失败", reason=msg, real_account=not testnet)
        return {"ok": False, "message": msg}


def run_futures_test_order(order_plan: dict[str, Any]) -> dict[str, Any]:
    settings = load_live_settings()
    plan = {**order_plan, "market_type": "futures"}
    validation = validate_order_against_exchange_rules({**plan, "testnet": settings.get("mode") == "testnet"})
    if not validation.get("ok"):
        result = {"ok": False, "message": "合约测试订单验证失败：" + "；".join(validation.get("errors") or []), "validation": validation}
        log_live_audit_event("Futures Test Order 结果", mode=str(settings.get("mode")), symbol=str(plan.get("symbol", "")), result="失败", reason=result["message"], real_account=True)
        return result
    credentials = load_api_credentials_safely(False)
    if not credentials.get("configured"):
        result = {"ok": False, "message": "API尚未配置，无法执行 Futures Test Order。"}
        log_live_audit_event("Futures Test Order 结果", mode=str(settings.get("mode")), symbol=str(plan.get("symbol", "")), result="失败", reason=result["message"], real_account=True)
        return result
    permission = check_api_permissions(False, "futures")
    if not permission.get("can_trade") or permission.get("can_withdraw"):
        result = {"ok": False, "message": "API权限不满足合约 Test Order 要求，或提现权限存在风险。"}
        log_live_audit_event("Futures Test Order 结果", mode=str(settings.get("mode")), symbol=str(plan.get("symbol", "")), result="失败", reason=result["message"], real_account=True)
        return result
    try:
        leverage_result = set_futures_leverage(str(plan.get("symbol", "")), int(plan.get("leverage", settings.get("max_leverage", 5) or 5)), False)
        if not leverage_result.get("ok"):
            return {"ok": False, "message": leverage_result.get("message", "合约杠杆同步失败。"), "validation": validation}
        _signed_request("POST", "/fapi/v1/order/test", _binance_futures_order_params(plan), credentials, "futures", False)
        row = {"ok": True, "message": "合约测试订单验证：通过。交易所已验证合约订单参数，但尚未进入真实撮合。", "time": _now(), "leverage": plan.get("leverage", 5)}
        log_live_audit_event("Futures Test Order 结果", mode=str(settings.get("mode")), symbol=str(plan.get("symbol", "")), result="通过", reason=row["message"], real_account=True)
        return row
    except Exception as exc:
        msg = f"合约测试订单验证失败：{exc}"
        log_live_audit_event("Futures Test Order 结果", mode=str(settings.get("mode")), symbol=str(plan.get("symbol", "")), result="失败", reason=msg, real_account=True)
        return {"ok": False, "message": msg, "validation": validation}


def run_live_futures_preflight(order_plan: dict[str, Any], test_order_result: dict[str, Any] | None = None, user_confirmed: bool = False, confirmation_phrase: str = "") -> dict[str, Any]:
    settings = load_live_settings()
    plan = {**order_plan, "market_type": "futures"}
    permission = check_api_permissions(False, "futures")
    validation = validate_order_against_exchange_rules({**plan, "testnet": settings.get("mode") == "testnet"})
    notional = _to_float(plan.get("notional"), _to_float(plan.get("price")) * _to_float(plan.get("quantity")))
    margin = _to_float(plan.get("quote_amount"), notional / max(_to_float(plan.get("leverage"), 5), 1))
    risk_max = min(_to_float(settings.get("max_live_notional_usdt"), 10), _to_float(settings.get("hard_max_live_notional_usdt"), 50))
    test_ok = bool((test_order_result or {}).get("ok"))
    phrase = require_confirmation_phrase(plan, confirmation_phrase)
    checklist = [
        _check("LIVE_TRADING_ENABLED 显式启用", LIVE_TRADING_ENABLED, "LIVE_TRADING_ENABLED=false，系统阻止真实合约下单。"),
        _check("U本位合约订单计划", str(plan.get("market_type")) == "futures", "订单计划不是 U本位合约。"),
        _check("安全锁未开启", not settings.get("kill_switch_enabled"), "实盘安全锁已开启。"),
        _check("API权限可交易", bool(permission.get("can_trade")), permission.get("message", "API不可交易。")),
        _check("提现权限关闭", not permission.get("can_withdraw"), "提现权限开启，高危阻止。"),
        _check("交易所规则通过", validation.get("ok"), "；".join(validation.get("errors") or []) or "规则校验失败。"),
        _check("保证金小资金限制", margin > 0 and margin <= risk_max, "合约保证金金额超过小资金风控上限。"),
        _check("Futures Test Order 通过", test_ok, "Futures Test Order 未通过。"),
        _check("用户/自动流程确认", bool(user_confirmed), "尚未确认真实合约订单。"),
        _check("确认短句正确", phrase.get("ok"), phrase.get("message", "")),
    ]
    failed = [item for item in checklist if item["status"] == "失败"]
    ok = not failed
    log_live_audit_event("实盘合约执行前检查" if ok else "实盘合约执行前检查失败", mode=str(settings.get("mode")), symbol=str(plan.get("symbol", "")), result="通过" if ok else "失败", reason="；".join(item["message"] for item in failed) or "全部检查通过。", real_account=True)
    return {"ok": ok, "checklist": checklist, "validation": validation, "message": "小资金 U本位合约执行前检查通过。" if ok else "检查未通过，系统阻止真实合约订单提交。"}


def submit_live_futures_order(order_plan: dict[str, Any], test_order_result: dict[str, Any], user_confirmed: bool, confirmation_phrase: str) -> dict[str, Any]:
    settings = load_live_settings()
    plan = {**order_plan, "market_type": "futures"}
    preflight = run_live_futures_preflight(plan, test_order_result, user_confirmed, confirmation_phrase)
    if not preflight.get("ok"):
        return {"ok": False, "message": preflight.get("message"), "preflight": preflight}
    credentials = load_api_credentials_safely(False)
    try:
        leverage = int(plan.get("leverage", settings.get("max_leverage", 5) or 5))
        leverage_result = set_futures_leverage(str(plan.get("symbol", "")), leverage, False)
        if not leverage_result.get("ok"):
            return {"ok": False, "message": leverage_result.get("message"), "preflight": preflight}
        response = _signed_request("POST", "/fapi/v1/order", _binance_futures_order_params(plan), credentials, "futures", False)
        order_id = str(response.get("orderId", ""))
        notional = _to_float(plan.get("notional"), _to_float(plan.get("price")) * _to_float(plan.get("quantity")))
        record = {
            "time": _now(),
            "order_id": order_id,
            "client_order_id": response.get("clientOrderId"),
            "symbol": response.get("symbol") or plan.get("symbol"),
            "market_type": "futures",
            "side": plan.get("side"),
            "order_type": plan.get("order_type"),
            "price": plan.get("price"),
            "quantity": plan.get("quantity"),
            "notional": notional,
            "margin_usdt": plan.get("quote_amount"),
            "leverage": leverage,
            "order_status": response.get("status", "SUBMITTED"),
            "executed_qty": response.get("executedQty"),
            "avg_price": response.get("avgPrice", ""),
            "source": plan.get("source"),
            "committee_action": (plan.get("committee_snapshot") or {}).get("action") or (plan.get("committee_snapshot") or {}).get("final_action"),
            "risk_score": ((plan.get("committee_snapshot") or {}).get("risk_score") or (plan.get("committee_snapshot") or {}).get("committee_risk_score")),
            "live_safety_status": "通过",
            "user_selected_amount": plan.get("user_selected_amount"),
            "system_suggested_amount": plan.get("system_suggested_amount"),
            "risk_max_amount": plan.get("risk_max_amount"),
            "manual_override": bool(plan.get("manual_override")),
            "user_confirm_time": _now(),
            "confirmation_phrase_ok": True,
            "raw_status_summary": response.get("status"),
        }
        save_live_order_record(record)
        log_live_audit_event("真实合约订单提交", mode=str(settings.get("mode")), symbol=str(plan.get("symbol", "")), result="已提交", reason=f"真实 U本位合约订单已提交，订单ID {order_id}", real_account=True)
        return {"ok": True, "message": "小资金真实 U本位合约订单已提交。", "order": record, "exchange_response": response}
    except Exception as exc:
        msg = f"真实合约订单提交失败，请检查 API 权限、余额、杠杆和交易规则：{exc}"
        log_live_audit_event("合约订单提交失败", mode=str(settings.get("mode")), symbol=str(plan.get("symbol", "")), result="失败", reason=msg, risk_level="高", real_account=True)
        return {"ok": False, "message": msg, "preflight": preflight}


def run_spot_test_order(order_plan: dict[str, Any]) -> dict[str, Any]:
    settings = load_live_settings()
    if str(order_plan.get("market_type", "spot")) != "spot":
        result = {"ok": False, "message": "本版本暂不开放 U本位合约真实下单，Spot Test Order 仅支持现货。"}
        log_live_audit_event("Spot Test Order 结果", mode=str(settings.get("mode")), symbol=str(order_plan.get("symbol", "")), result="失败", reason=result["message"], real_account=True)
        return result
    validation = validate_live_order_plan(order_plan)
    if not validation.get("ok"):
        result = {"ok": False, "message": "测试订单验证失败：" + "；".join(validation.get("errors") or []), "validation": validation}
        log_live_audit_event("Spot Test Order 结果", mode=str(settings.get("mode")), symbol=str(order_plan.get("symbol", "")), result="失败", reason=result["message"], real_account=True)
        return result
    credentials = load_api_credentials_safely(False)
    if not credentials.get("configured"):
        result = {"ok": False, "message": "API尚未配置，无法执行 Spot Test Order。"}
        log_live_audit_event("Spot Test Order 结果", mode=str(settings.get("mode")), symbol=str(order_plan.get("symbol", "")), result="失败", reason=result["message"], real_account=True)
        return result
    permission = check_api_permissions(False, "spot")
    if not permission.get("can_trade") or permission.get("can_withdraw"):
        result = {"ok": False, "message": "API权限不满足 Spot Test Order 要求，或提现权限存在风险。"}
        log_live_audit_event("Spot Test Order 结果", mode=str(settings.get("mode")), symbol=str(order_plan.get("symbol", "")), result="失败", reason=result["message"], real_account=True)
        return result
    try:
        _signed_request("POST", "/api/v3/order/test", _binance_order_params(order_plan), credentials, "spot", False)
        row = {"ok": True, "message": "测试订单验证：通过。交易所已验证订单参数，但尚未进入真实撮合。", "time": _now()}
        log_live_audit_event("Spot Test Order 结果", mode=str(settings.get("mode")), symbol=str(order_plan.get("symbol", "")), result="通过", reason=row["message"], real_account=True)
        return row
    except Exception as exc:
        msg = f"测试订单验证失败：{exc}"
        log_live_audit_event("Spot Test Order 结果", mode=str(settings.get("mode")), symbol=str(order_plan.get("symbol", "")), result="失败", reason=msg, real_account=True)
        return {"ok": False, "message": msg}


def require_user_manual_confirmation(order_plan: dict[str, Any], confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        return {"ok": False, "message": "尚未点击确认：我确认这是小资金真实订单，并理解风险。"}
    log_live_audit_event("用户点击确认", mode=str(load_live_settings().get("mode")), symbol=str(order_plan.get("symbol", "")), result="已确认", reason="用户完成第一层真实订单确认。", real_account=True)
    return {"ok": True, "message": "第一层人工确认已完成。"}


def require_confirmation_phrase(order_plan: dict[str, Any], phrase: str) -> dict[str, Any]:
    required = "我确认执行小资金实盘订单"
    if str(phrase or "").strip() != required:
        return {"ok": False, "message": f"确认短句不匹配，请输入：{required}"}
    log_live_audit_event("用户输入确认短句", mode=str(load_live_settings().get("mode")), symbol=str(order_plan.get("symbol", "")), result="已确认", reason="用户输入确认短句正确。", real_account=True)
    return {"ok": True, "message": "确认短句已通过。"}


def run_live_manual_preflight(order_plan: dict[str, Any], test_order_result: dict[str, Any] | None = None, user_confirmed: bool = False, confirmation_phrase: str = "") -> dict[str, Any]:
    settings = load_live_settings()
    permission = check_api_permissions(False, "spot")
    validation = validate_live_order_plan(order_plan)
    preview = create_live_order_preview(order_plan)
    test_ok = bool((test_order_result or {}).get("ok"))
    confirm = require_user_manual_confirmation(order_plan, user_confirmed)
    phrase = require_confirmation_phrase(order_plan, confirmation_phrase)
    checklist = [
        _check("LIVE_TRADING_ENABLED 显式启用", LIVE_TRADING_ENABLED, "LIVE_TRADING_ENABLED=false，系统阻止真实下单。"),
        _check("Live Manual 模式已开启", settings.get("mode") == "live_manual" and settings.get("live_manual_enabled"), "未开启 Live Manual 模式。"),
        _check("仅支持 Spot 现货", str(order_plan.get("market_type")) == "spot", "本版本暂不开放合约真实下单。"),
        _check("安全锁未开启", not settings.get("kill_switch_enabled"), "实盘安全锁已开启。"),
        _check("API权限可交易", bool(permission.get("can_trade")), permission.get("message", "API不可交易。")),
        _check("提现权限关闭", not permission.get("can_withdraw"), "提现权限开启，高危阻止。"),
        _check("交易所规则通过", validation.get("ok"), "；".join(validation.get("errors") or []) or "规则校验失败。"),
        _check("订单预览通过", preview.get("ok"), "订单预览未通过。"),
        _check("Spot Test Order 通过", test_ok, "Spot Test Order 未通过。"),
        _check("第一层人工确认", confirm.get("ok"), confirm.get("message", "")),
        _check("确认短句正确", phrase.get("ok"), phrase.get("message", "")),
    ]
    failed = [item for item in checklist if item["status"] == "失败"]
    ok = not failed
    log_live_audit_event("实盘手动执行前检查" if ok else "实盘手动执行前检查失败", mode=str(settings.get("mode")), symbol=str(order_plan.get("symbol", "")), result="通过" if ok else "失败", reason="；".join(item["message"] for item in failed) or "全部检查通过。", real_account=True)
    return {"ok": ok, "checklist": checklist, "preview": preview, "message": "小资金手动实盘执行前检查通过。" if ok else "检查未通过，系统阻止真实订单提交。"}


def save_live_order_record(order_record: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    records = load_live_order_records(1000)
    record = dict(order_record)
    record.setdefault("time", _now())
    records.insert(0, record)
    _write_json(LIVE_ORDER_JSON_PATH, records[:1000])
    try:
        fieldnames = sorted({key for row in records[:1000] for key in row.keys()})
        with LIVE_ORDER_CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records[:1000])
    except Exception as exc:
        print(f"[实盘安全中心] 真实订单记录CSV写入失败 error={exc!r}")
    return record


def load_live_order_records(limit: int = 100) -> list[dict[str, Any]]:
    data = _read_json(LIVE_ORDER_JSON_PATH, [])
    return (data if isinstance(data, list) else [])[:limit]


def submit_live_spot_order(order_plan: dict[str, Any], test_order_result: dict[str, Any], user_confirmed: bool, confirmation_phrase: str) -> dict[str, Any]:
    settings = load_live_settings()
    preflight = run_live_manual_preflight(order_plan, test_order_result, user_confirmed, confirmation_phrase)
    if not preflight.get("ok"):
        return {"ok": False, "message": preflight.get("message"), "preflight": preflight}
    credentials = load_api_credentials_safely(False)
    try:
        response = _signed_request("POST", "/api/v3/order", _binance_order_params(order_plan), credentials, "spot", False)
        order_id = str(response.get("orderId", ""))
        record = {
            "time": _now(),
            "order_id": order_id,
            "client_order_id": response.get("clientOrderId"),
            "symbol": response.get("symbol") or order_plan.get("symbol"),
            "market_type": "spot",
            "side": order_plan.get("side"),
            "order_type": order_plan.get("order_type"),
            "price": order_plan.get("price"),
            "quantity": order_plan.get("quantity"),
            "notional": order_plan.get("quote_amount"),
            "order_status": response.get("status", "SUBMITTED"),
            "executed_qty": response.get("executedQty"),
            "avg_price": "",
            "source": order_plan.get("source"),
            "committee_action": (order_plan.get("committee_snapshot") or {}).get("action") or (order_plan.get("committee_snapshot") or {}).get("final_action"),
            "local_strategy_action": (order_plan.get("local_strategy_snapshot") or {}).get("action"),
            "risk_score": ((order_plan.get("committee_snapshot") or {}).get("risk_score") or (order_plan.get("committee_snapshot") or {}).get("committee_risk_score")),
            "live_safety_status": "通过",
            "user_selected_amount": order_plan.get("user_selected_amount"),
            "system_suggested_amount": order_plan.get("system_suggested_amount"),
            "risk_max_amount": order_plan.get("risk_max_amount"),
            "manual_override": bool(order_plan.get("manual_override")),
            "user_confirm_time": _now(),
            "confirmation_phrase_ok": True,
            "raw_status_summary": response.get("status"),
        }
        save_live_order_record(record)
        log_live_audit_event("真实订单提交", mode=str(settings.get("mode")), symbol=str(order_plan.get("symbol", "")), result="已提交", reason=f"真实 Spot 订单已提交，订单ID {order_id}", real_account=True)
        status = fetch_live_order_status(order_id, str(order_plan.get("symbol", "")))
        return {"ok": True, "message": "小资金真实 Spot 订单已提交。", "order": record, "exchange_response": response, "status": status}
    except Exception as exc:
        msg = f"真实订单提交失败，请检查 API 权限、余额和交易规则：{exc}"
        log_live_audit_event("订单提交失败", mode=str(settings.get("mode")), symbol=str(order_plan.get("symbol", "")), result="失败", reason=msg, risk_level="高", real_account=True)
        return {"ok": False, "message": msg, "preflight": preflight}


def fetch_live_order_status(order_id: str, symbol: str) -> dict[str, Any]:
    credentials = load_api_credentials_safely(False)
    if not credentials.get("configured"):
        return {"ok": False, "message": "API尚未配置，订单状态暂时无法获取。"}
    try:
        data = _signed_request("GET", "/api/v3/order", {"symbol": str(symbol).upper(), "orderId": order_id}, credentials, "spot", False)
        log_live_audit_event("订单状态回查", mode=str(load_live_settings().get("mode")), symbol=str(symbol).upper(), result=str(data.get("status", "未知")), reason=f"订单ID {order_id} 状态回查完成。", real_account=True)
        return {"ok": True, "message": "订单状态回查成功。", "order": data}
    except Exception as exc:
        msg = f"订单状态暂时无法获取，请稍后刷新：{exc}"
        log_live_audit_event("订单状态回查", mode=str(load_live_settings().get("mode")), symbol=str(symbol).upper(), result="失败", reason=msg, real_account=True)
        return {"ok": False, "message": msg}


def cancel_live_order(order_id: str, symbol: str, user_confirmed: bool) -> dict[str, Any]:
    if not user_confirmed:
        return {"ok": False, "message": "撤销真实订单需要二次确认：我确认撤销该真实订单。"}
    credentials = load_api_credentials_safely(False)
    if not credentials.get("configured"):
        return {"ok": False, "message": "API尚未配置，无法撤销真实订单。"}
    try:
        data = _signed_request("DELETE", "/api/v3/order", {"symbol": str(symbol).upper(), "orderId": order_id}, credentials, "spot", False)
        record = {"time": _now(), "order_id": order_id, "symbol": str(symbol).upper(), "order_status": data.get("status", "CANCELED"), "event": "手动撤单", "raw_status_summary": data.get("status")}
        save_live_order_record(record)
        log_live_audit_event("手动撤单", mode=str(load_live_settings().get("mode")), symbol=str(symbol).upper(), result=str(data.get("status", "完成")), reason=f"用户二次确认撤销真实订单 {order_id}。", real_account=True)
        return {"ok": True, "message": "真实订单撤销请求已提交。", "order": data}
    except Exception as exc:
        msg = f"撤单失败：{exc}"
        log_live_audit_event("撤单结果", mode=str(load_live_settings().get("mode")), symbol=str(symbol).upper(), result="失败", reason=msg, real_account=True)
        return {"ok": False, "message": msg}


def get_live_manual_execution_status() -> dict[str, Any]:
    settings = load_live_settings()
    return {
        "live_trading_enabled": LIVE_TRADING_ENABLED,
        "live_manual_enabled": bool(settings.get("live_manual_enabled")),
        "mode": settings.get("mode"),
        "kill_switch_enabled": bool(settings.get("kill_switch_enabled")),
        "max_live_notional_usdt": settings.get("max_live_notional_usdt"),
        "hard_max_live_notional_usdt": settings.get("hard_max_live_notional_usdt"),
        "daily_live_notional": _daily_live_notional(),
        "daily_limit": settings.get("daily_live_notional_limit_usdt"),
        "records": load_live_order_records(50),
    }


def block_live_execution(reason: str) -> dict[str, Any]:
    log_live_audit_event("风控拒绝", mode=str(load_live_settings().get("mode")), result="拒绝", reason=reason, risk_level="高", real_account=True)
    return {"ok": False, "message": reason}


def load_live_position_records(limit: int = 200) -> list[dict[str, Any]]:
    data = _read_json(LIVE_POSITION_JSON_PATH, [])
    return (data if isinstance(data, list) else [])[:limit]


def save_live_position_record(position_record: dict[str, Any]) -> dict[str, Any]:
    records = load_live_position_records(1000)
    record = dict(position_record)
    record.setdefault("updated_time", _now())
    existing_index = next((idx for idx, row in enumerate(records) if row.get("live_position_id") == record.get("live_position_id")), None)
    if existing_index is None:
        records.insert(0, record)
    else:
        records[existing_index] = {**records[existing_index], **record}
    _write_json(LIVE_POSITION_JSON_PATH, records[:1000])
    return record


def log_live_position_audit_event(event: str, symbol: str = "", result: str = "", reason: str = "", live_position_id: str = "", risk_level: str = "低") -> None:
    row = {
        "time": _now(),
        "event": event,
        "symbol": symbol,
        "live_position_id": live_position_id,
        "risk_level": risk_level,
        "result": result,
        "reason": reason,
        "real_account": True,
    }
    logs = _read_json(LIVE_POSITION_AUDIT_JSON_PATH, [])
    logs.insert(0, row)
    _write_json(LIVE_POSITION_AUDIT_JSON_PATH, logs[:500])
    try:
        with LIVE_POSITION_AUDIT_CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
            writer.writeheader()
            writer.writerows(logs[:500])
    except Exception as exc:
        print(f"[实盘持仓中心] 审计日志CSV写入失败 error={exc!r}")


def load_live_position_audit_log(limit: int = 100) -> list[dict[str, Any]]:
    data = _read_json(LIVE_POSITION_AUDIT_JSON_PATH, [])
    return (data if isinstance(data, list) else [])[:limit]


def _base_asset_from_symbol(symbol: str) -> str:
    symbol = str(symbol or "").upper()
    for quote in ["USDT", "BUSD", "USDC", "FDUSD", "BTC", "ETH", "BNB"]:
        if symbol.endswith(quote):
            return symbol[: -len(quote)]
    return symbol[:-4] if len(symbol) > 4 else symbol


def _quote_asset_from_symbol(symbol: str) -> str:
    symbol = str(symbol or "").upper()
    for quote in ["USDT", "BUSD", "USDC", "FDUSD", "BTC", "ETH", "BNB"]:
        if symbol.endswith(quote):
            return quote
    return "USDT"


def _record_qty(row: dict[str, Any]) -> float:
    return _to_float(row.get("executed_qty"), _to_float(row.get("executedQty"), _to_float(row.get("quantity"))))


def _record_price(row: dict[str, Any]) -> float:
    avg = _to_float(row.get("avg_price"), _to_float(row.get("avgPrice")))
    return avg or _to_float(row.get("price"))


def _record_notional(row: dict[str, Any]) -> float:
    notional = _to_float(row.get("notional"))
    if notional:
        return notional
    return _record_qty(row) * _record_price(row)


def get_live_spot_balances_readonly() -> dict[str, Any]:
    snapshot = get_live_account_snapshot(False, "spot")
    if not snapshot.get("ok"):
        return {"ok": False, "balances": [], "message": snapshot.get("message", "真实账户只读余额暂不可用。"), "updated_time": _now()}
    balances = []
    for row in snapshot.get("balances") or []:
        free = _to_float(row.get("free"))
        locked = _to_float(row.get("locked"))
        if free + locked <= 0:
            continue
        balances.append({"asset": row.get("asset"), "free": free, "locked": locked, "total": free + locked})
    return {"ok": True, "balances": balances, "message": "真实账户只读余额读取完成。", "updated_time": snapshot.get("updated_time", _now())}


def calculate_live_unrealized_pnl(position: dict[str, Any], current_price: float | None = None) -> dict[str, float]:
    price = _to_float(current_price, _to_float(position.get("current_price"), _to_float(position.get("avg_entry_price"))))
    qty = _to_float(position.get("remaining_quantity"))
    entry = _to_float(position.get("avg_entry_price"))
    unrealized = (price - entry) * qty if entry and qty else 0.0
    cost = entry * qty
    return {"current_price": price, "unrealized_pnl": unrealized, "unrealized_pnl_pct": (unrealized / cost * 100) if cost else 0.0}


def calculate_live_position_cost_basis(records: list[dict[str, Any]]) -> dict[str, Any]:
    buy_records = [row for row in records if _normalize_side(row.get("side")) == "BUY"]
    total_qty = sum(_record_qty(row) for row in buy_records)
    total_cost = sum(_record_notional(row) for row in buy_records)
    return {"avg_entry_price": total_cost / total_qty if total_qty else 0.0, "original_quantity": total_qty, "quote_cost": total_cost}


def calculate_live_position_risk(position: dict[str, Any]) -> dict[str, Any]:
    pct = _to_float(position.get("unrealized_pnl_pct"))
    if pct <= -3:
        return {"risk_level": "高", "system_suggested_exit": "建议手动减仓或平仓复核", "warnings": ["实盘持仓浮亏扩大，建议人工复核。"]}
    if pct >= 3:
        return {"risk_level": "中", "system_suggested_exit": "建议考虑部分止盈", "warnings": ["已出现浮盈，可生成部分平仓预览。"]}
    return {"risk_level": "低", "system_suggested_exit": "继续观察", "warnings": ["系统仅提醒，不会自动卖出真实资产。"]}


def identify_app_created_live_positions(current_prices: dict[str, float] | None = None) -> list[dict[str, Any]]:
    prices = current_prices or {}
    records = load_live_order_records(1000)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        symbol = str(row.get("symbol", "")).upper()
        if not symbol or str(row.get("market_type", "spot")) != "spot":
            continue
        if str(row.get("order_status", "")).upper() in {"REJECTED", "CANCELED", "EXPIRED"}:
            continue
        grouped.setdefault(symbol, []).append(row)

    positions: list[dict[str, Any]] = []
    for symbol, rows in grouped.items():
        buys = [row for row in rows if _normalize_side(row.get("side")) == "BUY"]
        sells = [row for row in rows if _normalize_side(row.get("side")) == "SELL"]
        if not buys:
            continue
        basis = calculate_live_position_cost_basis(buys)
        sold_qty = sum(_record_qty(row) for row in sells)
        realized_pnl = 0.0
        avg_entry = _to_float(basis.get("avg_entry_price"))
        for row in sells:
            realized_pnl += (_record_price(row) - avg_entry) * _record_qty(row)
        remaining = max(_to_float(basis.get("original_quantity")) - sold_qty, 0.0)
        current_price = _to_float(prices.get(symbol), avg_entry)
        pnl = calculate_live_unrealized_pnl({"avg_entry_price": avg_entry, "remaining_quantity": remaining}, current_price)
        position = {
            "live_position_id": f"live_pos_{symbol.lower()}",
            "symbol": symbol,
            "base_asset": _base_asset_from_symbol(symbol),
            "quote_asset": _quote_asset_from_symbol(symbol),
            "source": "system_order",
            "status": "closed" if remaining <= 1e-12 else ("partially_closed" if sold_qty > 0 else "open"),
            "entry_order_ids": [str(row.get("order_id", "")) for row in buys if row.get("order_id")],
            "exit_order_ids": [str(row.get("order_id", "")) for row in sells if row.get("order_id")],
            "entry_time": buys[-1].get("time", ""),
            "avg_entry_price": avg_entry,
            "current_price": pnl["current_price"],
            "original_quantity": basis.get("original_quantity", 0),
            "remaining_quantity": remaining,
            "sold_quantity": sold_qty,
            "quote_cost": basis.get("quote_cost", 0),
            "estimated_fee": _to_float(basis.get("quote_cost")) * 0.0004,
            "unrealized_pnl": pnl["unrealized_pnl"],
            "unrealized_pnl_pct": pnl["unrealized_pnl_pct"],
            "realized_pnl": realized_pnl,
            "total_pnl": realized_pnl + pnl["unrealized_pnl"],
            "committee_snapshot": buys[0].get("committee_snapshot") or {},
            "local_strategy_snapshot": buys[0].get("local_strategy_snapshot") or {},
            "created_time": buys[-1].get("time", ""),
            "updated_time": _now(),
        }
        position.update(calculate_live_position_risk(position))
        save_live_position_record(position)
        positions.append(position)
    return positions


def match_live_orders_to_holdings(current_prices: dict[str, float] | None = None) -> list[dict[str, Any]]:
    return identify_app_created_live_positions(current_prices)


def get_live_position_summary(current_prices: dict[str, float] | None = None) -> dict[str, Any]:
    system_positions = identify_app_created_live_positions(current_prices)
    balance_snapshot = get_live_spot_balances_readonly()
    system_assets = {row.get("base_asset") for row in system_positions if row.get("status") != "closed"}
    external_assets = []
    if balance_snapshot.get("ok"):
        for bal in balance_snapshot.get("balances", []):
            asset = str(bal.get("asset", ""))
            if asset in {"USDT", "BUSD", "USDC", "FDUSD"} or asset in system_assets:
                continue
            source = "dust" if _to_float(bal.get("total")) < 0.000001 else "external"
            external_assets.append({**bal, "source": source, "message": "外部持仓只读显示，默认不纳入系统策略统计。"})
    open_positions = [row for row in system_positions if row.get("status") != "closed"]
    return {
        "ok": True,
        "system_positions": system_positions,
        "open_system_positions": open_positions,
        "external_assets": external_assets,
        "balance_snapshot": balance_snapshot,
        "system_position_count": len(open_positions),
        "external_asset_count": len(external_assets),
        "total_unrealized_pnl": sum(_to_float(row.get("unrealized_pnl")) for row in open_positions),
        "recent_audit": load_live_position_audit_log(50),
        "message": "当前暂无系统实盘持仓。" if not open_positions else "实盘持仓识别完成。",
    }


def sync_live_position_status(current_prices: dict[str, float] | None = None) -> dict[str, Any]:
    summary = get_live_position_summary(current_prices)
    log_live_position_audit_event("识别实盘持仓", result="完成", reason=f"系统持仓 {summary.get('system_position_count', 0)}，外部资产 {summary.get('external_asset_count', 0)}。")
    return summary


def create_live_exit_plan(position: dict[str, Any], user_inputs: dict[str, Any]) -> dict[str, Any]:
    ratio = min(max(_to_float(user_inputs.get("exit_ratio"), 1.0), 0.0), 1.0)
    remaining = _to_float(position.get("remaining_quantity"))
    price = _to_float(user_inputs.get("price"), _to_float(position.get("current_price"), _to_float(position.get("avg_entry_price"))))
    quantity = min(remaining, remaining * ratio)
    estimated_value = quantity * price
    plan = {
        "exit_plan_id": f"live_exit_plan_{uuid.uuid4().hex[:12]}",
        "live_position_id": position.get("live_position_id"),
        "symbol": str(position.get("symbol", "")).upper(),
        "market_type": "spot",
        "side": "SELL",
        "exit_ratio": ratio,
        "exit_quantity": quantity,
        "quantity": quantity,
        "order_type": _normalize_order_type(user_inputs.get("order_type", "LIMIT")),
        "price": price,
        "time_in_force": "GTC",
        "estimated_value": estimated_value,
        "quote_amount": estimated_value,
        "estimated_fee": estimated_value * 0.0004,
        "estimated_pnl": (price - _to_float(position.get("avg_entry_price"))) * quantity,
        "exit_reason": user_inputs.get("exit_reason") or "用户手动",
        "source": "manual_confirm",
        "created_time": _now(),
        "status": "draft",
        "position_snapshot": position,
    }
    log_live_position_audit_event("创建平仓计划", symbol=plan["symbol"], live_position_id=str(plan.get("live_position_id", "")), result="已创建", reason=f"用户创建 {ratio * 100:.0f}% 平仓计划。")
    return plan


def validate_live_exit_order(exit_plan: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    position = exit_plan.get("position_snapshot") or {}
    qty = _to_float(exit_plan.get("exit_quantity"), _to_float(exit_plan.get("quantity")))
    remaining = _to_float(position.get("remaining_quantity"))
    if str(exit_plan.get("market_type", "spot")) != "spot":
        errors.append("本版本暂不开放合约真实持仓管理。")
    if _normalize_side(exit_plan.get("side")) != "SELL":
        errors.append("平仓订单必须为 Spot SELL。")
    if qty <= 0:
        errors.append("平仓数量必须大于 0。")
    if remaining and qty > remaining + 1e-12:
        errors.append("平仓数量超过系统记录的剩余持仓数量。")
    if load_live_settings().get("kill_switch_enabled"):
        errors.append("安全锁已开启，当前不允许提交真实平仓订单。")
    rule_check = validate_order_against_exchange_rules({**exit_plan, "quantity": qty, "side": "SELL", "testnet": False})
    errors.extend(rule_check.get("errors") or [])
    warnings.extend(rule_check.get("warnings") or [])
    return {"ok": not errors, "errors": errors, "warnings": warnings, "rule_check": rule_check}


def preview_live_exit_order(exit_plan: dict[str, Any]) -> dict[str, Any]:
    position = exit_plan.get("position_snapshot") or {}
    validation = validate_live_exit_order(exit_plan)
    remaining = _to_float(position.get("remaining_quantity"))
    qty = _to_float(exit_plan.get("exit_quantity"))
    preview = {
        "ok": validation.get("ok"),
        "message": "这只是平仓预览，尚未执行真实卖出。",
        "exit_plan": exit_plan,
        "validation": validation,
        "remaining_after_exit": max(remaining - qty, 0.0),
        "remaining_value_after_exit": max(remaining - qty, 0.0) * _to_float(exit_plan.get("price")),
    }
    log_live_position_audit_event("生成平仓预览" if preview["ok"] else "平仓预览失败", symbol=str(exit_plan.get("symbol", "")), live_position_id=str(exit_plan.get("live_position_id", "")), result="通过" if preview["ok"] else "失败", reason="；".join(validation.get("errors") or []) or "平仓预览生成。")
    return preview


def run_exit_spot_test_order(exit_plan: dict[str, Any]) -> dict[str, Any]:
    validation = validate_live_exit_order(exit_plan)
    if not validation.get("ok"):
        result = {"ok": False, "message": "平仓测试订单验证失败：" + "；".join(validation.get("errors") or []), "validation": validation}
        log_live_position_audit_event("平仓 Test Order", symbol=str(exit_plan.get("symbol", "")), live_position_id=str(exit_plan.get("live_position_id", "")), result="失败", reason=result["message"], risk_level="高")
        return result
    result = run_spot_test_order({**exit_plan, "quantity": exit_plan.get("exit_quantity"), "side": "SELL", "source": "实盘持仓平仓"})
    log_live_position_audit_event("平仓 Test Order", symbol=str(exit_plan.get("symbol", "")), live_position_id=str(exit_plan.get("live_position_id", "")), result="通过" if result.get("ok") else "失败", reason=result.get("message", ""), risk_level="低" if result.get("ok") else "高")
    return result


def require_exit_manual_confirmation(exit_plan: dict[str, Any], confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        return {"ok": False, "message": "尚未点击确认：我确认这是小资金真实平仓订单，并理解风险。"}
    log_live_position_audit_event("用户确认平仓", symbol=str(exit_plan.get("symbol", "")), live_position_id=str(exit_plan.get("live_position_id", "")), result="已确认", reason="用户完成第一层真实平仓确认。")
    return {"ok": True, "message": "第一层平仓确认已完成。"}


def require_exit_confirmation_phrase(exit_plan: dict[str, Any], phrase: str) -> dict[str, Any]:
    required = "我确认执行小资金实盘平仓"
    if str(phrase or "").strip() != required:
        return {"ok": False, "message": f"确认短句不匹配，请输入：{required}"}
    log_live_position_audit_event("用户输入平仓确认短句", symbol=str(exit_plan.get("symbol", "")), live_position_id=str(exit_plan.get("live_position_id", "")), result="已确认", reason="用户输入平仓确认短句正确。")
    return {"ok": True, "message": "平仓确认短句已通过。"}


def run_live_exit_preflight(exit_plan: dict[str, Any], test_order_result: dict[str, Any] | None = None, user_confirmed: bool = False, confirmation_phrase: str = "") -> dict[str, Any]:
    settings = load_live_settings()
    permission = check_api_permissions(False, "spot")
    validation = validate_live_exit_order(exit_plan)
    preview = preview_live_exit_order(exit_plan)
    confirm = require_exit_manual_confirmation(exit_plan, user_confirmed)
    phrase = require_exit_confirmation_phrase(exit_plan, confirmation_phrase)
    checklist = [
        _check("LIVE_TRADING_ENABLED 显式启用", LIVE_TRADING_ENABLED, "LIVE_TRADING_ENABLED=false，系统阻止真实平仓。"),
        _check("Live Manual 模式已开启", settings.get("mode") == "live_manual" and settings.get("live_manual_enabled"), "未开启 Live Manual 模式。"),
        _check("仅支持 Spot 现货平仓", str(exit_plan.get("market_type", "spot")) == "spot", "本版本暂不开放合约真实持仓管理。"),
        _check("安全锁未开启", not settings.get("kill_switch_enabled"), "安全锁已开启，当前不允许提交真实平仓订单。"),
        _check("API权限可交易", bool(permission.get("can_trade")), permission.get("message", "API不可交易。")),
        _check("提现权限关闭", not permission.get("can_withdraw"), "提现权限开启，高危阻止。"),
        _check("平仓规则校验通过", validation.get("ok"), "；".join(validation.get("errors") or []) or "规则校验失败。"),
        _check("平仓预览通过", preview.get("ok"), "平仓预览未通过。"),
        _check("平仓 Spot Test Order 通过", bool((test_order_result or {}).get("ok")), "平仓 Spot Test Order 未通过。"),
        _check("第一层人工确认", confirm.get("ok"), confirm.get("message", "")),
        _check("确认短句正确", phrase.get("ok"), phrase.get("message", "")),
    ]
    failed = [item for item in checklist if item["status"] == "失败"]
    ok = not failed
    log_live_position_audit_event("平仓执行前检查" if ok else "平仓执行前检查失败", symbol=str(exit_plan.get("symbol", "")), live_position_id=str(exit_plan.get("live_position_id", "")), result="通过" if ok else "失败", reason="；".join(item["message"] for item in failed) or "全部检查通过。", risk_level="低" if ok else "高")
    return {"ok": ok, "checklist": checklist, "preview": preview, "message": "小资金真实平仓执行前检查通过。" if ok else "检查未通过，系统阻止真实平仓提交。"}


def submit_live_exit_order(exit_plan: dict[str, Any], test_order_result: dict[str, Any], user_confirmed: bool, confirmation_phrase: str) -> dict[str, Any]:
    preflight = run_live_exit_preflight(exit_plan, test_order_result, user_confirmed, confirmation_phrase)
    if not preflight.get("ok"):
        return {"ok": False, "message": preflight.get("message"), "preflight": preflight}
    credentials = load_api_credentials_safely(False)
    try:
        order_plan = {**exit_plan, "quantity": exit_plan.get("exit_quantity"), "side": "SELL", "source": "实盘持仓平仓"}
        response = _signed_request("POST", "/api/v3/order", _binance_order_params(order_plan), credentials, "spot", False)
        order_id = str(response.get("orderId", ""))
        record = {
            "time": _now(),
            "event": "真实平仓订单",
            "order_id": order_id,
            "client_order_id": response.get("clientOrderId"),
            "symbol": response.get("symbol") or exit_plan.get("symbol"),
            "market_type": "spot",
            "side": "SELL",
            "order_type": exit_plan.get("order_type"),
            "price": exit_plan.get("price"),
            "quantity": exit_plan.get("exit_quantity"),
            "notional": exit_plan.get("estimated_value"),
            "order_status": response.get("status", "SUBMITTED"),
            "executed_qty": response.get("executedQty"),
            "avg_price": "",
            "source": "实盘持仓平仓",
            "live_position_id": exit_plan.get("live_position_id"),
            "exit_ratio": exit_plan.get("exit_ratio"),
            "exit_reason": exit_plan.get("exit_reason"),
            "estimated_pnl": exit_plan.get("estimated_pnl"),
            "confirmation_phrase_ok": True,
            "raw_status_summary": response.get("status"),
        }
        save_live_order_record(record)
        log_live_position_audit_event("提交真实平仓订单", symbol=str(exit_plan.get("symbol", "")), live_position_id=str(exit_plan.get("live_position_id", "")), result="已提交", reason=f"真实 Spot SELL 平仓订单已提交，订单ID {order_id}", risk_level="高")
        log_live_audit_event("真实平仓订单提交", mode=str(load_live_settings().get("mode")), symbol=str(exit_plan.get("symbol", "")), result="已提交", reason=f"真实 Spot SELL 平仓订单已提交，订单ID {order_id}", risk_level="高", real_account=True)
        status = fetch_live_order_status(order_id, str(exit_plan.get("symbol", ""))) if order_id else {"ok": False, "message": "订单ID暂不可用。"}
        sync_live_position_status()
        return {"ok": True, "message": "小资金真实 Spot 平仓订单已提交。", "order": record, "exchange_response": response, "status": status}
    except Exception as exc:
        msg = f"真实平仓提交失败，请检查 API 权限、余额和网络：{exc}"
        log_live_position_audit_event("平仓失败", symbol=str(exit_plan.get("symbol", "")), live_position_id=str(exit_plan.get("live_position_id", "")), result="失败", reason=msg, risk_level="高")
        return {"ok": False, "message": msg, "preflight": preflight}


def submit_live_partial_exit_order(exit_plan: dict[str, Any], ratio: float, test_order_result: dict[str, Any], user_confirmed: bool, confirmation_phrase: str) -> dict[str, Any]:
    plan = dict(exit_plan)
    position = plan.get("position_snapshot") or {}
    ratio = min(max(_to_float(ratio, _to_float(plan.get("exit_ratio"), 1.0)), 0.0), 1.0)
    plan["exit_ratio"] = ratio
    plan["exit_quantity"] = _to_float(position.get("remaining_quantity")) * ratio
    plan["quantity"] = plan["exit_quantity"]
    plan["estimated_value"] = plan["exit_quantity"] * _to_float(plan.get("price"))
    plan["quote_amount"] = plan["estimated_value"]
    return submit_live_exit_order(plan, test_order_result, user_confirmed, confirmation_phrase)


def fetch_exit_order_status(order_id: str, symbol: str) -> dict[str, Any]:
    return fetch_live_order_status(order_id, symbol)


def record_live_exit_audit(event: dict[str, Any] | str) -> None:
    if isinstance(event, dict):
        log_live_position_audit_event(
            str(event.get("event", "实盘持仓审计")),
            symbol=str(event.get("symbol", "")),
            result=str(event.get("result", "")),
            reason=str(event.get("reason", "")),
            live_position_id=str(event.get("live_position_id", "")),
            risk_level=str(event.get("risk_level", "低")),
        )
    else:
        log_live_position_audit_event(str(event))


def generate_live_position_review_snapshot(position: dict[str, Any]) -> dict[str, Any]:
    risk = calculate_live_position_risk(position)
    return {
        "live_position_id": position.get("live_position_id"),
        "symbol": position.get("symbol"),
        "hold_decision": risk.get("system_suggested_exit", "继续观察"),
        "risk_level": risk.get("risk_level", "低"),
        "reasons": ["基于系统真实订单记录、当前价格和盈亏估算生成。"],
        "warnings": risk.get("warnings", []),
        "suggested_exit_ratio": "50%" if risk.get("system_suggested_exit") == "建议考虑部分止盈" else "0%",
        "auto_action": "none",
        "updated_time": _now(),
    }


def record_live_order_audit(event: dict[str, Any] | str) -> None:
    if isinstance(event, dict):
        log_live_audit_event(
            str(event.get("event", "实盘审计")),
            mode=str(event.get("mode", "")),
            symbol=str(event.get("symbol", "")),
            risk_level=str(event.get("risk_level", "低")),
            result=str(event.get("result", "")),
            reason=str(event.get("reason", "")),
            real_account=bool(event.get("real_account", True)),
        )
    else:
        log_live_audit_event(str(event), real_account=True)


def run_testnet_order_flow(order_plan: dict[str, Any]) -> dict[str, Any]:
    settings = load_live_settings()
    if settings.get("mode") != "testnet":
        return {"ok": False, "message": "当前不是 Testnet 模式，已阻止测试网络流程。"}
    validation = run_test_order_validation(order_plan)
    if not validation.get("ok"):
        return validation
    return {"ok": True, "message": "Testnet 流程预检通过。本版本只预留测试网流程，不自动提交测试网订单。", "preview": validation.get("preview")}


def block_live_order(reason: str) -> dict[str, Any]:
    log_live_audit_event("风控拒绝", result="拒绝", reason=reason, risk_level="高")
    return {"ok": False, "message": reason}


def trigger_live_kill_switch(reason: str) -> dict[str, Any]:
    settings = load_live_settings()
    settings["kill_switch_enabled"] = True
    settings["kill_switch_reason"] = reason or "用户触发实盘安全锁。"
    save_live_settings(settings)
    log_live_audit_event("安全锁触发", result="已锁定", reason=settings["kill_switch_reason"], risk_level="高")
    return settings


def release_live_kill_switch(reason: str) -> dict[str, Any]:
    settings = load_live_settings()
    settings["kill_switch_enabled"] = False
    settings["kill_switch_reason"] = ""
    save_live_settings(settings)
    log_live_audit_event("安全锁解除", result="已解除", reason=reason or "用户解除安全锁。", risk_level="中")
    return settings


def get_live_safety_status() -> dict[str, Any]:
    settings = load_live_settings()
    credentials = load_api_credentials_safely(settings.get("mode") == "testnet")
    connection = check_api_connection(settings.get("mode") == "testnet", settings.get("market_type", "spot"))
    permission = check_api_permissions(settings.get("mode") == "testnet", settings.get("market_type", "spot")) if credentials["configured"] else {"permission_status": "未配置", "can_withdraw": False, "message": "API尚未配置。"}
    withdraw = check_withdraw_permission_disabled(permission)
    ip_status = check_ip_restriction_status(permission, settings)
    sim_stats = calculate_sim_performance_stats()
    replay = analyze_replay_learning()
    allowed_candidates = get_strategy_candidates_for_simulation()
    allow_live_candidate = bool(allowed_candidates) and _to_float(sim_stats.get("total_trades")) >= 30 and _to_float(sim_stats.get("profit_factor")) >= 1.2 and replay.get("summary", {}).get("data_quality") != "poor" and not settings.get("kill_switch_enabled")
    return {"settings": settings, "credentials": {k: v for k, v in credentials.items() if k != "api_secret"}, "connection": connection, "permission": permission, "withdraw": withdraw, "ip_status": ip_status, "sim_stats": sim_stats, "replay_summary": replay.get("summary", {}), "strategy_candidates": get_strategy_candidates(), "allowed_strategy_candidates": allowed_candidates, "allow_live_candidate": allow_live_candidate, "recent_audit": load_live_audit_log(20), "safety_notice": "当前为实盘前置安全中心。默认不会执行真实订单。只有通过全部安全检查并人工确认后，后续版本才允许进入小资金实盘流程。"}


def log_live_audit_event(event: str, mode: str = "", symbol: str = "", risk_level: str = "低", result: str = "", reason: str = "", real_account: bool = False) -> None:
    row = {"time": _now(), "event": event, "mode": mode, "symbol": symbol, "risk_level": risk_level, "result": result, "reason": reason, "real_account": bool(real_account)}
    logs = _read_json(AUDIT_JSON_PATH, [])
    logs.insert(0, row)
    _write_json(AUDIT_JSON_PATH, logs[:500])
    try:
        with AUDIT_CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
            writer.writeheader()
            writer.writerows(logs[:500])
    except Exception as exc:
        print(f"[实盘安全中心] 审计日志CSV写入失败 error={exc!r}")


def load_live_audit_log(limit: int = 100) -> list[dict[str, Any]]:
    data = _read_json(AUDIT_JSON_PATH, [])
    return (data if isinstance(data, list) else [])[:limit]

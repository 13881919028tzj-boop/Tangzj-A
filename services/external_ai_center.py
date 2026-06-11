"""DeepSeek/Gemini 外部 AI 影子测试中心。

安全边界：
- 只发送脱敏交易摘要，不发送 API Secret、完整 API Key 或真实账户敏感明细。
- 只输出正式投票委员意见，不下单、不改仓位、不绕过风险委员和实盘安全委员。
- 外部 AI 失败、超时、限频或解析失败时，主系统继续运行。
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from services.external_ai_client import safe_post_json, test_ssl_environment

try:
    from services.sim_trade_engine import load_sim_trade_history
except Exception:  # pragma: no cover
    load_sim_trade_history = None


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SETTINGS_PATH = DATA_DIR / "external_ai_settings.json"
AUDIT_JSON_PATH = DATA_DIR / "external_ai_audit_log.json"
AUDIT_CSV_PATH = DATA_DIR / "external_ai_audit_log.csv"
CACHE_PATH = DATA_DIR / "external_ai_cache.json"

SENSITIVE_KEYS = {
    "api_key",
    "api_secret",
    "secret",
    "signature",
    "listenkey",
    "listen_key",
    "withdraw",
    "withdraw_permission",
    "password",
    "token",
    "authorization",
    "credential",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "deepseek": {
        "mode": "shadow",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "rate_limit_seconds": 60,
        "failure_cooldown_seconds": 120,
        "timeout_seconds": 20,
        "max_input_chars": 6000,
        "cache_enabled": True,
        "cache_ttl_seconds": 300,
        "show_in_committee": True,
        "include_in_replay_stats": True,
        "permissions": {
            "market_summary": True,
            "local_strategy_summary": True,
            "committee_votes_summary": True,
            "risk_radar_summary": True,
            "simulation_summary": True,
            "replay_error_tags": True,
            "strategy_factory_summary": True,
            "account_sensitive_info": False,
            "api_keys": False,
            "trade_execution": False,
        },
    },
    "gemini": {
        "mode": "shadow",
        "base_url": "https://generativelanguage.googleapis.com",
        "model": "gemini-1.5-flash",
        "rate_limit_seconds": 60,
        "failure_cooldown_seconds": 120,
        "timeout_seconds": 20,
        "max_input_chars": 6000,
        "cache_enabled": True,
        "cache_ttl_seconds": 300,
        "show_in_committee": True,
        "include_in_replay_stats": True,
        "permissions": {
            "market_summary": True,
            "chart_summary": True,
            "chart_screenshot": False,
            "local_strategy_summary": True,
            "committee_summary": True,
            "risk_summary": True,
            "account_sensitive_info": False,
            "api_keys": False,
            "trade_execution": False,
        },
    },
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _now_ts() -> float:
    return time.time()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_dotenv_values() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        return {}
    return values


def _get_api_key(provider: str) -> str:
    provider = provider.lower().strip()
    if provider == "deepseek":
        keys = ["DEEPSEEK_API_KEY"]
    else:
        keys = ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GEMINI_API_KEY"]
    dotenv = _load_dotenv_values()
    for env_key in keys:
        value = os.getenv(env_key) or dotenv.get(env_key)
        if value:
            return value
    return ""


def mask_api_key(api_key: str | None) -> str:
    value = str(api_key or "").strip()
    if not value:
        return "未配置"
    if len(value) <= 8:
        return value[:2] + "****"
    return f"{value[:4]}****{value[-4:]}"


def get_external_ai_secret_status(provider: str) -> dict[str, Any]:
    api_key = _get_api_key(provider)
    key_names = "DEEPSEEK_API_KEY" if provider.lower().strip() == "deepseek" else "GEMINI_API_KEY / GOOGLE_API_KEY / GOOGLE_GEMINI_API_KEY"
    return {
        "configured": bool(api_key),
        "masked_api_key": mask_api_key(api_key),
        "secret_status": "已隐藏" if api_key else "未配置",
        "source": f".env / 环境变量（{key_names}）" if api_key else f"未配置（支持 {key_names}）",
    }


def load_external_ai_settings() -> dict[str, Any]:
    try:
        if not SETTINGS_PATH.exists():
            return json.loads(json.dumps(DEFAULT_SETTINGS, ensure_ascii=False))
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return _deep_merge(DEFAULT_SETTINGS, raw if isinstance(raw, dict) else {})
    except Exception:
        return json.loads(json.dumps(DEFAULT_SETTINGS, ensure_ascii=False))


def save_external_ai_settings(settings: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    safe = _deep_merge(DEFAULT_SETTINGS, settings or {})
    for provider in ("deepseek", "gemini"):
        safe.setdefault(provider, {})
        safe[provider].pop("api_key", None)
        safe[provider].pop("api_secret", None)
        perms = safe[provider].setdefault("permissions", {})
        perms["account_sensitive_info"] = False
        perms["api_keys"] = False
        perms["trade_execution"] = False
    SETTINGS_PATH.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    log_external_ai_audit_event("接口配置保存", provider="system", result="已保存", reason="用户保存外部AI脱敏配置。")
    return safe


def _short_list(values: Any, limit: int = 4) -> list[Any]:
    return list(values or [])[:limit] if isinstance(values, list) else []


def build_external_ai_context(data: dict[str, Any]) -> dict[str, Any]:
    ticker = data.get("ticker") or {}
    signal = data.get("signal_analysis") or {}
    orderbook = data.get("orderbook_analysis") or {}
    local = data.get("local_strategy") or {}
    radar = data.get("radar") or {}
    capital = data.get("capital") or {}
    liquidation = data.get("liquidation") or {}
    derivatives = data.get("derivatives") or {}
    whale = data.get("whale") or {}
    dealer = data.get("dealer") or {}
    watch = data.get("watchlist_item") or {}
    votes = data.get("member_votes") or []
    return {
        "symbol": data.get("symbol"),
        "timestamp": data.get("timestamp") or _now(),
        "price_summary": {
            "last_price": ticker.get("last_price"),
            "change_pct": ticker.get("price_change_percent"),
            "volume": ticker.get("quote_volume"),
        },
        "kline_summary": {
            "rows_count": len(data.get("rows") or []),
            "ma20": signal.get("ma20"),
            "ma60": signal.get("ma60"),
            "ma200": signal.get("ma200"),
        },
        "indicator_summary": {
            "trend_score": signal.get("trend_score"),
            "risk_score": signal.get("risk_score"),
            "rsi": signal.get("rsi"),
            "macd_signal": signal.get("macd_signal"),
        },
        "market_structure": {
            "structure": signal.get("market_structure"),
            "suggestion": signal.get("suggestion"),
        },
        "orderbook_summary": {
            "buy_ratio": orderbook.get("buy_ratio"),
            "sell_ratio": orderbook.get("sell_ratio"),
            "bias": orderbook.get("bias"),
            "large_bid": orderbook.get("large_bid"),
            "large_ask": orderbook.get("large_ask"),
        },
        "whale_summary": {
            "score": whale.get("score"),
            "direction": whale.get("direction"),
            "net_flow_5m": whale.get("net_flow_5m"),
            "net_flow_15m": whale.get("net_flow_15m"),
            "dealer_state": dealer.get("state"),
            "dealer_explanation": dealer.get("explanation"),
        },
        "derivatives_summary": {
            "capital_score": capital.get("score"),
            "capital_state": capital.get("state"),
            "funding_rate": (derivatives.get("funding") or {}).get("rate"),
            "long_short_ratio": (derivatives.get("long_short") or {}).get("account_ratio"),
        },
        "liquidation_summary": {
            "risk_score": liquidation.get("risk_score"),
            "risk_level": liquidation.get("risk_level"),
            "squeeze_state": liquidation.get("squeeze_state"),
        },
        "local_strategy_summary": {
            "action": local.get("action"),
            "direction": local.get("direction"),
            "strategy_name": local.get("strategy_name"),
            "confidence": local.get("confidence"),
            "risk_score": local.get("risk_score"),
            "trade_permission": local.get("trade_permission"),
            "position_suggestion": local.get("position_suggestion"),
            "risk_reward_ratio": local.get("risk_reward_ratio"),
            "data_quality": local.get("data_quality"),
            "reasons": _short_list(local.get("reasons")),
            "risks": _short_list(local.get("risks")),
        },
        "committee_summary": [
            {
                "member_name": vote.get("member_name"),
                "direction": vote.get("direction"),
                "vote": vote.get("vote"),
                "confidence": vote.get("confidence"),
                "risk_level": vote.get("risk_level"),
                "veto": vote.get("veto"),
            }
            for vote in votes
            if str(vote.get("member_name", "")) not in {"DeepSeek委员", "Gemini委员"}
        ][:12],
        "risk_summary": {
            "overall_score": radar.get("overall_score"),
            "trade_safety": radar.get("trade_safety"),
            "risk_level": radar.get("risk_level"),
            "market_explanation": radar.get("market_explanation"),
        },
        "watchlist_summary": {
            "category": watch.get("category"),
            "watch_score": watch.get("watch_score"),
            "status": (watch.get("tracking") or {}).get("status"),
        },
        "data_quality": local.get("data_quality") or {"level": "poor"},
    }


def _contains_sensitive_key(obj: Any) -> bool:
    if isinstance(obj, dict):
        for key, value in obj.items():
            lowered = str(key).lower()
            if any(s in lowered for s in SENSITIVE_KEYS):
                return True
            if _contains_sensitive_key(value):
                return True
    elif isinstance(obj, list):
        return any(_contains_sensitive_key(item) for item in obj)
    return False


def sanitize_context_for_external_ai(context: dict[str, Any]) -> dict[str, Any]:
    safe = json.loads(json.dumps(context, ensure_ascii=False, default=str))
    if _contains_sensitive_key(safe):
        raise ValueError("外部AI上下文包含敏感字段，已阻止调用。")
    safe["contains_sensitive_data"] = False
    safe["can_execute_trade"] = False
    return safe


def _context_hash(context: dict[str, Any], provider: str) -> str:
    payload = json.dumps({"provider": provider, "context": context}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _trim_text(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + "\n...已截断，避免发送过长上下文。"


def build_deepseek_prompt(context: dict[str, Any], max_chars: int = 6000) -> str:
    payload = _trim_text(json.dumps(context, ensure_ascii=False, indent=2), max_chars)
    return (
        "你是AI交易委员会的DeepSeek正式投票委员，只能做独立复核和投票，不能下单，不能给绝对化结论。\n"
        "请重点检查本地策略是否自洽、是否过度乐观、是否有诱多诱空、追涨追空和风险收益比问题。\n"
        "必须只输出JSON，不要输出Markdown。字段：member_name, mode, direction, confidence, risk_level, vote, soft_veto, main_opinion, reasons, risks, conflicts_found, suggested_adjustment, summary。\n"
        f"脱敏上下文如下：\n{payload}"
    )


def build_gemini_prompt(context: dict[str, Any], max_chars: int = 6000) -> str:
    payload = _trim_text(json.dumps(context, ensure_ascii=False, indent=2), max_chars)
    return (
        "你是AI交易委员会的Gemini正式投票委员，只能做图形结构、多模型复核和投票，不能下单，不能绕过风控。\n"
        "请基于K线摘要、指标摘要和盘口/大单摘要判断图形偏多、偏空还是中性，指出是否追高追空或假突破。\n"
        "必须只输出JSON，不要输出Markdown。字段：member_name, mode, chart_bias, direction, confidence, risk_level, vote, soft_veto, chart_observation, reasons, risks, suggested_adjustment, summary。\n"
        f"脱敏上下文如下：\n{payload}"
    )


def _extract_json_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("外部AI返回为空。")
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        raise


def _normalize_ai_result(provider: str, parsed: dict[str, Any], source: str, duration_ms: int, context_hash: str) -> dict[str, Any]:
    name = "DeepSeek委员" if provider == "deepseek" else "Gemini委员"
    direction = str(parsed.get("direction") or "neutral")
    if direction not in {"long", "short", "neutral"}:
        direction = "neutral"
    risk_level = str(parsed.get("risk_level") or "中")
    if risk_level not in {"低", "中", "高", "极高"}:
        risk_level = "中"
    vote = str(parsed.get("vote") or "观望")
    soft_veto = bool(parsed.get("soft_veto")) or "软否决" in vote
    support_trade = direction in {"long", "short"} and ("支持" in vote or vote.lower() in {"support", "buy", "sell"})
    result = {
        "member_name": name,
        "member_type": "official",
        "mode": "formal",
        "direction": direction,
        "direction_text": "偏多" if direction == "long" else "偏空" if direction == "short" else "中性",
        "confidence": max(0, min(100, int(_to_float(parsed.get("confidence"), 0)))),
        "risk_level": risk_level,
        "vote": vote,
        "support_trade": support_trade,
        "veto": False,
        "soft_veto": soft_veto,
        "main_opinion": str(parsed.get("main_opinion") or parsed.get("chart_observation") or "外部AI正式投票复核完成。"),
        "chart_bias": str(parsed.get("chart_bias") or ("偏多" if direction == "long" else "偏空" if direction == "short" else "中性")),
        "chart_observation": str(parsed.get("chart_observation") or parsed.get("main_opinion") or "暂无图形观察。"),
        "reasons": _short_list(parsed.get("reasons"), 5) or ["外部AI返回了结构化影子意见。"],
        "risks": _short_list(parsed.get("risks"), 5) or ["外部AI作为正式委员参与投票，但不能执行交易或硬否决。"],
        "conflicts_found": _short_list(parsed.get("conflicts_found"), 5),
        "suggested_adjustment": str(parsed.get("suggested_adjustment") or "不调整"),
        "summary": str(parsed.get("summary") or "外部AI正式复核完成，参与权重投票但不执行交易。"),
        "shadow": False,
        "official": True,
        "participates_in_vote": True,
        "status": "正常" if source == "实时调用" else source,
        "source": source,
        "duration_ms": duration_ms,
        "updated_time": _now(),
        "context_hash": context_hash,
    }
    return validate_external_ai_response(result)


def validate_external_ai_response(result: dict[str, Any]) -> dict[str, Any]:
    result["veto"] = False
    direction = str(result.get("direction") or "neutral")
    vote = str(result.get("vote") or "")
    result["support_trade"] = bool(direction in {"long", "short"} and ("支持" in vote or vote.lower() in {"support", "buy", "sell"}))
    result["shadow"] = False
    result["official"] = True
    result["member_type"] = "official"
    result["participates_in_vote"] = True
    result["mode"] = "formal"
    result["can_execute_trade"] = False
    result["contains_sensitive_data"] = False
    return result


def _load_cache() -> list[dict[str, Any]]:
    try:
        if not CACHE_PATH.exists():
            return []
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_cache(rows: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(rows[:300], ensure_ascii=False, indent=2), encoding="utf-8")


def load_cached_external_ai_result(symbol: str, ai_name: str, context_hash: str | None = None) -> dict[str, Any] | None:
    now = datetime.now()
    for row in _load_cache():
        if str(row.get("symbol")) != str(symbol) or str(row.get("ai_name")) != str(ai_name):
            continue
        if context_hash and row.get("context_hash") != context_hash:
            continue
        try:
            expired = datetime.strptime(str(row.get("expired_time")), "%Y-%m-%d %H:%M:%S")
        except Exception:
            expired = now - timedelta(seconds=1)
        if expired >= now:
            result = row.get("result") or {}
            if isinstance(result, dict):
                result["source"] = "缓存结果"
                result["status"] = "缓存"
                return result
    return None


def cache_external_ai_result(symbol: str, ai_name: str, context_hash: str, result: dict[str, Any], ttl_seconds: int) -> None:
    rows = [row for row in _load_cache() if not (row.get("symbol") == symbol and row.get("ai_name") == ai_name and row.get("context_hash") == context_hash)]
    rows.insert(
        0,
        {
            "ai_name": ai_name,
            "symbol": symbol,
            "context_hash": context_hash,
            "result": result,
            "created_time": _now(),
            "expired_time": (datetime.now() + timedelta(seconds=max(30, ttl_seconds))).strftime("%Y-%m-%d %H:%M:%S"),
            "mode": result.get("mode", "shadow"),
        },
    )
    _save_cache(rows)


def _recent_audit(provider: str, symbol: str) -> dict[str, Any] | None:
    for row in load_external_ai_audit_log(200):
        if row.get("ai_name") == provider and row.get("symbol") == symbol and row.get("event") in {"影子委员复核", "影子委员调用失败", "影子委员解析失败"}:
            return row
    return None


def _rate_limited(provider: str, symbol: str, cfg: dict[str, Any]) -> tuple[bool, str]:
    last = _recent_audit(provider, symbol)
    if not last:
        return False, ""
    try:
        last_ts = datetime.strptime(str(last.get("time")), "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return False, ""
    elapsed = _now_ts() - last_ts
    wait = _to_float(cfg.get("failure_cooldown_seconds"), 120) if last.get("failed") else _to_float(cfg.get("rate_limit_seconds"), 60)
    if elapsed < wait:
        return True, f"外部AI调用过于频繁，剩余冷却约 {int(wait - elapsed)} 秒。"
    return False, ""


def _http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    result = safe_post_json(url, headers, payload, timeout=timeout)
    if result.get("ok"):
        return result.get("data") or {}
    error_type = str(result.get("error_type", "unknown_error"))
    error_message = str(result.get("error_message", "外部 AI 请求失败。"))
    suggestion = str(result.get("suggestion", ""))
    if error_type == "ssl_error":
        raise RuntimeError(f"SSL证书验证失败。{error_message}。{suggestion}")
    if error_type == "certifi_missing":
        raise RuntimeError(f"certifi 未安装或不可用。{error_message}。{suggestion}")
    raise RuntimeError(f"{error_message}。{suggestion}")


def _call_deepseek(prompt: str, cfg: dict[str, Any], api_key: str) -> str:
    base = str(cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    url = base if base.endswith("/chat/completions") else f"{base}/v1/chat/completions"
    payload = {
        "model": str(cfg.get("model") or "deepseek-chat"),
        "messages": [
            {"role": "system", "content": "你是交易委员会正式投票复核委员。只输出JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    data = _http_post_json(url, payload, {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}, int(_to_float(cfg.get("timeout_seconds"), 20)))
    return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def _call_gemini(prompt: str, cfg: dict[str, Any], api_key: str) -> str:
    base = str(cfg.get("base_url") or "https://generativelanguage.googleapis.com").rstrip("/")
    configured_model = str(cfg.get("model") or "gemini-2.0-flash")

    def normalize_model(value: str) -> str:
        value = str(value or "").strip()
        if value.startswith("models/"):
            return value.split("/", 1)[1]
        return value or "gemini-2.0-flash"

    def endpoint(model_name: str) -> str:
        if ":generateContent" in base:
            return base
        if base.endswith("/v1") or base.endswith("/v1beta"):
            return f"{base}/models/{model_name}:generateContent"
        return f"{base}/v1beta/models/{model_name}:generateContent"

    models: list[str] = []
    for item in [configured_model, "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]:
        normalized = normalize_model(item)
        if normalized not in models:
            models.append(normalized)
    base_payload = {"contents": [{"parts": [{"text": prompt}]}]}
    payloads = [
        {**base_payload, "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}},
        {**base_payload, "generationConfig": {"temperature": 0.2}},
    ]
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    timeout = int(_to_float(cfg.get("timeout_seconds"), 20))
    errors: list[str] = []
    for model in models:
        url = endpoint(model)
        sep = "&" if "?" in url else "?"
        url_with_key = url if "key=" in url else f"{url}{sep}key={api_key}"
        for payload in payloads:
            try:
                data = _http_post_json(url_with_key, payload, headers, timeout)
                candidates = data.get("candidates") or []
                parts = (((candidates[0] if candidates else {}).get("content") or {}).get("parts") or [])
                text = "\n".join(str(part.get("text", "")) for part in parts if isinstance(part, dict)).strip()
                if text:
                    return text
                reason = data.get("promptFeedback") or data.get("error") or data
                errors.append(f"{model}: Gemini返回为空：{reason}")
            except Exception as exc:
                errors.append(f"{model}: {exc}")
                continue
    raise RuntimeError("Gemini连接失败，已尝试多个模型和载荷格式：" + " | ".join(errors[-5:]))


def _unavailable_member(provider: str, symbol: str, reason: str, event: str = "影子委员未调用", failed: bool = True) -> dict[str, Any]:
    name = "DeepSeek委员" if provider == "deepseek" else "Gemini委员"
    output = {
        "member_name": name,
        "mode": "formal",
        "direction": "neutral",
        "direction_text": "中性",
        "confidence": 0,
        "risk_level": "中",
        "vote": "观望",
        "support_trade": False,
        "veto": False,
        "soft_veto": False,
        "main_opinion": reason,
        "chart_bias": "中性",
        "chart_observation": reason,
        "reasons": [reason],
        "risks": ["外部AI不可用时按观望处理，不影响本地系统运行。"],
        "conflicts_found": [],
        "suggested_adjustment": "不调整",
        "summary": reason,
        "shadow": False,
        "official": True,
        "participates_in_vote": True,
        "status": "失败" if failed else "未配置",
        "source": "降级结果",
        "duration_ms": 0,
        "updated_time": _now(),
    }
    log_external_ai_audit_event(event, provider=provider, symbol=symbol, result="失败" if failed else "未配置", output=output, reason=reason, failed=failed)
    return output


def _run_provider_shadow(provider: str, data: dict[str, Any]) -> dict[str, Any]:
    settings = load_external_ai_settings()
    cfg = settings.get(provider) or {}
    symbol = str(data.get("symbol") or "")
    mode = str(cfg.get("mode", "shadow"))
    if mode == "off":
        return _unavailable_member(provider, symbol, f"{provider} 当前已关闭，按观望处理。", failed=False)
    api_key = _get_api_key(provider)
    if not api_key:
        return _unavailable_member(provider, symbol, f"{provider} API未配置，当前按观望处理。", failed=False)
    try:
        context = sanitize_context_for_external_ai(build_external_ai_context(data))
    except Exception as exc:
        return _unavailable_member(provider, symbol, f"外部AI脱敏检查失败，已阻止调用：{exc}", event="影子委员安全拦截")
    h = _context_hash(context, provider)
    if bool(cfg.get("cache_enabled", True)):
        cached = load_cached_external_ai_result(symbol, provider, h)
        if cached:
            log_external_ai_audit_event("影子委员缓存命中", provider=provider, symbol=symbol, result="缓存", output=cached, context_hash=h, cache_used=True)
            return cached
    limited, reason = _rate_limited(provider, symbol, cfg)
    if limited:
        cached = load_cached_external_ai_result(symbol, provider)
        if cached:
            cached["source"] = "缓存结果"
            cached["status"] = "限频缓存"
            log_external_ai_audit_event("影子委员限频", provider=provider, symbol=symbol, result="缓存", output=cached, reason=reason, context_hash=h, cache_used=True)
            return cached
        return _unavailable_member(provider, symbol, reason, event="影子委员限频")
    start = _now_ts()
    try:
        prompt = build_deepseek_prompt(context, int(_to_float(cfg.get("max_input_chars"), 6000))) if provider == "deepseek" else build_gemini_prompt(context, int(_to_float(cfg.get("max_input_chars"), 6000)))
        raw = _call_deepseek(prompt, cfg, api_key) if provider == "deepseek" else _call_gemini(prompt, cfg, api_key)
        parsed = _extract_json_text(raw)
        duration = int((_now_ts() - start) * 1000)
        result = _normalize_ai_result(provider, parsed, "实时调用", duration, h)
        cache_external_ai_result(symbol, provider, h, result, int(_to_float(cfg.get("cache_ttl_seconds"), 300)))
        log_external_ai_audit_event("影子委员复核", provider=provider, symbol=symbol, result="完成", output=result, context_hash=h, duration_ms=duration, response_summary=result.get("summary", ""))
        return result
    except json.JSONDecodeError as exc:
        duration = int((_now_ts() - start) * 1000)
        output = _unavailable_member(provider, symbol, "外部 AI 返回格式异常，已忽略本次影子意见。", event="影子委员解析失败")
        output["duration_ms"] = duration
        output["error"] = str(exc)
        return output
    except (TimeoutError, OSError, ValueError, Exception) as exc:
        duration = int((_now_ts() - start) * 1000)
        output = _unavailable_member(provider, symbol, f"{provider} 暂不可用，系统已继续使用本地策略和委员会判断：{exc}", event="影子委员调用失败")
        output["duration_ms"] = duration
        return output


def run_deepseek_shadow_member(data: dict[str, Any]) -> dict[str, Any]:
    return _run_provider_shadow("deepseek", data)


def run_gemini_shadow_member(data: dict[str, Any]) -> dict[str, Any]:
    return _run_provider_shadow("gemini", data)


def build_external_ai_consensus(deepseek: dict[str, Any] | None, gemini: dict[str, Any] | None, final_direction: str = "") -> dict[str, Any]:
    deepseek = deepseek or {}
    gemini = gemini or {}
    d_dir = str(deepseek.get("direction") or "neutral")
    g_dir = str(gemini.get("direction") or "neutral")
    available = [x for x in [deepseek, gemini] if x and x.get("status") not in {"失败", "未配置"}]
    if len(available) < 2:
        agreement = "数据不足"
    elif d_dir == g_dir:
        agreement = "一致"
    elif "neutral" in {d_dir, g_dir}:
        agreement = "部分一致"
    else:
        agreement = "冲突"
    risk_rank = {"低": 1, "中": 2, "高": 3, "极高": 4}
    combined = max([str(x.get("risk_level", "中")) for x in [deepseek, gemini]], key=lambda r: risk_rank.get(r, 2), default="中")
    soft_count = int(bool(deepseek.get("soft_veto"))) + int(bool(gemini.get("soft_veto")))
    suggestions = [str(x.get("suggested_adjustment")) for x in [deepseek, gemini] if x.get("suggested_adjustment") and x.get("suggested_adjustment") != "不调整"]
    if soft_count >= 2:
        adjustment = "等待确认"
    elif suggestions:
        adjustment = suggestions[0]
    else:
        adjustment = "不调整"
    if agreement == "一致" and d_dir == final_direction and d_dir != "neutral":
        summary = "两个外部AI与委员会主方向一致，但仍只作为影子意见记录。"
    elif soft_count >= 2:
        summary = "两个外部AI均提出软否决，建议人工复核风险，但不触发硬否决。"
    elif agreement == "冲突":
        summary = "DeepSeek 与 Gemini 影子意见存在冲突，暂不作为执行依据。"
    else:
        summary = "外部AI影子数据不足或仅提供弱参考。"
    return {
        "deepseek_direction": d_dir,
        "gemini_direction": g_dir,
        "agreement": agreement,
        "combined_risk_level": combined,
        "soft_veto_count": soft_count,
        "suggested_adjustment": adjustment,
        "summary": summary,
    }


def test_external_ai_connection(provider: str) -> dict[str, Any]:
    provider = provider.lower().strip()
    api_key = _get_api_key(provider)
    if not api_key:
        return {"ok": False, "status": "未配置", "message": f"{provider} API Key 尚未配置。请在 .env 中配置后再测试。"}
    cfg = (load_external_ai_settings().get(provider) or {}).copy()
    cfg["timeout_seconds"] = min(int(_to_float(cfg.get("timeout_seconds"), 20)), 10)
    prompt = "请只输出JSON：{\"ok\":true,\"message\":\"连接测试成功\"}"
    try:
        raw = _call_deepseek(prompt, cfg, api_key) if provider == "deepseek" else _call_gemini(prompt, cfg, api_key)
        _extract_json_text(raw)
        log_external_ai_audit_event("连接测试", provider=provider, result="通过", reason="外部AI连接测试通过。")
        return {"ok": True, "status": "正常", "message": f"{provider} 连接测试通过。"}
    except Exception as exc:
        msg = str(exc)
        status = "请求失败"
        if "SSL证书验证失败" in msg:
            status = "请求失败"
            display = (
                f"{provider} 连接测试失败：SSL证书验证失败。\n"
                "可能原因：\n"
                "1. Python证书包过旧\n"
                "2. certifi未安装或过旧\n"
                "3. 电脑时间不正确\n"
                "4. VPN/代理拦截HTTPS证书\n\n"
                "建议执行：\n"
                "python -m pip install --upgrade certifi requests urllib3"
            )
        elif "certifi" in msg:
            status = "请求失败"
            display = f"{provider} 连接测试失败：certifi 不可用。请安装 certifi：python -m pip install certifi"
        elif "timeout" in msg.lower() or "timed out" in msg.lower() or "超时" in msg:
            status = "超时"
            display = f"{provider} 连接测试失败：请求超时。请检查服务器网络、代理、防火墙或模型服务状态。原始错误：{msg}"
        elif "429" in msg or "quota" in msg.lower() or "RESOURCE_EXHAUSTED" in msg:
            status = "额度不足"
            display = f"{provider} 连接测试失败：额度不足或触发限流。请检查 API 配额、计费和调用频率。原始错误：{msg}"
        elif "401" in msg or "403" in msg or "PERMISSION_DENIED" in msg or "API key not valid" in msg:
            status = "权限错误"
            display = f"{provider} 连接测试失败：API Key 权限错误或无效。请检查环境变量、.env 和 Google AI Studio/Gemini 权限。原始错误：{msg}"
        else:
            display = f"{provider} 连接测试失败：{msg}"
        log_external_ai_audit_event("连接测试", provider=provider, result="失败", reason=display, failed=True)
        return {"ok": False, "status": status, "message": display}


def test_external_ai_ssl_environment() -> dict[str, Any]:
    return test_ssl_environment()


def log_external_ai_audit_event(
    event: str,
    provider: str = "",
    symbol: str = "",
    result: str = "",
    output: dict[str, Any] | None = None,
    reason: str = "",
    failed: bool | None = None,
    context_hash: str = "",
    duration_ms: int | None = None,
    cache_used: bool = False,
    response_summary: str = "",
) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        output = output or {}
        row = {
            "time": _now(),
            "event": event,
            "ai_name": provider,
            "mode": output.get("mode", "shadow"),
            "symbol": symbol,
            "context_hash": context_hash or output.get("context_hash", ""),
            "input_types": "脱敏交易摘要",
            "sanitized": True,
            "contains_sensitive_data": False,
            "output_direction": output.get("direction", ""),
            "output_risk_level": output.get("risk_level", ""),
            "output_vote": output.get("vote", ""),
            "soft_veto": bool(output.get("soft_veto")),
            "suggested_adjustment": output.get("suggested_adjustment", ""),
            "duration_ms": int(duration_ms if duration_ms is not None else _to_float(output.get("duration_ms"), 0)),
            "cache_used": bool(cache_used or output.get("source") == "缓存结果"),
            "failed": bool(result in {"失败", "未配置"} if failed is None else failed),
            "result": result,
            "error": reason,
            "response_summary": str(response_summary or output.get("summary", ""))[:240],
        }
        history = load_external_ai_audit_log(800)
        history.insert(0, row)
        AUDIT_JSON_PATH.write_text(json.dumps(history[:800], ensure_ascii=False, indent=2), encoding="utf-8")
        write_header = not AUDIT_CSV_PATH.exists()
        with AUDIT_CSV_PATH.open("a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if write_header:
                writer.writeheader()
            writer.writerow(row)
    except Exception:
        return


def load_external_ai_audit_log(limit: int = 100) -> list[dict[str, Any]]:
    try:
        if not AUDIT_JSON_PATH.exists():
            return []
        data = json.loads(AUDIT_JSON_PATH.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else []
        return rows[:limit]
    except Exception:
        return []


def load_external_ai_opinions(symbol: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    rows = load_external_ai_audit_log(800)
    opinions = [row for row in rows if row.get("event") in {"影子委员复核", "影子委员缓存命中", "影子委员限频"}]
    if symbol:
        opinions = [row for row in opinions if str(row.get("symbol")) == str(symbol)]
    return opinions[:limit]


def calculate_external_ai_performance() -> dict[str, Any]:
    audit = load_external_ai_audit_log(800)
    history = load_sim_trade_history() if load_sim_trade_history else []
    result: dict[str, Any] = {}
    for provider in ("deepseek", "gemini"):
        rows = [row for row in audit if row.get("ai_name") == provider]
        valid = [row for row in rows if not row.get("failed") and row.get("output_direction")]
        failed = [row for row in rows if row.get("failed")]
        direction_counts = {
            "long": len([r for r in valid if r.get("output_direction") == "long"]),
            "short": len([r for r in valid if r.get("output_direction") == "short"]),
            "neutral": len([r for r in valid if r.get("output_direction") == "neutral"]),
        }
        soft_veto_rows = [r for r in valid if r.get("soft_veto")]
        trade_matches = 0
        trade_samples = 0
        over_conservative = 0
        over_aggressive = 0
        risk_hits = 0
        for trade in history:
            snap = (trade.get("committee_snapshot") or {}).get("external_ai") or {}
            item = snap.get(provider) or {}
            if not item:
                continue
            direction = str(item.get("direction") or "")
            if direction in {"long", "short"}:
                trade_samples += 1
                if direction == trade.get("direction") and trade.get("is_win"):
                    trade_matches += 1
                if direction == trade.get("direction") and not trade.get("is_win"):
                    over_aggressive += 1
            if item.get("soft_veto") or str(item.get("vote")) in {"反对", "软否决"}:
                if trade.get("is_win"):
                    over_conservative += 1
                else:
                    risk_hits += 1
        avg_duration = sum(_to_float(r.get("duration_ms")) for r in rows) / len(rows) if rows else 0
        sample_enough = len(valid) >= 30
        direction_accuracy = trade_matches / trade_samples * 100 if trade_samples else 0
        risk_effective = risk_hits / max(len([t for t in history if ((t.get("committee_snapshot") or {}).get("external_ai") or {}).get(provider, {}).get("soft_veto")]), 1) * 100 if history else 0
        failure_rate = len(failed) / len(rows) * 100 if rows else 0
        if not rows:
            upgrade = "保持关闭"
        elif not sample_enough:
            upgrade = "继续影子模式"
        elif direction_accuracy >= 60 and risk_effective >= 60 and failure_rate < 25:
            upgrade = "可进入咨询模式"
        elif failure_rate >= 40:
            upgrade = "不建议参与交易决策"
        else:
            upgrade = "继续影子模式"
        result[provider] = {
            "ai_name": provider,
            "total_calls": len(rows),
            "valid_calls": len(valid),
            "failed_calls": len(failed),
            "direction_counts": direction_counts,
            "soft_veto_count": len(soft_veto_rows),
            "trade_result_samples": trade_samples,
            "direction_accuracy": direction_accuracy,
            "risk_identification_effective_rate": risk_effective,
            "soft_veto_effective_rate": risk_effective,
            "over_conservative_count": over_conservative,
            "over_aggressive_count": over_aggressive,
            "over_conservative_rate": over_conservative / max(len(soft_veto_rows), 1) * 100 if soft_veto_rows else 0,
            "over_aggressive_rate": over_aggressive / max(trade_samples, 1) * 100 if trade_samples else 0,
            "failure_rate": failure_rate,
            "avg_duration_ms": avg_duration,
            "sample_enough": sample_enough,
            "upgrade_suggestion": upgrade,
            "sample_warning": "样本数量不足，暂不评估外部AI准确率。" if not sample_enough else "样本数量达到初步统计门槛，但仍需继续观察。",
        }
    return result


def get_external_ai_performance_summary() -> dict[str, Any]:
    perf = calculate_external_ai_performance()
    return {
        "deepseek": perf.get("deepseek", {}),
        "gemini": perf.get("gemini", {}),
        "summary": "外部AI当前只用于影子测试和表现统计，不自动修改委员会权重。",
    }


def get_external_ai_status() -> dict[str, Any]:
    settings = load_external_ai_settings()
    perf = calculate_external_ai_performance()
    status: dict[str, Any] = {}
    for provider in ("deepseek", "gemini"):
        secret = get_external_ai_secret_status(provider)
        rows = [r for r in load_external_ai_audit_log(200) if r.get("ai_name") == provider]
        last = rows[0] if rows else {}
        status[provider] = {
            "configured": secret.get("configured"),
            "masked_api_key": secret.get("masked_api_key"),
            "secret_status": secret.get("secret_status"),
            "mode": (settings.get(provider) or {}).get("mode", "shadow"),
            "last_call_time": last.get("time", "暂无"),
            "last_error": last.get("error", "") if last.get("failed") else "",
            "success_rate": 100 - _to_float((perf.get(provider) or {}).get("failure_rate"), 0),
            "avg_duration_ms": (perf.get(provider) or {}).get("avg_duration_ms", 0),
            "performance": perf.get(provider) or {},
        }
    return status


def export_external_ai_audit_log() -> list[dict[str, Any]]:
    return load_external_ai_audit_log(800)

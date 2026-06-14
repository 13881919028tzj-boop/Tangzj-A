"""Runtime diagnostics for Binance market data on server deployments."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT_DIR / "logs"
LOG_PATH = LOG_DIR / "binance_request_log.json"
DIAG_PATH = LOG_DIR / "system_diagnostics.json"

SPOT_BASE_URL = "https://api.binance.com"
SPOT_FALLBACK_BASE_URL = "https://data-api.binance.vision"
FUTURES_BASE_URL = "https://fapi.binance.com"
REQUEST_TIMEOUT = 12
_BASE_BAN_UNTIL: dict[str, float] = {}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ca_bundle() -> str | bool:
    try:
        import certifi  # type: ignore

        return certifi.where()
    except Exception:
        return True


def _append_json(path: Path, event: dict[str, Any], limit: int = 1000) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, Any]] = []
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8-sig") or "[]")
            rows = loaded if isinstance(loaded, list) else []
        rows.append(event)
        path.write_text(json.dumps(rows[-limit:], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def log_binance_request(level: str, path: str, params: dict[str, Any] | None, result: str, reason: str = "", elapsed_ms: int = 0, base_url: str = "") -> None:
    _append_json(
        LOG_PATH,
        {
            "time": _now(),
            "level": level,
            "base_url": base_url,
            "path": path,
            "symbol": (params or {}).get("symbol", ""),
            "params": params or {},
            "result": result,
            "reason": reason,
            "elapsed_ms": elapsed_ms,
        },
    )


def _ban_message_until(message: str) -> float:
    match = re.search(r"banned until (\d{10,13})", message)
    if not match:
        return 0.0
    raw = int(match.group(1))
    return raw / 1000 if raw > 10_000_000_000 else float(raw)


def _record_binance_ban(root: str, message: str) -> None:
    until = _ban_message_until(message)
    if until > time.time():
        _BASE_BAN_UNTIL[root] = until


def is_binance_base_banned(root: str) -> bool:
    """Return whether a Binance base URL is in the local ban circuit breaker."""
    return _BASE_BAN_UNTIL.get(root, 0.0) > time.time()


def safe_binance_rest_get(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    base_url: str = SPOT_BASE_URL,
    fallback_base_url: str | None = SPOT_FALLBACK_BASE_URL,
    timeout: int = REQUEST_TIMEOUT,
) -> Any:
    """GET Binance public REST data with logging and a public-data fallback."""
    headers = {"User-Agent": "AI-Model-Market-Diagnostics/8.5"}
    verify = _ca_bundle()
    last_error = ""
    for index, root in enumerate([base_url, fallback_base_url] if fallback_base_url else [base_url]):
        if not root:
            continue
        banned_until = _BASE_BAN_UNTIL.get(root, 0.0)
        if banned_until > time.time():
            last_error = f"{root} 已被 Binance 临时封禁，跳过请求至 {datetime.fromtimestamp(banned_until).strftime('%Y-%m-%d %H:%M:%S')}"
            log_binance_request("WARNING", path, params, "熔断跳过", last_error, 0, root)
            continue
        url = f"{root}{path}"
        started = time.perf_counter()
        try:
            response = requests.get(url, params=params, timeout=timeout, headers=headers, verify=verify)
            elapsed = int((time.perf_counter() - started) * 1000)
            if response.status_code in {418, 429}:
                reason = response.text[:500]
                _record_binance_ban(root, reason)
                raise RuntimeError(f"HTTP {response.status_code} Binance限流/封禁: {reason}")
            response.raise_for_status()
            data = response.json()
            log_binance_request("INFO", path, params, "正常", f"HTTP {response.status_code}", elapsed, root)
            return data
        except Exception as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            last_error = repr(exc)
            _record_binance_ban(root, last_error)
            level = "WARNING" if index == 0 and fallback_base_url else "ERROR"
            log_binance_request(level, path, params, "异常", last_error, elapsed, root)
    raise RuntimeError(f"Binance公共请求失败 path={path} params={params} error={last_error}")


def _check_endpoint(name: str, path: str, params: dict[str, Any] | None = None, base_url: str = SPOT_BASE_URL, fallback: str | None = SPOT_FALLBACK_BASE_URL) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        data = safe_binance_rest_get(path, params, base_url=base_url, fallback_base_url=fallback)
        elapsed = int((time.perf_counter() - started) * 1000)
        ok = data is not None
        return {"name": name, "status": "正常" if ok else "异常", "ok": ok, "elapsed_ms": elapsed, "error": "", "sample": str(data)[:180]}
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return {"name": name, "status": "异常", "ok": False, "elapsed_ms": elapsed, "error": repr(exc), "sample": ""}


def run_binance_diagnostics(symbol: str = "BTCUSDT") -> dict[str, Any]:
    symbol = str(symbol or "BTCUSDT").upper().strip()
    checks = [
        _check_endpoint("Binance REST Spot Time", "/api/v3/time"),
        _check_endpoint("Binance REST Futures Time", "/fapi/v1/time", base_url=FUTURES_BASE_URL, fallback=None),
        _check_endpoint("Ticker 24hr", "/api/v3/ticker/24hr", {"symbol": symbol}),
        _check_endpoint("Kline REST", "/api/v3/klines", {"symbol": symbol, "interval": "1m", "limit": 20}),
        _check_endpoint("Depth REST", "/api/v3/depth", {"symbol": symbol, "limit": 20}),
    ]
    websocket_status = {
        "name": "Binance WebSocket",
        "status": "REST回退正常",
        "ok": True,
        "elapsed_ms": 0,
        "error": "当前版本页面实时行情使用后台REST刷新；WebSocket异常时不会阻塞K线显示。",
        "sample": "REST fallback enabled",
    }
    checks.append(websocket_status)
    ok = all(item.get("ok") for item in checks if item.get("name") != "Binance WebSocket")
    result = {
        "time": _now(),
        "symbol": symbol,
        "status": "正常" if ok else "异常",
        "rest_status": "正常" if ok else "异常",
        "websocket_status": websocket_status["status"],
        "checks": checks,
        "last_success_time": _now() if ok else "",
        "recent_error": "；".join(str(item.get("error")) for item in checks if item.get("error"))[:500],
    }
    try:
        DIAG_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return result


def load_recent_binance_logs(limit: int = 100) -> list[dict[str, Any]]:
    try:
        rows = json.loads(LOG_PATH.read_text(encoding="utf-8-sig") or "[]")
        return (rows if isinstance(rows, list) else [])[-limit:][::-1]
    except Exception:
        return []


def load_last_diagnostics() -> dict[str, Any]:
    try:
        data = json.loads(DIAG_PATH.read_text(encoding="utf-8-sig") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

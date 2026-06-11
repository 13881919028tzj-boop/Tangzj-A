"""本地 API 密钥安全输入与 .env 写入层。

只负责本地保存、脱敏显示和清除。不会把密钥写入日志，也不会发送给外部 AI。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT_DIR / ".env"

MANAGED_KEYS = {
    "BINANCE_API_KEY": "Binance API Key",
    "BINANCE_API_SECRET": "Binance API Secret",
    "BINANCE_TESTNET_API_KEY": "Binance Testnet API Key",
    "BINANCE_TESTNET_API_SECRET": "Binance Testnet API Secret",
    "DEEPSEEK_API_KEY": "DeepSeek API Key",
    "GEMINI_API_KEY": "Gemini API Key",
}


def _mask(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return "未配置"
    if len(value) <= 8:
        return value[:2] + "****"
    return f"{value[:4]}****{value[-4:]}"


def _sanitize_value(value: str) -> str:
    return str(value or "").replace("\r", "").replace("\n", "").strip()


def _parse_env_lines() -> tuple[list[str], dict[str, str]]:
    if not ENV_PATH.exists():
        return [], {}
    lines = ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip().strip('"').strip("'")
    return lines, values


def read_secure_api_values() -> dict[str, str]:
    return _parse_env_lines()[1]


def get_secure_api_status() -> dict[str, Any]:
    values = read_secure_api_values()
    return {
        key: {
            "label": label,
            "configured": bool(values.get(key)),
            "masked": _mask(values.get(key, "")),
            "secret_status": "已隐藏" if values.get(key) else "未配置",
            "source": str(ENV_PATH) if values.get(key) else "未配置",
        }
        for key, label in MANAGED_KEYS.items()
    }


def write_secure_api_values(updates: dict[str, str]) -> dict[str, Any]:
    """写入非空更新。空字符串表示保持原值，不会覆盖已有密钥。"""
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines, values = _parse_env_lines()
    safe_updates = {key: _sanitize_value(value) for key, value in (updates or {}).items() if key in MANAGED_KEYS and _sanitize_value(value)}
    if not safe_updates:
        return {"ok": False, "message": "没有需要保存的新密钥。"}

    values.update(safe_updates)
    rendered: list[str] = []
    written: set[str] = set()
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            rendered.append(line)
            continue
        key = raw.split("=", 1)[0].strip()
        if key in values:
            rendered.append(f"{key}={values[key]}")
            written.add(key)
        else:
            rendered.append(line)
    for key in MANAGED_KEYS:
        if key in values and key not in written:
            rendered.append(f"{key}={values[key]}")
    ENV_PATH.write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")
    return {"ok": True, "message": "API 密钥已保存到本地 .env。页面只显示脱敏状态。", "updated_keys": list(safe_updates.keys()), "env_path": str(ENV_PATH)}


def clear_secure_api_values(keys: list[str]) -> dict[str, Any]:
    target = {key for key in keys if key in MANAGED_KEYS}
    if not target:
        return {"ok": False, "message": "没有选择需要清除的密钥。"}
    lines, values = _parse_env_lines()
    for key in target:
        values.pop(key, None)
    rendered: list[str] = []
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            rendered.append(line)
            continue
        key = raw.split("=", 1)[0].strip()
        if key in target:
            continue
        rendered.append(line)
    ENV_PATH.write_text("\n".join(rendered).rstrip() + ("\n" if rendered else ""), encoding="utf-8")
    return {"ok": True, "message": "已清除选中的本地 API 密钥。", "cleared_keys": list(target), "env_path": str(ENV_PATH)}

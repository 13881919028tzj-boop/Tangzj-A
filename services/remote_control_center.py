"""多设备远程控制、权限检查和远程操作审计。"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DEVICE_PATH = DATA_DIR / "registered_devices.json"
SESSION_PATH = DATA_DIR / "active_sessions.json"
REMOTE_AUDIT_JSON = DATA_DIR / "remote_action_audit_log.json"
REMOTE_AUDIT_CSV = DATA_DIR / "remote_action_audit_log.csv"

PERMISSIONS = {
    "viewer": {"view"},
    "operator": {"view", "mark_notification", "approval", "sim_control", "pause_auto_live"},
    "admin": {"view", "mark_notification", "approval", "sim_control", "pause_auto_live", "emergency_stop", "config", "release_lock"},
}

SENSITIVE_ACTIONS = {
    "enable_live_auto": "我确认开启远程小资金自动实盘",
    "emergency_stop": "我确认触发紧急停止",
    "release_auto_circuit": "我确认解除自动实盘熔断",
    "release_live_kill_switch": "我确认解除实盘安全锁",
    "config_change": "我确认修改远程配置",
}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Path, default: Any) -> Any:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            _write_json(path, default)
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        backup = path.with_suffix(path.suffix + f".broken_{int(time.time())}") if path.exists() else None
        try:
            if backup:
                path.rename(backup)
        except Exception:
            pass
        _write_json(path, default)
        return default


def _write_json(path: Path, data: Any) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def get_current_device_info(session_device_id: str | None = None, user_agent: str = "", page: str = "", device_name: str = "") -> dict[str, Any]:
    device_id = session_device_id or f"dev_{uuid.uuid4().hex[:12]}"
    fingerprint = hashlib.sha256(f"{device_id}:{user_agent}".encode("utf-8", errors="ignore")).hexdigest()[:16]
    return {
        "device_id": device_id,
        "device_name": device_name or "当前设备",
        "user_agent": user_agent[:300],
        "fingerprint": fingerprint,
        "current_page": page,
        "permission_level": "admin",
        "trusted": True,
        "allow_remote_control": True,
        "login_status": "logged_in",
    }


def register_device(device_info: dict[str, Any]) -> dict[str, Any]:
    rows = _read_json(DEVICE_PATH, [])
    if not isinstance(rows, list):
        rows = []
    device_id = str(device_info.get("device_id") or f"dev_{uuid.uuid4().hex[:12]}")
    existing = next((row for row in rows if row.get("device_id") == device_id), None)
    if existing:
        existing.update({
            "device_name": device_info.get("device_name") or existing.get("device_name"),
            "user_agent": device_info.get("user_agent", existing.get("user_agent", "")),
            "last_seen_time": _now(),
            "last_page": device_info.get("current_page", existing.get("last_page", "")),
            "login_status": device_info.get("login_status", existing.get("login_status", "")),
        })
        device = existing
    else:
        device = {
            "device_id": device_id,
            "device_name": device_info.get("device_name") or f"设备-{device_id[-4:]}",
            "user_agent": device_info.get("user_agent", ""),
            "fingerprint": device_info.get("fingerprint", ""),
            "first_seen_time": _now(),
            "last_seen_time": _now(),
            "last_page": device_info.get("current_page", ""),
            "login_status": device_info.get("login_status", "unknown"),
            "permission_level": device_info.get("permission_level", "admin"),
            "trusted": bool(device_info.get("trusted", True)),
            "allow_remote_control": bool(device_info.get("allow_remote_control", True)),
        }
        rows.insert(0, device)
    _write_json(DEVICE_PATH, rows[:200])
    create_session(device.get("user_agent", ""), "", device_id, device.get("last_page", ""))
    return device


def load_registered_devices() -> list[dict[str, Any]]:
    rows = _read_json(DEVICE_PATH, [])
    return rows if isinstance(rows, list) else []


def update_device_last_seen(device_id: str, page: str = "") -> None:
    rows = load_registered_devices()
    for row in rows:
        if row.get("device_id") == device_id:
            row["last_seen_time"] = _now()
            row["last_page"] = page or row.get("last_page", "")
            break
    _write_json(DEVICE_PATH, rows)


def create_session(user_agent: str, ip_hint: str = "", device_id: str = "", page: str = "") -> dict[str, Any]:
    rows = _read_json(SESSION_PATH, [])
    if not isinstance(rows, list):
        rows = []
    session_id = f"sess_{hashlib.sha1(f'{device_id}:{user_agent}'.encode('utf-8', errors='ignore')).hexdigest()[:12]}"
    existing = next((row for row in rows if row.get("session_id") == session_id), None)
    data = {
        "session_id": session_id,
        "device_id": device_id,
        "user_agent": user_agent[:300],
        "ip_hint": ip_hint,
        "last_seen_time": _now(),
        "last_page": page,
        "permission_level": "admin",
        "login_status": "logged_in",
    }
    if existing:
        existing.update(data)
    else:
        data["created_time"] = _now()
        rows.insert(0, data)
    _write_json(SESSION_PATH, rows[:300])
    return data


def get_active_sessions() -> list[dict[str, Any]]:
    rows = _read_json(SESSION_PATH, [])
    if not isinstance(rows, list):
        return []
    now = time.time()
    active = []
    for row in rows:
        try:
            seen = time.mktime(time.strptime(str(row.get("last_seen_time")), "%Y-%m-%d %H:%M:%S"))
        except Exception:
            seen = 0
        if now - seen <= 24 * 3600:
            active.append(row)
    return active


def validate_access_password(password: str) -> dict[str, Any]:
    expected = os.environ.get("APP_ACCESS_PASSWORD", "")
    if not expected:
        return {"ok": False, "message": "访问密码未配置。"}
    if str(password) == expected:
        return {"ok": True, "message": "登录成功。"}
    return {"ok": False, "message": "访问密码错误。"}


def check_permission(action: str, device_id: str = "", permission_level: str = "admin") -> dict[str, Any]:
    devices = load_registered_devices()
    device = next((row for row in devices if row.get("device_id") == device_id), {})
    level = str(device.get("permission_level") or permission_level or "viewer")
    if not device.get("allow_remote_control", True) and action != "view":
        return {"ok": False, "message": "当前设备没有权限执行该操作。", "permission_level": level}
    allowed = PERMISSIONS.get(level, PERMISSIONS["viewer"])
    if action in allowed or level == "admin":
        return {"ok": True, "message": "权限检查通过。", "permission_level": level}
    return {"ok": False, "message": "当前设备没有权限执行该操作。", "permission_level": level}


def require_confirmation(action: str, phrase: str) -> dict[str, Any]:
    required = SENSITIVE_ACTIONS.get(action)
    if not required:
        return {"ok": True, "message": "该操作不需要确认短句。", "required": ""}
    if str(phrase or "").strip() == required:
        return {"ok": True, "message": "确认短句通过。", "required": required}
    return {"ok": False, "message": f"确认短句不匹配，请输入：{required}", "required": required}


def record_remote_action(action: dict[str, Any]) -> dict[str, Any]:
    row = {
        "time": _now(),
        "audit_id": f"remote_{uuid.uuid4().hex[:12]}",
        "device_id": str(action.get("device_id", "")),
        "device_name": str(action.get("device_name", "")),
        "permission_level": str(action.get("permission_level", "viewer")),
        "action_type": str(action.get("action_type", "")),
        "page": str(action.get("page", "")),
        "from_state": str(action.get("from_state", "")),
        "to_state": str(action.get("to_state", "")),
        "success": bool(action.get("success", False)),
        "reason": str(action.get("reason", "")),
        "second_confirmed": bool(action.get("second_confirmed", False)),
        "confirm_phrase_ok": bool(action.get("confirm_phrase_ok", False)),
        "risk_status": str(action.get("risk_status", "")),
        "safety_lock_status": str(action.get("safety_lock_status", "")),
        "current_mode": str(action.get("current_mode", "")),
    }
    rows = _read_json(REMOTE_AUDIT_JSON, [])
    if not isinstance(rows, list):
        rows = []
    rows.insert(0, row)
    _write_json(REMOTE_AUDIT_JSON, rows[:1000])
    try:
        with REMOTE_AUDIT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
            writer.writeheader()
            writer.writerows(rows[:1000])
    except Exception:
        pass
    return {"ok": True, "audit": row}


def load_remote_action_audit(limit: int = 100) -> list[dict[str, Any]]:
    rows = _read_json(REMOTE_AUDIT_JSON, [])
    return (rows if isinstance(rows, list) else [])[:limit]


def get_remote_control_status(device_id: str = "") -> dict[str, Any]:
    devices = load_registered_devices()
    current = next((row for row in devices if row.get("device_id") == device_id), devices[0] if devices else {})
    return {
        "current_device": current,
        "registered_devices": devices[:50],
        "active_sessions": get_active_sessions(),
        "recent_actions": load_remote_action_audit(20),
        "permission_level": current.get("permission_level", "admin") if current else "admin",
    }


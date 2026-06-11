"""基础用户账户、登录会话、设备绑定和审计日志。

8.4 账户系统默认可关闭，方便本地开发；开启后支持管理员初始化、
密码哈希、会话超时、设备绑定和权限检查。不会保存明文密码。
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


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
ACCOUNT_DIR = DATA_DIR / "accounts"
PROFILE_DIR = ACCOUNT_DIR / "user_profiles"
USERS_PATH = ACCOUNT_DIR / "users.json"
SESSIONS_PATH = ACCOUNT_DIR / "user_sessions.json"
ACCOUNT_AUDIT_JSON = ACCOUNT_DIR / "account_audit_log.json"
ACCOUNT_AUDIT_CSV = ACCOUNT_DIR / "account_audit_log.csv"
LOGIN_AUDIT_JSON = ACCOUNT_DIR / "login_audit_log.json"
LOGIN_AUDIT_CSV = ACCOUNT_DIR / "login_audit_log.csv"

ROLE_PERMISSIONS = {
    "viewer": {
        "view_home",
        "view_market",
        "view_signals",
        "view_reports",
        "view_notifications",
    },
    "operator": {
        "view_home",
        "view_market",
        "view_signals",
        "view_reports",
        "view_notifications",
        "mark_notifications",
        "handle_approvals",
        "sim_control",
        "view_live",
    },
    "admin": {
        "view_home",
        "view_market",
        "view_signals",
        "view_reports",
        "view_notifications",
        "mark_notifications",
        "handle_approvals",
        "sim_control",
        "view_live",
        "manage_config",
        "manage_api",
        "manage_risk",
        "manage_modes",
        "manage_backups",
        "manage_sync",
        "manage_devices",
        "live_manual",
        "live_auto_pilot",
        "release_circuit",
        "emergency_stop",
    },
}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _ts(value: str) -> float:
    try:
        return time.mktime(time.strptime(str(value), "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


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


def _append_log(path: Path, csv_path: Path, row: dict[str, Any]) -> bool:
    safe = {"time": _now(), **{k: v for k, v in row.items() if "password" not in k.lower()}}
    rows = _read_json(path, [])
    if not isinstance(rows, list):
        rows = []
    rows.insert(0, safe)
    ok = _write_json(path, rows[:2000])
    try:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            fieldnames = sorted({key for item in rows[:2000] for key in item.keys()})
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows[:2000])
    except Exception:
        pass
    return ok


def account_login_enabled() -> bool:
    return str(os.environ.get("ENABLE_ACCOUNT_LOGIN", "false")).lower() in {"1", "true", "yes", "on"}


def session_timeout_minutes() -> int:
    try:
        return int(os.environ.get("SESSION_TIMEOUT_MINUTES", "120"))
    except ValueError:
        return 120


def load_users() -> list[dict[str, Any]]:
    users = _read_json(USERS_PATH, [])
    return users if isinstance(users, list) else []


def save_users(users: list[dict[str, Any]]) -> bool:
    sanitized = []
    for user in users:
        item = dict(user)
        item.pop("password", None)
        sanitized.append(item)
    return _write_json(USERS_PATH, sanitized)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or uuid.uuid4().hex
    digest = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
    return f"sha256${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algo, salt, digest = str(password_hash).split("$", 2)
        if algo != "sha256":
            return False
        candidate = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
        return hmac.compare_digest(candidate, digest)
    except Exception:
        return False


def _password_strength(password: str) -> tuple[bool, str]:
    if len(password or "") < 8:
        return False, "密码至少 8 位。"
    has_alpha = any(ch.isalpha() for ch in password)
    has_digit = any(ch.isdigit() for ch in password)
    if not (has_alpha and has_digit):
        return True, "密码可用，但建议包含数字和字母。"
    return True, "密码强度可用。"


def create_admin_user(username: str, password: str, display_name: str = "管理员") -> dict[str, Any]:
    username = str(username or "admin").strip()
    ok_strength, message = _password_strength(password)
    if not ok_strength:
        return {"ok": False, "message": message}
    users = load_users()
    if any(user.get("username") == username for user in users):
        return {"ok": False, "message": "该用户名已存在。"}
    user = {
        "user_id": f"user_{uuid.uuid4().hex[:12]}",
        "username": username,
        "display_name": display_name or username,
        "role": "admin",
        "password_hash": hash_password(password),
        "created_time": _now(),
        "last_login_time": "",
        "last_login_device": "",
        "status": "active",
        "trusted_devices": [],
        "permissions": sorted(ROLE_PERMISSIONS["admin"]),
        "profile": {},
        "settings": {},
        "failed_login_count": 0,
        "locked_until": "",
    }
    users.insert(0, user)
    save_users(users)
    save_user_profile(user["user_id"], {"display_name": user["display_name"], "default_page": "home", "mobile_layout": "compact", "language": "zh-CN"})
    log_account_event({"event": "create_admin_user", "user_id": user["user_id"], "username": username, "result": "ok"})
    return {"ok": True, "message": "管理员账户已创建。", "user": {k: v for k, v in user.items() if k != "password_hash"}}


def has_any_user() -> bool:
    return bool(load_users())


def log_account_event(event: dict[str, Any]) -> bool:
    return _append_log(ACCOUNT_AUDIT_JSON, ACCOUNT_AUDIT_CSV, event)


def log_login_event(event: dict[str, Any]) -> bool:
    return _append_log(LOGIN_AUDIT_JSON, LOGIN_AUDIT_CSV, event)


def authenticate_user(username: str, password: str, device_id: str = "") -> dict[str, Any]:
    users = load_users()
    now = time.time()
    for user in users:
        if user.get("username") != username:
            continue
        if user.get("status") != "active":
            log_login_event({"event": "login_failed", "username": username, "device_id": device_id, "reason": "账户不可用"})
            return {"ok": False, "message": "账户不可用。"}
        locked_until = _ts(str(user.get("locked_until", "")))
        if locked_until and locked_until > now:
            log_login_event({"event": "login_failed", "username": username, "device_id": device_id, "reason": "账户暂时锁定"})
            return {"ok": False, "message": "账户暂时锁定，请稍后再试。"}
        if verify_password(password, str(user.get("password_hash", ""))):
            user["failed_login_count"] = 0
            user["locked_until"] = ""
            user["last_login_time"] = _now()
            user["last_login_device"] = device_id
            save_users(users)
            session = create_session(user["user_id"], device_id)
            log_login_event({"event": "login_success", "username": username, "user_id": user["user_id"], "device_id": device_id, "session_id": session.get("session_id")})
            safe_user = {k: v for k, v in user.items() if k != "password_hash"}
            return {"ok": True, "message": "登录成功。", "user": safe_user, "session": session}
        user["failed_login_count"] = int(user.get("failed_login_count", 0) or 0) + 1
        max_fail = int(os.environ.get("MAX_LOGIN_FAILURES", "5") or 5)
        if user["failed_login_count"] >= max_fail:
            lock_minutes = int(os.environ.get("LOCK_MINUTES_AFTER_FAILURE", "15") or 15)
            user["locked_until"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now + lock_minutes * 60))
        save_users(users)
        log_login_event({"event": "login_failed", "username": username, "device_id": device_id, "reason": "密码错误"})
        return {"ok": False, "message": "用户名或密码错误。"}
    log_login_event({"event": "login_failed", "username": username, "device_id": device_id, "reason": "用户不存在"})
    return {"ok": False, "message": "用户名或密码错误。"}


def load_sessions() -> list[dict[str, Any]]:
    rows = _read_json(SESSIONS_PATH, [])
    return rows if isinstance(rows, list) else []


def save_sessions(rows: list[dict[str, Any]]) -> bool:
    return _write_json(SESSIONS_PATH, rows[:1000])


def create_session(user_id: str, device_id: str = "", user_agent: str = "", ip_hint: str = "") -> dict[str, Any]:
    expires = time.time() + session_timeout_minutes() * 60
    session = {
        "session_id": f"sess_{uuid.uuid4().hex[:16]}",
        "user_id": user_id,
        "device_id": device_id,
        "created_time": _now(),
        "last_active_time": _now(),
        "expires_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(expires)),
        "ip_hint": ip_hint,
        "user_agent": user_agent[:300],
        "status": "active",
    }
    rows = load_sessions()
    rows.insert(0, session)
    save_sessions(rows)
    return session


def validate_session(session_id: str) -> dict[str, Any]:
    rows = load_sessions()
    now = time.time()
    for session in rows:
        if session.get("session_id") != session_id:
            continue
        if session.get("status") != "active":
            return {"ok": False, "message": "会话已失效。"}
        if _ts(str(session.get("expires_at", ""))) < now:
            session["status"] = "expired"
            save_sessions(rows)
            return {"ok": False, "message": "会话已超时，请重新登录。"}
        session["last_active_time"] = _now()
        save_sessions(rows)
        user = next((u for u in load_users() if u.get("user_id") == session.get("user_id")), None)
        if not user:
            return {"ok": False, "message": "用户不存在。"}
        return {"ok": True, "message": "会话有效。", "session": session, "user": {k: v for k, v in user.items() if k != "password_hash"}}
    return {"ok": False, "message": "未找到会话。"}


def expire_session(session_id: str) -> dict[str, Any]:
    rows = load_sessions()
    for session in rows:
        if session.get("session_id") == session_id:
            session["status"] = "revoked"
            save_sessions(rows)
            log_account_event({"event": "expire_session", "session_id": session_id, "user_id": session.get("user_id"), "result": "ok"})
            return {"ok": True, "message": "会话已退出。"}
    return {"ok": False, "message": "未找到会话。"}


def change_password(username: str, old_password: str, new_password: str) -> dict[str, Any]:
    users = load_users()
    for user in users:
        if user.get("username") != username:
            continue
        if not verify_password(old_password, str(user.get("password_hash", ""))):
            log_account_event({"event": "change_password_failed", "username": username, "reason": "旧密码错误"})
            return {"ok": False, "message": "旧密码错误。"}
        ok_strength, message = _password_strength(new_password)
        if not ok_strength:
            return {"ok": False, "message": message}
        user["password_hash"] = hash_password(new_password)
        save_users(users)
        log_account_event({"event": "change_password", "username": username, "user_id": user.get("user_id"), "result": "ok"})
        return {"ok": True, "message": "密码已修改。"}
    return {"ok": False, "message": "用户不存在。"}


def get_user_permissions(user_id: str) -> list[str]:
    user = next((u for u in load_users() if u.get("user_id") == user_id), None)
    if not user:
        return []
    role = str(user.get("role", "viewer"))
    return sorted(set(user.get("permissions") or ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS["viewer"])))


def bind_device_to_user(user_id: str, device_id: str) -> dict[str, Any]:
    users = load_users()
    for user in users:
        if user.get("user_id") == user_id:
            devices = list(user.get("trusted_devices") or [])
            if device_id not in devices:
                devices.append(device_id)
            user["trusted_devices"] = devices
            save_users(users)
            log_account_event({"event": "bind_device", "user_id": user_id, "device_id": device_id, "result": "ok"})
            return {"ok": True, "message": "设备已绑定。"}
    return {"ok": False, "message": "用户不存在。"}


def unbind_device(user_id: str, device_id: str) -> dict[str, Any]:
    users = load_users()
    for user in users:
        if user.get("user_id") == user_id:
            user["trusted_devices"] = [d for d in user.get("trusted_devices", []) if d != device_id]
            save_users(users)
            for session in load_sessions():
                if session.get("user_id") == user_id and session.get("device_id") == device_id:
                    session["status"] = "revoked"
            log_account_event({"event": "unbind_device", "user_id": user_id, "device_id": device_id, "result": "ok"})
            return {"ok": True, "message": "设备已解绑。"}
    return {"ok": False, "message": "用户不存在。"}


def get_user_profile(user_id: str) -> dict[str, Any]:
    return _read_json(PROFILE_DIR / f"{user_id}.json", {})


def save_user_profile(user_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    safe_profile = dict(profile or {})
    safe_profile.pop("api_secret", None)
    safe_profile.pop("password", None)
    _write_json(PROFILE_DIR / f"{user_id}.json", safe_profile)
    return {"ok": True, "message": "用户配置已保存。", "profile": safe_profile}


def get_account_audit(limit: int = 100) -> list[dict[str, Any]]:
    rows = _read_json(ACCOUNT_AUDIT_JSON, [])
    return (rows if isinstance(rows, list) else [])[:limit]


def get_login_audit(limit: int = 100) -> list[dict[str, Any]]:
    rows = _read_json(LOGIN_AUDIT_JSON, [])
    return (rows if isinstance(rows, list) else [])[:limit]


def get_account_status() -> dict[str, Any]:
    users = load_users()
    sessions = load_sessions()
    return {
        "enabled": account_login_enabled(),
        "user_count": len(users),
        "has_admin": any(user.get("role") == "admin" for user in users),
        "active_sessions": len([s for s in sessions if s.get("status") == "active"]),
        "users": [{k: v for k, v in user.items() if k != "password_hash"} for user in users],
    }


"""云端同步基础适配器。

当前实现为本地 mock 云同步目录 cloud_sync/，未来可替换为自建 API、
S3/OSS、Google Drive 或私有 Git 仓库。同步不会触发交易，也不会同步密钥。
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
CONFIG_DIR = ROOT_DIR / "config"
CLOUD_DIR = ROOT_DIR / "cloud_sync"
SYNC_STATE_PATH = DATA_DIR / "sync_state.json"
SYNC_AUDIT_PATH = DATA_DIR / "sync_audit_log.json"

ALLOWED_RESOURCES = {"config", "notifications", "approvals", "reports", "backups", "user_profiles"}
SENSITIVE_KEYWORDS = {"secret", "api_secret", "password", "token", "private_key", "BINANCE_API_SECRET", "APP_ACCESS_PASSWORD"}


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
        return default


def _write_json(path: Path, data: Any) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def _audit(event: dict[str, Any]) -> None:
    rows = _read_json(SYNC_AUDIT_PATH, [])
    if not isinstance(rows, list):
        rows = []
    rows.insert(0, {"time": _now(), **event})
    _write_json(SYNC_AUDIT_PATH, rows[:1000])


def _contains_sensitive(data: Any) -> bool:
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).lower() in {x.lower() for x in SENSITIVE_KEYWORDS}:
                return True
            if _contains_sensitive(value):
                return True
    elif isinstance(data, list):
        return any(_contains_sensitive(item) for item in data)
    elif isinstance(data, str):
        lowered = data.lower()
        if "api_secret" in lowered or "app_access_password" in lowered:
            return True
    return False


def ensure_cloud_dirs() -> None:
    for name in ALLOWED_RESOURCES:
        (CLOUD_DIR / name).mkdir(parents=True, exist_ok=True)


def get_sync_status() -> dict[str, Any]:
    ensure_cloud_dirs()
    state = _read_json(SYNC_STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    return {
        "enabled": bool(state.get("enabled", False)),
        "last_sync_time": state.get("last_sync_time", ""),
        "last_result": state.get("last_result", "未同步"),
        "last_error": state.get("last_error", ""),
        "cloud_dir": str(CLOUD_DIR),
        "resources": sorted(ALLOWED_RESOURCES),
        "conflicts": state.get("conflicts", []),
    }


def save_sync_status(status: dict[str, Any]) -> dict[str, Any]:
    current = get_sync_status()
    current.update(status or {})
    _write_json(SYNC_STATE_PATH, current)
    return current


def push_data(resource_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if resource_type not in ALLOWED_RESOURCES:
        return {"ok": False, "message": "不支持的同步资源类型。"}
    if _contains_sensitive(payload):
        _audit({"event": "push_blocked", "resource_type": resource_type, "result": "blocked", "reason": "检测到敏感字段"})
        return {"ok": False, "message": "同步被阻止：检测到敏感字段，API Secret、密码和 Token 不允许同步。"}
    ensure_cloud_dirs()
    path = CLOUD_DIR / resource_type / f"{resource_type}_{uuid.uuid4().hex[:12]}.json"
    ok = _write_json(path, {"resource_type": resource_type, "synced_time": _now(), "payload": payload})
    save_sync_status({"last_sync_time": _now(), "last_result": "push_ok" if ok else "push_failed", "last_error": "" if ok else "写入失败"})
    _audit({"event": "push_data", "resource_type": resource_type, "result": "ok" if ok else "failed", "path": str(path)})
    return {"ok": ok, "message": "同步写入完成。" if ok else "同步失败，本地系统继续运行。", "path": str(path)}


def pull_data(resource_type: str) -> dict[str, Any]:
    if resource_type not in ALLOWED_RESOURCES:
        return {"ok": False, "message": "不支持的同步资源类型。"}
    ensure_cloud_dirs()
    files = sorted((CLOUD_DIR / resource_type).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {"ok": True, "message": "暂无云端数据。", "items": []}
    items = [_read_json(path, {}) for path in files[:20]]
    return {"ok": True, "message": "已读取 mock 云同步数据。", "items": items}


def list_remote_backups() -> list[dict[str, Any]]:
    ensure_cloud_dirs()
    rows = []
    for path in (CLOUD_DIR / "backups").glob("*"):
        rows.append({"backup_id": path.stem, "path": str(path), "size": path.stat().st_size, "updated_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))})
    return sorted(rows, key=lambda row: row["updated_time"], reverse=True)


def upload_backup(file_path: str) -> dict[str, Any]:
    source = Path(file_path)
    if not source.exists() or not source.is_file():
        return {"ok": False, "message": "备份文件不存在。"}
    ensure_cloud_dirs()
    target = CLOUD_DIR / "backups" / source.name
    try:
        shutil.copy2(source, target)
        _audit({"event": "upload_backup", "result": "ok", "path": str(target)})
        return {"ok": True, "message": "备份已写入 mock 云同步目录。", "path": str(target)}
    except Exception as exc:
        _audit({"event": "upload_backup", "result": "failed", "reason": str(exc)})
        return {"ok": False, "message": f"备份同步失败：{exc}"}


def download_backup(backup_id: str) -> dict[str, Any]:
    matches = [row for row in list_remote_backups() if row["backup_id"] == backup_id]
    if not matches:
        return {"ok": False, "message": "未找到备份。"}
    return {"ok": True, "message": "mock 云同步备份可用。", "backup": matches[0]}


def get_cloud_sync_status() -> dict[str, Any]:
    return get_sync_status()


def sync_config_to_cloud() -> dict[str, Any]:
    payload = {
        "server_settings": _read_json(CONFIG_DIR / "server_settings.json", {}),
        "notification_rules": _read_json(CONFIG_DIR / "notification_rules.json", {}),
    }
    return push_data("config", payload)


def sync_notifications() -> dict[str, Any]:
    notifications = _read_json(DATA_DIR / "notifications.json", [])
    if isinstance(notifications, list):
        slim = [
            {
                "notification_id": item.get("notification_id"),
                "status": item.get("status"),
                "read_time": item.get("read_time"),
                "read_by": item.get("read_by", []),
                "archived_by": item.get("archived_by", []),
            }
            for item in notifications
        ]
    else:
        slim = []
    return push_data("notifications", {"items": slim})


def sync_approvals() -> dict[str, Any]:
    approvals = _read_json(DATA_DIR / "approval_queue.json", [])
    if isinstance(approvals, list):
        slim = [{"approval_id": item.get("approval_id"), "status": item.get("status"), "updated_time": item.get("updated_time", "")} for item in approvals]
    else:
        slim = []
    return push_data("approvals", {"items": slim})


def sync_reports() -> dict[str, Any]:
    reports_dir = ROOT_DIR / "strategy_reports"
    reports = []
    if reports_dir.exists():
        reports = [{"name": p.name, "size": p.stat().st_size} for p in reports_dir.glob("*") if p.is_file()]
    return push_data("reports", {"reports": reports})


def sync_backups() -> dict[str, Any]:
    backups_dir = ROOT_DIR / "backups"
    manifests = []
    if backups_dir.exists():
        for path in backups_dir.rglob("backup_manifest.json"):
            manifests.append(_read_json(path, {}))
    return push_data("backups", {"manifests": manifests[-20:]})


def load_sync_audit(limit: int = 100) -> list[dict[str, Any]]:
    rows = _read_json(SYNC_AUDIT_PATH, [])
    return (rows if isinstance(rows, list) else [])[:limit]


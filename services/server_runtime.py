"""服务器部署、长期运行健康检查和安全启动工具。

8.1 的重点是让服务器启动默认保守：不自动恢复实盘、不自动恢复自动实盘；
目录、日志、备份和健康状态统一由这里管理。
"""

from __future__ import annotations

import csv
import json
import os
import platform
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT_DIR / "config"
DATA_DIR = ROOT_DIR / "data"
LOGS_DIR = ROOT_DIR / "logs"
BACKUPS_DIR = ROOT_DIR / "backups"
SCRIPTS_DIR = ROOT_DIR / "scripts"
DOCS_DIR = ROOT_DIR / "docs"
RUNTIME_DIR = ROOT_DIR / "runtime"

HEARTBEAT_PATH = RUNTIME_DIR / "server_heartbeat.json"
RESTART_LOG_PATH = RUNTIME_DIR / "server_restart_log.json"
SAFETY_EVENTS_PATH = RUNTIME_DIR / "runtime_safety_events.json"
STARTUP_MARKER_PATH = RUNTIME_DIR / "server_startup_marker.json"
BACKUP_SETTINGS_PATH = CONFIG_DIR / "backup_settings.json"
ROTATION_SETTINGS_PATH = CONFIG_DIR / "log_rotation_settings.json"
SERVER_SETTINGS_PATH = CONFIG_DIR / "server_settings.json"

DEFAULT_SERVER_SETTINGS = {
    "default_trading_mode": "READ_ONLY",
    "restore_sim_auto_on_restart": False,
    "enable_simple_auth": False,
    "app_access_password_set": False,
    "last_safe_startup_time": "",
    "last_startup_mode": "READ_ONLY",
}

DEFAULT_BACKUP_SETTINGS = {
    "auto_daily_backup": False,
    "auto_weekly_backup": False,
    "include_logs": True,
    "include_config": True,
    "include_data": True,
}

DEFAULT_ROTATION_SETTINGS = {
    "log_retention_days": 30,
    "audit_log_retention_days": 180,
    "auto_archive_logs": True,
    "last_rotation_time": "",
}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Path, default: Any) -> Any:
    try:
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


def _append_json_log(path: Path, event: dict[str, Any]) -> None:
    rows = _read_json(path, [])
    if not isinstance(rows, list):
        rows = []
    rows.insert(0, {"time": _now(), **event})
    _write_json(path, rows[:2000])


def ensure_server_directories() -> dict[str, Any]:
    results = []
    for name, path in {
        "config": CONFIG_DIR,
        "data": DATA_DIR,
        "logs": LOGS_DIR,
        "backups": BACKUPS_DIR,
        "scripts": SCRIPTS_DIR,
        "docs": DOCS_DIR,
        "runtime": RUNTIME_DIR,
    }.items():
        try:
            path.mkdir(parents=True, exist_ok=True)
            results.append({"name": name, "path": str(path), "ok": True, "message": "目录可用。"})
        except Exception as exc:
            results.append({"name": name, "path": str(path), "ok": False, "message": f"目录不可用：{exc}"})
    return {"ok": all(row["ok"] for row in results), "items": results}


def _can_write(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / f".write_test_{int(time.time() * 1000)}"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        return True, "可写"
    except Exception as exc:
        return False, str(exc)


def load_server_settings() -> dict[str, Any]:
    ensure_server_directories()
    raw = _read_json(SERVER_SETTINGS_PATH, DEFAULT_SERVER_SETTINGS.copy())
    settings = DEFAULT_SERVER_SETTINGS.copy()
    if isinstance(raw, dict):
        settings.update(raw)
    settings["enable_simple_auth"] = str(os.environ.get("ENABLE_SIMPLE_AUTH", settings.get("enable_simple_auth"))).lower() in {"1", "true", "yes", "on"}
    settings["app_access_password_set"] = bool(os.environ.get("APP_ACCESS_PASSWORD"))
    settings["default_trading_mode"] = os.environ.get("DEFAULT_TRADING_MODE", settings.get("default_trading_mode", "READ_ONLY"))
    return settings


def save_server_settings(settings: dict[str, Any]) -> dict[str, Any]:
    merged = DEFAULT_SERVER_SETTINGS.copy()
    merged.update(settings or {})
    _write_json(SERVER_SETTINGS_PATH, merged)
    return merged


def get_env_status() -> dict[str, Any]:
    env_path = ROOT_DIR / ".env"
    keys = [
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "BINANCE_TESTNET_API_KEY",
        "BINANCE_TESTNET_API_SECRET",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
        "LIVE_TRADING_ENABLED",
        "LIVE_AUTO_PILOT_ENABLED",
        "DEFAULT_TRADING_MODE",
        "SERVER_HOST",
        "SERVER_PORT",
        "ENABLE_SIMPLE_AUTH",
        "APP_ACCESS_PASSWORD",
    ]
    configured = {key: bool(os.environ.get(key)) for key in keys}
    return {
        "env_exists": env_path.exists(),
        "env_path": str(env_path),
        "configured": configured,
        "live_trading_enabled": str(os.environ.get("LIVE_TRADING_ENABLED", "false")).lower() in {"1", "true", "yes", "on"},
        "live_auto_env_enabled": str(os.environ.get("LIVE_AUTO_PILOT_ENABLED", "false")).lower() in {"1", "true", "yes", "on"},
    }


def check_server_config() -> dict[str, Any]:
    ensure_server_directories()
    logs_ok, logs_msg = _can_write(LOGS_DIR)
    data_ok, data_msg = _can_write(DATA_DIR)
    backup_ok, backup_msg = _can_write(BACKUPS_DIR)
    env = get_env_status()
    checks = [
        {"name": "config目录", "ok": CONFIG_DIR.exists(), "message": str(CONFIG_DIR)},
        {"name": "data目录可写", "ok": data_ok, "message": data_msg},
        {"name": "logs目录可写", "ok": logs_ok, "message": logs_msg},
        {"name": "backups目录可写", "ok": backup_ok, "message": backup_msg},
        {"name": ".env文件", "ok": env["env_exists"], "message": "存在" if env["env_exists"] else "缺少 .env，系统仍以 READ_ONLY 运行。"},
        {"name": "Python版本", "ok": sys.version_info >= (3, 10), "message": platform.python_version()},
        {"name": "requirements.txt", "ok": (ROOT_DIR / "requirements.txt").exists(), "message": str(ROOT_DIR / "requirements.txt")},
        {"name": "实盘环境变量", "ok": not env["live_trading_enabled"], "message": "LIVE_TRADING_ENABLED=true 时服务器启动会强制保持保守模式。"},
        {"name": "自动实盘环境变量", "ok": not env["live_auto_env_enabled"], "message": "LIVE_AUTO_PILOT_ENABLED=true 不会在服务器重启后自动恢复。"},
    ]
    severe = [row for row in checks if not row["ok"] and row["name"] in {"data目录可写", "logs目录可写"}]
    if severe:
        record_runtime_safety_event("服务器配置异常", "数据或日志目录不可写，交易相关操作应保持关闭。", "高")
    return {"ok": not severe, "checks": checks, "env": env}


def record_runtime_safety_event(event: str, reason: str, risk_level: str = "中") -> None:
    _append_json_log(SAFETY_EVENTS_PATH, {"event": event, "reason": reason, "risk_level": risk_level})


def apply_safe_startup(force: bool = False) -> dict[str, Any]:
    """服务器启动安全降级：不自动恢复 Live Manual / LIVE_AUTO_PILOT。"""
    ensure_server_directories()
    current_pid = os.getpid()
    marker = _read_json(STARTUP_MARKER_PATH, {})
    if not force and isinstance(marker, dict) and marker.get("pid") == current_pid and marker.get("safe_startup_done"):
        return {
            "ok": True,
            "event": "服务器安全启动",
            "restored_mode": marker.get("restored_mode", "READ_ONLY"),
            "actions": [],
            "message": "当前服务器进程已完成安全启动检查，未重复执行降级。",
        }
    server_settings = load_server_settings()
    config_check = check_server_config()
    previous_live = _read_json(DATA_DIR / "live_settings.json", {})
    previous_auto = _read_json(DATA_DIR / "live_auto_config.json", {})
    restored_mode = "READ_ONLY"
    actions: list[str] = []

    if isinstance(previous_live, dict):
        previous_live["mode"] = "read_only"
        previous_live["live_manual_enabled"] = False
        _write_json(DATA_DIR / "live_settings.json", previous_live)
        actions.append("Live Manual 已在服务器启动时关闭。")

    if isinstance(previous_auto, dict):
        previous_auto["mode"] = "OFF"
        previous_auto["live_auto_pilot_enabled"] = False
        previous_auto["live_auto_order_enabled"] = False
        previous_auto["live_auto_exit_enabled"] = False
        previous_auto["paused"] = True
        _write_json(DATA_DIR / "live_auto_config.json", previous_auto)
        actions.append("LIVE_AUTO_PILOT 已在服务器启动时关闭并暂停。")

    if not config_check.get("ok"):
        restored_mode = "READ_ONLY"
        actions.append("配置检查异常，保持 READ_ONLY。")

    server_settings["last_safe_startup_time"] = _now()
    server_settings["last_startup_mode"] = restored_mode
    save_server_settings(server_settings)
    event = {
        "event": "服务器安全启动",
        "abnormal_restart": False,
        "previous_live_mode": previous_live.get("mode", "unknown") if isinstance(previous_live, dict) else "unknown",
        "previous_auto_mode": previous_auto.get("mode", "unknown") if isinstance(previous_auto, dict) else "unknown",
        "restored_mode": restored_mode,
        "actions": actions,
        "message": "服务器启动完成，当前模式：READ_ONLY。真实交易未自动开启。",
    }
    _append_json_log(RESTART_LOG_PATH, event)
    _write_json(STARTUP_MARKER_PATH, {"pid": current_pid, "safe_startup_done": True, "restored_mode": restored_mode, "time": _now()})
    write_server_heartbeat({"mode": restored_mode, "last_error": ""})
    return {"ok": True, **event}


def write_server_heartbeat(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = _read_json(HEARTBEAT_PATH, {})
    start_time = previous.get("start_time") or _now()
    heartbeat = {
        "start_time": start_time,
        "last_heartbeat_time": _now(),
        "mode": "READ_ONLY",
        "last_market_update": "",
        "last_committee_run": "",
        "last_simulation_run": "",
        "last_approval_update": "",
        "last_live_check": "",
        "last_error": "",
    }
    heartbeat.update(previous if isinstance(previous, dict) else {})
    heartbeat.update(extra or {})
    heartbeat["last_heartbeat_time"] = _now()
    _write_json(HEARTBEAT_PATH, heartbeat)
    return heartbeat


def _runtime_seconds(start_time: str) -> float:
    try:
        return max(0.0, time.time() - time.mktime(time.strptime(start_time, "%Y-%m-%d %H:%M:%S")))
    except Exception:
        return 0.0


def _format_uptime(seconds: float) -> str:
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    if days:
        return f"{days}天 {hours}小时"
    if hours:
        return f"{hours}小时 {minutes}分钟"
    return f"{minutes}分钟"


def _system_metrics() -> dict[str, Any]:
    disk = shutil.disk_usage(ROOT_DIR)
    metrics = {
        "disk_total_gb": round(disk.total / 1024 / 1024 / 1024, 2),
        "disk_free_gb": round(disk.free / 1024 / 1024 / 1024, 2),
        "disk_free_pct": round(disk.free / disk.total * 100, 2) if disk.total else 0,
        "cpu_percent": None,
        "memory_percent": None,
        "psutil_available": False,
    }
    try:
        import psutil  # type: ignore

        metrics["psutil_available"] = True
        metrics["cpu_percent"] = psutil.cpu_percent(interval=0)
        metrics["memory_percent"] = psutil.virtual_memory().percent
    except Exception:
        pass
    return metrics


def get_server_health() -> dict[str, Any]:
    ensure_server_directories()
    heartbeat = write_server_heartbeat()
    config = check_server_config()
    restart_log = _read_json(RESTART_LOG_PATH, [])
    safety_events = _read_json(SAFETY_EVENTS_PATH, [])
    logs_ok, logs_msg = _can_write(LOGS_DIR)
    data_ok, data_msg = _can_write(DATA_DIR)
    backup_info = get_backup_status()
    metrics = _system_metrics()
    uptime_seconds = _runtime_seconds(str(heartbeat.get("start_time", "")))
    status = "运行中"
    if not logs_ok or not data_ok or metrics.get("disk_free_pct", 100) < 5:
        status = "异常"
    elif not config.get("ok") or metrics.get("disk_free_pct", 100) < 15:
        status = "警告"
    return {
        "status": status,
        "mode": heartbeat.get("mode", "READ_ONLY"),
        "start_time": heartbeat.get("start_time", ""),
        "uptime": _format_uptime(uptime_seconds),
        "last_heartbeat_time": heartbeat.get("last_heartbeat_time", ""),
        "logs_writable": logs_ok,
        "logs_message": logs_msg,
        "data_writable": data_ok,
        "data_message": data_msg,
        "backup": backup_info,
        "config": config,
        "metrics": metrics,
        "recent_restart": (restart_log if isinstance(restart_log, list) else [])[:5],
        "recent_safety_events": (safety_events if isinstance(safety_events, list) else [])[:10],
        "systemd_status": "当前环境暂不支持读取该项。" if platform.system().lower() == "windows" else "可通过 systemctl status ai_model 查看。",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }


def rotate_logs(retention_days: int | None = None) -> dict[str, Any]:
    ensure_server_directories()
    settings = _read_json(ROTATION_SETTINGS_PATH, DEFAULT_ROTATION_SETTINGS.copy())
    if not isinstance(settings, dict):
        settings = DEFAULT_ROTATION_SETTINGS.copy()
    keep_days = int(retention_days or settings.get("log_retention_days", 30))
    archive = LOGS_DIR / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - keep_days * 86400
    archived = []
    for path in LOGS_DIR.glob("*.log"):
        if path.stat().st_mtime < cutoff:
            target = archive / f"{path.stem}_{time.strftime('%Y%m%d', time.localtime(path.stat().st_mtime))}{path.suffix}"
            try:
                shutil.move(str(path), str(target))
                archived.append(str(target))
            except Exception:
                pass
    settings["last_rotation_time"] = _now()
    _write_json(ROTATION_SETTINGS_PATH, settings)
    return {"ok": True, "archived_count": len(archived), "archived": archived, "last_rotation_time": settings["last_rotation_time"]}


def load_backup_settings() -> dict[str, Any]:
    raw = _read_json(BACKUP_SETTINGS_PATH, DEFAULT_BACKUP_SETTINGS.copy())
    settings = DEFAULT_BACKUP_SETTINGS.copy()
    if isinstance(raw, dict):
        settings.update(raw)
    return settings


def save_backup_settings(settings: dict[str, Any]) -> dict[str, Any]:
    merged = DEFAULT_BACKUP_SETTINGS.copy()
    merged.update(settings or {})
    _write_json(BACKUP_SETTINGS_PATH, merged)
    return merged


def create_backup(backup_type: str = "manual") -> dict[str, Any]:
    ensure_server_directories()
    settings = load_backup_settings()
    date_dir = time.strftime("%Y-%m-%d")
    base = BACKUPS_DIR / ("weekly" if backup_type == "weekly" else "daily" if backup_type == "daily" else "manual") / date_dir
    base.mkdir(parents=True, exist_ok=True)
    zip_path = base / f"ai_model_backup_{backup_type}_{time.strftime('%H%M%S')}.zip"
    included_roots = []
    if settings.get("include_config"):
        included_roots.append(CONFIG_DIR)
    if settings.get("include_data"):
        included_roots.append(DATA_DIR)
    if settings.get("include_logs"):
        included_roots.append(LOGS_DIR)
    included_roots.extend([ROOT_DIR / "requirements.txt", ROOT_DIR / ".env.example"])
    files = []
    errors = []
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for root in included_roots:
                if not root.exists():
                    continue
                if root.is_file():
                    if root.name == ".env":
                        continue
                    archive.write(root, root.relative_to(ROOT_DIR))
                    files.append(str(root.relative_to(ROOT_DIR)))
                    continue
                for path in root.rglob("*"):
                    if path.is_file() and path.name != ".env":
                        archive.write(path, path.relative_to(ROOT_DIR))
                        files.append(str(path.relative_to(ROOT_DIR)))
    except Exception as exc:
        errors.append(str(exc))
    manifest = {
        "backup_time": _now(),
        "backup_type": backup_type,
        "zip_file": str(zip_path),
        "file_count": len(files),
        "files": files,
        "size_bytes": zip_path.stat().st_size if zip_path.exists() else 0,
        "ok": not errors,
        "errors": errors,
    }
    _write_json(base / "backup_manifest.json", manifest)
    return manifest


def get_backup_status() -> dict[str, Any]:
    latest: Path | None = None
    if BACKUPS_DIR.exists():
        backups = list(BACKUPS_DIR.rglob("backup_manifest.json"))
        latest = max(backups, key=lambda p: p.stat().st_mtime) if backups else None
    manifest = _read_json(latest, {}) if latest else {}
    return {
        "latest_manifest": str(latest) if latest else "",
        "latest_backup_time": manifest.get("backup_time", "") if isinstance(manifest, dict) else "",
        "latest_ok": manifest.get("ok", False) if isinstance(manifest, dict) else False,
        "latest_size_bytes": manifest.get("size_bytes", 0) if isinstance(manifest, dict) else 0,
    }


def get_git_status() -> dict[str, Any]:
    git_dir = ROOT_DIR / ".git"
    if not git_dir.exists():
        return {"ok": False, "message": "当前项目未连接 Git，跳过远程更新检查。"}
    return {"ok": True, "message": "当前项目已连接 Git，可手动运行 scripts/update_from_git.sh。"}

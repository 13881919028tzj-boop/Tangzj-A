"""Runtime file compatibility guard.

The server keeps state in data/config/database/reports. Git updates or fresh
deployments must not break market data modules just because one runtime file is
missing or contains invalid JSON.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]

DEFAULT_JSON_FILES: dict[str, Any] = {
    "config/server_settings.json": {
        "enable_simple_auth": False,
        "allow_remote_control": True,
        "safe_startup": True,
    },
    "config/notification_rules.json": {
        "enabled": False,
        "channels": [],
        "notify_on_approval": True,
        "notify_on_risk": True,
    },
    "data/active_sessions.json": [],
    "data/watchlist.json": {"items": {}, "updated_at": ""},
    "data/registered_devices.json": [],
    "data/notifications.json": [],
    "data/sim_account.json": {
        "initial_equity": 10000.0,
        "equity": 10000.0,
        "available_balance": 10000.0,
        "used_margin": 0.0,
        "status": "stopped",
        "mode": "manual",
    },
    "data/sim_orders.json": [],
    "data/sim_positions.json": [],
    "data/sim_trade_history.json": [],
    "data/sim_trade_log.json": [],
    "data/approval_queue.json": [],
    "data/approval_audit_log.json": [],
    "data/live_order_records.json": [],
    "data/live_auto_positions.json": [],
    "data/strategy_candidates.json": [],
    "data/sync_state.json": {},
    "data/sync_audit_log.json": [],
}


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _merge_defaults(value: Any, default: Any) -> Any:
    if isinstance(default, dict):
        base = dict(default)
        if isinstance(value, dict):
            base.update(value)
        return base
    if isinstance(value, type(default)):
        return value
    return default


def _repair_json_file(path: Path, default: Any) -> dict[str, Any]:
    event = {"path": str(path), "action": "ok", "reason": ""}
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
            event.update({"action": "created", "reason": "missing"})
            return event

        text = path.read_text(encoding="utf-8-sig").strip()
        loaded = json.loads(text) if text else default
        repaired = _merge_defaults(loaded, default)
        if repaired != loaded:
            path.write_text(json.dumps(repaired, ensure_ascii=False, indent=2), encoding="utf-8")
            event.update({"action": "filled_defaults", "reason": "missing_fields"})
        return event
    except Exception as exc:
        try:
            backup = path.with_name(f"{path.name}.broken_{_timestamp()}")
            if path.exists():
                shutil.copy2(path, backup)
            path.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
            event.update({"action": "rebuilt", "reason": f"invalid_json: {exc!r}", "backup": str(backup)})
        except Exception as write_exc:
            event.update({"action": "failed", "reason": f"{exc!r}; rebuild_failed={write_exc!r}"})
        return event


def ensure_runtime_files() -> list[dict[str, Any]]:
    """Create/repair runtime directories and JSON files without touching secrets."""
    events: list[dict[str, Any]] = []
    for folder in ("config", "data", "database", "reports", "logs", "runtime", "backups"):
        path = ROOT_DIR / folder
        path.mkdir(parents=True, exist_ok=True)
        keep = path / ".gitkeep"
        if not keep.exists():
            try:
                keep.write_text("", encoding="utf-8")
            except Exception:
                pass
    for relative, default in DEFAULT_JSON_FILES.items():
        events.append(_repair_json_file(ROOT_DIR / relative, default))
    return events

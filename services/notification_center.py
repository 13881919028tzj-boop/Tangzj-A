"""系统内通知中心与通知规则。

本版本完整实现本地通知；Telegram、邮件、Webhook 等外部渠道先预留配置，
失败不影响主系统。
"""

from __future__ import annotations

import csv
import json
import time
import uuid
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
CONFIG_DIR = ROOT_DIR / "config"
NOTIFICATION_PATH = DATA_DIR / "notifications.json"
NOTIFICATION_AUDIT_CSV = DATA_DIR / "notification_events.csv"
RULES_PATH = CONFIG_DIR / "notification_rules.json"

DEFAULT_RULES = {
    "approval_notifications": True,
    "risk_notifications": True,
    "live_notifications": True,
    "sim_notifications": False,
    "server_notifications": True,
    "auto_live_notifications": True,
    "external_ai_notifications": True,
    "only_high_priority": False,
    "mute_low_priority": True,
    "dedupe_minutes": 5,
    "retention_days": 90,
    "auto_archive_read": False,
    "channels": {
        "system": True,
        "telegram": False,
        "email": False,
        "webhook": False,
        "push": False,
        "wechat": False,
    },
}

PRIORITY_ORDER = {"紧急": 0, "高": 1, "中": 2, "低": 3}


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


def get_notification_rules() -> dict[str, Any]:
    raw = _read_json(RULES_PATH, DEFAULT_RULES.copy())
    rules = DEFAULT_RULES.copy()
    if isinstance(raw, dict):
        rules.update(raw)
        channels = DEFAULT_RULES["channels"].copy()
        channels.update(raw.get("channels") or {})
        rules["channels"] = channels
    return rules


def save_notification_rules(rules: dict[str, Any]) -> dict[str, Any]:
    merged = DEFAULT_RULES.copy()
    merged.update(rules or {})
    channels = DEFAULT_RULES["channels"].copy()
    channels.update((rules or {}).get("channels") or {})
    merged["channels"] = channels
    _write_json(RULES_PATH, merged)
    return merged


def load_notifications(status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    rows = _read_json(NOTIFICATION_PATH, [])
    if not isinstance(rows, list):
        rows = []
    if status:
        rows = [row for row in rows if row.get("status") == status]
    rows.sort(key=lambda row: (PRIORITY_ORDER.get(str(row.get("priority", "低")), 9), str(row.get("created_time", ""))), reverse=False)
    return rows[:limit]


def _save_notifications(rows: list[dict[str, Any]]) -> None:
    _write_json(NOTIFICATION_PATH, rows[:2000])


def _audit_notification(event: dict[str, Any]) -> None:
    row = {"time": _now(), **event}
    rows = []
    try:
        if NOTIFICATION_AUDIT_CSV.exists():
            with NOTIFICATION_AUDIT_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
    except Exception:
        rows = []
    rows.insert(0, row)
    try:
        NOTIFICATION_AUDIT_CSV.parent.mkdir(parents=True, exist_ok=True)
        with NOTIFICATION_AUDIT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["time", "event", "notification_id", "type", "priority", "result", "reason"])
            writer.writeheader()
            writer.writerows(rows[:1000])
    except Exception:
        pass


def _is_enabled_by_rules(notification_type: str, priority: str, rules: dict[str, Any]) -> tuple[bool, str]:
    if rules.get("only_high_priority") and priority not in {"高", "紧急"}:
        return False, "已开启仅通知高优先级。"
    if rules.get("mute_low_priority") and priority == "低":
        return False, "低优先级通知已静音。"
    mapping = {
        "approval": "approval_notifications",
        "risk": "risk_notifications",
        "live": "live_notifications",
        "sim": "sim_notifications",
        "server": "server_notifications",
        "auto_live": "auto_live_notifications",
        "external_ai": "external_ai_notifications",
    }
    key = mapping.get(notification_type, "")
    if key and not rules.get(key, True):
        return False, f"{notification_type} 通知已关闭。"
    return True, "规则允许。"


def create_notification(event: dict[str, Any]) -> dict[str, Any]:
    rules = get_notification_rules()
    notification_type = str(event.get("type", "system"))
    priority = str(event.get("priority", "中"))
    enabled, reason = _is_enabled_by_rules(notification_type, priority, rules)
    if not enabled:
        return {"ok": False, "message": reason, "skipped": True}
    rows = _read_json(NOTIFICATION_PATH, [])
    if not isinstance(rows, list):
        rows = []
    now_ts = time.time()
    dedupe_seconds = int(rules.get("dedupe_minutes", 5) or 5) * 60
    dedupe_key = str(event.get("dedupe_key") or f"{notification_type}:{event.get('symbol','')}:{event.get('title','')}")
    for row in rows:
        if row.get("dedupe_key") != dedupe_key or row.get("status") not in {"unread", "read"}:
            continue
        try:
            created_ts = time.mktime(time.strptime(str(row.get("created_time")), "%Y-%m-%d %H:%M:%S"))
        except Exception:
            created_ts = 0
        if now_ts - created_ts <= dedupe_seconds and priority != "紧急":
            row["message"] = f"{event.get('message', '')}\n\n同类事件已合并，累计 {int(row.get('merge_count', 1) or 1) + 1} 次。"
            row["merge_count"] = int(row.get("merge_count", 1) or 1) + 1
            row["updated_time"] = _now()
            _save_notifications(rows)
            return {"ok": True, "notification": row, "merged": True}
    notification = {
        "notification_id": f"ntf_{uuid.uuid4().hex[:12]}",
        "type": notification_type,
        "priority": priority,
        "title": str(event.get("title", "系统通知")),
        "message": str(event.get("message", "")),
        "symbol": str(event.get("symbol", "")),
        "related_id": str(event.get("related_id", "")),
        "created_time": _now(),
        "updated_time": _now(),
        "read_time": "",
        "status": "unread",
        "actions": event.get("actions") or [],
        "source": str(event.get("source", "system")),
        "requires_attention": bool(event.get("requires_attention", priority in {"高", "紧急"})),
        "dedupe_key": dedupe_key,
        "merge_count": 1,
    }
    rows.insert(0, notification)
    _save_notifications(rows)
    _audit_notification({"event": "create", "notification_id": notification["notification_id"], "type": notification_type, "priority": priority, "result": "ok", "reason": notification["title"]})
    return {"ok": True, "notification": notification, "merged": False}


def mark_notification_read(notification_id: str) -> dict[str, Any]:
    rows = _read_json(NOTIFICATION_PATH, [])
    if not isinstance(rows, list):
        return {"ok": False, "message": "通知中心暂不可用，系统已记录错误。"}
    for row in rows:
        if row.get("notification_id") == notification_id:
            row["status"] = "read"
            row["read_time"] = _now()
            _save_notifications(rows)
            return {"ok": True, "message": "通知已标记为已读。"}
    return {"ok": False, "message": "未找到通知。"}


def mark_all_notifications_read() -> dict[str, Any]:
    rows = _read_json(NOTIFICATION_PATH, [])
    if not isinstance(rows, list):
        return {"ok": False, "message": "通知中心暂不可用，系统已记录错误。"}
    count = 0
    for row in rows:
        if row.get("status") == "unread":
            row["status"] = "read"
            row["read_time"] = _now()
            count += 1
    _save_notifications(rows)
    return {"ok": True, "message": f"已标记 {count} 条通知为已读。"}


def archive_notification(notification_id: str) -> dict[str, Any]:
    rows = _read_json(NOTIFICATION_PATH, [])
    if not isinstance(rows, list):
        return {"ok": False, "message": "通知中心暂不可用。"}
    for row in rows:
        if row.get("notification_id") == notification_id:
            row["status"] = "archived"
            row["updated_time"] = _now()
            _save_notifications(rows)
            return {"ok": True, "message": "通知已归档。"}
    return {"ok": False, "message": "未找到通知。"}


def dispatch_notification(notification: dict[str, Any]) -> dict[str, Any]:
    rules = get_notification_rules()
    channels = rules.get("channels") or {}
    if not channels.get("system", True):
        return {"ok": False, "message": "系统内通知已关闭。"}
    # 外部渠道预留：不发送敏感数据，失败不影响主系统。
    return {"ok": True, "message": "系统内通知已保留。外部通知渠道为预留状态。"}


def trigger_system_alert(alert_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    titles = {
        "startup": "服务器启动",
        "heartbeat": "服务器心跳异常",
        "backup": "服务器备份事件",
        "config": "服务器配置提醒",
    }
    return create_notification({
        "type": "server",
        "priority": payload.get("priority", "中"),
        "title": titles.get(alert_type, "系统提醒"),
        "message": payload.get("message", ""),
        "source": "server",
        "dedupe_key": f"server:{alert_type}",
        "actions": [{"label": "查看服务器健康", "page": "server"}],
    })


def trigger_approval_alert(approval: dict[str, Any]) -> dict[str, Any]:
    symbol = str(approval.get("symbol", ""))
    priority = "高" if approval.get("priority") in {"高", "紧急"} else "中"
    return create_notification({
        "type": "approval",
        "priority": priority,
        "title": "新的交易审批单",
        "message": f"{symbol} 出现 {approval.get('approval_type', '交易')} 审批单，来源：{approval.get('source', '系统')}。",
        "symbol": symbol,
        "related_id": approval.get("approval_id", ""),
        "source": "approval",
        "actions": [{"label": "查看审批中心", "page": "approval"}],
        "dedupe_key": f"approval:{approval.get('approval_id','')}",
    })


def trigger_risk_alert(risk_event: dict[str, Any]) -> dict[str, Any]:
    return create_notification({
        "type": "risk",
        "priority": risk_event.get("priority", "高"),
        "title": risk_event.get("title", "风险提醒"),
        "message": risk_event.get("message", ""),
        "symbol": risk_event.get("symbol", ""),
        "source": risk_event.get("source", "risk"),
        "actions": [{"label": "查看信号页", "page": "signals"}],
        "dedupe_key": risk_event.get("dedupe_key") or f"risk:{risk_event.get('symbol','')}:{risk_event.get('title','')}",
    })


def trigger_server_alert(server_event: dict[str, Any]) -> dict[str, Any]:
    return create_notification({
        "type": "server",
        "priority": server_event.get("priority", "高"),
        "title": server_event.get("title", "服务器提醒"),
        "message": server_event.get("message", ""),
        "source": "server",
        "actions": [{"label": "查看健康中心", "page": "server"}],
        "dedupe_key": server_event.get("dedupe_key") or f"server:{server_event.get('title','')}",
    })


def get_notification_summary() -> dict[str, Any]:
    rows = load_notifications(limit=1000)
    unread = [row for row in rows if row.get("status") == "unread"]
    urgent = [row for row in unread if row.get("priority") == "紧急"]
    return {
        "total": len(rows),
        "unread_count": len(unread),
        "urgent_count": len(urgent),
        "approval_count": len([row for row in unread if row.get("type") == "approval"]),
        "risk_count": len([row for row in unread if row.get("type") == "risk"]),
        "server_count": len([row for row in unread if row.get("type") == "server"]),
        "live_count": len([row for row in unread if row.get("type") in {"live", "auto_live"}]),
        "latest": unread[:5],
    }


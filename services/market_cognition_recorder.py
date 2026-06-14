"""Controlled JSONL recorder for market cognition snapshots."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = ROOT / "data" / "market_cognition_snapshots"
MAX_DAILY_BYTES = 100 * 1024 * 1024
THROTTLE_SECONDS = 300
RETENTION_DAYS = 7
_LAST_WRITE: dict[str, float] = {}


def _today_path() -> Path:
    name = datetime.now(timezone.utc).strftime("market_cognition_%Y%m%d.jsonl")
    return SNAPSHOT_DIR / name


def _compact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    blocked_keys = {"orderbook", "raw_orderbook", "api_key", "secret", "password", "token"}
    return {key: value for key, value in (snapshot or {}).items() if key not in blocked_keys}


def _cleanup_old_files() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    for path in SNAPSHOT_DIR.glob("market_cognition_*.jsonl"):
        try:
            stem_date = path.stem.replace("market_cognition_", "")
            file_date = datetime.strptime(stem_date, "%Y%m%d").replace(tzinfo=timezone.utc)
            if file_date < cutoff:
                path.unlink(missing_ok=True)
        except Exception:
            continue


def save_market_cognition_snapshot(snapshot: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Save one compact snapshot.

    The recorder is intentionally best-effort.  It never raises into the caller
    because market cognition persistence must not affect the live page.
    """
    try:
        symbol = str((snapshot or {}).get("symbol") or "").upper()
        if not symbol:
            return {"ok": False, "skipped": True, "reason": "missing_symbol"}
        now = time.time()
        if not force and now - _LAST_WRITE.get(symbol, 0) < THROTTLE_SECONDS:
            return {"ok": True, "skipped": True, "reason": "throttled"}
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = _today_path()
        if path.exists() and path.stat().st_size >= MAX_DAILY_BYTES:
            return {"ok": False, "skipped": True, "reason": "daily_file_too_large"}
        _cleanup_old_files()
        payload = _compact_snapshot(snapshot)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        _LAST_WRITE[symbol] = now
        return {"ok": True, "skipped": False, "path": str(path)}
    except Exception as exc:
        return {"ok": False, "skipped": True, "reason": f"write_failed: {exc!r}"}

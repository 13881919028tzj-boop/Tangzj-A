"""Read-only system operations helpers for the operations center."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
APP_STARTED_AT = time.time()


def _run_command(args: list[str], timeout: int = 12) -> dict[str, Any]:
    try:
        completed = subprocess.run(args, cwd=BASE_DIR, capture_output=True, text=True, timeout=timeout, check=False)
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": (completed.stdout or "").strip()[-4000:],
            "stderr": (completed.stderr or "").strip()[-4000:],
        }
    except FileNotFoundError:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"命令不存在：{args[0]}"}
    except Exception as exc:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": repr(exc)}


def _format_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}天{hours}小时{minutes}分钟"
    if hours:
        return f"{hours}小时{minutes}分钟"
    return f"{minutes}分钟"


def get_git_version() -> str:
    result = _run_command(["git", "rev-parse", "--short", "HEAD"], timeout=5)
    return result.get("stdout") or "暂无"


def get_system_operations_status() -> dict[str, Any]:
    disk = shutil.disk_usage(BASE_DIR)
    cpu = "当前环境暂不支持"
    memory = "当前环境暂不支持"
    try:
        import psutil  # type: ignore

        cpu = f"{psutil.cpu_percent(interval=0.1):.1f}%"
        memory = f"{psutil.virtual_memory().percent:.1f}%"
    except Exception:
        pass
    is_linux = platform.system().lower() == "linux"
    service_status = _run_command(["systemctl", "is-active", "aimodel"], timeout=5) if is_linux else {"ok": False, "stdout": "非Linux/systemd环境"}
    return {
        "server_status": "运行中",
        "ai_model_status": (service_status.get("stdout") or "未知").strip(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "uptime": _format_seconds(time.time() - APP_STARTED_AT),
        "cpu": cpu,
        "memory": memory,
        "disk": f"{disk.free / 1024 / 1024 / 1024:.2f} GB 可用 / {disk.total / 1024 / 1024 / 1024:.2f} GB",
        "github_version": get_git_version(),
        "systemd_available": is_linux,
        "base_dir": str(BASE_DIR),
    }


def run_aimodel_control(action: str) -> dict[str, Any]:
    if platform.system().lower() != "linux":
        return {"ok": False, "stdout": "", "stderr": "当前不是 Ubuntu/systemd 环境，本地桌面仅显示状态，不执行 systemctl。"}
    if action == "status":
        return _run_command(["systemctl", "status", "aimodel", "--no-pager"], timeout=12)
    if action in {"restart", "stop", "start"}:
        return _run_command(["systemctl", action, "aimodel"], timeout=20)
    if action == "update":
        return _run_command(["/root/update_ai.sh"], timeout=120)
    return {"ok": False, "stdout": "", "stderr": "未知运维操作。"}


def load_recent_log_lines(limit: int = 100, keyword: str = "", level: str = "全部") -> list[dict[str, str]]:
    paths = [
        BASE_DIR / "data" / "sim_trade_log.json",
        BASE_DIR / "data" / "error_log.json",
        BASE_DIR / "logs" / "app.log",
        BASE_DIR / "streamlit.err.log",
    ]
    rows: list[dict[str, str]] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        except Exception:
            continue
        for line in text.splitlines()[-300:]:
            raw = line.strip()
            if not raw:
                continue
            upper = raw.upper()
            detected = "ERROR" if "ERROR" in upper or "失败" in raw or "异常" in raw else "WARNING" if "WARNING" in upper or "警告" in raw else "INFO"
            if level != "全部" and detected != level:
                continue
            if keyword and keyword.lower() not in raw.lower():
                continue
            rows.append({"level": detected, "source": path.name, "content": raw[-800:]})
    return rows[-limit:][::-1]

"""数据看板与经营分析报告聚合层。

本模块只读取已有本地数据和日志，不执行任何交易动作。
所有统计在数据缺失、为空或损坏时都返回保守结果，避免影响主系统运行。
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
REPORT_DIR = BASE_DIR / "reports"
QUALITY_LOG = DATA_DIR / "data_quality_log.json"

SAMPLE_LIMITS = {
    "simulation": 30,
    "live": 10,
    "auto_live": 30,
    "approval": 20,
    "committee": 30,
    "external_ai": 30,
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("daily", "weekly", "monthly", "exports"):
        (REPORT_DIR / name).mkdir(parents=True, exist_ok=True)


def _append_quality_issue(path: Path, issue: str) -> None:
    _ensure_dirs()
    items = _read_json_file(QUALITY_LOG, default=[])
    if not isinstance(items, list):
        items = []
    items.append({"time": _now(), "file": str(path), "issue": issue})
    QUALITY_LOG.write_text(json.dumps(items[-500:], ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json_file(path: Path, default: Any = None) -> Any:
    if default is None:
        default = []
    if not path.exists():
        return default
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
        if not text:
            return default
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001 - dashboard must never crash on bad data
        _append_quality_issue(path, f"JSON读取失败：{exc}")
        return default


def _read_csv_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as exc:  # noqa: BLE001
        _append_quality_issue(path, f"CSV读取失败：{exc}")
        return []


def _as_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("items", "records", "trades", "orders", "events", "queue", "history", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return [data]
    return []


def _load_first_existing(paths: list[Path]) -> tuple[list[dict[str, Any]], str]:
    for path in paths:
        if path.suffix.lower() == ".csv":
            rows = _read_csv_file(path)
        else:
            rows = _as_list(_read_json_file(path, default=[]))
        if rows:
            return rows, str(path.relative_to(BASE_DIR))
    return [], "暂无可用数据源"


def _sample_warning(count: int, limit: int, label: str) -> str:
    if count < limit:
        return f"{label}样本数量不足，当前结果仅供观察，不建议作为扩大实盘额度依据。"
    return f"{label}样本数量已达到基础观察门槛，但仍需持续复盘。"


def _pnl_from_row(row: dict[str, Any]) -> float:
    for key in ("pnl", "profit", "realized_pnl", "total_pnl", "net_pnl", "unrealized_pnl", "收益", "盈亏"):
        if key in row:
            return _safe_float(row.get(key))
    return 0.0


def _is_win(row: dict[str, Any]) -> bool:
    if "is_win" in row:
        return str(row.get("is_win")).lower() in {"1", "true", "yes", "盈利", "win"}
    return _pnl_from_row(row) > 0


def _profit_factor(pnls: list[float]) -> float:
    gross_profit = sum(x for x in pnls if x > 0)
    gross_loss = abs(sum(x for x in pnls if x < 0))
    if gross_loss == 0:
        return gross_profit if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def _max_drawdown_from_equity(rows: list[dict[str, Any]]) -> float:
    peak = None
    max_dd = 0.0
    for row in rows:
        equity = _safe_float(row.get("equity") or row.get("当前权益") or row.get("balance"))
        if equity <= 0:
            continue
        peak = equity if peak is None else max(peak, equity)
        if peak:
            max_dd = max(max_dd, (peak - equity) / peak * 100)
    return max_dd


def _basic_trade_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [_pnl_from_row(row) for row in rows]
    wins = [row for row in rows if _is_win(row)]
    losses = [row for row in rows if not _is_win(row) and _pnl_from_row(row) < 0]
    symbols = [str(row.get("symbol") or row.get("交易对象") or "-") for row in rows]
    return {
        "trade_count": len(rows),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": (len(wins) / len(rows) * 100) if rows else 0.0,
        "total_pnl": sum(pnls),
        "avg_pnl": mean(pnls) if pnls else 0.0,
        "profit_factor": _profit_factor(pnls),
        "best_trade": max(pnls) if pnls else 0.0,
        "worst_trade": min(pnls) if pnls else 0.0,
        "most_common_symbol": Counter(symbols).most_common(1)[0][0] if symbols else "-",
    }


def collect_simulation_metrics() -> dict[str, Any]:
    trades, source = _load_first_existing(
        [
            DATA_DIR / "sim_trade_history.json",
            DATA_DIR / "sim_trade_history.csv",
            BASE_DIR / "sim_trade_history.json",
            BASE_DIR / "sim_trade_history.csv",
        ]
    )
    equity, equity_source = _load_first_existing(
        [
            DATA_DIR / "sim_equity_curve.json",
            DATA_DIR / "sim_equity_curve.csv",
            BASE_DIR / "sim_equity_curve.json",
            BASE_DIR / "sim_equity_curve.csv",
        ]
    )
    metrics = _basic_trade_metrics(trades)
    metrics.update(
        {
            "source": source,
            "equity_source": equity_source,
            "equity_points": len(equity),
            "max_drawdown": _max_drawdown_from_equity(equity),
            "sample_warning": _sample_warning(len(trades), SAMPLE_LIMITS["simulation"], "模拟交易"),
            "data_quality": "good" if trades else "poor",
        }
    )
    return metrics


def collect_live_metrics() -> dict[str, Any]:
    orders, source = _load_first_existing(
        [
            DATA_DIR / "live_order_records.json",
            DATA_DIR / "live_order_records.csv",
            BASE_DIR / "live_order_records.json",
            BASE_DIR / "live_order_records.csv",
        ]
    )
    positions, position_source = _load_first_existing(
        [
            DATA_DIR / "live_position_records.json",
            DATA_DIR / "live_position_audit_log.json",
            DATA_DIR / "live_position_audit_log.csv",
            BASE_DIR / "live_position_records.json",
        ]
    )
    statuses = Counter(str(row.get("status") or row.get("订单状态") or "-") for row in orders)
    pnls = [_pnl_from_row(row) for row in orders + positions]
    metrics = _basic_trade_metrics(orders)
    metrics.update(
        {
            "source": source,
            "position_source": position_source,
            "filled_count": statuses.get("FILLED", 0) + statuses.get("filled", 0),
            "cancelled_count": statuses.get("CANCELED", 0) + statuses.get("cancelled", 0),
            "manual_override_count": sum(1 for row in orders if str(row.get("manual_override") or row.get("是否人工干预")).lower() in {"1", "true", "yes", "是"}),
            "total_invested": sum(_safe_float(row.get("quote_amount") or row.get("notional") or row.get("名义金额")) for row in orders),
            "estimated_fee": sum(_safe_float(row.get("fee") or row.get("estimated_fee") or row.get("手续费")) for row in orders),
            "combined_pnl": sum(pnls),
            "sample_warning": _sample_warning(len(orders), SAMPLE_LIMITS["live"], "实盘交易"),
            "data_quality": "good" if orders or positions else "poor",
        }
    )
    return metrics


def collect_auto_live_metrics() -> dict[str, Any]:
    rows, source = _load_first_existing(
        [
            DATA_DIR / "live_auto_audit_log.json",
            DATA_DIR / "live_auto_audit_log.csv",
            BASE_DIR / "live_auto_audit_log.json",
        ]
    )
    events = Counter(str(row.get("event") or row.get("type") or "-") for row in rows)
    results = Counter(str(row.get("result") or row.get("status") or "-") for row in rows)
    return {
        "source": source,
        "event_count": len(rows),
        "enabled_count": events.get("enable_live_auto", 0) + events.get("开启自动实盘", 0),
        "success_count": results.get("success", 0) + results.get("成功", 0),
        "failure_count": results.get("failed", 0) + results.get("失败", 0),
        "circuit_breaker_count": sum(1 for row in rows if "熔断" in str(row) or "circuit" in str(row).lower()),
        "cooldown_count": sum(1 for row in rows if "冷却" in str(row) or "cooldown" in str(row).lower()),
        "sample_warning": _sample_warning(len(rows), SAMPLE_LIMITS["auto_live"], "自动实盘"),
        "recommendation": "继续关闭自动实盘" if len(rows) < SAMPLE_LIMITS["auto_live"] else "可继续小资金试运行，暂不自动扩大额度",
        "data_quality": "good" if rows else "poor",
    }


def collect_approval_metrics() -> dict[str, Any]:
    rows, source = _load_first_existing(
        [
            DATA_DIR / "approval_queue.json",
            DATA_DIR / "approval_audit_log.json",
            DATA_DIR / "approval_audit_log.csv",
            BASE_DIR / "approval_queue.json",
        ]
    )
    statuses = Counter(str(row.get("status") or row.get("result") or "-") for row in rows)
    total = len(rows)
    approved = statuses.get("approved", 0) + statuses.get("已批准", 0)
    rejected = statuses.get("rejected", 0) + statuses.get("已拒绝", 0)
    expired = statuses.get("expired", 0) + statuses.get("已过期", 0)
    executed = statuses.get("executed", 0) + statuses.get("success", 0) + statuses.get("执行成功", 0)
    failed = statuses.get("failed", 0) + statuses.get("执行失败", 0)
    reasons = Counter(str(row.get("reason") or row.get("user_reason") or "-") for row in rows if row.get("reason") or row.get("user_reason"))
    return {
        "source": source,
        "total": total,
        "pending": statuses.get("pending", 0),
        "approved": approved,
        "rejected": rejected,
        "modified": statuses.get("modified", 0),
        "expired": expired,
        "executed": executed,
        "failed": failed,
        "approval_accept_rate": approved / total * 100 if total else 0.0,
        "approval_reject_rate": rejected / total * 100 if total else 0.0,
        "approval_expire_rate": expired / total * 100 if total else 0.0,
        "approval_execution_success_rate": executed / max(executed + failed, 1) * 100 if (executed or failed) else 0.0,
        "most_common_reason": reasons.most_common(1)[0][0] if reasons else "-",
        "sample_warning": _sample_warning(total, SAMPLE_LIMITS["approval"], "审批流"),
        "data_quality": "good" if rows else "poor",
    }


def collect_committee_metrics() -> dict[str, Any]:
    rows, source = _load_first_existing(
        [
            DATA_DIR / "committee_vote_log.json",
            DATA_DIR / "member_performance_log.json",
            BASE_DIR / "committee_vote_log.json",
            BASE_DIR / "member_performance_log.json",
        ]
    )
    member_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "support": 0, "oppose": 0, "wait": 0, "veto": 0})
    for row in rows:
        member = str(row.get("member_name") or row.get("member") or row.get("委员") or "未知委员")
        vote = str(row.get("vote") or row.get("decision") or row.get("投票") or "")
        member_stats[member]["count"] += 1
        if vote in {"支持", "support", "long", "short"}:
            member_stats[member]["support"] += 1
        elif vote in {"反对", "oppose", "软否决"}:
            member_stats[member]["oppose"] += 1
        elif vote in {"否决", "veto"}:
            member_stats[member]["veto"] += 1
        else:
            member_stats[member]["wait"] += 1
    return {
        "source": source,
        "total_votes": len(rows),
        "members": dict(member_stats),
        "sample_warning": _sample_warning(len(rows), SAMPLE_LIMITS["committee"], "委员投票"),
        "data_quality": "good" if rows else "poor",
    }


def collect_external_ai_metrics() -> dict[str, Any]:
    rows, source = _load_first_existing(
        [
            DATA_DIR / "external_ai_audit_log.json",
            DATA_DIR / "external_ai_audit_log.csv",
            DATA_DIR / "external_ai_shadow_log.json",
            BASE_DIR / "external_ai_audit_log.json",
        ]
    )
    ai_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "success": 0, "failed": 0, "soft_veto": 0, "latencies": []})
    for row in rows:
        name = str(row.get("ai_name") or row.get("AI名称") or row.get("member_name") or "外部AI")
        failed = bool(row.get("failed")) or str(row.get("result") or row.get("status")).lower() in {"failed", "error", "失败"}
        ai_stats[name]["total"] += 1
        ai_stats[name]["failed" if failed else "success"] += 1
        if str(row.get("soft_veto")).lower() in {"1", "true", "yes", "是"}:
            ai_stats[name]["soft_veto"] += 1
        latency = _safe_float(row.get("latency_ms") or row.get("耗时"))
        if latency:
            ai_stats[name]["latencies"].append(latency)
    for stat in ai_stats.values():
        stat["avg_latency_ms"] = mean(stat.pop("latencies")) if stat.get("latencies") else 0.0
        stat["failure_rate"] = stat["failed"] / max(stat["total"], 1) * 100
    return {
        "source": source,
        "total": len(rows),
        "ai_stats": dict(ai_stats),
        "sample_warning": _sample_warning(len(rows), SAMPLE_LIMITS["external_ai"], "外部AI"),
        "upgrade_suggestion": "外部AI样本不足，暂不建议提高权重。" if len(rows) < SAMPLE_LIMITS["external_ai"] else "可继续观察是否进入咨询模式，暂不自动修改权重。",
        "data_quality": "good" if rows else "poor",
    }


def collect_strategy_metrics() -> dict[str, Any]:
    rows, source = _load_first_existing(
        [
            DATA_DIR / "strategy_candidates.json",
            DATA_DIR / "backtest_results.json",
            BASE_DIR / "strategy_candidates.json",
            BASE_DIR / "backtest_results.json",
        ]
    )
    grades = Counter(str(row.get("grade") or row.get("strategy_grade") or "暂无评级") for row in rows)
    overfit = Counter(str(row.get("overfit_risk") or "unknown") for row in rows)
    return {
        "source": source,
        "strategy_count": len(rows),
        "grade_summary": dict(grades),
        "overfit_summary": dict(overfit),
        "sample_warning": "策略样本不足，当前仅用于候选观察。" if len(rows) < 10 else "策略样本可用于基础筛选，但仍需模拟验证。",
        "data_quality": "good" if rows else "poor",
    }


def collect_risk_metrics() -> dict[str, Any]:
    rows, source = _load_first_existing(
        [
            DATA_DIR / "sim_risk_events.json",
            DATA_DIR / "runtime_safety_events.json",
            DATA_DIR / "live_audit_log.json",
            DATA_DIR / "live_audit_log.csv",
            BASE_DIR / "runtime_safety_events.json",
        ]
    )
    text_rows = [json.dumps(row, ensure_ascii=False).lower() for row in rows]
    return {
        "source": source,
        "risk_event_count": len(rows),
        "risk_veto_count": sum(1 for text in text_rows if "veto" in text or "否决" in text),
        "data_quality_block_count": sum(1 for text in text_rows if "data_quality" in text or "数据质量" in text),
        "api_error_block_count": sum(1 for text in text_rows if "api" in text and ("error" in text or "异常" in text)),
        "circuit_breaker_count": sum(1 for text in text_rows if "circuit" in text or "熔断" in text),
        "cooldown_count": sum(1 for text in text_rows if "cooldown" in text or "冷却" in text),
        "conclusion": "风控数据样本不足，建议维持保守设置。" if len(rows) < 20 else "风控已有可观察样本，建议继续保留硬否决权。",
        "data_quality": "good" if rows else "poor",
    }


def collect_server_metrics() -> dict[str, Any]:
    rows, source = _load_first_existing(
        [
            DATA_DIR / "runtime_log.json",
            DATA_DIR / "runtime_log.csv",
            DATA_DIR / "server_restart_log.json",
            BASE_DIR / "runtime_log.json",
        ]
    )
    error_rows = [row for row in rows if "error" in json.dumps(row, ensure_ascii=False).lower() or "异常" in json.dumps(row, ensure_ascii=False)]
    uptime_days = 0
    if rows:
        times = []
        for row in rows:
            raw = row.get("time") or row.get("timestamp")
            if not raw:
                continue
            try:
                times.append(datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None))
            except ValueError:
                continue
        if times:
            uptime_days = max((max(times) - min(times)).total_seconds() / 86400, 0)
    return {
        "source": source,
        "runtime_events": len(rows),
        "estimated_runtime_days": uptime_days,
        "error_count": len(error_rows),
        "restart_count": sum(1 for row in rows if "restart" in json.dumps(row, ensure_ascii=False).lower() or "重启" in json.dumps(row, ensure_ascii=False)),
        "stability": "服务器运行稳定" if rows and len(error_rows) == 0 else ("暂无服务器运行日志" if not rows else "存在异常，建议检查日志。"),
        "data_quality": "good" if rows else "poor",
    }


def collect_notification_metrics() -> dict[str, Any]:
    rows, source = _load_first_existing(
        [
            DATA_DIR / "notifications.json",
            DATA_DIR / "remote_action_audit_log.json",
            DATA_DIR / "remote_action_audit_log.csv",
            BASE_DIR / "notifications.json",
        ]
    )
    text_rows = [json.dumps(row, ensure_ascii=False) for row in rows]
    return {
        "source": source,
        "notification_count": len(rows),
        "unread_count": sum(1 for row in rows if not row.get("read") and row.get("read") is not None),
        "urgent_count": sum(1 for text in text_rows if "紧急" in text or "urgent" in text.lower()),
        "approval_notice_count": sum(1 for text in text_rows if "审批" in text),
        "risk_notice_count": sum(1 for text in text_rows if "风险" in text),
        "remote_action_count": sum(1 for text in text_rows if "remote" in text.lower() or "远程" in text),
        "login_failure_count": sum(1 for text in text_rows if "login" in text.lower() and ("fail" in text.lower() or "失败" in text)),
        "data_quality": "good" if rows else "poor",
    }


def check_dashboard_data_quality() -> dict[str, Any]:
    checks = []
    expected = [
        DATA_DIR / "sim_trade_history.json",
        DATA_DIR / "live_order_records.json",
        DATA_DIR / "approval_audit_log.json",
        DATA_DIR / "external_ai_audit_log.json",
        DATA_DIR / "notifications.json",
    ]
    for path in expected:
        checks.append(
            {
                "file": str(path.relative_to(BASE_DIR)),
                "exists": path.exists(),
                "size": path.stat().st_size if path.exists() else 0,
                "status": "存在" if path.exists() else "暂无数据",
            }
        )
    issues = _as_list(_read_json_file(QUALITY_LOG, default=[]))
    return {"checks": checks, "issues": issues[-50:], "issue_count": len(issues)}


def collect_all_metrics() -> dict[str, Any]:
    simulation = collect_simulation_metrics()
    live = collect_live_metrics()
    auto_live = collect_auto_live_metrics()
    approval = collect_approval_metrics()
    committee = collect_committee_metrics()
    external_ai = collect_external_ai_metrics()
    strategy = collect_strategy_metrics()
    risk = collect_risk_metrics()
    server = collect_server_metrics()
    notification = collect_notification_metrics()
    sample_ok = all(
        [
            simulation["trade_count"] >= SAMPLE_LIMITS["simulation"],
            live["trade_count"] >= SAMPLE_LIMITS["live"],
            approval["total"] >= SAMPLE_LIMITS["approval"],
        ]
    )
    recommendation = "继续观察，不建议扩大额度"
    if sample_ok and live.get("combined_pnl", 0) >= 0 and risk.get("circuit_breaker_count", 0) == 0:
        recommendation = "可继续小资金观察，暂不自动扩大额度"
    return {
        "generated_time": _now(),
        "overview": {
            "system_runtime_days": server.get("estimated_runtime_days", 0),
            "simulation_trades": simulation.get("trade_count", 0),
            "simulation_pnl": simulation.get("total_pnl", 0),
            "simulation_max_drawdown": simulation.get("max_drawdown", 0),
            "live_orders": live.get("trade_count", 0),
            "live_pnl": live.get("combined_pnl", 0),
            "auto_live_events": auto_live.get("event_count", 0),
            "approvals": approval.get("total", 0),
            "risk_blocks": risk.get("risk_veto_count", 0),
            "circuit_breakers": risk.get("circuit_breaker_count", 0) + auto_live.get("circuit_breaker_count", 0),
            "deepseek_gemini_samples": external_ai.get("total", 0),
            "sample_enough": sample_ok,
            "recommendation": recommendation,
        },
        "simulation": simulation,
        "live": live,
        "auto_live": auto_live,
        "approval": approval,
        "committee": committee,
        "external_ai": external_ai,
        "strategy": strategy,
        "risk": risk,
        "server": server,
        "notification": notification,
        "quality": check_dashboard_data_quality(),
    }


def _report_period_title(kind: str, date_value: str) -> str:
    if kind == "daily":
        return f"每日交易与系统运行报告 - {date_value}"
    if kind == "weekly":
        return f"每周 AI量化交易系统报告 - {date_value}"
    return f"每月系统经营分析报告 - {date_value}"


def _build_report(kind: str, date_value: str) -> dict[str, Any]:
    metrics = collect_all_metrics()
    overview = metrics["overview"]
    conclusions = [
        f"当前建议：{overview['recommendation']}。",
        "样本数量不足，当前结果仅供观察，不建议作为扩大实盘额度依据。" if not overview["sample_enough"] else "样本达到基础观察门槛，但仍需继续复盘。",
        "本报告只提供分析建议，不会自动修改交易配置或执行交易。",
    ]
    return {
        "title": _report_period_title(kind, date_value),
        "kind": kind,
        "period": date_value,
        "generated_time": _now(),
        "overview": overview,
        "sections": {
            "模拟交易": metrics["simulation"],
            "实盘交易": metrics["live"],
            "自动实盘": metrics["auto_live"],
            "审批流": metrics["approval"],
            "委员表现": metrics["committee"],
            "外部AI": metrics["external_ai"],
            "策略表现": metrics["strategy"],
            "风控表现": metrics["risk"],
            "服务器运行": metrics["server"],
            "通知与远程操作": metrics["notification"],
            "数据质量": metrics["quality"],
        },
        "conclusions": conclusions,
    }


def export_report_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report.get('title', '系统报告')}",
        "",
        f"生成时间：{report.get('generated_time', _now())}",
        "",
        "## 核心结论",
    ]
    for item in report.get("conclusions", []):
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 平台总览")
    for key, value in (report.get("overview") or {}).items():
        lines.append(f"- {key}: {value}")
    for section, data in (report.get("sections") or {}).items():
        lines.append("")
        lines.append(f"## {section}")
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                lines.append(f"- {key}: {value}")
        else:
            lines.append(str(data))
    return "\n".join(lines) + "\n"


def export_report_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def export_report_csv(report: dict[str, Any]) -> str:
    rows = [("section", "metric", "value")]
    rows.extend(("overview", key, value) for key, value in (report.get("overview") or {}).items())
    for section, data in (report.get("sections") or {}).items():
        if not isinstance(data, dict):
            rows.append((section, "value", data))
            continue
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            rows.append((section, key, value))
    output = []
    for row in rows:
        output.append(",".join('"' + str(item).replace('"', '""') + '"' for item in row))
    return "\n".join(output) + "\n"


def _save_report(report: dict[str, Any], folder: str, name: str) -> dict[str, str]:
    _ensure_dirs()
    target = REPORT_DIR / folder
    md_path = target / f"{name}.md"
    json_path = target / f"{name}.json"
    csv_path = REPORT_DIR / "exports" / f"{name}_metrics.csv"
    md_path.write_text(export_report_markdown(report), encoding="utf-8")
    json_path.write_text(export_report_json(report), encoding="utf-8")
    csv_path.write_text(export_report_csv(report), encoding="utf-8-sig")
    return {
        "markdown": str(md_path),
        "json": str(json_path),
        "csv": str(csv_path),
    }


def generate_daily_report(date: str | None = None) -> dict[str, Any]:
    date_value = date or datetime.now().strftime("%Y-%m-%d")
    report = _build_report("daily", date_value)
    report["files"] = _save_report(report, "daily", date_value)
    return report


def generate_weekly_report(start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    today = datetime.now().date()
    start = datetime.fromisoformat(start_date).date() if start_date else today - timedelta(days=today.weekday())
    end = datetime.fromisoformat(end_date).date() if end_date else start + timedelta(days=6)
    date_value = f"{start.isoformat()}_{end.isoformat()}"
    report = _build_report("weekly", date_value)
    report["files"] = _save_report(report, "weekly", date_value)
    return report


def generate_monthly_report(month: str | None = None) -> dict[str, Any]:
    date_value = month or datetime.now().strftime("%Y-%m")
    report = _build_report("monthly", date_value)
    report["files"] = _save_report(report, "monthly", date_value)
    return report


def load_recent_reports(limit: int = 20) -> list[dict[str, Any]]:
    _ensure_dirs()
    files = sorted(REPORT_DIR.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "name": path.name,
            "path": str(path),
            "updated_time": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "size": path.stat().st_size,
        }
        for path in files[:limit]
    ]

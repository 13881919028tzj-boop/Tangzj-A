"""Presentation helpers for simulation diagnostics and score feedback."""

from __future__ import annotations

from typing import Any


def build_sim_diagnostic_rows(diagnostics: list[dict[str, Any]], limit: int = 80) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in diagnostics[:limit]:
        details = item.get("details") or {}
        rows.append(
            {
                "时间": item.get("time"),
                "事件": item.get("event_type"),
                "状态": item.get("status"),
                "币种": item.get("symbol"),
                "原因": item.get("reason"),
                "模拟分": details.get("simulation_score"),
                "基础分": details.get("base_quality_score"),
                "流动性": details.get("liquidity_quality_score"),
                "组合": details.get("portfolio_fit_score"),
                "风险": details.get("risk_score"),
            }
        )
    return rows


def build_sim_score_feedback_rows(feedback: dict[str, Any]) -> list[dict[str, Any]]:
    rows = feedback.get("stats") or []
    return rows if isinstance(rows, list) else []

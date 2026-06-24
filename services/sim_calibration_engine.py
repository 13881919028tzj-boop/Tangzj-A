"""Calibration utilities for simulated trading scores.

The module turns simulated trade history into score buckets, signal-type
statistics and simple expected-value estimates. It deliberately works from the
plain JSON history rows so it can be reused by pages, runners and tests without
introducing database coupling.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


SCORE_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("<60", 0, 60),
    ("60-70", 60, 70),
    ("70-75", 70, 75),
    ("75-80", 75, 80),
    ("80-85", 80, 85),
    ("85+", 85, None),
)
MIN_EV_SAMPLE_SIZE = 5


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    return int(round(_to_float(value, default)))


def _snapshot(row: dict[str, Any]) -> dict[str, Any]:
    snapshot = row.get("committee_snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def _nested_bool(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    if isinstance(value, dict):
        return bool(value.get("confirming"))
    return bool(value)


def _normalize_direction(value: Any) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if lowered in {"long", "buy", "bullish"} or "多" in text:
        return "long"
    if lowered in {"short", "sell", "bearish"} or "空" in text:
        return "short"
    return lowered


def extract_calibration_tags(row: dict[str, Any]) -> dict[str, Any]:
    snapshot = _snapshot(row)
    kline = row.get("kline_signal") or snapshot.get("kline_signal") or {}
    whale = row.get("whale_signal") or snapshot.get("whale_signal") or {}
    orderbook = row.get("orderbook_signal") or snapshot.get("orderbook_signal") or {}
    return {
        "professional_trade_score": _to_int(row.get("professional_trade_score", snapshot.get("professional_trade_score", snapshot.get("trade_score"))), 0),
        "simulation_score": _to_int(row.get("simulation_score", snapshot.get("simulation_score")), 0),
        "entry_state": str(row.get("entry_state") or snapshot.get("entry_state") or "unknown"),
        "direction": _normalize_direction(row.get("direction") or snapshot.get("trade_direction") or snapshot.get("direction") or ""),
        "consensus_count": _to_int(row.get("consensus_count", row.get("consensus_support_count", snapshot.get("consensus_support_count"))), 0),
        "kline_confirming": _nested_bool(kline, "confirming"),
        "whale_confirming": _nested_bool(whale, "confirming"),
        "orderbook_confirming": _nested_bool(orderbook, "confirming"),
        "risk_score": _to_int(row.get("risk_score", row.get("committee_risk_score", snapshot.get("risk_score"))), 0),
        "market_regime": str(row.get("market_regime") or snapshot.get("market_regime") or "unknown"),
        "liquidity_quality_score": _to_int(row.get("liquidity_quality_score", snapshot.get("liquidity_quality_score")), 0),
        "base_quality_score": _to_int(row.get("base_quality_score", snapshot.get("base_quality_score")), 0),
    }


def score_bucket(score: int | float) -> str:
    score_value = _to_float(score, 0)
    for label, low, high in SCORE_BUCKETS:
        if score_value >= low and (high is None or score_value < high):
            return label
    return "<60"


def _pnl(row: dict[str, Any]) -> float:
    for key in ("pnl", "net_pnl", "net_pnl_usdt", "realized_pnl", "pnl_usdt"):
        if row.get(key) is not None:
            return _to_float(row.get(key), 0)
    return 0.0


def _empty_group(key: str) -> dict[str, Any]:
    return {"key": key, "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0, "ev": 0.0}


def _add(group: dict[str, Any], row: dict[str, Any]) -> None:
    pnl = _pnl(row)
    group["trades"] += 1
    group["total_pnl"] += pnl
    if pnl > 0:
        group["wins"] += 1
    elif pnl < 0:
        group["losses"] += 1


def _finish(group: dict[str, Any]) -> dict[str, Any]:
    trades = int(group.get("trades") or 0)
    if trades:
        group["win_rate"] = group["wins"] / trades
        group["avg_pnl"] = group["total_pnl"] / trades
        group["ev"] = group["avg_pnl"]
    group["total_pnl"] = round(float(group.get("total_pnl") or 0), 4)
    group["avg_pnl"] = round(float(group.get("avg_pnl") or 0), 4)
    group["ev"] = round(float(group.get("ev") or 0), 4)
    group["win_rate"] = round(float(group.get("win_rate") or 0), 4)
    return group


def build_calibration_report(history: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in history if isinstance(row, dict)]
    score_groups: dict[str, dict[str, Any]] = {label: _empty_group(label) for label, _, _ in SCORE_BUCKETS}
    simulation_groups: dict[str, dict[str, Any]] = {label: _empty_group(label) for label, _, _ in SCORE_BUCKETS}
    entry_groups: dict[str, dict[str, Any]] = {}
    confirmation_groups: dict[str, dict[str, Any]] = {}
    market_groups: dict[str, dict[str, Any]] = {}
    tagged_rows: list[dict[str, Any]] = []
    for row in rows:
        tags = extract_calibration_tags(row)
        tagged_rows.append({**row, "calibration_tags": tags})
        _add(score_groups[score_bucket(tags["professional_trade_score"])], row)
        _add(simulation_groups[score_bucket(tags["simulation_score"])], row)
        entry_key = tags["entry_state"] or "unknown"
        _add(entry_groups.setdefault(entry_key, _empty_group(entry_key)), row)
        combo_key = "K{}-W{}-O{}".format(int(tags["kline_confirming"]), int(tags["whale_confirming"]), int(tags["orderbook_confirming"]))
        _add(confirmation_groups.setdefault(combo_key, _empty_group(combo_key)), row)
        market_key = tags["market_regime"] or "unknown"
        _add(market_groups.setdefault(market_key, _empty_group(market_key)), row)
    return {
        "summary": _finish_group(rows),
        "professional_score_buckets": [_finish(score_groups[label]) for label, _, _ in SCORE_BUCKETS],
        "simulation_score_buckets": [_finish(simulation_groups[label]) for label, _, _ in SCORE_BUCKETS],
        "entry_state": sorted((_finish(group) for group in entry_groups.values()), key=lambda item: (-item["trades"], item["key"])),
        "confirmation_combo": sorted((_finish(group) for group in confirmation_groups.values()), key=lambda item: (-item["trades"], item["key"])),
        "market_regime": sorted((_finish(group) for group in market_groups.values()), key=lambda item: (-item["trades"], item["key"])),
        "tagged_sample_count": len(tagged_rows),
    }


def _finish_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    group = _empty_group("all")
    for row in rows:
        _add(group, row)
    group = _finish(group)
    group["data_quality"] = "good" if group["trades"] >= 50 else "partial" if group["trades"] >= 10 else "poor"
    return group


def evaluate_signal_ev(signal: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    tags = extract_calibration_tags(signal)
    entry_state = tags["entry_state"]
    direction = tags["direction"]
    primary_candidates: list[dict[str, Any]] = []
    fallback_candidates: list[dict[str, Any]] = []
    for row in history:
        row_tags = extract_calibration_tags(row)
        if row_tags["entry_state"] != entry_state:
            continue
        fallback_candidates.append(row)
        if row_tags["direction"] == direction:
            primary_candidates.append(row)
    candidates = primary_candidates if len(primary_candidates) >= MIN_EV_SAMPLE_SIZE else fallback_candidates
    sample = candidates[:50]
    group = _finish_group(sample)
    return {
        "allowed": group["trades"] < MIN_EV_SAMPLE_SIZE or group["ev"] > 0,
        "ev": group["ev"],
        "sample_size": group["trades"],
        "win_rate": group["win_rate"],
        "entry_state": entry_state,
        "direction": direction,
        "reason": "样本不足，暂不以EV硬拦截。" if group["trades"] < MIN_EV_SAMPLE_SIZE else f"同类历史EV={group['ev']:.4f}，样本={group['trades']}。",
    }

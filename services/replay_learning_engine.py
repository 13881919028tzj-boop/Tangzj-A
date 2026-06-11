"""模拟交易复盘学习中心。

基于本地模拟交易历史生成可解释复盘结果：
- 分析输赢原因
- 发现策略弱点
- 评估 AI交易委员会委员表现
- 为策略工厂提供优化依据
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from services.sim_trade_engine import load_sim_trade_history
from services.manual_position_override import load_manual_position_override_log


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _direction_text(direction: str) -> str:
    return "空单" if direction == "short" else "多单" if direction == "long" else "未知方向"


def _win_rate(rows: list[dict[str, Any]]) -> float:
    return len([row for row in rows if row.get("is_win")]) / len(rows) * 100 if rows else 0.0


def _avg(rows: list[float]) -> float:
    return sum(rows) / len(rows) if rows else 0.0


def _bucket_by_key(history: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        value = str(row.get(key) or "未知")
        buckets[value].append(row)
    return dict(buckets)


def _extract_member_votes(row: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = row.get("committee_snapshot") or row.get("committee") or {}
    if not isinstance(snapshot, dict):
        return []
    votes = snapshot.get("member_votes") or snapshot.get("members") or []
    return votes if isinstance(votes, list) else []


def _member_supported_trade(vote: dict[str, Any], trade_direction: str) -> bool:
    vote_text = str(vote.get("vote") or "")
    member_direction = str(vote.get("direction") or "")
    if trade_direction == "long":
        return "做多" in vote_text or member_direction == "long"
    if trade_direction == "short":
        return "做空" in vote_text or member_direction == "short"
    return False


def analyze_win_loss_reasons(history: list[dict[str, Any]]) -> dict[str, Any]:
    wins = [row for row in history if row.get("is_win")]
    losses = [row for row in history if not row.get("is_win")]
    close_reason_buckets = _bucket_by_key(history, "close_reason")
    losing_reasons = sorted(
        [
            {
                "reason": reason,
                "count": len(rows),
                "loss_count": len([row for row in rows if not row.get("is_win")]),
                "avg_pnl": _avg([_to_float(row.get("pnl")) for row in rows]),
            }
            for reason, rows in close_reason_buckets.items()
        ],
        key=lambda item: (item["loss_count"], -item["avg_pnl"]),
        reverse=True,
    )
    win_text = "盈利交易主要来自止盈或正向平仓。" if wins else "暂无盈利交易，暂时无法归纳盈利来源。"
    loss_text = "亏损交易主要需要关注止损、手动平仓和信号失效类原因。" if losses else "暂无亏损交易，当前没有明显亏损来源。"
    return {
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_summary": win_text,
        "loss_summary": loss_text,
        "close_reason_breakdown": losing_reasons,
        "main_loss_reason": losing_reasons[0]["reason"] if losing_reasons else "暂无",
    }


def analyze_strategy_weaknesses(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weaknesses: list[dict[str, Any]] = []
    for key, label in [("strategy_name", "策略类型"), ("committee_action", "委员会动作"), ("direction", "交易方向"), ("symbol", "交易对象")]:
        for value, rows in _bucket_by_key(history, key).items():
            if len(rows) < 2:
                continue
            win_rate = _win_rate(rows)
            avg_pnl = _avg([_to_float(row.get("pnl")) for row in rows])
            avg_r = _avg([_to_float(row.get("r_multiple")) for row in rows if row.get("r_multiple") not in {"", None}])
            if win_rate < 45 or avg_pnl < 0 or avg_r < -0.2:
                weaknesses.append(
                    {
                        "dimension": label,
                        "name": value,
                        "trade_count": len(rows),
                        "win_rate": win_rate,
                        "avg_pnl": avg_pnl,
                        "avg_r": avg_r,
                        "explanation": f"{label}「{value}」表现偏弱，胜率 {win_rate:.1f}%，平均盈亏 {avg_pnl:.2f} USDT，建议降低权重或增加过滤条件。",
                    }
                )
    weaknesses.sort(key=lambda item: (item["avg_pnl"], item["win_rate"]))
    return weaknesses[:12]


def evaluate_committee_members(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    member_rows: dict[str, dict[str, Any]] = {}
    for trade in history:
        votes = _extract_member_votes(trade)
        trade_direction = str(trade.get("direction") or "")
        is_win = bool(trade.get("is_win"))
        pnl = _to_float(trade.get("pnl"))
        for vote in votes:
            name = str(vote.get("member_name") or "未知委员")
            row = member_rows.setdefault(name, {"member_name": name, "support_count": 0, "correct_count": 0, "wrong_count": 0, "pnl_when_supported": 0.0, "veto_count": 0, "reasons": []})
            supported = _member_supported_trade(vote, trade_direction)
            if supported:
                row["support_count"] += 1
                row["pnl_when_supported"] += pnl
                if is_win:
                    row["correct_count"] += 1
                else:
                    row["wrong_count"] += 1
            if vote.get("veto"):
                row["veto_count"] += 1
            summary = str(vote.get("summary") or "")
            if summary and len(row["reasons"]) < 3:
                row["reasons"].append(summary)
    results: list[dict[str, Any]] = []
    for row in member_rows.values():
        support_count = int(row["support_count"])
        accuracy = row["correct_count"] / support_count * 100 if support_count else 0
        results.append(
            {
                **row,
                "accuracy": accuracy,
                "avg_pnl_when_supported": row["pnl_when_supported"] / support_count if support_count else 0,
                "explanation": "该委员暂时缺少足够投票样本。" if support_count == 0 else f"该委员支持的交易胜率 {accuracy:.1f}%，支持后平均盈亏 {row['pnl_when_supported'] / support_count:.2f} USDT。",
            }
        )
    results.sort(key=lambda item: (item["accuracy"], item["avg_pnl_when_supported"]), reverse=True)
    return results


def generate_strategy_factory_suggestions(history: list[dict[str, Any]], weaknesses: list[dict[str, Any]], member_performance: list[dict[str, Any]]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    if not history:
        return [
            {
                "title": "先积累模拟交易样本",
                "priority": "高",
                "suggestion": "当前没有足够历史交易，策略工厂暂不应调整权重。建议先运行模拟交易，至少积累 20 笔以上样本。",
            }
        ]
    if weaknesses:
        first = weaknesses[0]
        suggestions.append(
            {
                "title": f"降低弱项权重：{first['dimension']} {first['name']}",
                "priority": "高",
                "suggestion": f"{first['explanation']} 策略工厂可先将该条件改为观察信号，避免直接触发模拟开仓。",
            }
        )
    weak_members = [m for m in member_performance if m.get("support_count", 0) >= 2 and m.get("accuracy", 0) < 45]
    if weak_members:
        member = weak_members[0]
        suggestions.append(
            {
                "title": f"复核委员权重：{member['member_name']}",
                "priority": "中",
                "suggestion": f"{member['member_name']} 支持交易后的胜率偏低，建议策略工厂降低其通过权重，或要求风险委员/本地策略二次确认。",
            }
        )
    loss_rows = [row for row in history if not row.get("is_win")]
    stop_loss_rows = [row for row in loss_rows if "止损" in str(row.get("close_reason") or "")]
    if len(stop_loss_rows) >= max(2, len(history) * 0.25):
        suggestions.append(
            {
                "title": "优化止损与入场区",
                "priority": "高",
                "suggestion": "止损类平仓占比较高，建议策略工厂检查入场区是否过宽、止损是否离结构位过近，并加入盘口/大单确认过滤。",
            }
        )
    if not suggestions:
        suggestions.append(
            {
                "title": "保持当前规则，继续积累样本",
                "priority": "中",
                "suggestion": "当前没有明显单一弱点。建议继续积累更多模拟交易，优先观察不同策略类型和不同委员组合的稳定性。",
            }
        )
    return suggestions[:8]


def analyze_replay_learning() -> dict[str, Any]:
    history = load_sim_trade_history()
    manual_overrides = load_manual_position_override_log(300)
    pnl_values = [_to_float(row.get("pnl")) for row in history]
    wins = [row for row in history if row.get("is_win")]
    losses = [row for row in history if not row.get("is_win")]
    win_loss = analyze_win_loss_reasons(history)
    weaknesses = analyze_strategy_weaknesses(history)
    members = evaluate_committee_members(history)
    suggestions = generate_strategy_factory_suggestions(history, weaknesses, members)
    return {
        "history": history,
        "summary": {
            "total_trades": len(history),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": _win_rate(history),
            "total_pnl": sum(pnl_values),
            "avg_pnl": _avg(pnl_values),
            "best_trade": max(pnl_values) if pnl_values else 0,
            "worst_trade": min(pnl_values) if pnl_values else 0,
            "data_quality": "good" if len(history) >= 20 else "partial" if history else "poor",
            "sample_warning": "样本较少，复盘结论仅作保守参考。" if len(history) < 20 else "样本数量较充足，可用于初步评估策略表现。",
            "manual_override_count": len(manual_overrides),
        },
        "win_loss": win_loss,
        "weaknesses": weaknesses,
        "member_performance": members,
        "strategy_factory_suggestions": suggestions,
        "manual_overrides": manual_overrides,
        "capabilities": ["能复盘", "能分析输赢原因", "能发现策略弱点", "能评估委员会委员表现", "能为策略工厂提供优化依据"],
    }

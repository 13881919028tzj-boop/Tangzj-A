"""观察池与重点币种跟踪系统。

本模块不生成交易信号，只读取本地策略结果并跟踪变化。
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
WATCHLIST_FILE = DATA_DIR / "watchlist.json"
ALERTS_FILE = DATA_DIR / "watchlist_alerts.json"
HISTORY_FILE = DATA_DIR / "watchlist_history.csv"
MAX_HISTORY_PER_SYMBOL = 50
VALID_USDT_SYMBOL = re.compile(r"^[A-Z0-9]+USDT$")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _ensure_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not WATCHLIST_FILE.exists():
        WATCHLIST_FILE.write_text(json.dumps({"items": {}}, ensure_ascii=False, indent=2), encoding="utf-8")
    if not ALERTS_FILE.exists():
        ALERTS_FILE.write_text(json.dumps([], ensure_ascii=False, indent=2), encoding="utf-8")
    if not HISTORY_FILE.exists():
        with HISTORY_FILE.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["时间", "交易对象", "价格", "策略方向", "操作建议", "策略类型", "置信度", "风险评分", "机会评分", "观察评分", "观察状态", "数据质量"])


def load_watchlist() -> dict[str, Any]:
    """读取观察池，文件损坏时重建为空池。"""
    try:
        _ensure_files()
        data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
            raise ValueError("watchlist schema invalid")
        items = data.get("items", {})
        invalid_symbols = [symbol for symbol in items if not VALID_USDT_SYMBOL.match(str(symbol or "").upper())]
        if invalid_symbols:
            for symbol in invalid_symbols:
                items.pop(symbol, None)
            save_watchlist(data)
        return data
    except Exception as exc:
        print(f"[观察池] 读取失败，已重建空观察池 error={repr(exc)}")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        data = {"items": {}}
        WATCHLIST_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data


def save_watchlist(data: dict[str, Any]) -> None:
    """保存观察池。"""
    _ensure_files()
    WATCHLIST_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_alerts() -> list[dict[str, Any]]:
    try:
        _ensure_files()
        alerts = json.loads(ALERTS_FILE.read_text(encoding="utf-8") or "[]")
        return alerts if isinstance(alerts, list) else []
    except Exception as exc:
        print(f"[观察池] 提醒读取失败 error={repr(exc)}")
        return []


def save_alerts(alerts: list[dict[str, Any]]) -> None:
    _ensure_files()
    ALERTS_FILE.write_text(json.dumps(alerts[-300:], ensure_ascii=False, indent=2), encoding="utf-8")


def _append_history(symbol: str, item: dict[str, Any]) -> None:
    strategy = item.get("local_strategy") or {}
    tracking = item.get("tracking") or {}
    data_quality = item.get("data_quality") or {}
    _ensure_files()
    with HISTORY_FILE.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                item.get("last_update_time"),
                symbol,
                item.get("current_price"),
                strategy.get("direction_text"),
                strategy.get("action"),
                strategy.get("strategy_name"),
                strategy.get("confidence"),
                strategy.get("risk_score"),
                strategy.get("opportunity_score"),
                item.get("watch_score"),
                tracking.get("status"),
                data_quality.get("level"),
            ]
        )


def add_to_watchlist(symbol: str, source: str = "manual", category: str | None = None) -> dict[str, Any]:
    """加入观察池。"""
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return load_watchlist()
    data = load_watchlist()
    items = data.setdefault("items", {})
    if normalized not in items:
        now = _now()
        items[normalized] = {
            "symbol": normalized,
            "source": source,
            "category": category or ("manual" if source == "manual" else "ai"),
            "added_time": now,
            "last_update_time": now,
            "current_price": 0,
            "price_change_24h": 0,
            "local_strategy": {},
            "tracking": {
                "status": "新机会",
                "status_explanation": "刚加入观察池，等待下一轮策略跟踪。",
                "score_change": 0,
                "risk_change": 0,
                "confidence_change": 0,
                "last_action": "",
                "current_action": "",
            },
            "watch_score": 0,
            "watch_level": "普通观察",
            "watch_explanation": "等待本地策略数据同步。",
            "alerts": [],
            "history": [],
            "data_quality": {"level": "poor", "missing_fields": ["等待策略数据"]},
        }
    else:
        items[normalized]["source"] = items[normalized].get("source") or source
    save_watchlist(data)
    return data


def remove_from_watchlist(symbol: str) -> dict[str, Any]:
    data = load_watchlist()
    data.setdefault("items", {}).pop(str(symbol or "").upper().strip(), None)
    save_watchlist(data)
    return data


def set_watchlist_category(symbol: str, category: str) -> dict[str, Any]:
    """手动设置观察分类。"""
    normalized = str(symbol or "").upper().strip()
    data = load_watchlist()
    item = data.setdefault("items", {}).get(normalized)
    if item:
        item["category"] = category
        item["last_update_time"] = _now()
        save_watchlist(data)
    return data


def clear_expired_watchlist() -> dict[str, Any]:
    """清除非手动来源的失效观察对象。"""
    data = load_watchlist()
    items = data.setdefault("items", {})
    for symbol in list(items):
        item = items[symbol]
        if item.get("category") == "expired" and item.get("source") != "manual":
            items.pop(symbol, None)
    save_watchlist(data)
    return data


def get_watchlist() -> list[dict[str, Any]]:
    data = load_watchlist()
    return list(data.get("items", {}).values())


def is_watched(symbol: str) -> bool:
    return str(symbol or "").upper().strip() in load_watchlist().get("items", {})


def _direction_text(direction: str) -> str:
    if direction == "long":
        return "偏多"
    if direction == "short":
        return "偏空"
    return "中性"


def _watch_score(strategy: dict[str, Any], previous: dict[str, Any] | None) -> tuple[int, str, str]:
    opportunity = _to_float(strategy.get("opportunity_score"))
    confidence = _to_float(strategy.get("confidence"))
    risk = _to_float(strategy.get("risk_score"), 70)
    data_quality = (strategy.get("data_quality") or {}).get("level", "poor")
    permission = str(strategy.get("trade_permission", "blocked"))
    score = opportunity * 0.38 + confidence * 0.27 + max(0, 100 - risk) * 0.25
    if previous:
        old_strategy = previous.get("local_strategy") or {}
        score += max(-8, min(8, opportunity - _to_float(old_strategy.get("opportunity_score")))) * 0.6
        score += max(-6, min(6, confidence - _to_float(old_strategy.get("confidence")))) * 0.4
    if data_quality == "partial":
        score -= 8
    if data_quality == "poor":
        score = min(score, 35)
    if permission == "blocked":
        score = min(score, 45)
    score_int = max(0, min(100, int(round(score))))
    if score_int >= 81:
        level = "强重点观察"
    elif score_int >= 61:
        level = "重点观察"
    elif score_int >= 41:
        level = "普通观察"
    elif score_int >= 21:
        level = "弱观察"
    else:
        level = "不值得观察"
    explanation = f"观察评分综合本地机会评分、置信度、风险反向分和数据质量；当前为{level}。"
    return score_int, level, explanation


def _tracking_status(strategy: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    current_action = str(strategy.get("action", "观望"))
    opportunity = int(_to_float(strategy.get("opportunity_score")))
    risk = int(_to_float(strategy.get("risk_score")))
    confidence = int(_to_float(strategy.get("confidence")))
    strategy_name = str(strategy.get("strategy_name", "无有效策略"))
    data_quality = (strategy.get("data_quality") or {}).get("level", "poor")
    permission = str(strategy.get("trade_permission", "blocked"))

    if not previous:
        return {
            "status": "新机会",
            "status_explanation": "刚加入观察池，尚未形成足够跟踪历史。",
            "score_change": 0,
            "risk_change": 0,
            "confidence_change": 0,
            "last_action": "",
            "current_action": current_action,
        }

    old_strategy = previous.get("local_strategy") or {}
    last_action = str(old_strategy.get("action", ""))
    score_change = opportunity - int(_to_float(old_strategy.get("opportunity_score")))
    risk_change = risk - int(_to_float(old_strategy.get("risk_score")))
    confidence_change = confidence - int(_to_float(old_strategy.get("confidence")))

    if data_quality == "poor" or permission == "blocked" or strategy_name == "无有效策略":
        status = "信号失效"
        explanation = "本地策略已转为禁止开仓、无有效策略或数据质量不足，原观察信号需要重新确认。"
    elif risk_change >= 15 or risk >= 75:
        status = "风险升高"
        explanation = "风险评分明显上升，建议降低预期，只保留观察。"
    elif score_change >= 8 or confidence_change >= 10 or (last_action == "观望" and current_action in {"轻仓试多", "顺势做多", "轻仓试空", "顺势做空"}):
        status = "机会增强"
        explanation = "机会评分或置信度上升，本地策略信号正在转强。"
    elif score_change <= -8 or confidence_change <= -10:
        status = "机会减弱"
        explanation = "机会评分或置信度下降，当前信号不如前一轮清晰。"
    elif current_action == "观望":
        status = "等待确认"
        explanation = "机会尚未失效，但入场结构还不清晰，需要继续确认。"
    else:
        status = "持续观察"
        explanation = "策略状态较稳定，继续跟踪机会和风险变化。"

    return {
        "status": status,
        "status_explanation": explanation,
        "score_change": score_change,
        "risk_change": risk_change,
        "confidence_change": confidence_change,
        "last_action": last_action,
        "current_action": current_action,
    }


def _alerts(symbol: str, item: dict[str, Any], previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    strategy = item.get("local_strategy") or {}
    tracking = item.get("tracking") or {}
    alerts: list[dict[str, Any]] = []
    now = item.get("last_update_time") or _now()
    status = str(tracking.get("status", "持续观察"))

    def add(level: str, content: str, reason: str) -> None:
        alerts.append({"time": now, "symbol": symbol, "level": level, "content": content, "reason": reason})

    if previous:
        old_strategy = previous.get("local_strategy") or {}
        old_action = str(old_strategy.get("action", ""))
        new_action = str(strategy.get("action", ""))
        if old_action and old_action != new_action:
            level = "高级提醒" if new_action == "禁止开仓" else "中级提醒"
            add(level, f"{symbol} 本地策略由“{old_action}”变为“{new_action}”。", "本地策略建议发生变化，需要重新确认交易条件。")
        if int(_to_float(strategy.get("risk_score"))) - int(_to_float(old_strategy.get("risk_score"))) >= 15:
            add("中级提醒", f"{symbol} 风险评分明显升高。", "风险变化较快，建议降低仓位或继续观察。")
        if int(_to_float(strategy.get("confidence"))) - int(_to_float(old_strategy.get("confidence"))) >= 10:
            add("低级提醒", f"{symbol} 置信度明显上升，机会正在增强。", "本地策略置信度改善，但仍需结合风险评分。")

    if status == "信号失效":
        add("高级提醒", f"{symbol} 观察信号失效。", tracking.get("status_explanation", "本地策略已转弱。"))
    if int(_to_float(strategy.get("opportunity_score"))) >= 82:
        add("低级提醒", f"{symbol} 机会评分进入强机会区。", "机会评分较高，可作为后续委员会候选。")
    if (strategy.get("data_quality") or {}).get("level") == "poor":
        add("中级提醒", f"{symbol} 数据质量不足。", "关键数据缺失，观察结论已降级为保守分析。")
    return alerts[-6:]


def _category(strategy: dict[str, Any], tracking: dict[str, Any], source: str, watch_score: int) -> str:
    if tracking.get("status") == "信号失效":
        return "expired"
    if int(_to_float(strategy.get("risk_score"))) >= 75:
        return "high_risk"
    if watch_score >= 61 and strategy.get("trade_permission") != "blocked" and (strategy.get("data_quality") or {}).get("level") != "poor":
        return "key_tracking"
    if source == "manual":
        return "manual"
    return "ai"


def update_watchlist_item(symbol: str, strategy: dict[str, Any], ticker: dict[str, Any] | None, source: str | None = None) -> dict[str, Any]:
    """更新单个观察对象，写入策略、状态、提醒和历史。"""
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return {}
    data = add_to_watchlist(normalized, source or "manual")
    items = data.setdefault("items", {})
    previous = json.loads(json.dumps(items.get(normalized, {}), ensure_ascii=False))
    item = items[normalized]
    now = _now()
    local_strategy = {
        "direction": strategy.get("direction"),
        "direction_text": _direction_text(str(strategy.get("direction", "neutral"))),
        "action": strategy.get("action"),
        "strategy_name": strategy.get("strategy_name"),
        "confidence": strategy.get("confidence"),
        "risk_score": strategy.get("risk_score"),
        "opportunity_score": strategy.get("opportunity_score"),
        "trade_permission": strategy.get("trade_permission"),
        "position_suggestion": strategy.get("position_suggestion"),
        "invalid_condition": strategy.get("invalid_condition"),
        "local_vote_score": strategy.get("local_vote_score"),
        "local_vote_grade": strategy.get("local_vote_grade"),
        "local_vote_decision": strategy.get("local_vote_decision"),
    }
    temp_item = {**item, "local_strategy": local_strategy, "data_quality": strategy.get("data_quality") or {}}
    tracking = _tracking_status(strategy, previous if previous.get("local_strategy") else None)
    watch_score, watch_level, watch_explanation = _watch_score(strategy, previous if previous.get("local_strategy") else None)
    item.update(
        {
            "symbol": normalized,
            "source": item.get("source") or source or "manual",
            "last_update_time": now,
            "current_price": (ticker or {}).get("last_price", item.get("current_price", 0)),
            "price_change_24h": (ticker or {}).get("price_change_percent", item.get("price_change_24h", 0)),
            "local_strategy": local_strategy,
            "tracking": tracking,
            "watch_score": watch_score,
            "watch_level": watch_level,
            "watch_explanation": watch_explanation,
            "data_quality": strategy.get("data_quality") or {},
        }
    )
    item["category"] = _category(strategy, tracking, str(item.get("source", source or "manual")), watch_score)
    new_alerts = _alerts(normalized, item, previous if previous.get("local_strategy") else None)
    item["alerts"] = new_alerts + list(item.get("alerts") or [])
    history = list(item.get("history") or [])
    history.append(
        {
            "time": now,
            "price": item.get("current_price"),
            "action": local_strategy.get("action"),
            "confidence": local_strategy.get("confidence"),
            "risk_score": local_strategy.get("risk_score"),
            "opportunity_score": local_strategy.get("opportunity_score"),
            "strategy_name": local_strategy.get("strategy_name"),
            "watch_score": watch_score,
            "status": tracking.get("status"),
            "data_quality": (strategy.get("data_quality") or {}).get("level"),
        }
    )
    item["history"] = history[-MAX_HISTORY_PER_SYMBOL:]
    save_watchlist(data)
    _append_history(normalized, item)
    if new_alerts:
        alerts = load_alerts()
        alerts.extend(new_alerts)
        save_alerts(alerts)
    return item


def get_watchlist_summary() -> dict[str, int]:
    items = get_watchlist()
    return {
        "total": len(items),
        "manual": sum(1 for item in items if item.get("category") == "manual"),
        "ai": sum(1 for item in items if item.get("category") == "ai"),
        "key_tracking": sum(1 for item in items if item.get("category") == "key_tracking"),
        "high_risk": sum(1 for item in items if item.get("category") == "high_risk"),
        "expired": sum(1 for item in items if item.get("category") == "expired"),
    }


def get_watchlist_alerts(limit: int = 20) -> list[dict[str, Any]]:
    return list(reversed(load_alerts()))[:limit]


def get_watchlist_candidates_for_committee() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in get_watchlist():
        strategy = item.get("local_strategy") or {}
        data_quality = item.get("data_quality") or {}
        if item.get("category") != "key_tracking":
            continue
        if strategy.get("trade_permission") == "blocked" or data_quality.get("level") == "poor":
            continue
        candidates.append(
            {
                "symbol": item.get("symbol"),
                "watch_score": item.get("watch_score", 0),
                "local_strategy_action": strategy.get("action"),
                "confidence": strategy.get("confidence"),
                "risk_score": strategy.get("risk_score"),
                "strategy_name": strategy.get("strategy_name"),
                "status": (item.get("tracking") or {}).get("status"),
                "main_reason": item.get("watch_explanation"),
                "main_risk": (strategy.get("invalid_condition") or "等待更多风险确认。"),
            }
        )
    return sorted(candidates, key=lambda row: row.get("watch_score", 0), reverse=True)


def auto_add_from_rankings(rankings: dict[str, list[dict[str, Any]]] | None, max_items: int = 30) -> None:
    """从机会榜自动加入少量候选，不做重计算。"""
    if not rankings:
        return
    current_count = len(get_watchlist())
    if current_count >= max_items:
        return
    source_map = {
        "long_opportunities": "多头机会榜",
        "short_opportunities": "空头机会榜",
        "abnormal": "异动币榜",
        "strong": "强势币榜",
        "weak": "弱势币榜",
    }
    for key, source in source_map.items():
        for row in (rankings.get(key) or [])[:5]:
            if len(get_watchlist()) >= max_items:
                return
            symbol = row.get("symbol")
            symbol = str(symbol or "").upper().strip()
            if not symbol or not VALID_USDT_SYMBOL.match(symbol):
                continue
            if is_watched(symbol):
                data = load_watchlist()
                item = data.setdefault("items", {}).get(symbol)
                if item and not item.get("local_strategy"):
                    item["current_price"] = row.get("last_price", item.get("current_price", 0))
                    item["price_change_24h"] = row.get("price_change_percent", item.get("price_change_24h", 0))
                    item["last_update_time"] = _now()
                    save_watchlist(data)
                continue
            if int(_to_float(row.get("opportunity_score"))) >= 75 or key in {"abnormal"}:
                data = add_to_watchlist(symbol, source=f"AI机会筛选：{source}", category="ai")
                item = data.setdefault("items", {}).get(symbol)
                if item:
                    item["current_price"] = row.get("last_price", item.get("current_price", 0))
                    item["price_change_24h"] = row.get("price_change_percent", item.get("price_change_24h", 0))
                    item["last_update_time"] = _now()
                    save_watchlist(data)


def _ranked_watchlist_candidates(rankings: dict[str, list[dict[str, Any]]] | None) -> dict[str, dict[str, Any]]:
    """从当前榜单提取仍具备观察池资格的对象。"""
    if not rankings:
        return {}
    source_map = {
        "long_opportunities": "多头机会榜",
        "short_opportunities": "空头机会榜",
        "abnormal": "异动币榜",
        "strong": "强势币榜",
        "weak": "弱势币榜",
    }
    candidates: dict[str, dict[str, Any]] = {}
    for key, source in source_map.items():
        for rank, row in enumerate(rankings.get(key) or [], start=1):
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol or not VALID_USDT_SYMBOL.match(symbol):
                continue
            opportunity = _to_float(row.get("final_opportunity_score", row.get("opportunity_score")))
            risk = _to_float(row.get("risk_score"), 100)
            data_quality = str(row.get("data_quality") or row.get("data_quality_level") or "good")
            eligible = key == "abnormal" or (opportunity >= 75 and risk < 80 and data_quality != "poor")
            if not eligible:
                continue
            old = candidates.get(symbol)
            if old and _to_float(old.get("final_opportunity_score", old.get("opportunity_score"))) >= opportunity:
                continue
            enriched = dict(row)
            enriched["watch_source"] = source
            enriched["watch_rank"] = rank
            candidates[symbol] = enriched
    return candidates


def _update_item_from_board_row(item: dict[str, Any], row: dict[str, Any]) -> None:
    """用榜单实时数据轻量更新观察对象，不替代本地策略深度分析。"""
    now = _now()
    previous_strategy = item.get("local_strategy") or {}
    opportunity = int(_to_float(row.get("final_opportunity_score", row.get("opportunity_score"))))
    risk = int(_to_float(row.get("risk_score"), _to_float(previous_strategy.get("risk_score"), 50)))
    confidence = max(int(_to_float(previous_strategy.get("confidence"), 0)), min(90, opportunity))
    action = row.get("advice") or previous_strategy.get("action") or "观察"
    direction = row.get("direction") or previous_strategy.get("direction") or "neutral"
    strategy = {
        **previous_strategy,
        "direction": direction,
        "direction_text": _direction_text(str(direction)),
        "action": action,
        "strategy_name": previous_strategy.get("strategy_name") or "榜单实时跟踪",
        "confidence": confidence,
        "risk_score": risk,
        "opportunity_score": opportunity,
        "trade_permission": "blocked" if risk >= 80 else "candidate" if opportunity >= 75 else "observe",
        "invalid_condition": row.get("opportunity_status") or "等待更多确认。",
    }
    previous = json.loads(json.dumps(item, ensure_ascii=False))
    item["local_strategy"] = strategy
    item["current_price"] = row.get("last_price", row.get("current_price", item.get("current_price", 0)))
    item["price_change_24h"] = row.get("price_change_percent", item.get("price_change_24h", 0))
    item["last_update_time"] = now
    item["data_quality"] = {"level": "good", "missing_fields": []}
    tracking = _tracking_status(strategy, previous if previous.get("local_strategy") else None)
    watch_score, watch_level, watch_explanation = _watch_score(strategy, previous if previous.get("local_strategy") else None)
    item["tracking"] = tracking
    item["watch_score"] = watch_score
    item["watch_level"] = watch_level
    item["watch_explanation"] = watch_explanation
    item["category"] = _category(strategy, tracking, str(item.get("source", "ai")), watch_score)
    item["missed_eligibility_count"] = 0
    history = list(item.get("history") or [])
    history.append(
        {
            "time": now,
            "price": item.get("current_price"),
            "action": strategy.get("action"),
            "confidence": strategy.get("confidence"),
            "risk_score": strategy.get("risk_score"),
            "opportunity_score": strategy.get("opportunity_score"),
            "strategy_name": strategy.get("strategy_name"),
            "watch_score": watch_score,
            "status": tracking.get("status"),
            "data_quality": "good",
            "source": "board_sync",
        }
    )
    item["history"] = history[-MAX_HISTORY_PER_SYMBOL:]


def sync_watchlist_from_rankings(rankings: dict[str, list[dict[str, Any]]] | None, max_items: int = 30, max_missed: int = 2) -> dict[str, Any]:
    """后台同步观察池：补充、更新、升级候选，并踢出失去资格的对象。"""
    eligible = _ranked_watchlist_candidates(rankings)
    data = load_watchlist()
    items = data.setdefault("items", {})
    removed: list[str] = []
    updated: list[str] = []
    added: list[str] = []

    for symbol, row in eligible.items():
        if len(items) >= max_items and symbol not in items:
            continue
        if symbol not in items:
            now = _now()
            items[symbol] = {
                "symbol": symbol,
                "source": f"AI机会筛选：{row.get('watch_source', '机会榜')}",
                "category": "ai",
                "added_time": now,
                "last_update_time": now,
                "current_price": 0,
                "price_change_24h": 0,
                "local_strategy": {},
                "tracking": {"status": "新机会", "status_explanation": "由榜单实时同步加入观察池。"},
                "watch_score": 0,
                "watch_level": "普通观察",
                "watch_explanation": "等待榜单和策略数据同步。",
                "alerts": [],
                "history": [],
                "data_quality": {"level": "partial", "missing_fields": ["本地策略深度复核"]},
                "missed_eligibility_count": 0,
            }
            added.append(symbol)
        _update_item_from_board_row(items[symbol], row)
        updated.append(symbol)

    for symbol in list(items):
        if symbol in eligible:
            continue
        item = items[symbol]
        missed = int(item.get("missed_eligibility_count", 0) or 0) + 1
        item["missed_eligibility_count"] = missed
        item["last_update_time"] = _now()
        if missed >= max_missed:
            removed.append(symbol)
            items.pop(symbol, None)
        else:
            item["category"] = "expired"
            item["tracking"] = {
                "status": "信号失效",
                "status_explanation": f"当前已不满足观察池入池条件，连续 {missed}/{max_missed} 次未命中，达到阈值后自动移出。",
            }

    save_watchlist(data)
    if removed:
        alerts = load_alerts()
        now = _now()
        for symbol in removed:
            alerts.append({"time": now, "symbol": symbol, "level": "中级提醒", "content": f"{symbol} 已失去观察池资格并自动移出。", "reason": "连续未满足机会榜观察条件。"})
        save_alerts(alerts)
    return {"added": added, "updated": updated, "removed": removed, "eligible_count": len(eligible)}

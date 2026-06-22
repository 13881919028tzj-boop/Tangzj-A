"""机会榜 TOP1 三秒快速捕捉与委员会快速预判。

本模块只做轻量机会捕捉、候选生成和日志记录：
- 不调用 DeepSeek/Gemini。
- 不执行真实下单。
- 不绕过完整委员会、风险委员、实盘安全委员和执行前检查。
"""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from services import market_cache
from services.orderbook_analyzer import analyze_orderbook

try:
    from services.watchlist_manager import get_watchlist_candidates_for_committee
    from services.watchlist_manager import remove_from_watchlist
except Exception:
    get_watchlist_candidates_for_committee = None
    remove_from_watchlist = None


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
SETTINGS_PATH = DATA_DIR / "fast_opportunity_settings.json"
SIM_POSITIONS_PATH = DATA_DIR / "sim_positions.json"
SIM_HISTORY_PATH = DATA_DIR / "sim_trade_history.json"

DEFAULT_SETTINGS = {
    "TOP10_OPPORTUNITY_REFRESH_SECONDS": 3,
    "TOP1_FAST_CAPTURE_SECONDS": 3,
    "COMMITTEE_FAST_PRECHECK_SECONDS": 3,
    "COMMITTEE_FULL_REVIEW_SECONDS": 15,
    "EXTERNAL_AI_REFRESH_SECONDS": 90,
    "LIGHT_MARKET_SCAN_SECONDS": 30,
    "DEEP_MARKET_SCAN_SECONDS": 120,
    "OPPORTUNITY_TRIGGER_SCORE": 80,
    "COMMITTEE_TARGET_MIN_STABLE_CYCLES": 2,
    "COMMITTEE_TARGET_SWITCH_SCORE_GAP": 5,
    "COMMITTEE_TARGET_COOLDOWN_SECONDS": 30,
    "TOP1_STRONG_SCORE": 90,
    "OPPORTUNITY_DUPLICATE_COOLDOWN_SECONDS": 120,
    "ENABLE_FAST_OPPORTUNITY_CAPTURE": True,
    "ENABLE_FAST_COMMITTEE_PRECHECK": True,
    "ENABLE_TOP10_COMMITTEE_PRECHECK": True,
    "ENABLE_COMMITTEE_ANCHOR_TOP1": True,
    "COMMITTEE_REVIEW_TOP_N": 10,
    "COMMITTEE_LIGHT_TRACK_TOP_N": 10,
    "TOP2_TO_TOP5_PRECHECK_SECONDS": 5,
    "FULL_REVIEW_TOP_N": 10,
    "FULL_REVIEW_INTERVAL_SECONDS": 15,
    "FULL_REVIEW_BATCH_SIZE": 2,
    "FULL_REVIEW_CYCLE_SECONDS": 5,
    "EXTERNAL_AI_BATCH_SIZE": 1,
    "MAX_FULL_REVIEW_AGE_SECONDS": 300,
    "REVIEW_RESULT_CACHE_SECONDS": 60,
    "OPPORTUNITY_REJECT_COOLDOWN_SECONDS": 120,
    "FULL_MARKET_RERANK_SECONDS": 1800,
    "MAX_REJECT_BEFORE_REMOVE": 2,
    "MAX_REVIEW_BEFORE_REMOVE": 3,
    "MAX_BLOCK_BEFORE_REMOVE": 2,
}

TOP10_OPPORTUNITY_REFRESH_SECONDS = DEFAULT_SETTINGS["TOP10_OPPORTUNITY_REFRESH_SECONDS"]
TOP1_FAST_CAPTURE_SECONDS = DEFAULT_SETTINGS["TOP1_FAST_CAPTURE_SECONDS"]
COMMITTEE_FAST_PRECHECK_SECONDS = DEFAULT_SETTINGS["COMMITTEE_FAST_PRECHECK_SECONDS"]
COMMITTEE_FULL_REVIEW_SECONDS = DEFAULT_SETTINGS["COMMITTEE_FULL_REVIEW_SECONDS"]
EXTERNAL_AI_REFRESH_SECONDS = DEFAULT_SETTINGS["EXTERNAL_AI_REFRESH_SECONDS"]
LIGHT_MARKET_SCAN_SECONDS = DEFAULT_SETTINGS["LIGHT_MARKET_SCAN_SECONDS"]
DEEP_MARKET_SCAN_SECONDS = DEFAULT_SETTINGS["DEEP_MARKET_SCAN_SECONDS"]
OPPORTUNITY_TRIGGER_SCORE = DEFAULT_SETTINGS["OPPORTUNITY_TRIGGER_SCORE"]
COMMITTEE_TARGET_MIN_STABLE_CYCLES = DEFAULT_SETTINGS["COMMITTEE_TARGET_MIN_STABLE_CYCLES"]
COMMITTEE_TARGET_SWITCH_SCORE_GAP = DEFAULT_SETTINGS["COMMITTEE_TARGET_SWITCH_SCORE_GAP"]
COMMITTEE_TARGET_COOLDOWN_SECONDS = DEFAULT_SETTINGS["COMMITTEE_TARGET_COOLDOWN_SECONDS"]
TOP1_STRONG_SCORE = DEFAULT_SETTINGS["TOP1_STRONG_SCORE"]
OPPORTUNITY_DUPLICATE_COOLDOWN_SECONDS = DEFAULT_SETTINGS["OPPORTUNITY_DUPLICATE_COOLDOWN_SECONDS"]
ENABLE_FAST_OPPORTUNITY_CAPTURE = DEFAULT_SETTINGS["ENABLE_FAST_OPPORTUNITY_CAPTURE"]
ENABLE_FAST_COMMITTEE_PRECHECK = DEFAULT_SETTINGS["ENABLE_FAST_COMMITTEE_PRECHECK"]
ENABLE_TOP10_COMMITTEE_PRECHECK = DEFAULT_SETTINGS["ENABLE_TOP10_COMMITTEE_PRECHECK"]
ENABLE_COMMITTEE_ANCHOR_TOP1 = DEFAULT_SETTINGS["ENABLE_COMMITTEE_ANCHOR_TOP1"]

_STATE: dict[str, Any] = {
    "last_capture_at": 0.0,
    "last_precheck_at": 0.0,
    "current_target": "",
    "target_since": 0.0,
    "target_score": 0,
    "stable_symbol": "",
    "stable_count": 0,
    "last_switch_at": 0.0,
    "seen_opportunities": {},
    "opportunity_lifecycle": {},
    "latest_capture": {},
    "latest_precheck": {},
    "latest_top10_precheck": [],
    "latest_multi_review": [],
    "latest_candidate": {},
}
_TRADABILITY_CACHE: dict[str, Any] = {"loaded_at": 0.0, "rows": []}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    return int(round(_to_float(value, default)))


def _clamp_score(value: Any, minimum: int = 0, maximum: int = 100) -> int:
    return max(minimum, min(maximum, _to_int(value)))


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_fast_opportunity_settings() -> dict[str, Any]:
    _ensure_data_dir()
    if not SETTINGS_PATH.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig") or "{}")
        if not isinstance(loaded, dict):
            return dict(DEFAULT_SETTINGS)
        merged = {**DEFAULT_SETTINGS, **loaded}
        merged["TOP10_OPPORTUNITY_REFRESH_SECONDS"] = max(3, int(_to_int(merged.get("TOP10_OPPORTUNITY_REFRESH_SECONDS"), 3)))
        merged["OPPORTUNITY_DUPLICATE_COOLDOWN_SECONDS"] = 120
        merged["COMMITTEE_REVIEW_TOP_N"] = max(10, int(_to_int(merged.get("COMMITTEE_REVIEW_TOP_N"), 10)))
        merged["FULL_REVIEW_TOP_N"] = max(10, int(_to_int(merged.get("FULL_REVIEW_TOP_N"), 10)))
        merged["COMMITTEE_LIGHT_TRACK_TOP_N"] = max(10, int(_to_int(merged.get("COMMITTEE_LIGHT_TRACK_TOP_N"), 10)))
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_fast_opportunity_settings(settings: dict[str, Any]) -> dict[str, Any]:
    _ensure_data_dir()
    cleaned = dict(DEFAULT_SETTINGS)
    for key, default in DEFAULT_SETTINGS.items():
        value = settings.get(key, default)
        if isinstance(default, bool):
            cleaned[key] = bool(value)
        elif isinstance(default, int):
            cleaned[key] = max(1, int(_to_int(value, default)))
        else:
            cleaned[key] = value
    SETTINGS_PATH.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    return cleaned


def _append_json_log(filename: str, event: dict[str, Any]) -> None:
    _ensure_data_dir()
    path = DATA_DIR / filename
    rows: list[dict[str, Any]] = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig") or "[]")
            rows = loaded if isinstance(loaded, list) else []
        except Exception:
            rows = []
    rows.append(event)
    path.write_text(json.dumps(rows[-1000:], ensure_ascii=False, indent=2), encoding="utf-8")


def _append_csv_log(filename: str, event: dict[str, Any]) -> None:
    _ensure_data_dir()
    path = DATA_DIR / filename
    fieldnames = ["time", "event", "symbol", "score", "result", "reason", "opportunity_id", "elapsed_ms"]
    exists = path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow({key: event.get(key, "") for key in fieldnames})


def _lifecycle_record(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {
        "symbol": "",
        "review_count": 0,
        "reject_count": 0,
        "approve_count": 0,
        "block_count": 0,
        "fast_checked_count": 0,
        "last_review_time": 0,
        "last_reject_time": 0,
        "cooldown_until": 0,
        "opportunity_round": 1,
        "round_index": 1,
        "status": "candidate",
        "last_reason": "",
        "removed_reason": "",
    }


def _cleanup_lifecycle() -> None:
    settings = get_fast_opportunity_settings()
    rerank_seconds = int(settings.get("FULL_MARKET_RERANK_SECONDS", 1800) or 1800)
    now = time.time()
    records = dict(_STATE.get("opportunity_lifecycle") or {})
    for symbol, raw in list(records.items()):
        record = _lifecycle_record(raw)
        last_reject = float(record.get("last_reject_time", 0) or 0)
        cooldown_until = float(record.get("cooldown_until", 0) or 0)
        last_review = float(record.get("last_review_time", 0) or 0)
        last_activity = max(last_reject, last_review)
        if last_activity and now - last_activity >= rerank_seconds:
            records.pop(symbol, None)
            _log_candidate({"time": _now(), "event": "重新排榜", "symbol": symbol, "result": "恢复", "reason": "全市场重新排榜周期到期，允许重新参与机会榜。"})
            continue
        if record.get("status") == "cooling" and cooldown_until <= now:
            record["status"] = "candidate"
            record["cooldown_until"] = 0
            records[symbol] = record
    _STATE["opportunity_lifecycle"] = records


def _symbol_lifecycle(symbol: str) -> dict[str, Any]:
    _cleanup_lifecycle()
    records = dict(_STATE.get("opportunity_lifecycle") or {})
    return _lifecycle_record(records.get(str(symbol or "").upper()))


def _purge_removed_from_latest(symbol: str) -> None:
    """从内存中的并行评审/预判展示队列移除已淘汰对象。"""
    normalized = str(symbol or "").upper().strip()
    for key in ("latest_top10_precheck", "latest_multi_review"):
        rows = []
        for row in _STATE.get(key, []) or []:
            if str(row.get("symbol", "")).upper().strip() != normalized:
                rows.append(row)
        _STATE[key] = rows
    latest_capture = _STATE.get("latest_capture") or {}
    if str(latest_capture.get("symbol", "")).upper().strip() == normalized:
        _STATE["latest_capture"] = {}
    latest_precheck = _STATE.get("latest_precheck") or {}
    if str(latest_precheck.get("symbol", "")).upper().strip() == normalized:
        _STATE["latest_precheck"] = {}
    latest_candidate = _STATE.get("latest_candidate") or {}
    if str(latest_candidate.get("symbol", "")).upper().strip() == normalized:
        _STATE["latest_candidate"] = {}


def _drop_removed_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    for row in rows or []:
        symbol = str(row.get("symbol", "")).upper().strip()
        if not symbol:
            continue
        if str(_symbol_lifecycle(symbol).get("status", "candidate")) == "removed":
            continue
        visible.append(row)
    return visible


def _force_remove_opportunity(symbol: str, reason: str, opportunity_id: str = "") -> dict[str, Any]:
    """强制从机会榜、观察池和并行评审队列中淘汰无效对象。"""
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return {}
    settings = get_fast_opportunity_settings()
    now = time.time()
    records = dict(_STATE.get("opportunity_lifecycle") or {})
    record = _lifecycle_record(records.get(normalized))
    record["symbol"] = normalized
    record["status"] = "removed"
    record["removed_reason"] = reason
    record["last_reason"] = reason
    record["last_reject_time"] = now
    record["cooldown_until"] = now + int(settings.get("FULL_MARKET_RERANK_SECONDS", 1800) or 1800)
    record["last_review_time"] = now
    records[normalized] = record
    _STATE["opportunity_lifecycle"] = records
    _purge_removed_from_latest(normalized)
    if remove_from_watchlist:
        try:
            remove_from_watchlist(normalized)
            watch_reason = "已同步从观察池移除。"
        except Exception as exc:
            watch_reason = f"观察池移除失败：{exc!r}"
    else:
        watch_reason = "观察池移除函数不可用。"
    _log_candidate(
        {
            "time": _now(),
            "event": "达到剔除条件",
            "symbol": normalized,
            "result": "removed",
            "reason": f"{reason}；{watch_reason}",
            "opportunity_id": opportunity_id,
        }
    )
    _log_candidate(
        {
            "time": _now(),
            "event": "移出机会榜",
            "symbol": normalized,
            "result": "removed",
            "reason": reason,
            "opportunity_id": opportunity_id,
        }
    )
    return dict(record)


def _evaluate_lifecycle_removal(symbol: str, opportunity_id: str = "") -> dict[str, Any]:
    """根据强制规则判断是否立即淘汰。"""
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return {}
    settings = get_fast_opportunity_settings()
    record = _symbol_lifecycle(normalized)
    if record.get("status") == "removed":
        return record
    if int(record.get("reject_count", 0) or 0) >= int(settings.get("MAX_REJECT_BEFORE_REMOVE", 2) or 2):
        return _force_remove_opportunity(normalized, "连续2次委员会否决", opportunity_id)
    if int(record.get("block_count", 0) or 0) >= int(settings.get("MAX_BLOCK_BEFORE_REMOVE", 2) or 2):
        return _force_remove_opportunity(normalized, "委员会判断为阻断/等待/不交易达到2次", opportunity_id)
    if int(record.get("fast_checked_count", 0) or 0) >= int(settings.get("MAX_REVIEW_BEFORE_REMOVE", 3) or 3) and int(record.get("approve_count", 0) or 0) <= 0:
        return _force_remove_opportunity(normalized, "连续多轮快速审查未生成候选", opportunity_id)
    if int(record.get("review_count", 0) or 0) >= int(settings.get("MAX_REVIEW_BEFORE_REMOVE", 3) or 3) and int(record.get("approve_count", 0) or 0) <= 0:
        return _force_remove_opportunity(normalized, "审查3次未通过", opportunity_id)
    return record


def _record_symbol_review(symbol: str, reason: str, opportunity_id: str = "", *, fast_checked: bool = False, blocked: bool = False) -> dict[str, Any]:
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return {}
    now = time.time()
    records = dict(_STATE.get("opportunity_lifecycle") or {})
    record = _lifecycle_record(records.get(normalized))
    record["symbol"] = normalized
    record["review_count"] = int(record.get("review_count", 0) or 0) + 1
    record["last_review_time"] = now
    record["last_reason"] = reason
    record["round_index"] = min(3, int(record.get("round_index", record.get("opportunity_round", 1)) or 1))
    record["opportunity_round"] = record["round_index"]
    if fast_checked:
        record["fast_checked_count"] = int(record.get("fast_checked_count", 0) or 0) + 1
    if blocked:
        record["block_count"] = int(record.get("block_count", 0) or 0) + 1
    if record.get("status") not in {"removed", "cooling", "approved"}:
        record["status"] = "reviewing"
    records[normalized] = record
    _STATE["opportunity_lifecycle"] = records
    _log_candidate({"time": _now(), "event": "审查次数+1", "symbol": normalized, "result": str(record["review_count"]), "reason": reason, "opportunity_id": opportunity_id})
    return _evaluate_lifecycle_removal(normalized, opportunity_id)


def _symbol_available(symbol: str) -> bool:
    record = _evaluate_lifecycle_removal(symbol)
    now = time.time()
    status = str(record.get("status") or "candidate")
    if status == "removed":
        return False
    if status == "cooling" and float(record.get("cooldown_until", 0) or 0) > now:
        return False
    return True


def _active_position_symbols(min_notional: float = 1.0) -> set[str]:
    """Symbols with meaningful open exposure should not occupy trade ranking slots."""
    symbols: set[str] = set()
    try:
        positions = json.loads((DATA_DIR / "sim_positions.json").read_text(encoding="utf-8-sig") or "[]")
        if not isinstance(positions, list):
            return set()
        for position in positions:
            if not isinstance(position, dict) or position.get("status") not in {"open", "partially_closed"}:
                continue
            symbol = str(position.get("symbol") or "").upper().strip()
            notional = _to_float(position.get("notional_usdt"), 0)
            margin = _to_float(position.get("margin_usdt"), 0)
            if symbol and max(abs(notional), abs(margin)) >= min_notional:
                symbols.add(symbol)
    except Exception:
        return set()
    return symbols


def _enrich_lifecycle(row: dict[str, Any]) -> dict[str, Any]:
    symbol = str(row.get("symbol", "")).upper()
    record = _symbol_lifecycle(symbol)
    enriched = dict(row)
    enriched["reject_count"] = int(record.get("reject_count", 0) or 0)
    enriched["review_count"] = int(record.get("review_count", 0) or 0)
    enriched["approve_count"] = int(record.get("approve_count", 0) or 0)
    enriched["block_count"] = int(record.get("block_count", 0) or 0)
    enriched["last_reject_time"] = record.get("last_reject_time", 0)
    enriched["cooldown_until"] = record.get("cooldown_until", 0)
    enriched["opportunity_round"] = int(record.get("opportunity_round", 1) or 1)
    enriched["round_index"] = int(record.get("round_index", record.get("opportunity_round", 1)) or 1)
    enriched["status"] = record.get("status", "candidate")
    enriched["removed_reason"] = record.get("removed_reason", "")
    return enriched


def _record_opportunity_reject(symbol: str, reason: str, opportunity_id: str = "") -> dict[str, Any]:
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return {}
    settings = get_fast_opportunity_settings()
    cooldown = int(settings.get("OPPORTUNITY_REJECT_COOLDOWN_SECONDS", 120) or 120)
    max_reject = int(settings.get("MAX_REJECT_BEFORE_REMOVE", 2) or 2)
    now = time.time()
    records = dict(_STATE.get("opportunity_lifecycle") or {})
    record = _lifecycle_record(records.get(normalized))
    record["reject_count"] = int(record.get("reject_count", 0) or 0) + 1
    record["last_reject_time"] = now
    record["last_review_time"] = now
    record["last_reason"] = reason
    record["opportunity_round"] = min(3, int(record.get("opportunity_round", 1) or 1) + 1)
    record["round_index"] = record["opportunity_round"]
    if int(record["reject_count"]) >= max_reject:
        record["status"] = "removed"
        record["cooldown_until"] = now + int(settings.get("FULL_MARKET_RERANK_SECONDS", 1800) or 1800)
        if remove_from_watchlist:
            try:
                remove_from_watchlist(normalized)
            except Exception:
                pass
        event = "移出机会榜"
        result = "二次否决"
        log_reason = "连续两次委员会否决，暂时移出机会池。"
    else:
        record["status"] = "cooling"
        record["cooldown_until"] = now + cooldown
        event = "进入冷却"
        result = "首次否决"
        log_reason = reason
    records[normalized] = record
    _STATE["opportunity_lifecycle"] = records
    _log_candidate({"time": _now(), "event": event, "symbol": normalized, "result": result, "reason": log_reason, "opportunity_id": opportunity_id})
    if int(record.get("reject_count", 0) or 0) >= max_reject:
        return _force_remove_opportunity(normalized, "连续2次委员会否决", opportunity_id)
    return _evaluate_lifecycle_removal(normalized, opportunity_id)


def _record_opportunity_approved(symbol: str) -> dict[str, Any]:
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return {}
    records = dict(_STATE.get("opportunity_lifecycle") or {})
    record = _lifecycle_record(records.get(normalized))
    record["status"] = "approved"
    record["approve_count"] = int(record.get("approve_count", 0) or 0) + 1
    record["cooldown_until"] = 0
    records[normalized] = record
    _STATE["opportunity_lifecycle"] = records
    return dict(record)


def _log_capture(event: dict[str, Any]) -> None:
    _append_json_log("fast_opportunity_capture_log.json", event)
    _append_csv_log("fast_opportunity_capture_log.csv", event)


def _log_precheck(event: dict[str, Any]) -> None:
    _append_json_log("committee_fast_precheck_log.json", event)
    _append_csv_log("committee_fast_precheck_log.csv", event)


def _log_candidate(event: dict[str, Any]) -> None:
    _append_json_log("opportunity_candidate_log.json", event)
    _append_csv_log("opportunity_candidate_log.csv", event)


def get_fast_opportunity_settings() -> dict[str, Any]:
    return load_fast_opportunity_settings()


def _best_top1(rankings: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for key in ("long_opportunities", "short_opportunities", "strong", "abnormal"):
        for row in (rankings.get(key) or [])[:1]:
            item = dict(row)
            item["source_rank"] = key
            candidates.append(item)
    if not candidates:
        return None
    assessed = [assess_trade_opportunity(row) for row in candidates]
    return max(
        assessed,
        key=lambda row: (
            1 if row.get("tradable_now") else 0,
            _to_int(row.get("professional_trade_score", row.get("final_opportunity_score", row.get("opportunity_score")))),
            _to_float(row.get("quote_volume"), 0),
        ),
    )


def collect_top10_opportunities(rankings: dict[str, list[dict[str, Any]]] | None = None, limit: int = 10) -> list[dict[str, Any]]:
    rankings = rankings or market_cache.get_rankings() or {}
    by_symbol: dict[str, dict[str, Any]] = {}
    active_symbols = _active_position_symbols()
    for key, label in [
        ("long_opportunities", "多头机会榜"),
        ("short_opportunities", "空头机会榜"),
        ("strong", "强势币榜"),
        ("abnormal", "异动币榜"),
    ]:
        for row in rankings.get(key, []) or []:
            symbol = str(row.get("symbol", "")).upper()
            if not symbol:
                continue
            if symbol in active_symbols:
                continue
            if not _symbol_available(symbol):
                continue
            item = dict(row)
            item["source_rank"] = key
            item["opportunity_source"] = item.get("opportunity_source") or label
            item = assess_trade_opportunity(_enrich_lifecycle(item))
            old = by_symbol.get(symbol)
            score = _to_int(item.get("simulation_score", item.get("professional_trade_score", item.get("final_opportunity_score", item.get("opportunity_score")))))
            old_score = _to_int((old or {}).get("simulation_score", (old or {}).get("professional_trade_score", (old or {}).get("final_opportunity_score", (old or {}).get("opportunity_score"))))) if old else -1
            if old is None or score > old_score:
                by_symbol[symbol] = item
    if get_watchlist_candidates_for_committee:
        try:
            for candidate in get_watchlist_candidates_for_committee():
                symbol = str(candidate.get("symbol", "")).upper()
                if not symbol:
                    continue
                if symbol in active_symbols:
                    continue
                if not _symbol_available(symbol):
                    continue
                watch_score = _to_int(candidate.get("watch_score"))
                confidence = _to_int(candidate.get("confidence"), watch_score)
                risk = _to_int(candidate.get("risk_score"), 50)
                score = max(watch_score, min(100, confidence))
                if score < 61 or risk >= 80:
                    continue
                ticker = market_cache.get_ticker(symbol) or {}
                item = {
                    "symbol": symbol,
                    "last_price": ticker.get("last_price"),
                    "current_price": ticker.get("last_price"),
                    "price_change_percent": ticker.get("price_change_percent"),
                    "quote_volume": ticker.get("quote_volume", 0),
                    "final_opportunity_score": score,
                    "raw_opportunity_score": score,
                    "opportunity_score": score,
                    "risk_score": risk,
                    "direction": candidate.get("local_strategy_action") or "观察",
                    "advice": candidate.get("local_strategy_action") or "观察池候选",
                    "opportunity_status": candidate.get("status") or "观察池候选",
                    "current_market_state": candidate.get("strategy_name") or "观察池重点跟踪",
                    "source_rank": "watchlist",
                    "opportunity_source": "观察池候选",
                    "watch_score": watch_score,
                    "watchlist_candidate": True,
                }
                item = assess_trade_opportunity(_enrich_lifecycle(item))
                old = by_symbol.get(symbol)
                item_score = _to_int(item.get("simulation_score", item.get("professional_trade_score", score)))
                old_score = _to_int((old or {}).get("simulation_score", (old or {}).get("professional_trade_score", (old or {}).get("final_opportunity_score", (old or {}).get("opportunity_score"))))) if old else -1
                if old is None or item_score > old_score:
                    by_symbol[symbol] = item
        except Exception as exc:
            print(f"[观察池] 合并到交易机会榜失败，不影响主榜单。error={repr(exc)}")
    rows = sorted(
        by_symbol.values(),
        key=lambda row: (
            1 if row.get("tradable_now") else 0,
            _to_int(row.get("simulation_score"), 0),
            _to_int(row.get("professional_trade_score", row.get("final_opportunity_score", row.get("opportunity_score")))),
            _to_int(row.get("base_quality_score"), 0),
            _to_float(row.get("quote_volume"), 0),
            -_to_int(row.get("risk_score"), 50),
        ),
        reverse=True,
    )[:limit]
    for row in rows:
        _log_candidate({"time": _now(), "event": "进入机会榜", "symbol": row.get("symbol"), "score": row.get("professional_trade_score", row.get("final_opportunity_score", row.get("opportunity_score"))), "result": row.get("opportunity_class", "有效候选"), "reason": f"第{row.get('opportunity_round', 1)}轮，入场状态 {row.get('entry_state', 'unknown')}。"})
    return rows


def _direction(row: dict[str, Any]) -> str:
    direction = str(row.get("direction") or "观察")
    if direction in {"多头", "long", "BUY"}:
        return "long"
    if direction in {"空头", "short", "SELL"}:
        return "short"
    long_score = _to_int(row.get("long_score"))
    short_score = _to_int(row.get("short_score"))
    if abs(long_score - short_score) < 12:
        return "neutral"
    return "long" if long_score > short_score else "short"


def _market_regime() -> dict[str, Any]:
    """Lightweight market-context committee using cached BTC/ETH 24h changes."""
    btc = market_cache.get_ticker("BTCUSDT") or {}
    eth = market_cache.get_ticker("ETHUSDT") or {}
    btc_change = _to_float(btc.get("price_change_percent"))
    eth_change = _to_float(eth.get("price_change_percent"))
    average = (btc_change + eth_change) / 2 if btc or eth else 0.0
    if average >= 2.0 and btc_change >= 0:
        regime = "bullish"
        bias = "long"
    elif average <= -2.0 and btc_change <= 0:
        regime = "bearish"
        bias = "short"
    elif btc_change > 0.8 and eth_change > 0.8:
        regime = "rebound"
        bias = "long"
    elif btc_change < -0.8 and eth_change < -0.8:
        regime = "weak"
        bias = "short"
    else:
        regime = "range"
        bias = "neutral"
    return {
        "market_regime": regime,
        "direction_bias": bias,
        "btc_change_percent": btc_change,
        "eth_change_percent": eth_change,
    }


def _market_alignment_score(direction: str, regime: dict[str, Any]) -> tuple[int, str]:
    bias = str(regime.get("direction_bias") or "neutral")
    if direction not in {"long", "short"}:
        return 35, "方向不明确"
    if bias == "neutral":
        return 68, "大盘中性"
    if bias == direction:
        return 88, "大盘同向"
    return 35, "大盘反向"


def _read_json_file(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _open_sim_positions() -> list[dict[str, Any]]:
    rows = _read_json_file(SIM_POSITIONS_PATH, [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("status") in {"open", "partially_closed"}]


def _recent_sim_history() -> list[dict[str, Any]]:
    now = time.time()
    if now - float(_TRADABILITY_CACHE.get("loaded_at", 0) or 0) < 30:
        return list(_TRADABILITY_CACHE.get("rows") or [])
    rows = _read_json_file(SIM_HISTORY_PATH, [])
    if not isinstance(rows, list):
        rows = []
    rows = [row for row in rows if isinstance(row, dict)]
    _TRADABILITY_CACHE["loaded_at"] = now
    _TRADABILITY_CACHE["rows"] = rows[:300]
    return rows[:300]


def _liquidity_quality_score(row: dict[str, Any], price: float, change: float, orderbook: dict[str, Any]) -> tuple[int, list[str]]:
    notes: list[str] = []
    volume_score = _to_int(row.get("volume_score", row.get("liquidity_score")), 50)
    tradeability = _to_int(row.get("tradeability_score"), volume_score)
    quote_volume = _to_float(row.get("quote_volume"), 0)
    score = volume_score * 0.55 + tradeability * 0.25 + _to_int(row.get("liquidity_score"), volume_score) * 0.20
    if quote_volume <= 0:
        score -= 18
        notes.append("成交额缺失")
    elif quote_volume < 1_000_000:
        score -= 10
        notes.append("成交额偏低")
    if price <= 0:
        score -= 30
        notes.append("价格不可用")
    if abs(change) >= 18:
        score -= 14
        notes.append("24小时波动过大")
    if str(orderbook.get("bias") or "").startswith("订单簿"):
        score -= 8
        notes.append("盘口样本不足")
    return _clamp_score(score), notes or ["流动性可用"]


def _relative_strength_score(row: dict[str, Any], direction: str, market: dict[str, Any], change: float) -> tuple[int, str]:
    market_change = (_to_float(market.get("btc_change_percent")) + _to_float(market.get("eth_change_percent"))) / 2
    relative = change - market_change
    direction_gap = _to_int(row.get("direction_gap"), abs(_to_int(row.get("long_score")) - _to_int(row.get("short_score"))))
    base = 52 + min(20, direction_gap * 0.45)
    if direction == "long":
        score = base + relative * 4
        note = f"相对大盘强弱 {relative:.2f}%"
    elif direction == "short":
        score = base - relative * 4
        note = f"相对大盘弱势 {relative:.2f}%"
    else:
        score = 35
        note = "方向不明确"
    return _clamp_score(score), note


def _signal_freshness_score(row: dict[str, Any], entry_state: str, change: float) -> tuple[int, list[str]]:
    notes: list[str] = []
    score = 75
    review_count = _to_int(row.get("review_count"))
    reject_count = _to_int(row.get("reject_count"))
    block_count = _to_int(row.get("block_count"))
    opportunity_round = max(1, _to_int(row.get("opportunity_round"), 1))
    score -= min(24, review_count * 5 + reject_count * 8 + block_count * 8)
    if opportunity_round >= 4:
        score -= min(16, (opportunity_round - 3) * 4)
        notes.append("机会已多轮停留")
    if entry_state.startswith("wait") or "wait" in entry_state:
        score -= 12
        notes.append("入场确认未完成")
    if abs(change) >= 12:
        score -= 8
        notes.append("波动扩张后信号衰减")
    if entry_state in {"tradable_now", "pullback_confirmed", "failed_retest_confirmed"}:
        score += 8
        notes.append("入场状态新鲜")
    return _clamp_score(score), notes or ["信号新鲜度正常"]


def _historical_tradability_score(symbol: str, direction: str) -> tuple[int, list[str]]:
    rows = [row for row in _recent_sim_history() if str(row.get("symbol") or "").upper() == symbol]
    if direction in {"long", "short"}:
        same_direction = [row for row in rows if str(row.get("direction") or "") == direction]
        if same_direction:
            rows = same_direction
    rows = rows[:20]
    if not rows:
        return 60, ["缺少该币近期模拟样本，按中性处理"]
    wins = 0
    total_pnl = 0.0
    stop_count = 0
    tp_count = 0
    for row in rows:
        pnl = _to_float(row.get("realized_pnl", row.get("pnl_usdt")))
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        reason = str(row.get("close_reason") or "")
        if "止损" in reason:
            stop_count += 1
        if "止盈" in reason or "TP" in reason.upper():
            tp_count += 1
    win_rate = wins / len(rows)
    avg_pnl = total_pnl / len(rows)
    score = 45 + win_rate * 34 + min(14, max(-14, avg_pnl * 2)) + tp_count * 2 - stop_count * 4
    return _clamp_score(score, 25, 90), [f"近期样本{len(rows)}笔，胜率{win_rate * 100:.0f}%"]


def _portfolio_fit_score(symbol: str, direction: str) -> tuple[int, list[str]]:
    positions = _open_sim_positions()
    notes: list[str] = []
    same_symbol = [p for p in positions if str(p.get("symbol") or "").upper() == symbol]
    same_direction = [p for p in positions if str(p.get("direction") or "") == direction]
    opposite = [p for p in positions if str(p.get("direction") or "") in {"long", "short"} and str(p.get("direction") or "") != direction]
    score = 82
    if same_symbol:
        score -= 32
        notes.append("同币种已有敞口")
    if len(same_direction) >= 4:
        score -= min(24, (len(same_direction) - 3) * 6)
        notes.append("同方向仓位偏多")
    if len(positions) >= 10:
        score -= min(20, (len(positions) - 9) * 4)
        notes.append("组合仓位较密集")
    if opposite and len(opposite) >= len(same_direction) + 3:
        score -= 6
        notes.append("多空结构冲突")
    return _clamp_score(score, 20, 95), notes or ["组合敞口适配"]


def _base_quality_score(
    liquidity: int,
    relative_strength: int,
    freshness: int,
    historical: int,
    portfolio_fit: int,
    risk_score: int,
    data_quality: str,
) -> int:
    score = (
        liquidity * 0.25
        + relative_strength * 0.20
        + freshness * 0.15
        + historical * 0.15
        + portfolio_fit * 0.15
        + (100 - risk_score) * 0.10
    )
    if data_quality == "poor":
        score -= 12
    return _clamp_score(score)


def _signal_direction(value: float, positive: float, negative: float) -> str:
    if value >= positive:
        return "long"
    if value <= negative:
        return "short"
    return "neutral"


def _whale_signal(symbol: str) -> dict[str, Any]:
    whale = market_cache.get_whales(symbol) or {}
    stats = whale.get("stats") or {}
    stats_5m = stats.get("5m") or {}
    stats_15m = stats.get("15m") or {}
    net_5m = _to_float(whale.get("net_inflow_5m"), _to_float(stats_5m.get("net_amount"), 0))
    net_15m = _to_float(whale.get("net_inflow_15m"), _to_float(stats_15m.get("net_amount"), 0))
    score = _to_int(whale.get("whale_score", whale.get("score")), 0)
    quality = str(whale.get("data_quality") or ("missing" if not whale else "partial"))
    threshold = max(1000.0, _to_float(whale.get("threshold"), 0) * 0.5)
    combined = net_5m * 0.4 + net_15m * 0.6
    direction = _signal_direction(combined, threshold, -threshold)
    return {
        "direction": direction,
        "net_5m": net_5m,
        "net_15m": net_15m,
        "score": score,
        "quality": quality,
        "confirming": direction in {"long", "short"} and quality in {"good", "partial"},
    }


def _orderbook_signal(symbol: str, price: float) -> dict[str, Any]:
    orderbook = market_cache.get_orderbook(symbol) or {}
    analysis = analyze_orderbook(orderbook, price) if orderbook else {}
    buy_ratio = _to_float(analysis.get("buy_ratio"), 0)
    sell_ratio = _to_float(analysis.get("sell_ratio"), 0)
    direction = "long" if buy_ratio >= 58 else "short" if sell_ratio >= 58 else "neutral"
    return {
        "direction": direction,
        "buy_ratio": buy_ratio,
        "sell_ratio": sell_ratio,
        "bias": analysis.get("bias") or "等待数据",
        "confirming": direction in {"long", "short"},
    }


def _kline_signal(symbol: str) -> dict[str, Any]:
    interval = market_cache.get_kline_interval()
    rows = market_cache.get_klines(symbol, interval)
    if len(rows) < 30:
        rows = market_cache.get_klines(symbol, "1m")
    if len(rows) < 30:
        return {"direction": "neutral", "entry_state": "wait_data", "confirming": False, "reason": "K线样本不足"}
    closes = [_to_float(row.get("close"), 0) for row in rows[-30:]]
    highs = [_to_float(row.get("high"), 0) for row in rows[-12:]]
    lows = [_to_float(row.get("low"), 0) for row in rows[-12:]]
    close = closes[-1]
    ma8 = sum(closes[-8:]) / 8
    ma20 = sum(closes[-20:]) / 20
    recent_high = max(highs[-5:])
    prior_high = max(highs[:7])
    recent_low = min(lows[-5:])
    prior_low = min(lows[:7])
    if close < ma8 < ma20 and recent_high <= prior_high * 1.002:
        return {"direction": "short", "entry_state": "failed_retest_confirmed", "confirming": True, "ma8": ma8, "ma20": ma20}
    if close > ma8 > ma20 and recent_low >= prior_low * 0.998:
        return {"direction": "long", "entry_state": "pullback_confirmed", "confirming": True, "ma8": ma8, "ma20": ma20}
    if close < ma20:
        return {"direction": "short", "entry_state": "wait_failed_retest", "confirming": False, "ma8": ma8, "ma20": ma20}
    if close > ma20:
        return {"direction": "long", "entry_state": "wait_pullback", "confirming": False, "ma8": ma8, "ma20": ma20}
    return {"direction": "neutral", "entry_state": "range_no_edge", "confirming": False, "ma8": ma8, "ma20": ma20}


def _consensus_count(direction: str, signals: dict[str, str]) -> tuple[int, list[str], list[str]]:
    support: list[str] = []
    conflict: list[str] = []
    for name, value in signals.items():
        if value == direction:
            support.append(name)
        elif value in {"long", "short"} and value != direction:
            conflict.append(name)
    return len(support), support, conflict


def assess_trade_opportunity(row: dict[str, Any]) -> dict[str, Any]:
    """Convert an anomaly/momentum row into a tradable-opportunity verdict.

    The light scanner can only prove that a symbol is moving. This layer decides
    whether the current price is a professional entry or merely a watch item.
    """
    assessed = dict(row or {})
    direction = _direction(assessed)
    long_score = _to_int(assessed.get("long_score"))
    short_score = _to_int(assessed.get("short_score"))
    direction_gap = abs(long_score - short_score)
    risk_score = _to_int(assessed.get("risk_score"), 100)
    score = _to_int(assessed.get("final_opportunity_score", assessed.get("opportunity_score")))
    change = _to_float(assessed.get("price_change_percent"))
    abs_change = abs(change)
    symbol = str(assessed.get("symbol") or "").upper().strip()
    price = (
        _to_float(assessed.get("current_price"), 0)
        or _to_float(assessed.get("last_price"), 0)
        or _to_float(assessed.get("price"), 0)
    )
    volume_score = _to_int(assessed.get("volume_score", assessed.get("liquidity_score")), 50)
    data_quality = str(assessed.get("data_quality") or "good")
    market = _market_regime()
    market_score, market_note = _market_alignment_score(direction, market)
    whale = _whale_signal(symbol) if symbol else {"direction": "neutral", "confirming": False, "quality": "missing"}
    orderbook = _orderbook_signal(symbol, price) if symbol and price > 0 else {"direction": "neutral", "confirming": False, "bias": "价格缺失"}
    kline = _kline_signal(symbol) if symbol else {"direction": "neutral", "entry_state": "wait_data", "confirming": False}
    consensus_signals = {
        "机会榜": direction,
        "大盘": str(market.get("direction_bias") or "neutral"),
        "K线": str(kline.get("direction") or "neutral"),
        "大单": str(whale.get("direction") or "neutral"),
        "盘口": str(orderbook.get("direction") or "neutral"),
    }
    consensus_count, consensus_support, consensus_conflict = _consensus_count(direction, consensus_signals)
    trend_score = max(long_score, short_score)
    flow_score = max(35, min(95, volume_score))
    liquidity_score = flow_score
    rr_score = 88 if risk_score < 45 else 76 if risk_score < 55 else 62 if risk_score < 65 else 35
    entry_quality = 80
    entry_state = "tradable_now"
    action_gate = "open_now"
    block_reasons: list[str] = []
    risk_flags: list[str] = []

    if direction == "neutral" or direction_gap < 12:
        entry_quality = 35
        entry_state = "range_no_edge"
        action_gate = "wait"
        block_reasons.append("多空分差不足，方向不明确。")
    if data_quality == "poor":
        block_reasons.append("数据质量不足。")
    if score < 78:
        block_reasons.append(f"交易机会分 {score} 低于专业开仓阈值 78。")
    if risk_score >= 65:
        block_reasons.append(f"风险评分 {risk_score} 进入高风险区。")
    if market_score < 50:
        block_reasons.append(market_note)
    if consensus_count < 3:
        block_reasons.append(f"方向共识不足：{consensus_count}/5，同向来源 {','.join(consensus_support) or '无'}。")
    if len(consensus_conflict) >= 2:
        block_reasons.append(f"反向冲突过多：{','.join(consensus_conflict)}。")
    if whale.get("direction") != direction:
        block_reasons.append("大单资金未同向确认，禁止由单一榜单开仓。")
    if str(whale.get("quality") or "missing") in {"missing", "poor"}:
        block_reasons.append("大单数据质量不足，等待真实大单确认。")
    if orderbook.get("direction") in {"long", "short"} and orderbook.get("direction") != direction:
        block_reasons.append("盘口买卖盘方向与开仓方向冲突。")
    if not kline.get("confirming") or kline.get("direction") != direction:
        entry_quality = min(entry_quality, 48)
        entry_state = str(kline.get("entry_state") or "wait_structure")
        action_gate = "wait"
        if direction == "long":
            block_reasons.append("多单未完成回踩/重新走强确认。")
        elif direction == "short":
            block_reasons.append("空单未完成反抽失败确认。")
    else:
        entry_state = str(kline.get("entry_state") or entry_state)

    if direction == "long":
        if change >= 8:
            entry_quality = min(entry_quality, 42)
            entry_state = "wait_pullback"
            action_gate = "wait"
            risk_flags.append("24小时涨幅较大，禁止直接追多，等待回踩确认。")
        elif change < 1.2:
            entry_quality = min(entry_quality, 55)
            entry_state = "wait_breakout"
            action_gate = "wait"
            risk_flags.append("多头动量尚未确认。")
    elif direction == "short":
        if change <= -8:
            entry_quality = min(entry_quality, 42)
            entry_state = "wait_failed_retest"
            action_gate = "wait"
            risk_flags.append("24小时跌幅较大，禁止直接追空，等待反抽失败。")
        elif change > -1.2:
            entry_quality = min(entry_quality, 55)
            entry_state = "wait_breakdown"
            action_gate = "wait"
            risk_flags.append("空头动量尚未确认。")

    if volume_score < 55:
        flow_score = min(flow_score, 52)
        risk_flags.append("成交额确认不足。")
    if abs_change >= 14:
        entry_quality = min(entry_quality, 36)
        if entry_state in {"tradable_now", "pullback_confirmed", "failed_retest_confirmed"}:
            entry_state = "overextended_no_chase"
            action_gate = "wait"
        risk_flags.append("短线波动过大，追涨追空风险高。")

    liquidity_quality, liquidity_notes = _liquidity_quality_score(assessed, price, change, orderbook)
    relative_strength, relative_note = _relative_strength_score(assessed, direction, market, change)
    signal_freshness, freshness_notes = _signal_freshness_score(assessed, entry_state, change)
    historical_tradability, historical_notes = _historical_tradability_score(symbol, direction)
    portfolio_fit, portfolio_notes = _portfolio_fit_score(symbol, direction)
    base_quality = _base_quality_score(
        liquidity_quality,
        relative_strength,
        signal_freshness,
        historical_tradability,
        portfolio_fit,
        risk_score,
        data_quality,
    )
    if liquidity_quality < 38:
        block_reasons.append(f"流动性质量 {liquidity_quality} 过低，成交/滑点风险不适合模拟开仓。")
    if signal_freshness < 32:
        block_reasons.append(f"信号新鲜度 {signal_freshness} 过低，机会已衰减。")
    if portfolio_fit < 25:
        block_reasons.append(f"组合适配 {portfolio_fit} 过低，当前敞口过于拥挤。")

    consensus_score = min(95, 45 + consensus_count * 10 - len(consensus_conflict) * 8)
    raw_trade_score = round(
        market_score * 0.10
        + trend_score * 0.16
        + entry_quality * 0.24
        + flow_score * 0.12
        + rr_score * 0.13
        + liquidity_score * 0.08
        + consensus_score * 0.17
        - max(0, risk_score - 45) * 0.45
    )
    trade_score = round(raw_trade_score * 0.72 + base_quality * 0.28)
    simulation_score = _clamp_score(trade_score * 0.45 + base_quality * 0.45 + (100 - risk_score) * 0.10)
    open_entry_states = {"tradable_now", "pullback_confirmed", "failed_retest_confirmed"}
    if entry_state not in open_entry_states:
        block_reasons.extend(risk_flags or ["当前不是专业入场位置。"])
    if trade_score < 75:
        block_reasons.append(f"专业交易分 {trade_score} 低于开仓阈值 75。")

    tradable_now = not block_reasons and entry_state in open_entry_states
    assessed.update(
        {
            **market,
            "direction": "多头" if direction == "long" else "空头" if direction == "short" else "观察",
            "trade_direction": direction,
            "direction_gap": direction_gap,
            "market_alignment_score": market_score,
            "market_alignment_note": market_note,
            "consensus_support_count": consensus_count,
            "consensus_support_sources": consensus_support,
            "consensus_conflict_sources": consensus_conflict,
            "consensus_signals": consensus_signals,
            "whale_signal": whale,
            "orderbook_signal": orderbook,
            "kline_signal": kline,
            "entry_quality_score": entry_quality,
            "flow_confirmation_score": flow_score,
            "risk_reward_score": rr_score,
            "consensus_score": consensus_score,
            "liquidity_quality_score": liquidity_quality,
            "relative_strength_score": relative_strength,
            "signal_freshness_score": signal_freshness,
            "historical_tradability_score": historical_tradability,
            "portfolio_fit_score": portfolio_fit,
            "base_quality_score": base_quality,
            "simulation_score": simulation_score,
            "base_score_breakdown": {
                "liquidity_quality": {"score": liquidity_quality, "notes": liquidity_notes},
                "relative_strength": {"score": relative_strength, "notes": [relative_note]},
                "signal_freshness": {"score": signal_freshness, "notes": freshness_notes},
                "historical_tradability": {"score": historical_tradability, "notes": historical_notes},
                "portfolio_fit": {"score": portfolio_fit, "notes": portfolio_notes},
            },
            "professional_trade_score": max(0, min(100, int(trade_score))),
            "trade_score": max(0, min(100, int(trade_score))),
            "entry_state": entry_state,
            "action_gate": "open_now" if tradable_now else action_gate,
            "tradable_now": tradable_now,
            "trade_block_reasons": block_reasons,
            "risk_flags": risk_flags,
            "opportunity_class": "交易机会榜" if tradable_now else "观察机会榜" if entry_state.startswith("wait") else "异动风险榜",
        }
    )
    return assessed


def build_opportunity_id(row: dict[str, Any], bucket_seconds: int = 300) -> str:
    symbol = str(row.get("symbol", "")).upper()
    direction = _direction(row)
    state = str(row.get("current_market_state") or row.get("market_state") or "unknown").replace(" ", "_")
    bucket = int(time.time() // bucket_seconds)
    return f"{symbol}:{direction}:{state}:{bucket}"


def _seen_record(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {"last_seen": float(value or 0), "review_count": 0}


def _duplicate_status(opportunity_id: str) -> tuple[bool, float, int]:
    settings = get_fast_opportunity_settings()
    cooldown = int(settings.get("OPPORTUNITY_DUPLICATE_COOLDOWN_SECONDS", OPPORTUNITY_DUPLICATE_COOLDOWN_SECONDS))
    now = time.time()
    seen = dict(_STATE.get("seen_opportunities") or {})
    current = _seen_record(seen.get(opportunity_id))
    last = float(current.get("last_seen", 0) or 0)
    review_count = int(current.get("review_count", 0) or 0)
    for key, value in list(seen.items()):
        record = _seen_record(value)
        if now - float(record.get("last_seen", 0) or 0) > cooldown:
            seen.pop(key, None)
    _STATE["seen_opportunities"] = seen
    if last and now - last < cooldown:
        return True, cooldown - (now - last), review_count
    return False, 0.0, review_count


def _mark_opportunity_seen(opportunity_id: str) -> None:
    seen = dict(_STATE.get("seen_opportunities") or {})
    record = _seen_record(seen.get(opportunity_id))
    record["last_seen"] = time.time()
    seen[opportunity_id] = record
    _STATE["seen_opportunities"] = seen


def _mark_opportunity_review(opportunity_id: str) -> int:
    """记录一次审查，但不刷新候选冷却起点。"""
    seen = dict(_STATE.get("seen_opportunities") or {})
    record = _seen_record(seen.get(opportunity_id))
    record["review_count"] = int(record.get("review_count", 0) or 0) + 1
    seen[opportunity_id] = record
    _STATE["seen_opportunities"] = seen
    return int(record["review_count"])


def _target_switch_allowed(row: dict[str, Any]) -> tuple[bool, str]:
    settings = get_fast_opportunity_settings()
    min_cycles = int(settings.get("COMMITTEE_TARGET_MIN_STABLE_CYCLES", COMMITTEE_TARGET_MIN_STABLE_CYCLES))
    score_gap_required = int(settings.get("COMMITTEE_TARGET_SWITCH_SCORE_GAP", COMMITTEE_TARGET_SWITCH_SCORE_GAP))
    switch_cooldown = int(settings.get("COMMITTEE_TARGET_COOLDOWN_SECONDS", COMMITTEE_TARGET_COOLDOWN_SECONDS))
    strong_score = int(settings.get("TOP1_STRONG_SCORE", TOP1_STRONG_SCORE))
    symbol = str(row.get("symbol", "")).upper()
    score = _to_int(row.get("final_opportunity_score", row.get("opportunity_score")))
    now = time.time()
    if not symbol:
        return False, "交易对象为空。"
    if str(_STATE.get("stable_symbol")) == symbol:
        _STATE["stable_count"] = int(_STATE.get("stable_count", 0) or 0) + 1
    else:
        _STATE["stable_symbol"] = symbol
        _STATE["stable_count"] = 1
    current = str(_STATE.get("current_target") or "")
    if not current:
        return int(_STATE["stable_count"]) >= min_cycles, "新目标需要连续确认。"
    if current == symbol:
        return True, "延续当前委员会目标。"
    if now - float(_STATE.get("last_switch_at", 0) or 0) < switch_cooldown:
        return False, "委员会目标切换冷却中，避免频繁跳币。"
    score_gap = score - int(_STATE.get("target_score", 0) or 0)
    stable_needed = int(_STATE.get("stable_count", 0) or 0) >= min_cycles
    if score >= strong_score and stable_needed:
        return True, "新TOP1为强机会且连续确认。"
    if score_gap >= score_gap_required and stable_needed:
        return True, "新TOP1评分明显领先且连续确认。"
    return False, "新TOP1尚未满足稳定切换条件。"


def _set_committee_target(row: dict[str, Any]) -> None:
    symbol = str(row.get("symbol", "")).upper()
    if _STATE.get("current_target") != symbol:
        _STATE["last_switch_at"] = time.time()
        _STATE["target_since"] = time.time()
    _STATE["current_target"] = symbol
    _STATE["target_score"] = _to_int(row.get("final_opportunity_score", row.get("opportunity_score")))


def fast_capture_top1_opportunity(rankings: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    started = time.monotonic()
    settings = get_fast_opportunity_settings()
    trigger_score = int(settings.get("OPPORTUNITY_TRIGGER_SCORE", OPPORTUNITY_TRIGGER_SCORE))
    full_review_seconds = int(settings.get("COMMITTEE_FULL_REVIEW_SECONDS", COMMITTEE_FULL_REVIEW_SECONDS))
    enabled = bool(settings.get("ENABLE_FAST_OPPORTUNITY_CAPTURE", ENABLE_FAST_OPPORTUNITY_CAPTURE))
    rankings = rankings or market_cache.get_rankings() or {}
    top1 = _best_top1(rankings)
    if not enabled:
        result = {"symbol": "", "fast_score": 0, "still_valid": False, "trigger_committee_precheck": False, "risk_fast_up": False, "reason": "快速捕捉已关闭。", "timestamp": _now()}
        _STATE["latest_capture"] = result
        return result
    if not top1:
        result = {"symbol": "", "fast_score": 0, "still_valid": False, "trigger_committee_precheck": False, "risk_fast_up": False, "reason": "暂无TOP1机会。", "timestamp": _now()}
        _STATE["latest_capture"] = result
        return result

    symbol = str(top1.get("symbol", "")).upper()
    score = _to_int(top1.get("final_opportunity_score", top1.get("opportunity_score")))
    professional_score = _to_int(top1.get("professional_trade_score", score))
    risk_score = _to_int(top1.get("risk_score"), 100)
    data_quality = str(top1.get("data_quality") or "good")
    quote_volume = _to_float(top1.get("quote_volume"))
    trigger = True
    reasons = []
    warnings = []
    if score < trigger_score:
        trigger = False
        reasons.append(f"机会评分 {score} 低于触发阈值 {trigger_score}。")
    if professional_score < 75:
        trigger = False
        reasons.append(f"专业交易分 {professional_score} 低于触发阈值 75。")
    if not top1.get("tradable_now"):
        trigger = False
        reasons.extend(str(reason) for reason in top1.get("trade_block_reasons", []) if reason)
    if data_quality == "poor":
        trigger = False
        reasons.append("数据质量 poor。")
    if risk_score >= 70:
        trigger = False
        reasons.append(f"风险评分 {risk_score} 已达到快速阻断区。")
    if quote_volume <= 0:
        warnings.append("成交额数据不足，流动性需完整复核。")
    switch_ok, switch_reason = _target_switch_allowed(top1)
    if not switch_ok:
        trigger = False
        warnings.append(switch_reason)
    else:
        _set_committee_target(top1)

    result = {
        "symbol": symbol,
        "fast_score": professional_score,
        "raw_opportunity_score": score,
        "still_valid": trigger,
        "trigger_committee_precheck": trigger,
        "risk_fast_up": risk_score >= 70,
        "reason": "；".join(reasons or ([] if trigger else warnings)) if (reasons or not trigger) else "TOP1评分达到80分并通过快速捕捉。",
        "warnings": warnings,
        "top1": top1,
        "next_full_review_seconds": full_review_seconds,
        "timestamp": _now(),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    _STATE["latest_capture"] = result
    _STATE["last_capture_at"] = time.time()
    _log_capture({"time": result["timestamp"], "event": "TOP1快速捕捉", "symbol": symbol, "score": professional_score, "result": "通过" if trigger else "未通过", "reason": result["reason"], "elapsed_ms": result["elapsed_ms"]})
    return result


def run_committee_fast_precheck(symbol: str, opportunity: dict[str, Any] | None = None) -> dict[str, Any]:
    started = time.monotonic()
    settings = get_fast_opportunity_settings()
    trigger_score = int(settings.get("OPPORTUNITY_TRIGGER_SCORE", OPPORTUNITY_TRIGGER_SCORE))
    enabled = bool(settings.get("ENABLE_FAST_COMMITTEE_PRECHECK", ENABLE_FAST_COMMITTEE_PRECHECK))
    opportunity = assess_trade_opportunity(opportunity or {})
    score = _to_int(opportunity.get("final_opportunity_score", opportunity.get("opportunity_score")))
    professional_score = _to_int(opportunity.get("professional_trade_score", score))
    risk_score = _to_int(opportunity.get("risk_score"), 100)
    data_quality = str(opportunity.get("data_quality") or "good")
    direction = str(opportunity.get("trade_direction") or _direction(opportunity))
    block_reasons: list[str] = []
    warnings: list[str] = []
    if not enabled:
        block_reasons.append("委员会快速预判已关闭。")
    if score < trigger_score:
        block_reasons.append(f"机会评分 {score} 未达到 {trigger_score}。")
    if professional_score < 75:
        block_reasons.append(f"专业交易分 {professional_score} 未达到 75。")
    if risk_score >= 70:
        block_reasons.append(f"风险评分 {risk_score} 偏高。")
    if data_quality == "poor":
        block_reasons.append("数据质量 poor。")
    if direction not in {"long", "short"}:
        warnings.append("方向不够明确，需要完整复核。")
    if not opportunity.get("tradable_now"):
        block_reasons.extend(str(reason) for reason in opportunity.get("trade_block_reasons", []) if reason)

    safety = "blocked" if block_reasons else "auto_candidate"
    allowed = not block_reasons
    result = {
        "allowed_candidate": allowed,
        "candidate_type": safety if allowed else "blocked",
        "fast_action": "进入候选" if allowed else "禁止",
        "block_reasons": block_reasons,
        "warnings": warnings,
        "symbol": str(symbol or "").upper(),
        "score": score,
        "professional_trade_score": professional_score,
        "risk_score": risk_score,
        "direction": direction,
        "entry_state": opportunity.get("entry_state"),
        "action_gate": opportunity.get("action_gate"),
        "tradable_now": bool(opportunity.get("tradable_now")),
        "market_regime": opportunity.get("market_regime"),
        "opportunity_class": opportunity.get("opportunity_class"),
        "timestamp": _now(),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    _STATE["latest_precheck"] = result
    _STATE["last_precheck_at"] = time.time()
    _log_precheck({"time": result["timestamp"], "event": "委员会快速预判", "symbol": result["symbol"], "score": professional_score, "result": "通过" if allowed else "阻止", "reason": "；".join(block_reasons or warnings or ["进入候选"]), "elapsed_ms": result["elapsed_ms"]})
    return result


def run_committee_top10_precheck(rankings: dict[str, list[dict[str, Any]]] | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """对机会榜前10执行轻量委员会判断，不调用外部AI，不生成真实订单。"""
    settings = get_fast_opportunity_settings()
    enabled = bool(settings.get("ENABLE_TOP10_COMMITTEE_PRECHECK", ENABLE_TOP10_COMMITTEE_PRECHECK))
    rows = collect_top10_opportunities(rankings, limit)
    results: list[dict[str, Any]] = []
    if not enabled:
        results = [
            {
                "rank": index,
                "symbol": str(row.get("symbol", "")).upper(),
                "allowed_candidate": False,
                "fast_action": "已关闭",
                "block_reasons": ["TOP10委员会快速判断已关闭。"],
                "warnings": [],
                "score": _to_int(row.get("final_opportunity_score", row.get("opportunity_score"))),
                "risk_score": _to_int(row.get("risk_score")),
                "direction": _direction(row),
                "opportunity": row,
                "timestamp": _now(),
            }
            for index, row in enumerate(rows, start=1)
        ]
        _STATE["latest_top10_precheck"] = results
        return results
    for index, row in enumerate(rows, start=1):
        result = run_committee_fast_precheck(str(row.get("symbol", "")), row)
        result["rank"] = index
        result["opportunity"] = row
        if index == 1:
            result["review_lane"] = "TOP1快速捕捉"
        elif index <= 5:
            result["review_lane"] = "TOP2-TOP5快速候选队列"
        else:
            result["review_lane"] = "TOP6-TOP10完整复核队列"
        result["review_status"] = "full_done" if result.get("allowed_candidate") else "blocked"
        result["deepseek_status"] = "外部AI待补充"
        result["gemini_status"] = "外部AI待补充"
        if not result.get("allowed_candidate") and result.get("risk_score", 0) < 70 and result.get("score", 0) >= 75:
            result["fast_action"] = "观察复核"
            result["review_status"] = "full_done"
        results.append(result)
    _STATE["latest_top10_precheck"] = results
    _append_json_log(
        "committee_top10_precheck_log.json",
        {
            "time": _now(),
            "event": "TOP10委员会快速判断",
            "count": len(results),
            "allowed": sum(1 for item in results if item.get("allowed_candidate")),
            "blocked": sum(1 for item in results if not item.get("allowed_candidate")),
            "symbols": [item.get("symbol") for item in results],
        },
    )
    return results


def _build_candidate_signal(opportunity: dict[str, Any], precheck: dict[str, Any], opportunity_id: str) -> dict[str, Any]:
    price = _to_float(opportunity.get("current_price") or opportunity.get("last_price"))
    score = _to_int(opportunity.get("final_opportunity_score", opportunity.get("opportunity_score")))
    professional_score = _to_int(opportunity.get("professional_trade_score", score))
    direction = precheck.get("direction") or _direction(opportunity)
    rank = int(precheck.get("rank", 0) or 0)
    now_text = _now()
    return {
        "symbol": str(opportunity.get("symbol", "")).upper(),
        "current_price": price,
        "entry_price": price,
        "direction": direction,
        "action": "轻仓试多" if direction == "long" else "轻仓试空",
        "final_action": "轻仓试多" if direction == "long" else "轻仓试空",
        "confidence": professional_score,
        "risk_score": _to_int(opportunity.get("risk_score")),
        "opportunity_score": score,
        "raw_opportunity_score": _to_int(opportunity.get("raw_opportunity_score"), score),
        "final_opportunity_score": score,
        "professional_trade_score": professional_score,
        "entry_state": opportunity.get("entry_state"),
        "action_gate": opportunity.get("action_gate"),
        "tradable_now": bool(opportunity.get("tradable_now")),
        "market_regime": opportunity.get("market_regime"),
        "market_alignment_score": opportunity.get("market_alignment_score"),
        "direction_gap": opportunity.get("direction_gap"),
        "trade_block_reasons": opportunity.get("trade_block_reasons", []),
        "risk_flags": opportunity.get("risk_flags", []),
        "risk_penalty": _to_int(opportunity.get("risk_penalty")),
        "score_cap": _to_int(opportunity.get("score_cap"), 100),
        "opportunity_status": opportunity.get("opportunity_status"),
        "risk_breakdown": opportunity.get("risk_breakdown"),
        "opportunity_breakdown": opportunity.get("opportunity_breakdown"),
        "system_suggested_amount": 5,
        "risk_max_amount": 10,
        "order_type": "LIMIT",
        "mode": "LIVE_MANUAL",
        "source": str(precheck.get("source") or "多机会快速预判"),
        "opportunity_id": opportunity_id,
        "source_opportunity_id": opportunity_id,
        "source_board_rank": rank,
        "source_committee_result": precheck.get("fast_action"),
        "source_resonance_level": "中等共振" if precheck.get("allowed_candidate") else "无共振",
        "source_review_time": now_text,
        "candidate_status": "自动候选",
        "entry_snapshot": {
            "entry_price": price,
            "entry_time": now_text,
            "entry_rank": rank,
            "entry_score": professional_score,
            "entry_risk_score": _to_int(opportunity.get("risk_score")),
            "entry_structure": opportunity.get("current_market_state"),
            "entry_reason": opportunity.get("opportunity_status") or opportunity.get("advice"),
        },
        "live_snapshot": {
            "live_price": price,
            "live_change_since_entry": 0,
            "live_opportunity_score": professional_score,
            "live_risk_score": _to_int(opportunity.get("risk_score")),
            "live_committee_status": precheck.get("fast_action"),
            "live_candidate_status": "自动候选",
            "live_updated_at": now_text,
            "data_age_seconds": 0,
        },
        "summary": "专业交易预审通过，仅进入候选；真实交易仍需完整委员会与风控确认。",
        "external_ai": {"deepseek": {}, "gemini": {}},
        "committee_snapshot": {"fast_precheck": precheck, "opportunity": opportunity},
    }


def maybe_create_multi_opportunity_candidates(prechecks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为TOP10通过快速预判的机会生成自动候选；真实执行仍需完整风控。"""
    settings = get_fast_opportunity_settings()
    review_top_n = int(settings.get("COMMITTEE_REVIEW_TOP_N", 5) or 5)
    results: list[dict[str, Any]] = []
    for item in prechecks:
        rank = int(item.get("rank", 0) or 0)
        opportunity = item.get("opportunity") or {}
        symbol = str(item.get("symbol") or opportunity.get("symbol") or "").upper()
        score = _to_int(item.get("score", opportunity.get("final_opportunity_score", opportunity.get("opportunity_score"))))
        professional_score = _to_int(item.get("professional_trade_score", opportunity.get("professional_trade_score", score)))
        risk_score = _to_int(item.get("risk_score", opportunity.get("risk_score")))
        if rank > review_top_n:
            results.append({"symbol": symbol, "rank": rank, "review_status": "expired", "candidate_created": False, "block_reason": "不在当前TOP10完整复核范围。", "last_review_time": _now()})
            continue
        if not item.get("allowed_candidate"):
            opportunity_id = build_opportunity_id(opportunity)
            reason = "；".join(item.get("block_reasons") or ["快速预判未通过。"])
            _record_symbol_review(symbol, reason, opportunity_id, fast_checked=True, blocked=True)
            lifecycle = _record_opportunity_reject(symbol, reason, opportunity_id)
            if lifecycle.get("status") == "removed":
                continue
            results.append({"symbol": symbol, "rank": rank, "review_status": "blocked", "candidate_created": False, "block_reason": reason, "opportunity_id": opportunity_id, "reject_count": lifecycle.get("reject_count", 0), "opportunity_round": lifecycle.get("opportunity_round", 1), "status": lifecycle.get("status", "rejected"), "cooldown_until": lifecycle.get("cooldown_until", 0), "last_review_time": _now()})
            continue
        if score < int(settings.get("OPPORTUNITY_TRIGGER_SCORE", OPPORTUNITY_TRIGGER_SCORE) or 80) or professional_score < 75 or risk_score >= 70 or not item.get("tradable_now"):
            opportunity_id = build_opportunity_id(opportunity)
            reason = "；".join(item.get("block_reasons") or opportunity.get("trade_block_reasons") or ["评分、风险或入场状态未满足候选规则。"])
            _record_symbol_review(symbol, reason, opportunity_id, fast_checked=True, blocked=True)
            lifecycle = _record_opportunity_reject(symbol, reason, opportunity_id)
            if lifecycle.get("status") == "removed":
                continue
            results.append({"symbol": symbol, "rank": rank, "review_status": "blocked", "candidate_created": False, "block_reason": reason, "opportunity_id": opportunity_id, "reject_count": lifecycle.get("reject_count", 0), "opportunity_round": lifecycle.get("opportunity_round", 1), "status": lifecycle.get("status", "rejected"), "cooldown_until": lifecycle.get("cooldown_until", 0), "last_review_time": _now()})
            continue
        opportunity_id = build_opportunity_id(opportunity)
        review_count = _mark_opportunity_review(opportunity_id)
        duplicate, remaining, review_count = _duplicate_status(opportunity_id)
        if duplicate:
            lifecycle = _record_symbol_review(symbol, "同一机会冷却中，未生成候选。", opportunity_id, fast_checked=True, blocked=False)
            if lifecycle.get("status") == "removed":
                continue
            results.append({"symbol": symbol, "rank": rank, "review_status": "fast_checked", "candidate_created": False, "block_reason": f"同一机会冷却中，剩余 {remaining:.0f} 秒。审查 {review_count} 次。", "opportunity_id": opportunity_id, "review_count": lifecycle.get("review_count", review_count), "reject_count": lifecycle.get("reject_count", 0), "opportunity_round": lifecycle.get("opportunity_round", 1), "status": lifecycle.get("status", "fast_checked"), "cooldown_until": lifecycle.get("cooldown_until", 0), "removed_reason": lifecycle.get("removed_reason", ""), "last_review_time": _now()})
            continue
        item["source"] = "TOP1三秒快速捕捉" if rank == 1 else f"TOP{rank}多机会快速预判"
        signal = _build_candidate_signal(opportunity, item, opportunity_id)
        candidate_id = f"auto_cand_{int(time.time())}_{rank}_{symbol}"
        lifecycle = _record_opportunity_approved(symbol)
        _mark_opportunity_seen(opportunity_id)
        result = {
            "symbol": symbol,
            "rank": rank,
            "score": score,
            "professional_trade_score": professional_score,
            "risk_score": risk_score,
            "review_status": "full_done",
            "candidate_created": True,
            "candidate_id": candidate_id,
            "candidate_payload": signal,
            "opportunity_id": opportunity_id,
            "review_count": review_count,
            "reject_count": lifecycle.get("reject_count", 0),
            "opportunity_round": lifecycle.get("opportunity_round", 1),
            "status": lifecycle.get("status", "approved"),
            "cooldown_until": lifecycle.get("cooldown_until", 0),
            "block_reason": "",
            "last_review_time": _now(),
        }
        results.append(result)
        _log_candidate({"time": result["last_review_time"], "event": "多机会生成自动候选", "symbol": symbol, "score": score, "result": "成功", "reason": f"TOP{rank}快速预判通过，进入自动候选。", "opportunity_id": opportunity_id})
    _STATE["latest_multi_review"] = results
    _append_json_log(
        "multi_opportunity_review_log.json",
        {
            "time": _now(),
            "event": "TOP1-TOP5多机会候选评审",
            "review_top_n": review_top_n,
            "results": results,
            "created_count": sum(1 for item in results if item.get("candidate_created")),
        },
    )
    return results


def maybe_create_fast_candidate(capture: dict[str, Any], precheck: dict[str, Any]) -> dict[str, Any]:
    opportunity = capture.get("top1") or {}
    opportunity_id = build_opportunity_id(opportunity)
    review_count = _mark_opportunity_review(opportunity_id)
    duplicate, remaining, review_count = _duplicate_status(opportunity_id)
    symbol = str(opportunity.get("symbol", "")).upper()
    if duplicate:
        lifecycle = _record_symbol_review(symbol, "同一机会冷却中，未生成候选。", opportunity_id, fast_checked=True, blocked=False)
        result = {"ok": False, "symbol": symbol, "opportunity_id": opportunity_id, "review_count": review_count, "status": "duplicate", "message": f"同一机会冷却中，剩余 {remaining:.0f} 秒。审查 {review_count} 次。", "timestamp": _now()}
        result.update({"lifecycle_status": lifecycle.get("status"), "removed_reason": lifecycle.get("removed_reason", "")})
        _STATE["latest_candidate"] = result
        _log_candidate({"time": result["timestamp"], "event": "候选去重", "symbol": symbol, "score": opportunity.get("opportunity_score"), "result": "重复", "reason": result["message"], "opportunity_id": opportunity_id})
        return result
    if not capture.get("trigger_committee_precheck") or not precheck.get("allowed_candidate"):
        _record_symbol_review(symbol, "快速捕捉或快速预判未通过。", opportunity_id, fast_checked=True, blocked=True)
        result = {"ok": False, "symbol": symbol, "opportunity_id": opportunity_id, "status": "blocked", "message": "快速捕捉或快速预判未通过。", "timestamp": _now()}
        _STATE["latest_candidate"] = result
        return result

    signal = _build_candidate_signal(opportunity, precheck, opportunity_id)
    _mark_opportunity_seen(opportunity_id)
    result = {
        "ok": True,
        "symbol": symbol,
        "opportunity_id": opportunity_id,
        "status": "auto_candidate_created",
        "candidate_id": f"auto_cand_{int(time.time())}_top1_{symbol}",
        "candidate_payload": signal,
        "message": "已生成自动交易候选；真实执行仍需完整风控、交易所规则和自动交易开关通过。",
        "timestamp": _now(),
    }
    _STATE["latest_candidate"] = result
    _log_candidate({"time": result["timestamp"], "event": "生成自动候选", "symbol": symbol, "score": opportunity.get("opportunity_score"), "result": "成功", "reason": result["message"], "opportunity_id": opportunity_id})
    return result


def process_top1_fast_opportunity(rankings: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    capture = fast_capture_top1_opportunity(rankings)
    top10_precheck = run_committee_top10_precheck(rankings)
    multi_review = maybe_create_multi_opportunity_candidates(top10_precheck)
    top10_precheck = _drop_removed_rows(_STATE.get("latest_top10_precheck", top10_precheck) or [])
    multi_review = _drop_removed_rows(_STATE.get("latest_multi_review", multi_review) or [])
    _STATE["latest_top10_precheck"] = top10_precheck
    _STATE["latest_multi_review"] = multi_review
    if not capture.get("trigger_committee_precheck"):
        return {"capture": capture, "top10_precheck": top10_precheck, "multi_review": multi_review, "precheck": {}, "candidate": {}, "status": "capture_blocked"}
    opportunity = capture.get("top1") or {}
    precheck = run_committee_fast_precheck(str(opportunity.get("symbol", "")), opportunity)
    candidate = maybe_create_fast_candidate(capture, precheck)
    return {"capture": capture, "top10_precheck": top10_precheck, "multi_review": multi_review, "precheck": precheck, "candidate": candidate, "status": candidate.get("status", "checked")}


def get_fast_opportunity_status() -> dict[str, Any]:
    _cleanup_lifecycle()
    _STATE["latest_top10_precheck"] = _drop_removed_rows(_STATE.get("latest_top10_precheck", []) or [])
    _STATE["latest_multi_review"] = _drop_removed_rows(_STATE.get("latest_multi_review", []) or [])
    return {
        "settings": get_fast_opportunity_settings(),
        "current_target": _STATE.get("current_target", ""),
        "target_score": _STATE.get("target_score", 0),
        "stable_symbol": _STATE.get("stable_symbol", ""),
        "stable_count": _STATE.get("stable_count", 0),
        "latest_capture": _STATE.get("latest_capture", {}),
        "latest_precheck": _STATE.get("latest_precheck", {}),
        "latest_top10_precheck": _STATE.get("latest_top10_precheck", []),
        "latest_multi_review": _STATE.get("latest_multi_review", []),
        "latest_candidate": _STATE.get("latest_candidate", {}),
        "opportunity_lifecycle": _STATE.get("opportunity_lifecycle", {}),
    }

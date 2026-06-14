"""Validation tools for market cognition JSONL snapshots."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT_DIR = ROOT / "data" / "market_cognition_snapshots"
DEFAULT_REPORT_PATH = ROOT / "reports" / "cognition_snapshot_validation_report.json"
STATE_CODE_RE = re.compile(r"^T[1-5]-C[1-5]-S[1-5]-B[1-5]-R[1-5]-D[1-5]$")
EXPECTED_PROBABILITY_TYPE = "rule_based_v1"

REQUIRED_BASE_FIELDS = (
    "symbol",
    "timestamp_utc",
    "app_version",
    "schema_version",
    "state_language_version",
    "cognition_model_version",
    "weight_config_version",
    "similarity_config_version",
)
REQUIRED_VECTOR_FIELDS = (
    "trend_direction",
    "trend_strength",
    "trend_quality_score",
    "capital_score",
    "capital_direction",
    "structure_score",
    "behavior_score",
    "risk_score",
    "risk_safe_score",
    "demand_score",
    "buy_demand_score",
    "sell_supply_score",
    "net_demand_score",
    "urgency_score",
    "sustainability_score",
    "trap_risk_score",
    "confidence",
    "data_integrity_score",
)
REQUIRED_COGNITION_FIELDS = (
    "market_cognition_score",
    "market_cognition_label",
    "main_conflict",
    "attack_point",
    "defense_point",
    "failure_point",
    "cognition_summary",
)
OPTIONAL_COMMITTEE_FIELDS = (
    "committee_final_action",
    "risk_judge_verdict",
    "position_plan_summary",
    "execution_plan_summary",
)
FUTURE_LABEL_FIELDS = (
    "future_30m_return",
    "future_30m_mfe",
    "future_30m_mae",
    "future_30m_first_tp_hit",
    "future_30m_first_sl_hit",
    "future_60m_return",
    "future_60m_mfe",
    "future_60m_mae",
    "future_60m_first_tp_hit",
    "future_60m_first_sl_hit",
)
RANGE_0_100_FIELDS = (
    "trend_strength",
    "trend_quality_score",
    "capital_score",
    "structure_score",
    "behavior_score",
    "risk_score",
    "risk_safe_score",
    "demand_score",
    "buy_demand_score",
    "sell_supply_score",
    "urgency_score",
    "sustainability_score",
    "trap_risk_score",
    "confidence",
    "data_integrity_score",
    "market_cognition_score",
)
SENSITIVE_KEY_PARTS = ("api_key", "secret", "password", "token")
RAW_LARGE_KEYS = ("orderbook", "raw_orderbook")


def _resolve_path(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _vector_value(snapshot: dict[str, Any], key: str) -> Any:
    vector = snapshot.get("state_vector") if isinstance(snapshot.get("state_vector"), dict) else {}
    if key in vector:
        return vector.get(key)
    return snapshot.get(key)


def _add_counter(counter: Counter[str], values: list[str]) -> None:
    for value in values:
        counter[str(value)] += 1


def _contains_blocked_key(value: Any, blocked: tuple[str, ...]) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in blocked):
                return True
            if _contains_blocked_key(item, blocked):
                return True
    elif isinstance(value, list):
        return any(_contains_blocked_key(item, blocked) for item in value)
    return False


def _contains_large_object_key(value: Any, blocked: tuple[str, ...]) -> bool:
    blocked_set = {item.lower() for item in blocked}
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in blocked_set and isinstance(item, (dict, list)):
                return True
            if _contains_large_object_key(item, blocked):
                return True
    elif isinstance(value, list):
        return any(_contains_large_object_key(item, blocked) for item in value)
    return False


def _timestamp_is_utc(timestamp: Any) -> bool:
    text = str(timestamp or "")
    if text.endswith("Z") or text.endswith("+00:00"):
        return True
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def _path_probability(path: dict[str, Any], name: str) -> Any:
    long_key = f"{name}_probability"
    if long_key in path:
        return path.get(long_key)
    return path.get(name)


def _validate_path(path: Any, name: str, errors: list[str], warnings: list[str]) -> bool:
    if not isinstance(path, dict):
        errors.append(f"{name} 不是对象")
        return False
    ok = True
    values: list[float] = []
    for key in ("up", "sideways", "down"):
        value = _to_float(_path_probability(path, key))
        if value is None:
            errors.append(f"{name} 缺少 {key}_probability")
            ok = False
            continue
        if value < 0 or value > 100:
            errors.append(f"{name}.{key}_probability 超出0~100")
            ok = False
        values.append(value)
    if not path.get("reason"):
        errors.append(f"{name} 缺少 reason")
        ok = False
    if len(values) == 3:
        delta = abs(sum(values) - 100)
        if delta > 5:
            errors.append(f"{name} 概率合计偏离100超过5，当前{sum(values):.2f}")
            ok = False
        elif delta > 2:
            warnings.append(f"{name} 概率合计偏离100超过2，当前{sum(values):.2f}")
    return ok


def validate_cognition_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate one market cognition snapshot for 9.2.2 sample compatibility."""
    errors: list[str] = []
    warnings: list[str] = []
    missing_fields: list[str] = []
    range_errors: list[str] = []

    if not isinstance(snapshot, dict):
        return {
            "valid": False,
            "quality_score": 0,
            "errors": ["snapshot 不是对象"],
            "warnings": [],
            "missing_fields": [],
            "range_errors": [],
            "schema_version_ok": False,
            "state_code_ok": False,
            "state_vector_ok": False,
            "experience_sample_compatible": False,
        }

    for field in REQUIRED_BASE_FIELDS:
        if field not in snapshot:
            missing_fields.append(field)
    for field in ("state_code", "state_vector", "path_30m", "path_60m", "probability_type"):
        if field not in snapshot:
            missing_fields.append(field)
    for field in REQUIRED_COGNITION_FIELDS:
        if field not in snapshot:
            missing_fields.append(field)

    schema_version_ok = bool(snapshot.get("schema_version"))
    if not schema_version_ok:
        errors.append("schema_version 缺失")

    state_code = str(snapshot.get("state_code") or "")
    state_code_ok = bool(STATE_CODE_RE.match(state_code))
    if not state_code_ok:
        errors.append(f"state_code 格式错误: {state_code or '-'}")

    vector = snapshot.get("state_vector")
    state_vector_ok = isinstance(vector, dict)
    if not state_vector_ok:
        errors.append("state_vector 缺失或不是对象")
    else:
        for field in REQUIRED_VECTOR_FIELDS:
            if field not in vector:
                missing_fields.append(f"state_vector.{field}")
                state_vector_ok = False

    for field in OPTIONAL_COMMITTEE_FIELDS:
        if field not in snapshot:
            warnings.append(f"委员会字段缺失: {field}")
    for field in FUTURE_LABEL_FIELDS:
        if field not in snapshot:
            warnings.append(f"未来标签字段缺失: {field}")

    for field in RANGE_0_100_FIELDS:
        value = _to_float(_vector_value(snapshot, field))
        if value is None:
            range_errors.append(f"{field} 不是数值")
            continue
        if value < 0 or value > 100:
            range_errors.append(f"{field} 超出0~100: {value}")
    net = _to_float(_vector_value(snapshot, "net_demand_score"))
    if net is None:
        range_errors.append("net_demand_score 不是数值")
    elif net < -100 or net > 100:
        range_errors.append(f"net_demand_score 超出-100~100: {net}")

    risk = _to_float(_vector_value(snapshot, "risk_score"))
    risk_safe = _to_float(_vector_value(snapshot, "risk_safe_score"))
    if risk is not None and risk_safe is not None:
        diff = abs(risk_safe - (100 - risk))
        if diff > 5:
            errors.append(f"risk_safe_score 与 100-risk_score 偏差超过5: {diff:.2f}")
        elif diff > 1:
            warnings.append(f"risk_safe_score 与 100-risk_score 偏差超过1: {diff:.2f}")

    buy = _to_float(_vector_value(snapshot, "buy_demand_score"))
    sell = _to_float(_vector_value(snapshot, "sell_supply_score"))
    if buy is not None and sell is not None and net is not None:
        diff = abs(net - (buy - sell))
        if diff > 5:
            errors.append(f"net_demand_score 与 buy-sell 偏差超过5: {diff:.2f}")
        elif diff > 1:
            warnings.append(f"net_demand_score 与 buy-sell 偏差超过1: {diff:.2f}")
        demand = _to_float(_vector_value(snapshot, "demand_score"))
        if demand is not None:
            expected = _clamp(50 + net / 2)
            if abs(demand - expected) > 5:
                warnings.append(f"demand_score 与 50+net/2 偏差较大: {abs(demand - expected):.2f}")

    path_30m_ok = _validate_path(snapshot.get("path_30m"), "path_30m", errors, warnings)
    path_60m_ok = _validate_path(snapshot.get("path_60m"), "path_60m", errors, warnings)
    if snapshot.get("probability_type") != EXPECTED_PROBABILITY_TYPE:
        warnings.append(f"probability_type 不是 {EXPECTED_PROBABILITY_TYPE}")

    data_integrity = _to_float(_vector_value(snapshot, "data_integrity_score"))
    confidence = _to_float(_vector_value(snapshot, "confidence"))
    if data_integrity is not None and data_integrity < 40:
        warnings.append("数据完整度较低，不建议用于高置信经验库。")
    if not _timestamp_is_utc(snapshot.get("timestamp_utc")):
        warnings.append("timestamp_utc 不是明确UTC时间。")
    if _contains_blocked_key(snapshot, SENSITIVE_KEY_PARTS):
        errors.append("快照包含敏感字段名。")
    if _contains_large_object_key(snapshot, RAW_LARGE_KEYS):
        errors.append("快照包含完整盘口原始大对象字段。")

    valid = not errors and not range_errors and not missing_fields
    versions_ok = all(snapshot.get(field) for field in REQUIRED_BASE_FIELDS if field.endswith("_version"))
    demand_fields_ok = all(_vector_value(snapshot, field) is not None for field in ("demand_score", "buy_demand_score", "sell_supply_score", "net_demand_score"))
    risk_fields_ok = risk is not None and risk_safe is not None
    experience_sample_compatible = bool(
        state_code_ok
        and state_vector_ok
        and versions_ok
        and demand_fields_ok
        and risk_fields_ok
        and path_30m_ok
        and path_60m_ok
        and _timestamp_is_utc(snapshot.get("timestamp_utc"))
        and not _contains_blocked_key(snapshot, SENSITIVE_KEY_PARTS)
        and not _contains_large_object_key(snapshot, RAW_LARGE_KEYS)
    )

    quality_score = 100
    quality_score -= len(errors) * 12
    quality_score -= len(range_errors) * 8
    quality_score -= len(missing_fields) * 2
    quality_score -= len(warnings) * 3
    if not state_code_ok:
        quality_score -= 20
    if not state_vector_ok:
        quality_score -= 20
    if data_integrity is not None and data_integrity < 40:
        quality_score -= 15
    if confidence is not None and confidence < 40:
        quality_score -= 8
    quality_score = int(_clamp(quality_score))

    return {
        "valid": valid,
        "quality_score": quality_score,
        "errors": errors,
        "warnings": warnings,
        "missing_fields": missing_fields,
        "range_errors": range_errors,
        "schema_version_ok": schema_version_ok,
        "state_code_ok": state_code_ok,
        "state_vector_ok": state_vector_ok,
        "experience_sample_compatible": experience_sample_compatible,
    }


def validate_snapshot_file(path: str) -> dict[str, Any]:
    """Validate one JSONL snapshot file."""
    file_path = _resolve_path(path)
    total = valid_count = invalid_count = warning_count = compatible_count = 0
    quality_sum = integrity_sum = confidence_sum = 0.0
    integrity_count = confidence_count = 0
    state_codes: Counter[str] = Counter()
    missing_counts: Counter[str] = Counter()
    error_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    symbols: set[str] = set()
    latest_timestamp = ""
    latest_state_code = ""

    if not file_path.exists():
        return {
            "file": str(file_path),
            "total": 0,
            "valid": 0,
            "invalid": 0,
            "warnings": 0,
            "avg_quality_score": 0,
            "avg_data_integrity_score": 0,
            "avg_confidence": 0,
            "state_code_distribution": {},
            "missing_field_counts": {},
            "error_counts": {"file_not_found": 1},
            "warning_counts": {},
            "latest_timestamp": "",
            "latest_state_code": "",
            "symbols": [],
            "experience_sample_compatible": 0,
        }

    with file_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            total += 1
            try:
                snapshot = json.loads(raw)
            except json.JSONDecodeError:
                invalid_count += 1
                error_counts[f"line_{line_no}_json_parse_failed"] += 1
                continue
            result = validate_cognition_snapshot(snapshot)
            if result.get("valid"):
                valid_count += 1
            else:
                invalid_count += 1
            warning_count += len(result.get("warnings") or [])
            quality_sum += float(result.get("quality_score") or 0)
            if result.get("experience_sample_compatible"):
                compatible_count += 1
            _add_counter(missing_counts, result.get("missing_fields") or [])
            _add_counter(error_counts, (result.get("errors") or []) + (result.get("range_errors") or []))
            _add_counter(warning_counts, result.get("warnings") or [])
            state_code = str(snapshot.get("state_code") or "")
            if state_code:
                state_codes[state_code] += 1
            symbol = str(snapshot.get("symbol") or "").upper()
            if symbol:
                symbols.add(symbol)
            timestamp = str(snapshot.get("timestamp_utc") or "")
            if timestamp >= latest_timestamp:
                latest_timestamp = timestamp
                latest_state_code = state_code
            integrity = _to_float(_vector_value(snapshot, "data_integrity_score"))
            confidence = _to_float(_vector_value(snapshot, "confidence"))
            if integrity is not None:
                integrity_sum += integrity
                integrity_count += 1
            if confidence is not None:
                confidence_sum += confidence
                confidence_count += 1

    return {
        "file": str(file_path),
        "total": total,
        "valid": valid_count,
        "invalid": invalid_count,
        "warnings": warning_count,
        "avg_quality_score": round(quality_sum / total, 2) if total else 0,
        "avg_data_integrity_score": round(integrity_sum / integrity_count, 2) if integrity_count else 0,
        "avg_confidence": round(confidence_sum / confidence_count, 2) if confidence_count else 0,
        "state_code_distribution": dict(state_codes.most_common()),
        "missing_field_counts": dict(missing_counts.most_common()),
        "error_counts": dict(error_counts.most_common()),
        "warning_counts": dict(warning_counts.most_common()),
        "latest_timestamp": latest_timestamp,
        "latest_state_code": latest_state_code,
        "symbols": sorted(symbols),
        "experience_sample_compatible": compatible_count,
    }


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def build_snapshot_validation_report(snapshot_dir: str = "data/market_cognition_snapshots") -> dict[str, Any]:
    """Build an aggregate validation report for all market cognition JSONL files."""
    directory = _resolve_path(snapshot_dir)
    files = sorted(directory.glob("market_cognition_*.jsonl")) if directory.exists() else []
    file_results = [validate_snapshot_file(str(path)) for path in files]

    total_snapshots = sum(item.get("total", 0) for item in file_results)
    valid_snapshots = sum(item.get("valid", 0) for item in file_results)
    invalid_snapshots = sum(item.get("invalid", 0) for item in file_results)
    warning_count = sum(item.get("warnings", 0) for item in file_results)
    compatible = sum(item.get("experience_sample_compatible", 0) for item in file_results)
    size_bytes = _directory_size(directory)
    size_mb = round(size_bytes / 1024 / 1024, 2)
    disk_risk_status = "OK"
    if size_mb > 1024:
        disk_risk_status = "DANGER"
    elif size_mb >= 100:
        disk_risk_status = "WARNING"

    quality_sum = sum(float(item.get("avg_quality_score", 0) or 0) * int(item.get("total", 0) or 0) for item in file_results)
    integrity_sum = sum(float(item.get("avg_data_integrity_score", 0) or 0) * int(item.get("total", 0) or 0) for item in file_results)
    confidence_sum = sum(float(item.get("avg_confidence", 0) or 0) * int(item.get("total", 0) or 0) for item in file_results)

    states: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    warnings: Counter[str] = Counter()
    symbols: set[str] = set()
    latest_snapshot_time = ""
    latest_state_code = ""
    for result in file_results:
        states.update(result.get("state_code_distribution") or {})
        missing.update(result.get("missing_field_counts") or {})
        errors.update(result.get("error_counts") or {})
        warnings.update(result.get("warning_counts") or {})
        symbols.update(result.get("symbols") or [])
        if str(result.get("latest_timestamp") or "") >= latest_snapshot_time:
            latest_snapshot_time = str(result.get("latest_timestamp") or "")
            latest_state_code = str(result.get("latest_state_code") or "")

    largest_file = ""
    largest_size = 0
    for path in files:
        size = path.stat().st_size
        if size > largest_size:
            largest_size = size
            largest_file = str(path)

    today_name = datetime.now(timezone.utc).strftime("market_cognition_%Y%m%d.jsonl")
    today_snapshots = sum(item.get("total", 0) for item in file_results if Path(str(item.get("file", ""))).name == today_name)

    if total_snapshots <= 0:
        recommendation = "暂无市场认知快照，请等待系统运行生成。"
    elif invalid_snapshots > 0:
        recommendation = "存在无效快照，建议先修复缺失字段或格式问题后再进入9.3样本工厂准备。"
    elif compatible < total_snapshots:
        recommendation = "部分快照尚不兼容未来经验库样本，建议补齐state_vector、版本号和路径概率字段。"
    elif disk_risk_status == "DANGER":
        recommendation = "快照目录过大，建议检查写入频率和保留天数。"
    elif (integrity_sum / total_snapshots if total_snapshots else 0) < 50:
        recommendation = "数据完整度偏低，建议先检查行情、盘口和大单数据源。"
    else:
        recommendation = "快照质量良好，可进入9.3样本工厂准备。"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_dir": str(directory),
        "total_files": len(files),
        "total_snapshots": total_snapshots,
        "today_snapshots": today_snapshots,
        "valid_snapshots": valid_snapshots,
        "invalid_snapshots": invalid_snapshots,
        "warning_count": warning_count,
        "valid_ratio": round(valid_snapshots / total_snapshots * 100, 2) if total_snapshots else 0,
        "avg_quality_score": round(quality_sum / total_snapshots, 2) if total_snapshots else 0,
        "avg_data_integrity_score": round(integrity_sum / total_snapshots, 2) if total_snapshots else 0,
        "avg_confidence": round(confidence_sum / total_snapshots, 2) if total_snapshots else 0,
        "snapshot_dir_size_mb": size_mb,
        "largest_file": largest_file,
        "largest_file_size_mb": round(largest_size / 1024 / 1024, 2) if largest_size else 0,
        "latest_snapshot_time": latest_snapshot_time,
        "latest_state_code": latest_state_code,
        "symbols_count": len(symbols),
        "symbols": sorted(symbols),
        "state_code_distribution": dict(states.most_common()),
        "missing_field_top10": dict(missing.most_common(10)),
        "error_top10": dict(errors.most_common(10)),
        "warning_top10": dict(warnings.most_common(10)),
        "experience_sample_compatible_ratio": round(compatible / total_snapshots * 100, 2) if total_snapshots else 0,
        "experience_sample_compatible": compatible,
        "disk_risk_status": disk_risk_status,
        "recommendation": recommendation,
        "file_results": file_results,
    }


def save_snapshot_validation_report(
    report: dict[str, Any],
    path: str = "reports/cognition_snapshot_validation_report.json",
) -> None:
    """Persist the validation report as JSON."""
    report_path = _resolve_path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

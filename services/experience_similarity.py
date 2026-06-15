"""Similarity helpers for experience-library state matching."""

from __future__ import annotations

import json
import math
import re
from typing import Any


STATE_CODE_WEIGHTS = {
    "T": 0.20,
    "C": 0.12,
    "S": 0.12,
    "B": 0.14,
    "R": 0.20,
    "D": 0.22,
}

STATE_VECTOR_WEIGHTS = {
    "demand_score": 0.18,
    "net_demand_score": 0.12,
    "trend_quality_score": 0.14,
    "trend_strength": 0.08,
    "capital_score": 0.12,
    "behavior_score": 0.12,
    "structure_score": 0.08,
    "risk_score": 0.10,
    "trap_risk_score": 0.06,
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def _clamp_100(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(100.0, _to_float(value, default)))


def _parse_state_code(value: Any) -> dict[str, int]:
    text = str(value or "").upper()
    return {key: int(number) for key, number in re.findall(r"([TCSBRD])\s*(\d+)", text)}


def _fallback_state_parts(value: Any) -> dict[str, int]:
    numbers = [int(item) for item in re.findall(r"\d+", str(value or ""))]
    return {key: numbers[index] for index, key in enumerate(STATE_CODE_WEIGHTS) if index < len(numbers)}


def state_code_distance(current: Any, candidate: Any) -> float:
    """Weighted T-C-S-B-R-D absolute distance.

    The returned value is in state-code digit units; one D-step contributes
    0.22, one R-step contributes 0.20, and so on.
    """

    current_parts = _parse_state_code(current) or _fallback_state_parts(current)
    candidate_parts = _parse_state_code(candidate) or _fallback_state_parts(candidate)
    if not current_parts or not candidate_parts:
        return 999.0
    distance = 0.0
    for key, weight in STATE_CODE_WEIGHTS.items():
        if key not in current_parts or key not in candidate_parts:
            return 999.0
        distance += abs(current_parts[key] - candidate_parts[key]) * weight
    return round(distance, 6)


def state_code_similarity(current: Any, candidate: Any) -> float:
    if str(current or "") == str(candidate or "") and str(current or ""):
        return 100.0
    distance = state_code_distance(current, candidate)
    if distance >= 999.0:
        return 0.0
    return round(max(0.0, 100.0 - distance * 20.0), 2)


def parse_state_vector_center(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            raw = json.loads(value)
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}
    return {}


def state_vector_similarity(query_vector: dict[str, Any], center_value: Any) -> dict[str, Any]:
    """Return weighted vector similarity and confidence for comparable fields."""

    center = parse_state_vector_center(center_value)
    if not isinstance(query_vector, dict) or not center:
        return {
            "similarity": 0.0,
            "similarity_confidence": 0.0,
            "weighted_distance": 999.0,
            "matched_fields": [],
            "missing_fields": list(STATE_VECTOR_WEIGHTS),
            "warning": "state_vector_center 缺失或无法解析。",
        }
    total_weight = 0.0
    weighted_distance = 0.0
    matched_fields: list[str] = []
    missing_fields: list[str] = []
    for key, weight in STATE_VECTOR_WEIGHTS.items():
        if key not in query_vector or key not in center:
            missing_fields.append(key)
            continue
        weighted_distance += abs(_to_float(query_vector.get(key), 0) - _to_float(center.get(key), 0)) * weight
        total_weight += weight
        matched_fields.append(key)
    if total_weight <= 0:
        return {
            "similarity": 0.0,
            "similarity_confidence": 0.0,
            "weighted_distance": 999.0,
            "matched_fields": [],
            "missing_fields": missing_fields,
            "warning": "状态向量可比字段不足。",
        }
    normalized_distance = weighted_distance / total_weight
    return {
        "similarity": round(max(0.0, 100.0 - normalized_distance), 2),
        "similarity_confidence": round(_clamp_100(total_weight * 100.0), 2),
        "weighted_distance": round(normalized_distance, 6),
        "matched_fields": matched_fields,
        "missing_fields": missing_fields,
        "warning": "" if total_weight >= 0.70 else "状态向量字段缺失，向量相似度置信度已降低。",
    }

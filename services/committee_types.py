"""Committee result helpers for AI_MODEL 9.1.

The structures are plain dictionaries on purpose.  The rest of the app already
passes committee payloads through JSON files, Streamlit session state and audit
logs, so keeping the boundary JSON-native avoids migration risk.
"""

from __future__ import annotations

from typing import Any


VOTE_SUPPORT = "SUPPORT"
VOTE_CAUTIOUS_SUPPORT = "CAUTIOUS_SUPPORT"
VOTE_OPPOSE = "OPPOSE"
VOTE_VETO = "VETO"
VOTE_ABSTAIN = "ABSTAIN"

DIRECTION_LONG = "LONG"
DIRECTION_SHORT = "SHORT"
DIRECTION_WAIT = "WAIT"


def clamp_score(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(0.0, min(100.0, number))


def normalize_direction(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"long", "buy", "多", "做多", "偏多"}:
        return DIRECTION_LONG
    if text in {"short", "sell", "空", "做空", "偏空"}:
        return DIRECTION_SHORT
    return DIRECTION_WAIT


def member_result(
    *,
    name: str,
    role: str,
    vote: str,
    direction: str = DIRECTION_WAIT,
    score: Any = 0,
    confidence: Any = 0,
    data_integrity_score: Any = 0,
    reason: str = "",
    evidence: dict[str, Any] | None = None,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "role": role,
        "vote": vote,
        "direction": normalize_direction(direction),
        "score": clamp_score(score),
        "confidence": clamp_score(confidence),
        "data_integrity_score": clamp_score(data_integrity_score),
        "reason": reason or "当前委员未提供理由。",
        "evidence": evidence or {},
        "raw": raw or {},
    }


def abstain_member(name: str, role: str, reason: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return member_result(
        name=name,
        role=role,
        vote=VOTE_ABSTAIN,
        direction=DIRECTION_WAIT,
        score=0,
        confidence=0,
        data_integrity_score=0,
        reason=reason,
        evidence=evidence or {},
    )


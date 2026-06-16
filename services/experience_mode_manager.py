"""Experience mode defaults and compatibility helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "experience_mode_config.json"
DEFAULT_EXPERIENCE_MODE = "FUSED"
SINGLE_EXPERIENCE_MODE = "SINGLE"
AVAILABLE_EXPERIENCE_MODES = {DEFAULT_EXPERIENCE_MODE, SINGLE_EXPERIENCE_MODE}
DEFAULT_SINGLE_LIBRARY = "current"
DEFAULT_AVAILABLE_LIBRARIES = ["current", "funding_v1", "oi_longshort_recent30_v1"]


def load_experience_mode_config() -> dict[str, Any]:
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except Exception:
        raw = {}
    default_mode = normalize_experience_mode(raw.get("default_experience_mode") or DEFAULT_EXPERIENCE_MODE)
    libraries = raw.get("available_libraries")
    if not isinstance(libraries, list) or not libraries:
        libraries = list(DEFAULT_AVAILABLE_LIBRARIES)
    return {
        "default_experience_mode": default_mode,
        "allow_single_library_debug": bool(raw.get("allow_single_library_debug", True)),
        "default_single_library": str(raw.get("default_single_library") or DEFAULT_SINGLE_LIBRARY),
        "available_libraries": [str(item) for item in libraries if str(item).strip()],
    }


def normalize_experience_mode(value: Any) -> str:
    text = str(value or DEFAULT_EXPERIENCE_MODE).strip().upper()
    if text in {"FUSED", "FUSION", "MERGED", "MULTI"}:
        return DEFAULT_EXPERIENCE_MODE
    if text in {"SINGLE", "SINGLE_LIBRARY", "DEBUG"}:
        return SINGLE_EXPERIENCE_MODE
    return DEFAULT_EXPERIENCE_MODE


def experience_mode_to_legacy(value: Any) -> str:
    return "single" if normalize_experience_mode(value) == SINGLE_EXPERIENCE_MODE else "fused"


def experience_mode_label(value: Any) -> str:
    return "单库调试模式" if normalize_experience_mode(value) == SINGLE_EXPERIENCE_MODE else "融合模式"


def experience_vote_source(mode: Any, selected_library: Any = None) -> str:
    if normalize_experience_mode(mode) == DEFAULT_EXPERIENCE_MODE:
        return "FUSED"
    return str(selected_library or DEFAULT_SINGLE_LIBRARY)

"""Safe status and data reader for AI-Training-Factory experience libraries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_EXPERIENCE_LIBRARY_VERSION = "current"
EXPERIENCE_LIBRARY_VERSIONS = {
    "current": "/data/ai-training-data/experience_library/current",
    "funding_v1": "/data/ai-training-data/experience_library/funding_v1",
}
EXPERIENCE_LIBRARY_LABELS = {
    "current": "K线版经验库",
    "funding_v1": "K线 + Funding 增强版经验库",
}
EXPERIENCE_LIBRARY_DATA_SOURCES = {
    "current": "klines_5m",
    "funding_v1": "klines_5m + funding_rate",
}
DEFAULT_EXPERIENCE_LIBRARY_PATH = EXPERIENCE_LIBRARY_VERSIONS[DEFAULT_EXPERIENCE_LIBRARY_VERSION]
LEVEL_FILES = {
    "symbol_level": "symbol_level_experience",
    "group_level": "group_level_experience",
    "global_level": "global_level_experience",
}
LEVEL_REQUIRED_COLUMNS = {
    "symbol_level": {"scope_type", "symbol", "state_code", "sample_count"},
    "group_level": {"scope_type", "symbol_group", "state_code", "sample_count"},
    "global_level": {"scope_type", "state_code", "sample_count"},
}
SUPPORTED_EXTENSIONS = (".parquet", ".jsonl", ".json", ".csv")


def get_default_experience_library_path() -> str:
    return DEFAULT_EXPERIENCE_LIBRARY_PATH


def normalize_experience_library_version(version: str | None = None) -> str:
    key = str(version or DEFAULT_EXPERIENCE_LIBRARY_VERSION).strip()
    return key if key in EXPERIENCE_LIBRARY_VERSIONS else DEFAULT_EXPERIENCE_LIBRARY_VERSION


def get_experience_library_path(version: str | None = None) -> str:
    return EXPERIENCE_LIBRARY_VERSIONS[normalize_experience_library_version(version)]


def get_experience_library_data_sources(version: str | None = None) -> str:
    return EXPERIENCE_LIBRARY_DATA_SOURCES.get(normalize_experience_library_version(version), "klines_5m")


def resolve_experience_library_path(path: str | None = None, version: str | None = None) -> tuple[Path, str]:
    selected_version = normalize_experience_library_version(version)
    if path:
        raw = str(path).strip()
        if raw in EXPERIENCE_LIBRARY_VERSIONS:
            selected_version = normalize_experience_library_version(raw)
            return Path(EXPERIENCE_LIBRARY_VERSIONS[selected_version]), selected_version
        for key, mapped_path in EXPERIENCE_LIBRARY_VERSIONS.items():
            if Path(raw) == Path(mapped_path):
                selected_version = key
                break
        return Path(raw), selected_version
    return Path(EXPERIENCE_LIBRARY_VERSIONS[selected_version]), selected_version


def get_experience_library_versions_status() -> dict[str, dict[str, Any]]:
    return {
        version: check_experience_library_available(version=version)
        for version in EXPERIENCE_LIBRARY_VERSIONS
    }


def _base_path(path: str | None = None, version: str | None = None) -> Path:
    return resolve_experience_library_path(path, version)[0]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists() or not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        return {"_read_error": repr(exc)}


def load_experience_manifest(path: str | None = None, version: str | None = None) -> dict[str, Any]:
    return _read_json(_base_path(path, version) / "experience_manifest.json")


def load_experience_version(path: str | None = None, version: str | None = None) -> dict[str, Any]:
    return _read_json(_base_path(path, version) / "experience_version.json")


def _find_level_file(base: Path, stem: str) -> Path | None:
    for ext in SUPPORTED_EXTENSIONS:
        candidate = base / f"{stem}{ext}"
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _detect_format(files: dict[str, Path | None]) -> str:
    extensions = {path.suffix.lstrip(".") for path in files.values() if path is not None}
    if not extensions:
        return "unknown"
    if len(extensions) == 1:
        return next(iter(extensions))
    return "mixed"


def _parquet_dependency_available() -> bool:
    try:
        import pandas  # noqa: F401
        import pyarrow  # noqa: F401
        return True
    except Exception:
        return False


def _read_parquet_records(path: Path, filters: list[tuple[str, str, Any]] | None = None, columns: list[str] | None = None) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    try:
        import pandas as pd
        import pyarrow  # noqa: F401
    except Exception as exc:
        return [], ["缺少 pandas/pyarrow，无法读取 parquet 经验库。"], [repr(exc)]
    try:
        df = pd.read_parquet(path, filters=filters or None, columns=columns or None)
    except Exception as exc:
        return [], [f"经验库文件读取失败：{path.name}"], [repr(exc)]
    return df.to_dict("records"), warnings, errors


def load_experience_level_records(
    level: str,
    path: str | None = None,
    *,
    version: str | None = None,
    filters: list[tuple[str, str, Any]] | None = None,
    columns: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Load one experience level with optional parquet filters.

    The current factory output is small enough for direct reads.  Keeping the
    filter/column boundary here lets the matcher move to partitioned or chunked
    reads without changing its public contract.
    """
    base, selected_version = resolve_experience_library_path(path, version)
    stem = LEVEL_FILES.get(level)
    if not stem:
        return {"available": False, "records": [], "warnings": [], "errors": [f"未知经验层级：{level}"], "missing_columns": []}
    level_path = _find_level_file(base, stem)
    if not level_path:
        return {"available": False, "records": [], "warnings": [f"{stem} 文件缺失，已跳过该层。"], "errors": [], "missing_columns": []}

    warnings: list[str] = []
    errors: list[str] = []
    records: list[dict[str, Any]] = []
    if level_path.suffix == ".parquet":
        records, read_warnings, read_errors = _read_parquet_records(level_path, filters=filters, columns=columns)
        warnings.extend(read_warnings)
        errors.extend(read_errors)
    elif level_path.suffix == ".json":
        raw = _read_json(level_path)
        items = raw.get("records") if isinstance(raw, dict) else []
        records = items if isinstance(items, list) else []
    elif level_path.suffix == ".jsonl":
        try:
            records = [json.loads(line) for line in level_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except Exception as exc:
            errors.append(repr(exc))
            warnings.append(f"经验库文件读取失败：{level_path.name}")
    elif level_path.suffix == ".csv":
        try:
            import pandas as pd
            df = pd.read_csv(level_path, usecols=columns or None)
            records = df.to_dict("records")
        except Exception as exc:
            errors.append(repr(exc))
            warnings.append(f"经验库文件读取失败：{level_path.name}")

    if limit is not None and limit > 0:
        records = records[:limit]
    present = set(records[0].keys()) if records else set()
    required = LEVEL_REQUIRED_COLUMNS.get(level, set())
    missing_columns = sorted(required - present) if records else []
    if missing_columns:
        warnings.append(f"{stem} 字段缺失：{', '.join(missing_columns)}")
    return {
        "available": bool(level_path and not errors),
        "experience_library_version": selected_version,
        "data_sources": get_experience_library_data_sources(selected_version),
        "file": str(level_path),
        "records": records,
        "row_count": len(records),
        "warnings": warnings,
        "errors": errors,
        "missing_columns": missing_columns,
    }


def check_experience_library_available(path: str | None = None, version: str | None = None) -> dict[str, Any]:
    """Return lightweight library status without loading large data files."""
    base, selected_version = resolve_experience_library_path(path, version)
    common = {
        "experience_library_version": selected_version,
        "version_key": selected_version,
        "label": EXPERIENCE_LIBRARY_LABELS.get(selected_version, selected_version),
        "data_sources": get_experience_library_data_sources(selected_version),
    }
    errors: list[str] = []
    warnings: list[str] = []
    if not base.exists():
        return {
            **common,
            "available": False,
            "path": str(base),
            "manifest_found": False,
            "version_found": False,
            "symbol_level_found": False,
            "group_level_found": False,
            "global_level_found": False,
            "format": "unknown",
            "message": "经验库未找到，等待 AI-Training-Factory 生成。",
            "errors": [],
            "warnings": ["经验库目录不存在。"],
            "files": {},
        }
    if not base.is_dir():
        return {
            **common,
            "available": False,
            "path": str(base),
            "manifest_found": False,
            "version_found": False,
            "symbol_level_found": False,
            "group_level_found": False,
            "global_level_found": False,
            "format": "unknown",
            "message": "经验库路径不是目录。",
            "errors": ["经验库路径不是目录。"],
            "warnings": [],
            "files": {},
        }

    manifest_path = base / "experience_manifest.json"
    version_path = base / "experience_version.json"
    files = {level: _find_level_file(base, stem) for level, stem in LEVEL_FILES.items()}
    found = {level: path_obj is not None for level, path_obj in files.items()}
    format_name = _detect_format(files)
    if format_name == "parquet" and not _parquet_dependency_available():
        warnings.append("当前环境未安装 parquet 读取依赖，经验库暂不可读。")
    missing_levels = [level for level, ok in found.items() if not ok]
    if missing_levels:
        warnings.append(f"经验库层级文件缺失：{', '.join(missing_levels)}")
    if not manifest_path.exists():
        warnings.append("experience_manifest.json 缺失。")
    if not version_path.exists():
        warnings.append("experience_version.json 缺失。")

    available = bool(manifest_path.exists() and version_path.exists() and all(found.values()))
    if format_name == "parquet" and not _parquet_dependency_available():
        available = False
    message = "经验库可用。" if available else "经验库未接入或格式不完整，经验委员保持弃权。"
    return {
        **common,
        "available": available,
        "path": str(base),
        "manifest_found": manifest_path.exists(),
        "version_found": version_path.exists(),
        "symbol_level_found": found["symbol_level"],
        "group_level_found": found["group_level"],
        "global_level_found": found["global_level"],
        "format": format_name,
        "message": message,
        "errors": errors,
        "warnings": warnings,
        "files": {level: str(path_obj) if path_obj else "" for level, path_obj in files.items()},
    }


def load_experience_library_summary(path: str | None = None, version: str | None = None) -> dict[str, Any]:
    """Load only manifest/version and file status. Never loads full datasets."""
    status = check_experience_library_available(path, version)
    manifest = load_experience_manifest(path, status.get("experience_library_version"))
    version_info = load_experience_version(path, status.get("experience_library_version"))
    return {
        **status,
        "manifest": manifest,
        "version": version_info,
        "experience_version": version_info.get("experience_version") or manifest.get("experience_version") or "none",
        "generated_at": version_info.get("generated_at") or manifest.get("generated_at") or "",
    }

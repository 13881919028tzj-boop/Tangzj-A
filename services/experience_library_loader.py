"""Safe status reader for future AI-Training-Factory experience libraries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_EXPERIENCE_LIBRARY_PATH = "/data/ai-training-data/experience_library/current/"
LEVEL_FILES = {
    "symbol_level": "symbol_level_experience",
    "group_level": "group_level_experience",
    "global_level": "global_level_experience",
}
SUPPORTED_EXTENSIONS = (".parquet", ".jsonl", ".json", ".csv")


def get_default_experience_library_path() -> str:
    return DEFAULT_EXPERIENCE_LIBRARY_PATH


def _base_path(path: str | None = None) -> Path:
    return Path(path or DEFAULT_EXPERIENCE_LIBRARY_PATH)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists() or not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        return {"_read_error": repr(exc)}


def load_experience_manifest(path: str | None = None) -> dict[str, Any]:
    return _read_json(_base_path(path) / "experience_manifest.json")


def load_experience_version(path: str | None = None) -> dict[str, Any]:
    return _read_json(_base_path(path) / "experience_version.json")


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


def check_experience_library_available(path: str | None = None) -> dict[str, Any]:
    """Return lightweight library status without loading large data files."""
    base = _base_path(path)
    errors: list[str] = []
    warnings: list[str] = []
    if not base.exists():
        return {
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


def load_experience_library_summary(path: str | None = None) -> dict[str, Any]:
    """Load only manifest/version and file status. Never loads full datasets."""
    status = check_experience_library_available(path)
    manifest = load_experience_manifest(path)
    version = load_experience_version(path)
    return {
        **status,
        "manifest": manifest,
        "version": version,
        "experience_version": version.get("experience_version") or manifest.get("experience_version") or "none",
        "generated_at": version.get("generated_at") or manifest.get("generated_at") or "",
    }

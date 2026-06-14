"""Command-line validation for market cognition snapshots."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.cognition_snapshot_validator import (  # noqa: E402
    build_snapshot_validation_report,
    save_snapshot_validation_report,
)


REPORT_PATH = "reports/cognition_snapshot_validation_report.json"


def main() -> int:
    report = build_snapshot_validation_report()
    save_snapshot_validation_report(report, REPORT_PATH)
    total = int(report.get("total_snapshots") or 0)
    if total <= 0:
        print("暂无市场认知快照，请等待系统运行生成。")
    else:
        print(f"总快照数: {total}")
        print(f"有效率: {float(report.get('valid_ratio') or 0):.2f}%")
        print(f"平均质量分: {float(report.get('avg_quality_score') or 0):.2f}")
        print(f"平均数据完整度: {float(report.get('avg_data_integrity_score') or 0):.2f}")
        print(f"经验库样本兼容率: {float(report.get('experience_sample_compatible_ratio') or 0):.2f}%")
        print(f"快照目录大小: {float(report.get('snapshot_dir_size_mb') or 0):.2f} MB")
        print(f"磁盘风险状态: {report.get('disk_risk_status') or 'OK'}")
    print(f"报告保存路径: {ROOT / REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

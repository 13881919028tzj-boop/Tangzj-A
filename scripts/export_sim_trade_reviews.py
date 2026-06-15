"""Export simulation trade review summaries to reports."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from services.sim_trade_review_engine import export_review_summary  # noqa: E402


def main() -> None:
    reports_dir = PROJECT_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / "sim_trade_review_summary.json"
    csv_path = reports_dir / "sim_trade_review_summary.csv"
    summary = export_review_summary(json_path, csv_path)
    print(f"exported: {json_path}")
    print(f"exported: {csv_path}")
    print(f"total_trades: {summary.get('total_trades', 0)}")


if __name__ == "__main__":
    main()

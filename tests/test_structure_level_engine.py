from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.structure_level_engine import build_structure_exit_plan


def make_rows() -> list[dict[str, float]]:
    closes = [
        98.0, 99.0, 100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 104.0, 103.0,
        102.0, 101.0, 100.0, 99.0, 98.5, 99.5, 100.5, 101.5, 102.5, 103.5,
        104.5, 105.5, 106.0, 105.0, 104.0, 103.0, 102.0, 101.0, 100.0, 99.0,
        98.0, 99.0, 100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0,
        108.0, 107.0, 106.0, 105.0, 104.0, 103.0, 102.0, 101.0, 100.0, 99.0,
        98.5, 99.5, 100.5, 101.5, 102.5, 103.5, 104.5, 105.5, 106.5, 107.5,
        108.5, 107.5, 106.5, 105.5, 104.5, 103.5, 102.5, 101.5, 100.5, 99.5,
        98.8, 99.8, 100.8, 101.8, 102.8, 103.8, 104.8, 105.8, 106.8, 107.8,
        108.8, 107.8, 106.8, 105.8, 104.8, 103.8, 102.8, 101.8, 100.8, 100.0,
    ]
    rows = []
    for index, close in enumerate(closes):
        rows.append(
            {
                "open": close,
                "high": close + 0.4,
                "low": close - 0.4,
                "close": close,
                "volume": 1000 + index,
            }
        )
    return rows


def test_structure_exit_plan_for_long_has_sane_levels():
    plan = build_structure_exit_plan("TESTUSDT", "long", 100.0, make_rows(), risk_score=35)

    assert plan["valid"] is True
    assert plan["stop_loss"] < 100.0
    assert plan["take_profit_1"] > 100.0
    assert plan["take_profit_2"] > plan["take_profit_1"]
    assert plan["rr1"] >= 0.7


def test_structure_exit_plan_requires_enough_rows():
    plan = build_structure_exit_plan("TESTUSDT", "short", 100.0, make_rows()[:10], risk_score=50)

    assert plan["valid"] is False

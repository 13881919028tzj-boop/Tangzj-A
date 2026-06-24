from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.sim_calibration_engine import build_calibration_report, evaluate_signal_ev, extract_calibration_tags


def history_row(pnl, score=78, entry_state="pullback_confirmed", direction="long"):
    return {
        "pnl": pnl,
        "professional_trade_score": score,
        "simulation_score": score - 2,
        "entry_state": entry_state,
        "direction": direction,
        "consensus_count": 4,
        "risk_score": 35,
        "market_regime": "bullish",
        "liquidity_quality_score": 72,
        "base_quality_score": 74,
        "kline_signal": {"direction": direction, "confirming": True},
        "whale_signal": {"direction": direction, "confirming": True},
        "orderbook_signal": {"direction": direction, "confirming": False},
    }


def test_extract_calibration_tags_normalizes_direction():
    tags = extract_calibration_tags({"direction": "空头", "professional_trade_score": 80})

    assert tags["direction"] == "short"
    assert tags["professional_trade_score"] == 80


def test_calibration_report_groups_score_buckets_and_signal_type():
    report = build_calibration_report(
        [
            history_row(2.0, score=76, entry_state="pullback_confirmed"),
            history_row(-1.0, score=82, entry_state="breakout_confirmed"),
            history_row(1.0, score=66, entry_state="pullback_confirmed"),
        ]
    )

    bucket_75_80 = next(row for row in report["professional_score_buckets"] if row["key"] == "75-80")
    assert bucket_75_80["trades"] == 1
    assert bucket_75_80["ev"] == 2.0
    assert report["entry_state"][0]["key"] == "pullback_confirmed"
    assert report["summary"]["trades"] == 3


def test_evaluate_signal_ev_blocks_negative_same_type_history():
    history = [history_row(-1.0) for _ in range(5)]

    result = evaluate_signal_ev(history_row(0), history)

    assert result["allowed"] is False
    assert result["sample_size"] == 5
    assert result["ev"] == -1.0


def test_evaluate_signal_ev_allows_when_sample_is_insufficient():
    history = [history_row(-1.0) for _ in range(2)]

    result = evaluate_signal_ev(history_row(0), history)

    assert result["allowed"] is True
    assert result["sample_size"] == 2

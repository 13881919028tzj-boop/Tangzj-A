from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import auto_simulation_runner as runner
from services import sim_trade_engine as sim


class SimpleMonkeyPatch:
    def setattr(self, obj, name, value):
        setattr(obj, name, value)


def use_temp_store(tmp_path):
    sim.DATA_DIR = tmp_path
    sim.ACCOUNT_PATH = tmp_path / "sim_account.json"
    sim.SETTINGS_PATH = tmp_path / "sim_settings.json"
    sim.POSITIONS_PATH = tmp_path / "sim_positions.json"
    sim.ORDERS_PATH = tmp_path / "sim_orders.json"
    sim.HISTORY_JSON_PATH = tmp_path / "sim_trade_history.json"
    sim.HISTORY_CSV_PATH = tmp_path / "sim_trade_history.csv"
    sim.LOG_PATH = tmp_path / "sim_trade_log.json"
    sim.DIAGNOSTICS_PATH = tmp_path / "sim_diagnostics.json"
    sim.EQUITY_JSON_PATH = tmp_path / "sim_equity_curve.json"
    sim.EQUITY_CSV_PATH = tmp_path / "sim_equity_curve.csv"
    sim.EARLY_EXIT_SHADOW_PATH = tmp_path / "sim_early_exit_shadow.json"
    tmp_path.mkdir(parents=True, exist_ok=True)


def precheck(**row_overrides):
    row = {
        "symbol": "BTCUSDT",
        "current_price": 100.0,
        "professional_trade_score": 84,
        "risk_score": 25,
        "tradable_now": True,
        "trade_direction": "short",
        "entry_state": "failed_retest_confirmed",
        "action_gate": "open_now",
        "direction_gap": 30,
        "market_alignment_score": 88,
        "market_regime": "weak",
        "direction_bias": "short",
        "price_change_percent": -2.5,
        "consensus_support_count": 4,
        "consensus_conflict_sources": [],
        "liquidity_quality_score": 70,
        "relative_strength_score": 68,
        "signal_freshness_score": 72,
        "historical_tradability_score": 60,
        "portfolio_fit_score": 76,
        "base_quality_score": 70,
        "simulation_score": 72,
        "kline_signal": {"direction": "short", "confirming": True},
        "whale_signal": {"direction": "short", "confirming": True, "quality": "good"},
        "orderbook_signal": {"direction": "short", "confirming": True},
    }
    row.update(row_overrides)
    return {
        "symbol": row["symbol"],
        "allowed_candidate": row.pop("allowed_candidate", True),
        "direction": row.get("trade_direction"),
        "score": row["professional_trade_score"],
        "risk_score": row["risk_score"],
        "rank": 1,
        "opportunity": row,
    }


def test_confirmed_short_precheck_becomes_sim_signal(tmp_path):
    use_temp_store(tmp_path)

    signal = runner._signal_from_precheck(precheck())

    assert signal is not None
    assert signal["symbol"] == "BTCUSDT"
    assert signal["direction"] == "short"
    assert signal["action"] == "轻仓试空"


def test_auto_sim_rejects_short_chasing_after_large_drop(tmp_path):
    use_temp_store(tmp_path)

    signal = runner._signal_from_precheck(
        precheck(
            entry_state="tradable_now",
            price_change_percent=-10.2,
            risk_flags=["24小时跌幅较大，禁止直接追空，等待反抽失败。"],
        )
    )

    assert signal is None


def test_auto_sim_rejects_market_misalignment(tmp_path):
    use_temp_store(tmp_path)

    signal = runner._signal_from_precheck(
        precheck(
            market_alignment_score=35,
            market_regime="rebound",
            direction_bias="long",
        )
    )

    assert signal is None


def test_auto_sim_rejects_blocked_precheck(tmp_path):
    use_temp_store(tmp_path)

    signal = runner._signal_from_precheck(
        precheck(
            allowed_candidate=False,
            professional_trade_score=66,
            risk_score=62,
            tradable_now=False,
            action_gate="wait_confirm",
            entry_state="waiting_retest",
        )
    )

    assert signal is None


def test_auto_sim_rejects_poor_liquidity_quality(tmp_path):
    use_temp_store(tmp_path)

    signal = runner._signal_from_precheck(
        precheck(
            liquidity_quality_score=35,
            base_quality_score=68,
            simulation_score=70,
        )
    )

    assert signal is None


def test_price_map_includes_pending_early_exit_shadow_symbol(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    sim.save_early_exit_shadow_rows(
        [
            {
                "position_id": "shadow_case_1",
                "symbol": "ETHUSDT",
                "status": "tracking",
                "deadline_ts": sim._ts() + 3600,
            }
        ]
    )
    monkeypatch.setattr(runner, "_fetch_live_price", lambda symbol: 123.0 if symbol == "ETHUSDT" else 0.0)

    prices = runner._build_price_map([])

    assert prices["ETHUSDT"] == 123.0


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as raw:
        test_confirmed_short_precheck_becomes_sim_signal(Path(raw) / "case1")
    with tempfile.TemporaryDirectory() as raw:
        test_auto_sim_rejects_short_chasing_after_large_drop(Path(raw) / "case2")
    with tempfile.TemporaryDirectory() as raw:
        test_auto_sim_rejects_market_misalignment(Path(raw) / "case3")
    with tempfile.TemporaryDirectory() as raw:
        test_auto_sim_rejects_blocked_precheck(Path(raw) / "case4")
    with tempfile.TemporaryDirectory() as raw:
        test_auto_sim_rejects_poor_liquidity_quality(Path(raw) / "case5")
    with tempfile.TemporaryDirectory() as raw:
        test_price_map_includes_pending_early_exit_shadow_symbol(Path(raw) / "case6", SimpleMonkeyPatch())
    print("auto_simulation_runner tests passed")

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import sim_observation_engine as obs


def use_temp_store(tmp_path):
    obs.DATA_DIR = tmp_path
    obs.OBSERVATION_PATH = tmp_path / "sim_observation_signals.json"
    tmp_path.mkdir(parents=True, exist_ok=True)


def test_record_observation_signal_deduplicates_nearby_candidates(tmp_path):
    use_temp_store(tmp_path)
    row = {
        "entry_state": "waiting_retest",
        "action_gate": "wait_confirm",
        "consensus_support_count": 3,
    }
    scores = {
        "professional_trade_score": 68,
        "simulation_score": 66,
        "base_quality_score": 70,
        "liquidity_quality_score": 71,
        "portfolio_fit_score": 65,
        "risk_score": 42,
    }

    first = obs.record_observation_signal(
        symbol="BTCUSDT",
        direction="short",
        entry_price=100.0,
        rank=1,
        reasons=["专业预审未进入 open_now。"],
        row=row,
        precheck={"rank": 1},
        scores=scores,
    )
    second = obs.record_observation_signal(
        symbol="BTCUSDT",
        direction="short",
        entry_price=100.0,
        rank=1,
        reasons=["专业预审未进入 open_now。"],
        row=row,
        precheck={"rank": 1},
        scores=scores,
    )

    rows = obs.load_observation_signals()
    assert first is not None
    assert second is not None
    assert first["id"] == second["id"]
    assert len(rows) == 1


def test_update_observation_signals_records_horizon_results(tmp_path):
    use_temp_store(tmp_path)
    record = obs.record_observation_signal(
        symbol="ETHUSDT",
        direction="long",
        entry_price=100.0,
        rank=2,
        reasons=["同类历史EV不支持开仓。"],
        row={"entry_state": "pullback_waiting"},
        precheck={},
        scores={"professional_trade_score": 70, "simulation_score": 70},
    )
    rows = obs.load_observation_signals()
    rows[0]["created_ts"] = obs._ts() - 121 * 60
    obs.save_observation_signals(rows)

    summary = obs.update_observation_signals({"ETHUSDT": 102.0})
    saved = obs.load_observation_signals()[0]

    assert record is not None
    assert summary["updated"] == 1
    assert saved["result_30m"]["outcome"] == "win"
    assert saved["result_60m"]["pct"] == 2.0
    assert saved["result_120m"]["outcome"] == "win"
    assert saved["status"] == "completed"


def test_pending_observation_symbols_excludes_completed_rows(tmp_path):
    use_temp_store(tmp_path)
    obs.record_observation_signal(
        symbol="SOLUSDT",
        direction="short",
        entry_price=50.0,
        rank=3,
        reasons=["盘口方向与开仓方向冲突。"],
        row={},
        precheck={},
        scores={"professional_trade_score": 61, "simulation_score": 60},
    )
    rows = obs.load_observation_signals()
    rows.append({"symbol": "BNBUSDT", "status": "completed", "created_ts": obs._ts()})
    obs.save_observation_signals(rows)

    assert obs.get_pending_observation_symbols() == ["SOLUSDT"]

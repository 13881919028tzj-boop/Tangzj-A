from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import sim_trade_engine as sim


def use_temp_store(tmp_path):
    sim.DATA_DIR = tmp_path
    sim.ACCOUNT_PATH = tmp_path / "sim_account.json"
    sim.SETTINGS_PATH = tmp_path / "sim_settings.json"
    sim.POSITIONS_PATH = tmp_path / "sim_positions.json"
    sim.ORDERS_PATH = tmp_path / "sim_orders.json"
    sim.HISTORY_JSON_PATH = tmp_path / "sim_trade_history.json"
    sim.HISTORY_CSV_PATH = tmp_path / "sim_trade_history.csv"
    sim.LOG_PATH = tmp_path / "sim_trade_log.json"
    tmp_path.mkdir(parents=True, exist_ok=True)


def approved_signal(**overrides):
    data = {
        "symbol": "BTCUSDT",
        "direction": "long",
        "action": "轻仓试多",
        "trade_permission": "cautious",
        "approved_for_simulation": True,
        "veto_members": [],
        "committee_confidence": 72,
        "risk_score": 42,
        "position_suggestion": "3%-5%",
        "entry_zone": {"low": 99, "high": 101},
        "stop_loss": {"price": 95},
        "take_profit_1": {"price": 104},
        "take_profit_2": {"price": 108},
        "risk_reward_ratio": 1.8,
        "invalid_condition": "跌破95后信号失效。",
        "chairman_summary": "委员会谨慎通过，仅用于模拟训练。",
        "approved_time": "2026-06-08 00:00:00",
    }
    data.update(overrides)
    return data


def prepare_running_account(tmp_path):
    use_temp_store(tmp_path)
    settings = sim.load_settings()
    settings["mode"] = "manual"
    settings["entry_mode"] = "等待入场区"
    sim.save_settings(settings)
    sim.reset_sim_account(1000)
    sim.set_sim_status("running")


def test_create_pending_order_from_committee_signal(tmp_path):
    prepare_running_account(tmp_path)
    order = sim.create_pending_sim_order(approved_signal(), 102)
    assert order is not None
    assert order["status"] == "pending"
    summary = sim.get_sim_account_summary()
    assert len(summary["orders"]) == 1
    assert summary["orders"][0]["source"] == "AI交易委员会"


def test_blocked_signal_is_rejected(tmp_path):
    prepare_running_account(tmp_path)
    signal = approved_signal(action="禁止开仓", trade_permission="blocked", approved_for_simulation=False)
    ok, reasons = sim.validate_signal_for_simulation(signal, {"BTCUSDT": 100})
    assert ok is False
    assert any("可模拟开仓动作" in reason or "交易许可" in reason for reason in reasons)


def test_pending_order_fills_and_stop_loss_closes(tmp_path):
    prepare_running_account(tmp_path)
    order = sim.create_pending_sim_order(approved_signal(), 102)
    assert order and order["status"] == "pending"
    sim.update_simulation({"BTCUSDT": 100}, [])
    summary = sim.get_sim_account_summary()
    assert len([p for p in summary["positions"] if p["status"] == "open"]) == 1
    sim.update_simulation({"BTCUSDT": 94}, [])
    summary = sim.get_sim_account_summary()
    assert len([p for p in summary["positions"] if p["status"] in {"open", "partially_closed"}]) == 0
    assert summary["history"][0]["close_reason"] == "触发止损"


def test_take_profit_1_is_persisted_after_partial_close(tmp_path):
    prepare_running_account(tmp_path)
    order = sim.create_pending_sim_order(approved_signal(), 100)
    assert order and order["status"] == "filled"

    sim.update_simulation({"BTCUSDT": 104}, [])
    summary = sim.get_sim_account_summary()
    position = next(p for p in summary["positions"] if p["status"] == "partially_closed")
    quantity_after_tp1 = position["quantity"]
    assert position["tp1_hit"] is True
    assert position["stop_loss"] == position["entry_price"]

    sim.update_simulation({"BTCUSDT": 104}, [])
    summary = sim.get_sim_account_summary()
    position = next(p for p in summary["positions"] if p["status"] == "partially_closed")
    assert position["quantity"] == quantity_after_tp1


def test_zero_position_limits_mean_unlimited(tmp_path):
    prepare_running_account(tmp_path)
    settings = sim.load_settings()
    settings["max_positions"] = 0
    settings["max_same_symbol_positions"] = 0
    settings["max_same_direction_positions"] = 0
    sim.save_settings(settings)
    assert sim.create_pending_sim_order(approved_signal(), 100)

    ok, reasons = sim.validate_signal_for_simulation(approved_signal(), {"BTCUSDT": 100})

    assert ok is True
    assert not any("持仓数量已达到上限" in reason or "当前已持有" in reason for reason in reasons)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as raw:
        test_create_pending_order_from_committee_signal(Path(raw) / "case1")
    with tempfile.TemporaryDirectory() as raw:
        test_blocked_signal_is_rejected(Path(raw) / "case2")
    with tempfile.TemporaryDirectory() as raw:
        test_pending_order_fills_and_stop_loss_closes(Path(raw) / "case3")
    with tempfile.TemporaryDirectory() as raw:
        test_take_profit_1_is_persisted_after_partial_close(Path(raw) / "case4")
    with tempfile.TemporaryDirectory() as raw:
        test_zero_position_limits_mean_unlimited(Path(raw) / "case5")
    print("sim_trade_engine tests passed")

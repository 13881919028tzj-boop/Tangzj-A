from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import sim_trade_engine as sim
from services import market_cache


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
        "professional_trade_score": 78,
        "simulation_score": 72,
        "base_quality_score": 72,
        "liquidity_quality_score": 70,
        "relative_strength_score": 65,
        "signal_freshness_score": 72,
        "historical_tradability_score": 60,
        "portfolio_fit_score": 75,
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


def test_low_liquidity_quality_signal_is_rejected(tmp_path):
    prepare_running_account(tmp_path)
    signal = approved_signal(liquidity_quality_score=35, simulation_score=70, base_quality_score=70)
    ok, reasons = sim.validate_signal_for_simulation(signal, {"BTCUSDT": 100})

    assert ok is False
    assert any("流动性质量" in reason for reason in reasons)


def test_quality_scores_reduce_position_size(tmp_path):
    prepare_running_account(tmp_path)
    settings = sim.load_settings()

    pct = sim._position_pct(
        approved_signal(
            position_suggestion="5%-10%",
            action="顺势做多",
            simulation_score=61,
            liquidity_quality_score=52,
            portfolio_fit_score=46,
        ),
        settings,
    )

    assert pct == 8.0


def test_position_suggestion_text_is_kept_when_execution_size_is_scaled(tmp_path):
    prepare_running_account(tmp_path)
    order = sim.create_pending_sim_order(approved_signal(position_suggestion="3%-5%"), 100)

    assert order is not None
    assert order["position_pct"] == "3%-5%"
    assert 159 <= order["margin_usdt"] <= 160


def test_rejected_signal_writes_diagnostic(tmp_path):
    prepare_running_account(tmp_path)
    settings = sim.load_settings()
    settings["mode"] = "auto"
    sim.save_settings(settings)

    results = sim.process_committee_signals(
        {"BTCUSDT": 100},
        [approved_signal(liquidity_quality_score=35, simulation_score=70, base_quality_score=70)],
    )
    diagnostics = sim.load_sim_diagnostics()

    assert results[0]["status"] == "rejected"
    assert diagnostics[0]["event_type"] == "模拟信号拒绝"
    assert diagnostics[0]["details"]["liquidity_quality_score"] == 35


def test_score_feedback_summarizes_quality_buckets(tmp_path):
    use_temp_store(tmp_path)
    history = [
        {
            "pnl": -2.0,
            "is_win": False,
            "committee_snapshot": {
                "simulation_score": 80,
                "base_quality_score": 78,
                "liquidity_quality_score": 76,
            },
        },
        {
            "pnl": 1.5,
            "is_win": True,
            "committee_snapshot": {
                "simulation_score": 58,
                "base_quality_score": 55,
                "liquidity_quality_score": 52,
            },
        },
    ]

    feedback = sim.calculate_sim_score_feedback(history)

    assert feedback["sample_count"] == 2
    assert feedback["stats"][0]["评分项"] == "模拟适配分"
    assert feedback["stats"][0]["高分样本"] == 1


def test_early_exit_shadow_records_30m_and_60m_results(tmp_path):
    prepare_running_account(tmp_path)
    position = {
        "position_id": "shadow_case_1",
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry_price": 100.0,
        "quantity": 2.0,
        "notional_usdt": 200.0,
        "leverage": 5,
        "open_time": "2026-06-22 00:00:00",
        "open_ts": sim._ts() - 1300,
        "stop_loss": 98.0,
    }

    sim.create_early_exit_shadow(position, 99.0, -0.5)
    rows = sim.load_early_exit_shadow_rows()
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["early_exit_pnl"] == -2.0

    rows[0]["check_30m_ts"] = sim._ts() - 1
    rows[0]["check_60m_ts"] = sim._ts() - 1
    sim.save_early_exit_shadow_rows(rows)
    sim.update_early_exit_shadow_tracks({"BTCUSDT": 101.0}, {"BTCUSDT": "live"})
    rows = sim.load_early_exit_shadow_rows()

    assert rows[0]["status"] == "completed"
    assert rows[0]["check_30m"]["result"] == "win"
    assert rows[0]["check_60m"]["pnl_delta_vs_early_exit"] == 4.0


def test_structure_levels_override_signal_exit_plan_when_valid(tmp_path):
    prepare_running_account(tmp_path)
    symbol = "STRUCTUSDT"
    rows = []
    closes = [
        98, 99, 100, 101, 102, 103, 104, 105, 104, 103,
        102, 101, 100, 99, 98, 99, 100, 101, 102, 103,
        104, 105, 106, 105, 104, 103, 102, 101, 100, 99,
        98, 99, 100, 101, 102, 103, 104, 105, 106, 107,
        108, 107, 106, 105, 104, 103, 102, 101, 100, 99,
        98, 99, 100, 101, 102, 103, 104, 105, 106, 107,
        108, 107, 106, 105, 104, 103, 102, 101, 100, 100,
    ]
    for index, close in enumerate(closes):
        rows.append({"open": close, "high": close + 0.4, "low": close - 0.4, "close": close, "volume": 1000 + index})
    market_cache.set_klines(symbol, "1m", rows)

    order = sim.create_pending_sim_order(
        approved_signal(
            symbol=symbol,
            entry_zone={"low": 99, "high": 101},
            stop_loss={"price": 95},
            take_profit_1={"price": 101},
            take_profit_2={"price": 102},
            risk_score=35,
        ),
        100,
    )
    assert order is not None
    assert order["status"] == "filled"
    assert order["exit_plan_source"] == "structure_levels"
    position = sim.get_open_positions()[0]
    assert position["exit_plan_source"] == "structure_levels"
    assert position["stop_loss"] > 95


def test_sim_fee_and_slippage_are_applied_to_entry_and_exit(tmp_path):
    prepare_running_account(tmp_path)
    settings = sim.load_settings()
    settings["sim_fee_rate"] = 0.001
    settings["sim_slippage_pct"] = 0.01
    sim.save_settings(settings)

    order = sim.create_pending_sim_order(
        approved_signal(entry_zone={"low": 99, "high": 101}, stop_loss={"price": 90}, take_profit_1={"price": 120}, take_profit_2={"price": 130}),
        100,
    )
    assert order and order["status"] == "filled"

    position = sim.get_open_positions()[0]
    assert abs(position["entry_price"] - 101.0) < 1e-9
    assert position["entry_fee"] > 0
    account_after_open = sim.load_sim_account()
    assert account_after_open["total_fee_usdt"] == position["entry_fee"]
    assert account_after_open["realized_pnl"] == -position["entry_fee"]

    sim.close_sim_position(position["position_id"], "测试平仓", 110)
    history = sim.load_sim_trade_history()
    trade = history[0]
    expected_exit_price = 108.9
    expected_gross = (expected_exit_price - position["entry_price"]) * position["quantity"]
    expected_exit_fee = expected_exit_price * position["quantity"] * 0.001

    assert abs(trade["exit_price"] - expected_exit_price) < 1e-9
    assert abs(trade["gross_pnl"] - expected_gross) < 1e-9
    assert abs(trade["exit_fee"] - expected_exit_fee) < 1e-9
    assert abs(trade["pnl"] - (expected_gross - expected_exit_fee)) < 1e-9
    assert trade["fee_usdt"] > expected_exit_fee


def test_closed_trade_history_keeps_calibration_tags(tmp_path):
    prepare_running_account(tmp_path)
    order = sim.create_pending_sim_order(
        approved_signal(
            entry_zone={"low": 99, "high": 101},
            entry_state="pullback_confirmed",
            consensus_count=4,
            kline_signal={"direction": "long", "confirming": True},
            whale_signal={"direction": "long", "confirming": True},
            orderbook_signal={"direction": "long", "confirming": False},
            market_regime="bullish",
        ),
        100,
    )
    assert order and order["status"] == "filled"

    position = sim.get_open_positions()[0]
    sim.close_sim_position(position["position_id"], "测试平仓", 101)
    trade = sim.load_sim_trade_history()[0]

    assert trade["professional_trade_score"] == 78
    assert trade["simulation_score"] == 72
    assert trade["entry_state"] == "pullback_confirmed"
    assert trade["consensus_count"] == 4
    assert trade["kline_confirming"] is True
    assert trade["whale_confirming"] is True
    assert trade["orderbook_confirming"] is False
    assert trade["market_regime"] == "bullish"
    assert trade["calibration_tags"]["base_quality_score"] == 72


def test_corrupted_positions_restore_from_last_good_backup(tmp_path):
    use_temp_store(tmp_path)
    rows = [{"position_id": "safe_pos_1", "symbol": "BTCUSDT", "status": "open", "margin_usdt": 10}]
    sim.save_positions(rows)
    sim.POSITIONS_PATH.write_text("", encoding="utf-8")

    restored = sim.load_positions()

    assert restored == rows
    assert sim.POSITIONS_PATH.exists()
    assert sim._positions_last_good_path().exists()


def test_early_exit_closes_and_opens_reverse_position_with_expanded_targets(tmp_path):
    prepare_running_account(tmp_path)
    position = {
        "position_id": "reverse_case_1",
        "symbol": "BTCUSDT",
        "direction": "long",
        "status": "open",
        "entry_price": 100.0,
        "current_price": 100.0,
        "quantity": 2.0,
        "margin_usdt": 40.0,
        "notional_usdt": 200.0,
        "leverage": 5,
        "market_type": "futures",
        "contract_type": "USDT_PERPETUAL",
        "open_time": "2026-06-24 00:00:00",
        "open_ts": sim._ts() - 1300,
        "stop_loss": 98.0,
        "take_profit_1": 102.0,
        "take_profit_2": 104.0,
        "committee_snapshot": {"position_suggestion": "3%-5%"},
        "local_strategy_snapshot": {},
    }
    sim.save_positions([position])
    account = sim.load_sim_account()
    account["available_balance"] = 960.0
    account["used_margin"] = 40.0
    account["equity"] = 1000.0
    sim.save_sim_account(account)

    sim.update_sim_positions({"BTCUSDT": 98.5}, {"BTCUSDT": "live"})
    positions = sim.load_positions()
    closed = [p for p in positions if p.get("position_id") == "reverse_case_1"][0]
    reverse = [p for p in positions if p.get("status") == "open"][0]

    assert closed["status"] == "closed"
    assert closed["close_reason"] == "反向复核提前退出"
    assert reverse["direction"] == "short"
    assert reverse["reverse_from_early_exit"] is True
    assert reverse["reverse_source_position_id"] == "reverse_case_1"
    assert abs(reverse["margin_usdt"] - 40.0) < 1e-9
    assert abs(reverse["notional_usdt"] - 200.0) < 1e-9
    assert abs(reverse["stop_loss"] - 102.5) < 1e-9
    assert abs(reverse["take_profit_1"] - 94.5) < 1e-9
    assert abs(reverse["take_profit_2"] - 90.5) < 1e-9


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
    with tempfile.TemporaryDirectory() as raw:
        test_low_liquidity_quality_signal_is_rejected(Path(raw) / "case6")
    with tempfile.TemporaryDirectory() as raw:
        test_quality_scores_reduce_position_size(Path(raw) / "case7")
    with tempfile.TemporaryDirectory() as raw:
        test_position_suggestion_text_is_kept_when_execution_size_is_scaled(Path(raw) / "case7b")
    with tempfile.TemporaryDirectory() as raw:
        test_rejected_signal_writes_diagnostic(Path(raw) / "case8")
    with tempfile.TemporaryDirectory() as raw:
        test_score_feedback_summarizes_quality_buckets(Path(raw) / "case9")
    with tempfile.TemporaryDirectory() as raw:
        test_early_exit_shadow_records_30m_and_60m_results(Path(raw) / "case10")
    with tempfile.TemporaryDirectory() as raw:
        test_structure_levels_override_signal_exit_plan_when_valid(Path(raw) / "case11")
    with tempfile.TemporaryDirectory() as raw:
        test_sim_fee_and_slippage_are_applied_to_entry_and_exit(Path(raw) / "case12")
    with tempfile.TemporaryDirectory() as raw:
        test_closed_trade_history_keeps_calibration_tags(Path(raw) / "case13")
    with tempfile.TemporaryDirectory() as raw:
        test_corrupted_positions_restore_from_last_good_backup(Path(raw) / "case14")
    with tempfile.TemporaryDirectory() as raw:
        test_early_exit_closes_and_opens_reverse_position_with_expanded_targets(Path(raw) / "case15")
    print("sim_trade_engine tests passed")

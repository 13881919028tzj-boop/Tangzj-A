from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import grid_trade_engine as grid


def use_temp_store(tmp_path):
    grid.DATA_DIR = tmp_path
    grid.BOTS_PATH = tmp_path / "grid_bots.json"
    grid.TRADES_PATH = tmp_path / "grid_trades.json"
    grid.EVENTS_PATH = tmp_path / "grid_events.json"
    tmp_path.mkdir(parents=True, exist_ok=True)


def test_create_grid_bot_builds_independent_orders(tmp_path):
    use_temp_store(tmp_path)

    bot = grid.create_grid_bot("BTCUSDT", 90, 110, 4, 100, 100, 0.0)

    assert bot["status"] == "running"
    assert bot["grid_count"] == 4
    assert len([order for order in bot["open_orders"] if order["side"] == "buy"]) == 2
    assert len([order for order in bot["open_orders"] if order["side"] == "sell"]) == 2
    assert grid.get_grid_summary()["total_running_bots"] == 1


def test_grid_buy_then_sell_records_profit(tmp_path):
    use_temp_store(tmp_path)
    bot = grid.create_grid_bot("BTCUSDT", 90, 110, 4, 100, 100, 0.0)

    first = grid.update_grid_bots({"BTCUSDT": 95})
    bot = grid.load_grid_bots()[0]
    assert first["fill_count"] == 1
    assert any(order["side"] == "sell" and order["price"] == 100 for order in bot["open_orders"])

    second = grid.update_grid_bots({"BTCUSDT": 100})
    trades = grid.load_grid_trades()

    assert second["fill_count"] >= 1
    assert trades[0]["side"] == "sell"
    assert trades[0]["profit"] > 0


def test_invalid_grid_config_requires_price_inside_range(tmp_path):
    use_temp_store(tmp_path)

    ok, reasons = grid.validate_grid_config("BTCUSDT", 90, 110, 10, 100, 120)

    assert ok is False
    assert any("当前价格" in reason for reason in reasons)


def test_stop_grid_bot_changes_status(tmp_path):
    use_temp_store(tmp_path)
    bot = grid.create_grid_bot("ETHUSDT", 90, 110, 4, 100, 100, 0.0)

    stopped = grid.stop_grid_bot(bot["bot_id"])

    assert stopped is not None
    assert grid.load_grid_bots()[0]["status"] == "stopped"


def test_pause_grid_bot_prevents_fills_until_resumed(tmp_path):
    use_temp_store(tmp_path)
    bot = grid.create_grid_bot("BTCUSDT", 90, 110, 4, 100, 100, 0.0)

    paused = grid.pause_grid_bot(bot["bot_id"])
    paused_result = grid.update_grid_bots({"BTCUSDT": 95})
    resumed = grid.resume_grid_bot(bot["bot_id"])
    resumed_result = grid.update_grid_bots({"BTCUSDT": 95})

    assert paused is not None
    assert resumed is not None
    assert paused_result["fill_count"] == 0
    assert resumed_result["fill_count"] == 1


def test_cancel_grid_orders_stops_and_clears_open_orders(tmp_path):
    use_temp_store(tmp_path)
    bot = grid.create_grid_bot("BTCUSDT", 90, 110, 4, 100, 100, 0.0)

    stopped = grid.cancel_grid_orders(bot["bot_id"])
    saved = grid.load_grid_bots()[0]

    assert stopped is not None
    assert saved["status"] == "stopped"
    assert saved["open_orders"] == []
    assert saved["canceled_orders"] > 0


def test_close_grid_position_sells_inventory_and_records_trade(tmp_path):
    use_temp_store(tmp_path)
    bot = grid.create_grid_bot("BTCUSDT", 90, 110, 4, 100, 100, 0.0)

    closed = grid.close_grid_position(bot["bot_id"], 100)
    saved = grid.load_grid_bots()[0]
    trade = grid.load_grid_trades()[0]

    assert closed is not None
    assert saved["status"] == "stopped"
    assert saved["base_inventory"] == 0.0
    assert saved["open_orders"] == []
    assert trade["action"] == "market_close"


def test_emergency_close_marks_emergency_stopped(tmp_path):
    use_temp_store(tmp_path)
    bot = grid.create_grid_bot("BTCUSDT", 90, 110, 4, 100, 100, 0.0)

    closed = grid.close_grid_position(bot["bot_id"], 100, emergency=True)

    assert closed is not None
    assert grid.load_grid_bots()[0]["status"] == "emergency_stopped"


def test_short_grid_sells_high_and_buys_low_for_profit(tmp_path):
    use_temp_store(tmp_path)
    bot = grid.create_grid_bot("BTCUSDT", 90, 110, 4, 100, 100, 0.0, "short_contract")

    assert bot["grid_direction"] == "short_contract"
    assert bot["short_inventory"] > 0

    result = grid.update_grid_bots({"BTCUSDT": 95})
    trades = grid.load_grid_trades()

    assert result["fill_count"] >= 1
    assert trades[0]["side"] == "buy"
    assert trades[0]["profit"] > 0


def test_short_grid_ignores_legacy_zero_quantity_sell_order(tmp_path):
    use_temp_store(tmp_path)
    bot = grid.create_grid_bot("BTCUSDT", 90, 110, 4, 100, 100, 0.0, "short_contract")
    bot["open_orders"] = [{"side": "sell", "position": "short", "level_index": 3, "price": 105}]
    grid.save_grid_bots([bot])

    result = grid.update_grid_bots({"BTCUSDT": 106})

    saved = grid.load_grid_bots()[0]
    assert result["fill_count"] == 0
    assert grid.load_grid_trades() == []
    assert saved["open_orders"] == []


def test_compact_grid_storage_removes_bad_trades_events_and_orders(tmp_path):
    use_temp_store(tmp_path)
    bot = grid.create_grid_bot("BTCUSDT", 90, 110, 4, 100, 100, 0.0)
    bot["open_orders"].append({"side": "sell", "level_index": 3, "price": 105, "quantity": 0.0})
    grid.save_grid_bots([bot])
    grid.save_grid_trades([
        {"symbol": "BTCUSDT", "quantity": 0.0},
        {"symbol": "BTCUSDT", "quantity": 0.1},
    ])
    grid.save_grid_events([
        {"event_type": "网格成交", "content": "sell 0.00000000 @ 105.00000000，利润 +0.0000 USDT。"},
        {"event_type": "启动网格", "content": "ok"},
    ])

    result = grid.compact_grid_storage()

    assert result["removed_trades"] == 1
    assert result["removed_events"] == 1
    assert result["removed_orders"] >= 1
    assert len(grid.load_grid_trades()) == 1
    assert len(grid.load_grid_events()) == 1


def test_neutral_grid_can_open_long_and_short_orders(tmp_path):
    use_temp_store(tmp_path)
    bot = grid.create_grid_bot("BTCUSDT", 90, 110, 4, 100, 100, 0.0, "neutral_contract")

    positions = {order.get("position") for order in bot["open_orders"]}

    assert bot["grid_direction"] == "neutral_contract"
    assert "long" in positions
    assert "short" in positions

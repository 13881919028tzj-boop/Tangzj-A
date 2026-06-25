from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import grid_trade_engine as grid
from services import live_grid_trade_engine as live_grid


def use_temp_store(tmp_path):
    grid.DATA_DIR = tmp_path
    grid.BOTS_PATH = tmp_path / "grid_bots.json"
    grid.TRADES_PATH = tmp_path / "grid_trades.json"
    grid.EVENTS_PATH = tmp_path / "grid_events.json"
    live_grid.DATA_DIR = tmp_path
    live_grid.CONFIG_PATH = tmp_path / "live_grid_settings.json"
    live_grid.AUDIT_PATH = tmp_path / "live_grid_audit_log.json"
    tmp_path.mkdir(parents=True, exist_ok=True)


def test_live_grid_builds_review_only_spot_buy_plans(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    bot = grid.create_grid_bot("BTCUSDT", 90, 110, 4, 100, 100, 0.0, "long_spot")
    live_grid.save_live_grid_settings({"max_initial_orders": 1, "max_order_usdt": 3.0})

    def fake_create_plan(_signal, user_inputs):
        return {
            "plan_id": "plan_1",
            "symbol": user_inputs["symbol"],
            "side": user_inputs["side"],
            "order_type": user_inputs["order_type"],
            "price": user_inputs["price"],
            "quote_amount": user_inputs["quote_amount"],
            "quantity": user_inputs["quote_amount"] / user_inputs["price"],
        }

    monkeypatch.setattr(live_grid, "create_live_order_plan", fake_create_plan)
    monkeypatch.setattr(live_grid, "create_live_order_preview", lambda plan: {"ok": True, "risk_errors": []})

    result = live_grid.build_live_grid_order_plans(bot["bot_id"])

    assert result["ok"] is True
    assert len(result["plans"]) == 1
    plan = result["plans"][0]["plan"]
    assert plan["symbol"] == "BTCUSDT"
    assert plan["side"] == "BUY"
    assert plan["live_grid_review_only"] is True
    assert plan["quote_amount"] == 3.0


def test_live_grid_rejects_short_contract_grid(tmp_path):
    use_temp_store(tmp_path)
    bot = grid.create_grid_bot("ETHUSDT", 90, 110, 4, 100, 100, 0.0, "short_contract")

    result = live_grid.build_live_grid_order_plans(bot["bot_id"])

    assert result["ok"] is False
    assert "合约网格开关" in result["message"]


def test_live_grid_builds_short_futures_sell_plans_when_enabled(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    live_grid.save_live_grid_settings({"allow_futures_grid": True, "max_initial_orders": 1, "max_order_usdt": 4.0, "futures_leverage": 7})
    bot = grid.create_grid_bot("ETHUSDT", 90, 110, 4, 100, 100, 0.0, "short_contract")

    def fake_create_plan(_signal, user_inputs):
        return {
            "plan_id": "plan_short",
            "symbol": user_inputs["symbol"],
            "market_type": user_inputs["market_type"],
            "leverage": user_inputs.get("leverage", 1),
            "side": user_inputs["side"],
            "order_type": user_inputs["order_type"],
            "price": user_inputs["price"],
            "quote_amount": user_inputs["quote_amount"],
            "quantity": user_inputs["quote_amount"] / user_inputs["price"],
        }

    monkeypatch.setattr(live_grid, "create_live_order_plan", fake_create_plan)
    monkeypatch.setattr(live_grid, "create_live_order_preview", lambda plan: {"ok": True, "risk_errors": []})

    result = live_grid.build_live_grid_order_plans(bot["bot_id"])

    assert result["ok"] is True
    plan = result["plans"][0]["plan"]
    assert plan["market_type"] == "futures"
    assert plan["side"] == "SELL"
    assert plan["quote_amount"] == 4.0
    assert plan["leverage"] == 7


def test_live_grid_builds_neutral_spot_and_futures_plans(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    live_grid.save_live_grid_settings({"allow_futures_grid": True, "max_initial_orders": 4, "max_order_usdt": 2.0, "futures_leverage": 6})
    bot = grid.create_grid_bot("SOLUSDT", 90, 110, 4, 100, 100, 0.0, "neutral_contract")

    def fake_create_plan(_signal, user_inputs):
        return {
            "plan_id": f"{user_inputs['market_type']}_{user_inputs['side']}",
            "symbol": user_inputs["symbol"],
            "market_type": user_inputs["market_type"],
            "leverage": user_inputs.get("leverage", 1),
            "side": user_inputs["side"],
            "order_type": user_inputs["order_type"],
            "price": user_inputs["price"],
            "quote_amount": user_inputs["quote_amount"],
            "quantity": user_inputs["quote_amount"] / user_inputs["price"],
        }

    monkeypatch.setattr(live_grid, "create_live_order_plan", fake_create_plan)
    monkeypatch.setattr(live_grid, "create_live_order_preview", lambda plan: {"ok": True, "risk_errors": []})

    result = live_grid.build_live_grid_order_plans(bot["bot_id"])
    plan_types = {(item["plan"]["market_type"], item["plan"]["side"]) for item in result["plans"]}

    assert result["ok"] is True
    assert ("spot", "BUY") in plan_types
    assert ("futures", "SELL") in plan_types
    assert any(item["plan"]["market_type"] == "futures" and item["plan"]["leverage"] == 6 for item in result["plans"])


def test_live_grid_builds_recommendation_plans(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    live_grid.save_live_grid_settings({"allow_futures_grid": True, "max_initial_orders": 2, "max_order_usdt": 5.0, "futures_leverage": 8})

    def fake_create_plan(_signal, user_inputs):
        return {
            "plan_id": f"{user_inputs['market_type']}_{user_inputs['side']}",
            "symbol": user_inputs["symbol"],
            "market_type": user_inputs["market_type"],
            "leverage": user_inputs.get("leverage", 1),
            "side": user_inputs["side"],
            "order_type": user_inputs["order_type"],
            "price": user_inputs["price"],
            "quote_amount": user_inputs["quote_amount"],
            "quantity": user_inputs["quote_amount"] / user_inputs["price"],
        }

    monkeypatch.setattr(live_grid, "create_live_order_plan", fake_create_plan)
    monkeypatch.setattr(live_grid, "create_live_order_preview", lambda plan: {"ok": True, "risk_errors": []})

    result = live_grid.build_live_grid_recommendation_order_plans(
        {
            "symbol": "BNBUSDT",
            "last_price": 100,
            "lower_price": 90,
            "upper_price": 110,
            "suggested_direction": "neutral_contract",
        }
    )

    assert result["ok"] is True
    assert {item["plan"]["market_type"] for item in result["plans"]} == {"spot", "futures"}
    assert any(item["plan"]["market_type"] == "futures" and item["plan"]["leverage"] == 8 for item in result["plans"])


def test_live_grid_clamps_futures_leverage_to_configured_max(tmp_path):
    use_temp_store(tmp_path)
    saved = live_grid.save_live_grid_settings({"max_futures_leverage": 5, "futures_leverage": 50})

    assert saved["max_futures_leverage"] == 5
    assert saved["futures_leverage"] == 5


def test_live_grid_real_submit_switch_is_saved_but_not_ready_without_global_flag(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    live_grid.save_live_grid_settings(
        {
            "live_grid_interface_enabled": True,
            "allow_reading": True,
            "allow_spot_long_grid": True,
            "allow_futures_grid": True,
            "allow_real_order_submit": True,
            "require_ip_restrict": True,
        }
    )
    monkeypatch.setattr(live_grid, "check_api_connection", lambda *_args, **_kwargs: {"ok": True, "status": "正常"})
    monkeypatch.setattr(live_grid, "LIVE_TRADING_ENABLED", False)
    monkeypatch.setattr(
        live_grid,
        "check_api_permissions",
        lambda *_args, **_kwargs: {"ok": True, "can_trade": True, "can_withdraw": False, "permission_status": "可交易"},
    )
    monkeypatch.setattr(
        live_grid,
        "check_api_key_restrictions",
        lambda *_args, **_kwargs: {
            "ok": True,
            "ipRestrict": True,
            "enableWithdrawals": False,
            "enableReading": True,
            "enableSpotAndMarginTrading": True,
            "enableFutures": True,
            "enableMargin": False,
        },
    )

    status = live_grid.get_live_grid_status()
    saved = live_grid.load_live_grid_settings()

    assert saved["allow_real_order_submit"] is True
    assert status["ready_for_review"] is True
    assert status["real_submit_enabled"] is False
    assert "withdrawals" in status["exchange_permission_switches"]
    assert status["exchange_permission_switches"]["withdrawals"] is False


def test_live_grid_withdrawal_permission_blocks_interface(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    live_grid.save_live_grid_settings({"live_grid_interface_enabled": True, "allow_reading": True, "allow_spot_long_grid": True})
    monkeypatch.setattr(live_grid, "check_api_connection", lambda *_args, **_kwargs: {"ok": True, "status": "正常"})
    monkeypatch.setattr(
        live_grid,
        "check_api_permissions",
        lambda *_args, **_kwargs: {"ok": True, "can_trade": True, "can_withdraw": True, "permission_status": "权限异常"},
    )
    monkeypatch.setattr(
        live_grid,
        "check_api_key_restrictions",
        lambda *_args, **_kwargs: {
            "ok": True,
            "ipRestrict": True,
            "enableWithdrawals": True,
            "enableReading": True,
            "enableSpotAndMarginTrading": True,
            "enableFutures": True,
            "enableMargin": False,
        },
    )

    status = live_grid.get_live_grid_status()

    assert status["ready_for_review"] is False
    assert status["real_submit_enabled"] is False
    assert any("提现权限" in item for item in status["blockers"])

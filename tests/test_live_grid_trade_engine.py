from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import grid_trade_engine as grid
from services import live_grid_trade_engine as live_grid


def fake_exchange_rule(min_notional=1.0):
    return {
        "ok": True,
        "status": "TRADING",
        "tickSize": 0.1,
        "minPrice": 0.1,
        "maxPrice": 1000000,
        "stepSize": 0.001,
        "minQty": 0.001,
        "maxQty": 1000000,
        "minNotional": min_notional,
        "maxNotional": 0,
    }


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
    monkeypatch.setattr(live_grid, "load_exchange_rules", lambda *_args, **_kwargs: fake_exchange_rule())

    result = live_grid.build_live_grid_order_plans(bot["bot_id"])

    assert result["ok"] is True
    assert len(result["plans"]) == 1
    plan = result["plans"][0]["plan"]
    assert plan["symbol"] == "BTCUSDT"
    assert plan["side"] == "BUY"
    assert plan["live_grid_review_only"] is True
    assert plan["quote_amount"] <= 3.0
    assert plan["quote_amount"] >= 1.0


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
    monkeypatch.setattr(live_grid, "load_exchange_rules", lambda *_args, **_kwargs: fake_exchange_rule())

    result = live_grid.build_live_grid_order_plans(bot["bot_id"])

    assert result["ok"] is True
    plan = result["plans"][0]["plan"]
    assert plan["market_type"] == "futures"
    assert plan["side"] == "SELL"
    assert plan["quote_amount"] <= 4.0
    assert plan["quote_amount"] >= 1.0
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
    monkeypatch.setattr(live_grid, "load_exchange_rules", lambda *_args, **_kwargs: fake_exchange_rule())

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
    monkeypatch.setattr(live_grid, "load_exchange_rules", lambda *_args, **_kwargs: fake_exchange_rule())

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


def test_live_grid_manual_total_amount_allocates_initial_orders(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    live_grid.save_live_grid_settings({"max_initial_orders": 2, "max_order_usdt": 10.0})

    def fake_create_plan(_signal, user_inputs):
        return {
            "plan_id": f"plan_{user_inputs['price']}",
            "symbol": user_inputs["symbol"],
            "market_type": user_inputs["market_type"],
            "side": user_inputs["side"],
            "order_type": user_inputs["order_type"],
            "price": user_inputs["price"],
            "quote_amount": user_inputs["quote_amount"],
            "quantity": user_inputs["quote_amount"] / user_inputs["price"],
        }

    monkeypatch.setattr(live_grid, "create_live_order_plan", fake_create_plan)
    monkeypatch.setattr(live_grid, "create_live_order_preview", lambda plan: {"ok": True, "risk_errors": []})
    monkeypatch.setattr(live_grid, "load_exchange_rules", lambda *_args, **_kwargs: fake_exchange_rule())

    result = live_grid.build_live_grid_manual_order_plans(
        {
            "symbol": "BTCUSDT",
            "current_price": 100,
            "lower_price": 90,
            "upper_price": 110,
            "direction": "long_spot",
            "grid_count": 4,
            "quote_amount": 1,
            "funding_mode": "total_amount",
            "total_quote_amount": 12,
            "investment_mode": "compound_reinvest",
        }
    )

    assert result["ok"] is True
    assert len(result["plans"]) == 2
    assert all(2.9 <= item["plan"]["quote_amount"] <= 3.0 for item in result["plans"])
    assert all(item["plan"]["grid_investment_mode"] == "compound_reinvest" for item in result["plans"])
    assert all(item["plan"]["grid_profit_reinvestment"] is True for item in result["plans"])
    assert all(item["plan"]["grid_total_investment_usdt"] == 12.0 for item in result["plans"])


def test_live_grid_total_amount_uses_grid_count_and_min_notional(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    live_grid.save_live_grid_settings({"max_initial_orders": 5, "max_order_usdt": 10.0})
    monkeypatch.setattr(live_grid, "load_exchange_rules", lambda *_args, **_kwargs: fake_exchange_rule(5.0))

    def fake_create_plan(_signal, user_inputs):
        price = user_inputs["price"]
        quote = user_inputs["quote_amount"]
        return {
            "plan_id": f"plan_{price}",
            "symbol": user_inputs["symbol"],
            "market_type": user_inputs["market_type"],
            "side": user_inputs["side"],
            "order_type": user_inputs["order_type"],
            "price": price,
            "quote_amount": quote,
            "quantity": quote / price,
        }

    monkeypatch.setattr(live_grid, "create_live_order_plan", fake_create_plan)
    monkeypatch.setattr(live_grid, "create_live_order_preview", lambda plan: {"ok": True, "risk_errors": []})

    result = live_grid.build_live_grid_manual_order_plans(
        {
            "symbol": "BTCUSDT",
            "current_price": 100,
            "lower_price": 90,
            "upper_price": 110,
            "direction": "long_spot",
            "grid_count": 20,
            "funding_mode": "total_amount",
            "total_quote_amount": 100,
            "investment_mode": "fixed_equal",
        }
    )

    assert result["ok"] is True
    assert len(result["plans"]) == 5
    assert all(item["plan"]["quote_amount"] >= 5 for item in result["plans"])
    assert all(item["plan"]["exchange_rule_aligned"] is True for item in result["plans"])


def test_live_grid_total_amount_rejects_when_total_too_small_for_grid_count(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    live_grid.save_live_grid_settings({"max_initial_orders": 5, "max_order_usdt": 10.0})
    monkeypatch.setattr(live_grid, "load_exchange_rules", lambda *_args, **_kwargs: fake_exchange_rule(5.0))

    def fake_create_plan(_signal, user_inputs):
        return {
            "plan_id": "plan",
            "symbol": user_inputs["symbol"],
            "market_type": user_inputs["market_type"],
            "side": user_inputs["side"],
            "order_type": user_inputs["order_type"],
            "price": user_inputs["price"],
            "quote_amount": user_inputs["quote_amount"],
            "quantity": user_inputs["quote_amount"] / user_inputs["price"],
        }

    monkeypatch.setattr(live_grid, "create_live_order_plan", fake_create_plan)
    monkeypatch.setattr(live_grid, "create_live_order_preview", lambda plan: {"ok": True, "risk_errors": []})

    result = live_grid.build_live_grid_manual_order_plans(
        {
            "symbol": "BTCUSDT",
            "current_price": 100,
            "lower_price": 90,
            "upper_price": 110,
            "direction": "long_spot",
            "grid_count": 20,
            "funding_mode": "total_amount",
            "total_quote_amount": 12,
            "investment_mode": "fixed_equal",
        }
    )

    assert result["ok"] is False
    assert result["plans"] == []


def test_live_grid_caps_order_amount_to_live_notional_limit(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    live_grid.save_live_grid_settings({"max_initial_orders": 3, "max_order_usdt": 22.0})
    monkeypatch.setattr(live_grid, "load_live_settings", lambda: {"max_live_notional_usdt": 10.0, "hard_max_live_notional_usdt": 50.0})
    monkeypatch.setattr(live_grid, "load_exchange_rules", lambda *_args, **_kwargs: fake_exchange_rule(5.0))

    def fake_create_plan(_signal, user_inputs):
        price = user_inputs["price"]
        quote = user_inputs["quote_amount"]
        return {
            "plan_id": f"plan_{price}",
            "symbol": user_inputs["symbol"],
            "market_type": user_inputs["market_type"],
            "side": user_inputs["side"],
            "order_type": user_inputs["order_type"],
            "price": price,
            "quote_amount": quote,
            "quantity": quote / price,
        }

    monkeypatch.setattr(live_grid, "create_live_order_plan", fake_create_plan)
    monkeypatch.setattr(live_grid, "create_live_order_preview", lambda plan: {"ok": True, "risk_errors": []})

    result = live_grid.build_live_grid_manual_order_plans(
        {
            "symbol": "REUSDT",
            "current_price": 0.48,
            "lower_price": 0.42,
            "upper_price": 0.54,
            "direction": "long_spot",
            "grid_count": 20,
            "funding_mode": "total_amount",
            "total_quote_amount": 100,
            "quote_amount": 22,
        }
    )

    assert result["ok"] is True
    assert all(item["plan"]["quote_amount"] <= 10.0 for item in result["plans"])
    assert all(item["plan"]["quote_amount"] >= 5.0 for item in result["plans"])


def test_live_grid_blocks_plan_when_exchange_rules_unavailable(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    live_grid.save_live_grid_settings({"max_initial_orders": 2, "max_order_usdt": 10.0})
    monkeypatch.setattr(live_grid, "load_exchange_rules", lambda *_args, **_kwargs: {"ok": False, "message": "交易规则获取失败"})
    monkeypatch.setattr(live_grid, "create_live_order_preview", lambda plan: {"ok": True, "risk_errors": []})

    result = live_grid.build_live_grid_manual_order_plans(
        {
            "symbol": "BTCUSDT",
            "current_price": 100,
            "lower_price": 90,
            "upper_price": 110,
            "direction": "long_spot",
            "grid_count": 4,
            "quote_amount": 10,
        }
    )

    assert result["ok"] is False
    assert result["plans"] == []


def test_live_grid_blocks_single_order_below_min_notional(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    live_grid.save_live_grid_settings({"max_initial_orders": 2, "max_order_usdt": 10.0})
    monkeypatch.setattr(live_grid, "load_exchange_rules", lambda *_args, **_kwargs: fake_exchange_rule(5.0))
    monkeypatch.setattr(live_grid, "create_live_order_preview", lambda plan: {"ok": True, "risk_errors": []})

    result = live_grid.build_live_grid_manual_order_plans(
        {
            "symbol": "BTCUSDT",
            "current_price": 100,
            "lower_price": 90,
            "upper_price": 110,
            "direction": "long_spot",
            "grid_count": 4,
            "quote_amount": 3,
        }
    )

    assert result["ok"] is False
    assert result["plans"] == []


def test_live_grid_plan_submit_spot_uses_grid_preflight(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    live_grid.save_live_grid_settings({"allow_real_order_submit": True})
    plan = {
        "plan_id": "grid_plan_1",
        "symbol": "BTCUSDT",
        "market_type": "spot",
        "side": "BUY",
        "order_type": "LIMIT",
        "price": 100,
        "quantity": 0.1,
        "quote_amount": 10,
        "source": "真实网格手动参数",
    }

    monkeypatch.setattr(live_grid, "get_live_grid_status", lambda: {"real_submit_enabled": True, "blockers": []})
    monkeypatch.setattr(live_grid, "validate_live_order_plan", lambda _plan: {"ok": True, "errors": []})
    monkeypatch.setattr(live_grid, "create_live_order_preview", lambda _plan: {"ok": True, "risk_errors": []})
    monkeypatch.setattr(live_grid, "load_exchange_rules", lambda *_args, **_kwargs: fake_exchange_rule())
    monkeypatch.setattr(live_grid, "run_spot_test_order", lambda _plan: {"ok": True, "message": "测试通过"})
    monkeypatch.setattr(live_grid, "load_api_credentials_safely", lambda _testnet=False: {"configured": True})
    monkeypatch.setattr(live_grid, "_signed_request", lambda *_args, **_kwargs: {"orderId": 123, "clientOrderId": "abc", "symbol": "BTCUSDT", "status": "NEW", "executedQty": "0"})
    monkeypatch.setattr(live_grid, "fetch_live_order_status", lambda *_args, **_kwargs: {"ok": True, "message": "已回查"})

    result = live_grid.submit_live_grid_plan_orders([{"plan": plan, "preview": {"ok": True}}], "我确认执行小资金实盘订单")

    assert result["ok"] is True
    assert result["results"][0]["order"]["source"] == "真实网格手动参数"


def test_live_grid_replenish_plan_after_spot_buy_fill(tmp_path):
    use_temp_store(tmp_path)
    record = {
        "order_id": "1001",
        "symbol": "BTCUSDT",
        "market_type": "spot",
        "side": "BUY",
        "price": 95,
        "quantity": 0.1,
        "notional": 9.5,
        "grid_level_index": 1,
        "grid_position": "long",
        "grid_direction": "long_spot",
        "grid_lower_price": 90,
        "grid_upper_price": 110,
        "grid_count": 4,
        "grid_price_step": 5,
    }
    status = {"ok": True, "order": {"status": "FILLED", "executedQty": "0.1"}}

    plan = live_grid._build_replenish_plan(record, status)

    assert plan is not None
    assert plan["side"] == "SELL"
    assert plan["price"] == 100
    assert plan["quantity"] == 0.1
    assert plan["grid_level_index"] == 2
    assert plan["grid_source_order_id"] == "1001"


def test_live_grid_runtime_skips_old_grid_records_without_metadata(tmp_path, monkeypatch):
    use_temp_store(tmp_path)
    live_grid.save_live_grid_settings({"auto_replenish_enabled": True})
    old_record = {
        "time": "2026-06-26 00:00:00",
        "order_id": "123",
        "symbol": "BTCUSDT",
        "market_type": "spot",
        "side": "BUY",
        "source": "真实网格手动参数",
    }

    monkeypatch.setattr(live_grid, "get_live_grid_status", lambda: {"real_submit_enabled": True, "blockers": []})
    monkeypatch.setattr(live_grid, "load_live_order_records", lambda _limit=500: [old_record])

    called = {"fetch": False}

    def fake_fetch(_record):
        called["fetch"] = True
        return {"ok": False}

    monkeypatch.setattr(live_grid, "_fetch_grid_order_status", fake_fetch)

    result = live_grid.run_live_grid_runtime_cycle(limit=20, force=True)

    assert result["ok"] is True
    assert result["checked"] == 0
    assert result["replenished"] == 0
    assert called["fetch"] is False


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

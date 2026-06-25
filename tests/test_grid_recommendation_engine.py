from services import grid_recommendation_engine as rec
from services import grid_trade_engine as grid
from services import market_cache


def use_temp_store(tmp_path):
    grid.DATA_DIR = tmp_path
    grid.BOTS_PATH = tmp_path / "grid_bots.json"
    grid.TRADES_PATH = tmp_path / "grid_trades.json"
    grid.EVENTS_PATH = tmp_path / "grid_events.json"
    tmp_path.mkdir(parents=True, exist_ok=True)


def test_grid_recommendations_prefer_range_bound_liquid_symbol():
    market_cache.set_ticker("RANGEUSDT", {"symbol": "RANGEUSDT", "last_price": 100, "quote_volume": 5_000_000, "price_change_percent": 1.2})
    market_cache.set_ticker("TRENDUSDT", {"symbol": "TRENDUSDT", "last_price": 150, "quote_volume": 5_000_000, "price_change_percent": 25})
    range_rows = [{"open": 100, "high": 101, "low": 99, "close": 100 + (idx % 5 - 2) * 0.2, "volume": 1000} for idx in range(120)]
    trend_rows = [{"open": 100 + idx, "high": 101 + idx, "low": 99 + idx, "close": 100 + idx, "volume": 1000} for idx in range(120)]
    market_cache.set_klines("RANGEUSDT", "1m", range_rows)
    market_cache.set_klines("TRENDUSDT", "1m", trend_rows)

    rows = rec.build_grid_recommendations(5)
    symbols = [row["symbol"] for row in rows]

    assert "RANGEUSDT" in symbols
    assert rows[0]["symbol"] == "RANGEUSDT"
    assert rows[0]["suggested_direction"] == "neutral_contract"


def test_auto_open_recommended_grids_creates_bot(tmp_path):
    use_temp_store(tmp_path)
    market_cache.set_ticker("AUTOUSDT", {"symbol": "AUTOUSDT", "last_price": 100, "quote_volume": 8_000_000, "price_change_percent": 0.5})
    rows = [{"open": 100, "high": 101, "low": 99, "close": 100 + (idx % 4 - 2) * 0.1, "volume": 1000} for idx in range(120)]
    market_cache.set_klines("AUTOUSDT", "1m", rows)

    result = rec.auto_open_recommended_grids(max_bots=1, min_score=50, investment_usdt=100, grid_count=10, fee_rate=0.0)

    assert result["opened_count"] == 1
    saved = grid.load_grid_bots()[0]
    assert saved["symbol"] == "AUTOUSDT"
    assert saved["auto_opened"] is True


def test_auto_open_recommended_grids_skips_active_symbol(tmp_path):
    use_temp_store(tmp_path)
    market_cache.set_ticker("SKIPUSDT", {"symbol": "SKIPUSDT", "last_price": 100, "quote_volume": 500_000_000, "price_change_percent": 0.5})
    rows = [{"open": 100, "high": 101, "low": 99, "close": 100 + (idx % 4 - 2) * 0.1, "volume": 1000} for idx in range(120)]
    market_cache.set_klines("SKIPUSDT", "1m", rows)
    grid.create_grid_bot("SKIPUSDT", 98, 102, 10, 100, 100, 0.0)

    result = rec.auto_open_recommended_grids(max_bots=1, min_score=50, investment_usdt=100, grid_count=10, fee_rate=0.0)

    symbols = [bot["symbol"] for bot in grid.load_grid_bots()]
    assert symbols.count("SKIPUSDT") == 1
    assert any(row["symbol"] == "SKIPUSDT" and "已有" in row["reason"] for row in result["skipped"])

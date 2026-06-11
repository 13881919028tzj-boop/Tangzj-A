from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import whale_monitor as wm


def test_whale_snapshot_standard_fields():
    now = 1_800_000_000_000
    raw = [
        {"p": "100000", "q": "2", "T": now, "m": False},
        {"p": "100100", "q": "1.5", "T": now - 60_000, "m": True},
        {"p": "99900", "q": "0.01", "T": now - 120_000, "m": False},
    ]
    original_request = wm._request_futures
    original_datetime = wm.datetime

    class FakeDateTime:
        @classmethod
        def now(cls):
            class FakeNow:
                def timestamp(self):
                    return now / 1000

                def strftime(self, fmt):
                    return "2026-06-08 20:12:52"

            return FakeNow()

        @classmethod
        def fromtimestamp(cls, value):
            class FakeTs:
                def strftime(self, fmt):
                    return "20:12:52"

            return FakeTs()

    try:
        wm._request_futures = lambda path, params=None: raw
        wm.datetime = FakeDateTime
        snapshot = wm.get_whale_snapshot("BTCUSDT", {"quote_volume": 1_000_000_000, "price_change_percent": 1.2}, {})
    finally:
        wm._request_futures = original_request
        wm.datetime = original_datetime

    assert snapshot["symbol"] == "BTCUSDT"
    assert snapshot["whale_score"] >= 20
    assert snapshot["active_buy_amount"] == 200000
    assert snapshot["active_sell_amount"] == 150150
    assert snapshot["net_inflow_5m"] == 49850
    assert snapshot["buy_whale_count"] == 1
    assert snapshot["sell_whale_count"] == 1
    assert snapshot["largest_buy_order"]["amount"] == 200000
    assert snapshot["largest_sell_order"]["amount"] == 150150
    assert snapshot["data_quality"] == "good"
    assert snapshot["debug"]["raw_trade_count"] == 3


if __name__ == "__main__":
    test_whale_snapshot_standard_fields()
    print("whale_monitor tests passed")

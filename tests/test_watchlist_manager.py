"""观察池管理器基础测试。

运行：
py -3.12 tests/test_watchlist_manager.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import services.watchlist_manager as wm  # noqa: E402
from services.watchlist_manager import (  # noqa: E402
    add_to_watchlist,
    get_watchlist,
    get_watchlist_candidates_for_committee,
    load_alerts,
    remove_from_watchlist,
    save_alerts,
    sync_watchlist_from_rankings,
    update_watchlist_item,
)


def _strategy(action: str = "轻仓试多", confidence: int = 72, risk: int = 43, opportunity: int = 78) -> dict:
    return {
        "direction": "long",
        "action": action,
        "strategy_name": "回踩确认",
        "confidence": confidence,
        "risk_score": risk,
        "opportunity_score": opportunity,
        "trade_permission": "cautious" if action != "禁止开仓" else "blocked",
        "position_suggestion": "3%-5%" if action != "禁止开仓" else "0%",
        "invalid_condition": "跌破关键支撑后信号失效。",
        "local_vote_score": 76,
        "local_vote_grade": "B",
        "local_vote_decision": "轻仓支持",
        "data_quality": {"level": "good", "missing_fields": []},
    }


def main() -> None:
    tmp = tempfile.TemporaryDirectory()
    tmp_path = Path(tmp.name)
    wm.WATCHLIST_FILE = tmp_path / "watchlist.json"
    wm.ALERTS_FILE = tmp_path / "watchlist_alerts.json"
    wm.HISTORY_FILE = tmp_path / "watchlist_history.csv"

    symbol = "ZZZTESTUSDT"
    remove_from_watchlist(symbol)
    add_to_watchlist(symbol, source="manual")
    first = update_watchlist_item(symbol, _strategy(), {"last_price": 100, "price_change_percent": 2.1})
    assert first["symbol"] == symbol
    assert first["watch_score"] > 0
    assert first["tracking"]["status"] in {"新机会", "持续观察", "机会增强", "等待确认"}
    second = update_watchlist_item(symbol, _strategy(confidence=86, opportunity=88, risk=35), {"last_price": 103, "price_change_percent": 3.4})
    assert second["tracking"]["status"] in {"机会增强", "持续观察", "新机会"}
    assert second["history"], "history missing"
    candidates = get_watchlist_candidates_for_committee()
    assert isinstance(candidates, list)
    assert any(item["symbol"] == symbol for item in get_watchlist())
    remove_from_watchlist(symbol)

    board_symbol = "ZZZBOARDUSDT"
    remove_from_watchlist(board_symbol)
    sync_watchlist_from_rankings(
        {
            "long_opportunities": [
                {
                    "symbol": board_symbol,
                    "direction": "long",
                    "advice": "重点候选",
                    "final_opportunity_score": 84,
                    "risk_score": 31,
                    "data_quality": "good",
                    "last_price": 1.23,
                    "price_change_percent": 4.5,
                    "opportunity_status": "可交易候选",
                }
            ]
        },
        max_items=100,
    )
    board_item = next(item for item in get_watchlist() if item["symbol"] == board_symbol)
    assert board_item["category"] != "expired"
    assert board_item["tracking"]["status"] != "信号失效"
    remove_from_watchlist(board_symbol)

    save_alerts([alert for alert in load_alerts() if alert.get("symbol") != symbol])
    tmp.cleanup()
    print("watchlist manager ok")


if __name__ == "__main__":
    main()

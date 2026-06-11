"""本地策略引擎场景回归测试。

运行：
py -3.12 tests/test_local_strategy_engine.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.local_strategy_engine import build_local_strategy  # noqa: E402


def _rows(start: float = 100.0, step: float = 0.1, count: int = 120) -> list[dict]:
    rows = []
    price = start
    for index in range(count):
        price += step
        rows.append({"open": price - step, "high": price + 0.5, "low": price - 0.5, "close": price, "volume": 1000 + index})
    return rows


def _base(change: float = 2.0, trend_score: int = 70, risk_score: int = 35, structure: str = "上升趋势") -> dict:
    return {
        "symbol": "TESTUSDT",
        "ticker": {"last_price": 112, "price_change_percent": change, "quote_volume": 10_000_000},
        "rows": _rows(),
        "signal_analysis": {
            "trend_score": trend_score,
            "risk_score": risk_score,
            "market_structure": structure,
            "macd_signal": "多头延续" if trend_score >= 50 else "空头延续",
            "rsi": 58 if trend_score >= 50 else 42,
            "support": 108,
            "resistance": 118,
            "ma20": 110,
            "ma60": 105,
        },
        "orderbook_analysis": {"buy_ratio": 62, "sell_ratio": 38},
        "derivatives": {
            "oi": {"changes": {"1h": 2.0}},
            "funding": {"rate": 0.0001},
            "long_short": {"account_ratio": 1.2},
        },
        "capital": {"score": 72},
        "liquidation": {"risk_score": 35, "risk_level": "低", "squeeze_state": "正常"},
        "whale": {"score": 62, "level": "大单活跃", "stats": {"5m": {"net_amount": 300_000}}},
        "dealer": {"state": "疑似吸筹"},
        "radar": {"overall_score": 38, "market_state": "健康上涨"},
    }


def _run_case(name: str, expected_actions: set[str], **overrides) -> None:
    data = _base()
    data.update(overrides)
    result = build_local_strategy(primary_timeframe="15m", **data)
    assert result["action"] in expected_actions, f"{name} failed: {result}"
    assert isinstance(result.get("local_vote_score"), int), f"{name} missing local_vote_score"
    assert result.get("local_vote_grade") in {"S", "A", "B", "C", "D"}, f"{name} invalid vote grade"
    assert result.get("local_vote_decision") in {"支持交易", "轻仓支持", "只观察", "反对交易"}, f"{name} invalid vote decision"
    sections = result.get("analysis_sections") or {}
    assert sections.get("long_reasons"), f"{name} missing long reasons"
    assert sections.get("short_reasons"), f"{name} missing short reasons"
    assert sections.get("current_risks"), f"{name} missing current risks"
    assert sections.get("signal_conflicts"), f"{name} missing signal conflicts"
    assert sections.get("blocked_reasons"), f"{name} missing blocked reasons"
    assert len(result.get("score_breakdown") or []) >= 8, f"{name} missing score breakdown"
    assert result.get("data_quality_handling"), f"{name} missing data quality handling"
    if result["trade_permission"] == "blocked":
        assert result["local_vote_score"] <= 59, f"{name} blocked but vote score too high: {result}"
    print(name, "=>", result["action"], result["strategy_name"], result["trade_permission"], "投票", result["local_vote_score"], result["local_vote_grade"], result["local_vote_decision"])


def main() -> None:
    _run_case("健康上涨", {"轻仓试多", "顺势做多"})
    hot = _base()
    hot["derivatives"] = {"oi": {"changes": {"1h": 4.0}}, "funding": {"rate": 0.001}, "long_short": {"account_ratio": 2.4}}
    _run_case("危险上涨", {"禁止开仓", "不建议追多"}, **hot)
    cover = _base()
    cover["derivatives"] = {"oi": {"changes": {"1h": -3.0}}, "funding": {"rate": 0.0001}, "long_short": {"account_ratio": 1.1}}
    _run_case("空头回补", {"轻仓试多", "观望", "禁止开仓"}, **cover)
    down = _base(change=-3, trend_score=25, structure="下降趋势")
    down["ticker"]["last_price"] = 88
    down["rows"] = _rows(100, -0.1)
    down["orderbook_analysis"] = {"buy_ratio": 35, "sell_ratio": 65}
    down["whale"] = {"score": 70, "level": "大单活跃", "stats": {"5m": {"net_amount": -400_000}}}
    down["dealer"] = {"state": "疑似派发"}
    _run_case("健康下跌", {"轻仓试空", "顺势做空"}, **down)
    crowded_short = dict(down)
    crowded_short["derivatives"] = {"oi": {"changes": {"1h": 1.0}}, "funding": {"rate": -0.001}, "long_short": {"account_ratio": 0.4}}
    _run_case("空头拥挤", {"禁止开仓", "观望"}, **crowded_short)
    partial = _base()
    partial["derivatives"] = None
    partial["whale"] = None
    _run_case("数据缺失", {"轻仓试多", "顺势做多", "禁止开仓"}, **partial)


if __name__ == "__main__":
    main()

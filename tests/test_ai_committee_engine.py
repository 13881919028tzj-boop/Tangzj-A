from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ai_committee_engine import run_committee_meeting


def base_strategy(**overrides):
    data = {
        "symbol": "BTCUSDT",
        "direction": "long",
        "action": "轻仓试多",
        "confidence": 72,
        "risk_score": 38,
        "opportunity_score": 78,
        "strategy_name": "回踩确认",
        "trade_permission": "cautious",
        "position_suggestion": "3%-5%",
        "risk_reward_ratio": "1:1.8",
        "invalid_condition": "跌破最近低点后信号失效。",
        "entry_zone": {"text": "回踩区间"},
        "stop_loss": {"price": 98000, "reason": "结构失效位"},
        "take_profit_1": {"price": 103000, "reason": "前高"},
        "take_profit_2": {"price": 106000, "reason": "2R"},
        "reasons": ["价格站上MA20。", "资金结构健康。"],
        "risks": ["上方仍有压力。"],
        "warnings": [],
        "data_quality": {"level": "good", "missing_fields": []},
    }
    data.update(overrides)
    return data


def test_committee_cautious_long():
    decision = run_committee_meeting(
        "BTCUSDT",
        ticker={"last_price": 100000, "price_change_percent": 2.5},
        signal_analysis={"trend_score": 76, "risk_score": 42, "market_structure": "回踩确认", "ma20": 99000, "ma60": 97000, "macd_signal": "金叉"},
        derivatives={"funding": {"rate": 0.0001}, "long_short": {"account_ratio": 1.2}, "oi": {"changes": {"1h": 2.0}}},
        capital={"score": 72, "explanation": "OI增加，Funding正常。"},
        orderbook_analysis={"buy_ratio": 60, "sell_ratio": 40, "bias": "买盘强势"},
        liquidation={"risk_score": 35, "risk_level": "低", "squeeze_state": "正常"},
        whale={"score": 66, "net_flow_15m": 50000},
        dealer={"state": "疑似吸筹", "explanation": "大单净流入。"},
        radar={"overall_score": 42, "trade_safety": "轻仓可试", "market_explanation": "风险可控。"},
        local_strategy=base_strategy(),
    )
    assert decision["trade_permission"] in {"approved", "cautious"}
    assert decision["approved_for_simulation"] is True
    assert len(decision["member_votes"]) >= 8


def test_risk_veto_blocks_trade():
    decision = run_committee_meeting(
        "BTCUSDT",
        ticker={"last_price": 100000, "price_change_percent": 5},
        signal_analysis={"trend_score": 82, "risk_score": 90, "market_structure": "加速上涨", "ma20": 95000, "ma60": 90000},
        derivatives={"funding": {"rate": 0.002}, "long_short": {"account_ratio": 3.2}, "oi": {"changes": {"1h": 8}}},
        capital={"score": 88, "explanation": "资金过热。"},
        orderbook_analysis={"buy_ratio": 64, "sell_ratio": 36, "bias": "买盘强势"},
        liquidation={"risk_score": 92, "risk_level": "极高", "squeeze_state": "多空双杀风险"},
        whale={"score": 80, "net_flow_15m": 100000},
        dealer={"state": "疑似诱多"},
        radar={"overall_score": 91, "trade_safety": "禁止开仓", "market_explanation": "综合风险极高。"},
        local_strategy=base_strategy(confidence=55, risk_score=91),
    )
    assert decision["trade_permission"] == "blocked"
    assert decision["approved_for_simulation"] is False
    assert "风险委员" in decision["veto_members"]


def test_poor_data_blocks_trade():
    decision = run_committee_meeting(
        "BTCUSDT",
        local_strategy=base_strategy(
            action="禁止开仓",
            trade_permission="blocked",
            data_quality={"level": "poor", "missing_fields": ["盘口", "K线"]},
        ),
    )
    assert decision["trade_permission"] == "blocked"
    assert decision["approved_for_simulation"] is False

"""AI交易委员会决策引擎。

本模块只做本地多委员复核、外部AI正式投票复核和安全治理，不执行真实交易。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from services.watchlist_manager import get_watchlist, get_watchlist_candidates_for_committee
except Exception:  # pragma: no cover - import fallback for direct tests
    get_watchlist = None
    get_watchlist_candidates_for_committee = None

try:
    from services.external_ai_center import build_external_ai_consensus, run_deepseek_shadow_member, run_gemini_shadow_member
except Exception:  # pragma: no cover
    build_external_ai_consensus = None
    run_deepseek_shadow_member = None
    run_gemini_shadow_member = None

try:
    from services.live_trading_center import get_live_safety_status
except Exception:  # pragma: no cover
    get_live_safety_status = None

try:
    from services.strategy_factory import get_strategy_candidates
except Exception:  # pragma: no cover
    get_strategy_candidates = None

try:
    from services.sim_trade_engine import calculate_sim_stats
except Exception:  # pragma: no cover
    calculate_sim_stats = None

try:
    from services.replay_learning_engine import analyze_replay_learning
except Exception:  # pragma: no cover
    analyze_replay_learning = None

try:
    from services.trading_committee_v91 import attach_trading_committee_v91
except Exception:  # pragma: no cover
    attach_trading_committee_v91 = None

try:
    from services.market_cognition_engine import build_market_cognition
except Exception:  # pragma: no cover
    build_market_cognition = None


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
COMMITTEE_LOG_PATH = DATA_DIR / "committee_decision_log.json"

COMMITTEE_WEIGHTS = {
    "本地策略委员": 22,
    "趋势委员": 8,
    "资金委员": 9,
    "盘口委员": 6,
    "清算委员": 7,
    "大单 / 庄家委员": 8,
    "风险委员": 14,
    "实盘安全委员": 10,
    "DeepSeek委员": 10,
    "Gemini委员": 6,
}
SHADOW_MEMBERS = {"观察池委员", "策略验证委员"}
FORMAL_EXTERNAL_MEMBERS = {"DeepSeek委员", "Gemini委员"}
OFFICIAL_MEMBERS = set(COMMITTEE_WEIGHTS)
VOTE_STRENGTH = {
    "strong_support": 1.0,
    "support": 0.75,
    "weak_support": 0.5,
    "neutral_support": 0.25,
    "observe": 0.0,
    "weak_oppose": -0.5,
    "oppose": -0.75,
    "veto": 0.0,
}
VOTE_TEXT = {
    "strong_support": "强支持",
    "support": "支持",
    "weak_support": "弱支持",
    "neutral_support": "中性偏支持",
    "observe": "建议观望",
    "weak_oppose": "弱反对",
    "oppose": "反对",
    "veto": "否决",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_committee_text(value: Any, limit: int = 260) -> str:
    text = str(value or "")
    leak_markers = [
        "HTTPSConnectionPool",
        "NameResolutionError",
        "Max retries exceeded",
        "generateContent",
        "chat/completions",
        "api.deepseek.com",
        "generativelanguage.googleapis.com",
        "api_key",
        "x-goog-api-key",
        "Authorization",
        "Traceback",
    ]
    if any(marker.lower() in text.lower() for marker in leak_markers):
        return "外部AI暂不可用，已按观望处理；本地委员会继续运行。"
    text = re.sub(r"([?&]key=)[^&\s)\"']+", r"\1[已隐藏]", text, flags=re.IGNORECASE)
    text = re.sub(r"(Authorization:\s*Bearer\s+)[^\s,;]+", r"\1[已隐藏]", text, flags=re.IGNORECASE)
    text = re.sub(r"(x-goog-api-key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,;]+", r"\1[已隐藏]", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://[^\s)\"']+", "[外部接口地址已隐藏]", text)
    text = re.sub(r"\b[A-Za-z0-9_\-]{24,}\b", "[敏感片段已隐藏]", text)
    text = re.sub(r"```.*?```", "[代码块已隐藏]", text, flags=re.DOTALL)
    text = " ".join(text.replace("\n", " ").split())
    if len(text) > limit:
        text = text[:limit].rstrip() + "..."
    return text


def _external_ai_status_text(row: dict[str, Any], label: str) -> str:
    status = str(row.get("status") or "等待")
    vote = str(row.get("vote") or row.get("vote_text") or "观望")
    risk = str(row.get("risk_level") or "中")
    if status in {"失败", "未配置", "限频缓存"} or _to_float(row.get("confidence"), 0) <= 0:
        return f"{label}{status}，按{vote}处理"
    return f"{label}{status}，投票{vote}，风险{risk}"


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    return int(round(_to_float(value, default)))


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def _direction_text(direction: str) -> str:
    if direction == "long":
        return "偏多"
    if direction == "short":
        return "偏空"
    if direction == "conflict":
        return "多空冲突"
    if direction == "unknown":
        return "数据不足"
    return "中性"


def _normalize_direction(direction: Any) -> str:
    text = str(direction or "").strip().lower()
    if text in {"long", "多", "做多", "偏多", "buy"}:
        return "long"
    if text in {"short", "空", "做空", "偏空", "sell"}:
        return "short"
    if text in {"conflict", "冲突", "多空冲突"}:
        return "conflict"
    if text in {"unknown", "数据不足", "none", ""}:
        return "unknown"
    return "neutral"


def _normalize_vote(vote: Any, *, veto: bool = False, support_trade: bool = False, direction: str = "neutral") -> str:
    if veto:
        return "veto"
    text = str(vote or "").strip().lower()
    if text in VOTE_STRENGTH:
        return text
    if "否决" in text or "veto" in text:
        return "oppose"
    if "强支持" in text or "strong" in text:
        return "strong_support"
    if "弱支持" in text:
        return "weak_support"
    if "中性偏支持" in text:
        return "neutral_support"
    if "支持" in text or "允许" in text:
        return "support" if support_trade or direction in {"long", "short"} else "neutral_support"
    if "弱反对" in text:
        return "weak_oppose"
    if "反对" in text or "高风险" in text:
        return "oppose"
    if "警告" in text:
        return "observe"
    return "observe"


def _calibrate_confidence(name: str, confidence: Any, data: dict[str, Any], vote_code: str, direction: str) -> int:
    value = _clamp(_to_float(confidence, 0))
    strategy = data.get("local_strategy") or {}
    quality = (strategy.get("data_quality") or {}).get("level", "partial")
    cap = 95
    if quality == "poor":
        cap = min(cap, 40)
    elif quality == "partial":
        cap = min(cap, 75)
    if vote_code in {"observe", "weak_oppose", "oppose"}:
        cap = min(cap, 78)
    if name == "本地策略委员":
        risk_score = _to_int(strategy.get("risk_score"), 65)
        if risk_score >= 65:
            cap = min(cap, 70)
        if direction not in {"long", "short"}:
            cap = min(cap, 65)
        cap = min(cap, 95)
    elif name == "趋势委员":
        rows = data.get("rows") or []
        if len(rows) < 60:
            cap = min(cap, 65)
        risk_score = _to_int((data.get("signal_analysis") or {}).get("risk_score"), 50)
        if risk_score >= 65:
            cap = min(cap, 75)
        cap = min(cap, 90)
    elif name == "盘口委员":
        orderbook = data.get("orderbook_analysis") or {}
        buy_ratio = _to_float(orderbook.get("buy_ratio"), 50)
        sell_ratio = _to_float(orderbook.get("sell_ratio"), 50)
        dominant = max(buy_ratio, sell_ratio)
        if dominant < 60:
            cap = min(cap, 55)
        elif dominant < 65:
            cap = min(cap, 65)
        elif dominant < 72:
            cap = min(cap, 78)
        else:
            cap = min(cap, 85)
    elif name == "资金委员":
        derivatives = data.get("derivatives") or {}
        if not derivatives.get("funding") or not derivatives.get("oi"):
            cap = min(cap, 60)
    elif name == "大单 / 庄家委员":
        if not data.get("whale"):
            cap = min(cap, 40)
    elif name == "清算委员":
        if not data.get("liquidation"):
            cap = min(cap, 45)
    if value >= 100:
        value = 95
    return _clamp(min(value, cap))


def _normalize_member(member: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    name = str(member.get("member_name", "委员"))
    is_shadow = name in SHADOW_MEMBERS or bool(member.get("shadow"))
    direction = _normalize_direction(member.get("direction"))
    veto_allowed = name in {"风险委员", "实盘安全委员", "本地策略委员", "清算委员"} and bool(member.get("veto"))
    if bool(member.get("veto")) and name in FORMAL_EXTERNAL_MEMBERS:
        member["soft_warning"] = True
        veto_allowed = False
    vote_code = _normalize_vote(member.get("vote"), veto=veto_allowed, support_trade=bool(member.get("support_trade")), direction=direction)
    if name == "盘口委员":
        orderbook = data.get("orderbook_analysis") or {}
        dominant = max(_to_float(orderbook.get("buy_ratio"), 50), _to_float(orderbook.get("sell_ratio"), 50))
        if 60 <= dominant < 65 and vote_code in {"support", "strong_support"}:
            vote_code = "weak_support"
    confidence = _calibrate_confidence(name, member.get("confidence"), data, vote_code, direction)
    weight = float(COMMITTEE_WEIGHTS.get(name, 0 if is_shadow else 1))
    vote_strength = float(VOTE_STRENGTH.get(vote_code, 0))
    weighted_score = 0.0 if is_shadow or vote_code == "veto" else round(weight * vote_strength * confidence / 100, 2)
    member.update(
        {
            "member_type": "shadow" if is_shadow else "official",
            "shadow": bool(is_shadow),
            "official": not is_shadow,
            "weight": weight,
            "vote_code": vote_code,
            "vote_text": VOTE_TEXT.get(vote_code, "建议观望"),
            "vote": VOTE_TEXT.get(vote_code, str(member.get("vote", "建议观望"))),
            "direction": direction,
            "direction_text": _direction_text(direction),
            "confidence": confidence,
            "vote_strength": vote_strength,
            "weighted_score": weighted_score,
            "participates_in_vote": not is_shadow,
            "veto": bool(vote_code == "veto" and not is_shadow),
            "soft_warning": bool(member.get("soft_warning") or member.get("soft_veto") or vote_code in {"weak_oppose", "oppose"}),
        }
    )
    if is_shadow:
        member.setdefault("shadow_reason", "当前样本不足，仅作参考，不参与正式加权投票。")
    return member


def _normalize_members(votes: list[dict[str, Any]], data: dict[str, Any]) -> list[dict[str, Any]]:
    return [_normalize_member(dict(vote), data) for vote in votes]


def _risk_level(score: int | float) -> str:
    value = _to_float(score)
    if value >= 85:
        return "极高"
    if value >= 65:
        return "高"
    if value >= 40:
        return "中"
    return "低"


def _member(
    name: str,
    direction: str,
    confidence: int,
    risk_score: int,
    vote: str,
    *,
    support_trade: bool = False,
    veto: bool = False,
    reasons: list[str] | None = None,
    risks: list[str] | None = None,
    summary: str = "",
) -> dict[str, Any]:
    return {
        "member_name": name,
        "direction": direction,
        "direction_text": _direction_text(direction),
        "confidence": _clamp(confidence),
        "risk_level": _risk_level(risk_score),
        "vote": vote,
        "support_trade": bool(support_trade),
        "veto": bool(veto),
        "reasons": list(reasons or [])[:5],
        "risks": list(risks or [])[:5],
        "summary": summary or "该委员基于当前可用数据给出保守判断。",
    }


def _direction_from_action(action: str) -> str:
    if "多" in action:
        return "long"
    if "空" in action:
        return "short"
    return "neutral"


def collect_committee_inputs(symbol: str, **kwargs: Any) -> dict[str, Any]:
    """收集委员会输入，允许页面直接传入已计算好的模块结果。"""
    return {
        "symbol": str(symbol or "BTCUSDT").upper(),
        "timestamp": _now(),
        "ticker": kwargs.get("ticker") or {},
        "rows": kwargs.get("rows") or [],
        "signal_analysis": kwargs.get("signal_analysis") or {},
        "orderbook_analysis": kwargs.get("orderbook_analysis") or {},
        "derivatives": kwargs.get("derivatives") or {},
        "capital": kwargs.get("capital") or {},
        "liquidation": kwargs.get("liquidation") or {},
        "whale": kwargs.get("whale") or {},
        "dealer": kwargs.get("dealer") or {},
        "radar": kwargs.get("radar") or {},
        "local_strategy": kwargs.get("local_strategy") or {},
        "market_cognition": kwargs.get("market_cognition") or {},
        "watchlist_item": kwargs.get("watchlist_item") or _find_watchlist_item(symbol),
    }


def _find_watchlist_item(symbol: str) -> dict[str, Any]:
    if not get_watchlist:
        return {}
    normalized = str(symbol or "").upper()
    try:
        for item in get_watchlist():
            if str(item.get("symbol", "")).upper() == normalized:
                return item
    except Exception:
        return {}
    return {}


def run_local_strategy_member(data: dict[str, Any]) -> dict[str, Any]:
    strategy = data.get("local_strategy") or {}
    action = str(strategy.get("action", "观望"))
    permission = str(strategy.get("trade_permission", "blocked"))
    quality = (strategy.get("data_quality") or {}).get("level", "poor")
    direction = str(strategy.get("direction") or _direction_from_action(action))
    confidence = _to_int(strategy.get("confidence"), 0)
    risk_score = _to_int(strategy.get("risk_score"), 85)
    reasons = list(strategy.get("reasons") or [])[:3]
    risks = list(strategy.get("risks") or [])[:3]
    veto = permission == "blocked" or quality == "poor" or action == "禁止开仓"
    if veto:
        vote = "否决交易"
        support = False
    elif direction == "long":
        vote = "支持做多"
        support = True
    elif direction == "short":
        vote = "支持做空"
        support = True
    else:
        vote = "建议观望"
        support = False
    if not reasons:
        reasons = [f"本地策略当前动作为：{action}。"]
    if not risks:
        risks = ["仍需等待更多结构确认，避免盲目开仓。"]
    return _member(
        "本地策略委员",
        direction,
        confidence,
        risk_score,
        vote,
        support_trade=support,
        veto=veto,
        reasons=reasons,
        risks=risks,
        summary=f"本地策略为基础提案层，策略为{strategy.get('strategy_name', '无有效策略')}，建议：{action}。",
    )


def run_trend_member(data: dict[str, Any]) -> dict[str, Any]:
    signal = data.get("signal_analysis") or {}
    trend_score = _to_int(signal.get("trend_score"), 50)
    risk_score = _to_int(signal.get("risk_score"), 50)
    structure = str(signal.get("market_structure", "震荡整理"))
    macd = str(signal.get("macd_signal", "等待数据"))
    price = _to_float((data.get("ticker") or {}).get("last_price"))
    ma20 = _to_float(signal.get("ma20"))
    ma60 = _to_float(signal.get("ma60"))
    reasons: list[str] = [f"当前市场结构：{structure}。", f"趋势评分为 {trend_score}/100。"]
    risks: list[str] = []
    direction = "neutral"
    vote = "建议观望"
    support = False
    if trend_score >= 70 and price and ma20 and ma60 and price >= ma20 >= ma60:
        direction, vote, support = "long", "支持做多", True
        reasons.append("价格位于MA20和MA60上方，趋势结构偏多。")
    elif trend_score <= 35 and price and ma20 and ma60 and price <= ma20 <= ma60:
        direction, vote, support = "short", "支持做空", True
        reasons.append("价格位于MA20和MA60下方，趋势结构偏空。")
    elif "假突破" in structure:
        vote = "反对交易"
        risks.append("市场结构疑似假突破，当前方向需要重新确认。")
    else:
        risks.append("趋势信号尚未形成稳定共振。")
    if "金叉" in macd:
        reasons.append("MACD信号偏多。")
    elif "死叉" in macd:
        reasons.append("MACD信号偏空。")
    if risk_score >= 70:
        risks.append("技术风险偏高，不适合追涨追空。")
    return _member("趋势委员", direction, trend_score, risk_score, vote, support_trade=support, reasons=reasons, risks=risks, summary="趋势委员主要复核多周期结构、均线和动量。")


def run_capital_member(data: dict[str, Any]) -> dict[str, Any]:
    ticker = data.get("ticker") or {}
    derivatives = data.get("derivatives") or {}
    capital = data.get("capital") or {}
    price_change = _to_float(ticker.get("price_change_percent"))
    funding_rate = _to_float(((derivatives.get("funding") or {}).get("rate")), 0)
    long_short = derivatives.get("long_short") or {}
    account_ratio = _to_float(long_short.get("account_ratio"), 1)
    oi = derivatives.get("oi") or {}
    oi_changes = oi.get("changes") or {}
    oi_1h = _to_float(oi_changes.get("1h"), 0)
    score = _to_int(capital.get("score"), 50)
    reasons = [str(capital.get("explanation") or "资金结构数据已纳入复核。")]
    risks: list[str] = []
    veto = False
    direction = "neutral"
    vote = "建议观望"
    support = False
    crowded_long = funding_rate > 0.0008 or account_ratio >= 2.0
    crowded_short = funding_rate < -0.0008 or account_ratio <= 0.5
    if price_change > 0 and oi_1h > 0 and not crowded_long:
        direction, vote, support = "long", "支持做多", True
        reasons.append("价格上涨且OI增加，Funding未明显过热，属于较健康的资金流入。")
    elif price_change > 0 and oi_1h < 0:
        direction, vote = "neutral", "建议观望"
        risks.append("上涨可能来自空头回补，不一定代表新多资金进场。")
    elif price_change > 0 and crowded_long:
        direction, vote = "long", "反对交易"
        risks.append("多头交易拥挤，继续追多容易被反向波动收割。")
        veto = funding_rate > 0.0015 or account_ratio >= 2.8
    elif price_change < 0 and oi_1h > 0 and not crowded_short:
        direction, vote, support = "short", "支持做空", True
        reasons.append("价格下跌且OI增加，空头主动加仓迹象较强。")
    elif price_change < 0 and crowded_short:
        direction, vote = "short", "反对交易"
        risks.append("空头拥挤，继续追空存在空头回补反弹风险。")
        veto = funding_rate < -0.0015 or account_ratio <= 0.35
    else:
        risks.append("资金方向暂不明确，建议等待OI和Funding进一步确认。")
    return _member("资金委员", direction, score, 100 - score if score < 50 else 45, vote, support_trade=support, veto=veto, reasons=reasons, risks=risks, summary="资金委员复核OI、Funding和多空比是否支持当前方向。")


def run_orderbook_member(data: dict[str, Any]) -> dict[str, Any]:
    orderbook = data.get("orderbook_analysis") or {}
    buy_ratio = _to_float(orderbook.get("buy_ratio"), 50)
    sell_ratio = _to_float(orderbook.get("sell_ratio"), 50)
    bias = str(orderbook.get("bias", "多空均衡"))
    reasons = [f"盘口状态：{bias}，买盘占比约 {buy_ratio:.0f}%。"]
    risks: list[str] = []
    if buy_ratio >= 58:
        return _member("盘口委员", "long", _clamp(buy_ratio), 42, "支持做多", support_trade=True, reasons=reasons + ["买盘深度强于卖盘，短线承接较好。"], risks=risks or ["盘口变化较快，只能作为短线确认。"], summary="盘口委员认为买盘力量占优。")
    if sell_ratio >= 58:
        return _member("盘口委员", "short", _clamp(sell_ratio), 42, "支持做空", support_trade=True, reasons=reasons + ["卖盘深度强于买盘，上方压制较明显。"], risks=risks or ["盘口变化较快，只能作为短线确认。"], summary="盘口委员认为卖盘力量占优。")
    return _member("盘口委员", "neutral", 50, 50, "建议观望", reasons=reasons, risks=["买卖力量差距不大，盘口暂未给出明确方向。"], summary="盘口委员认为当前盘口偏均衡。")


def run_liquidation_member(data: dict[str, Any]) -> dict[str, Any]:
    liquidation = data.get("liquidation") or {}
    risk_score = _to_int(liquidation.get("risk_score"), 50)
    squeeze = str(liquidation.get("squeeze_state", "正常"))
    risk_level = str(liquidation.get("risk_level", _risk_level(risk_score)))
    reasons = [str(liquidation.get("explanation") or f"清算状态：{squeeze}。")]
    risks: list[str] = []
    veto = risk_score >= 85 or "极高" in risk_level or "双杀" in squeeze
    direction = "neutral"
    vote = "建议观望"
    if veto:
        vote = "否决交易"
        risks.append("清算或爆仓风险过高，当前不适合开仓。")
    elif "空头挤压" in squeeze:
        direction, vote = "long", "支持做多"
        reasons.append("上方空头清算压力可能带来短线拉升，但不适合追高。")
    elif "多头踩踏" in squeeze:
        direction, vote = "short", "支持做空"
        reasons.append("下方多头清算压力可能带来短线下杀，但不适合追空。")
    else:
        risks.append("清算数据仅用于风险定位，不能单独作为入场依据。")
    return _member("清算委员", direction, max(35, 100 - risk_score), risk_score, vote, support_trade=vote.startswith("支持"), veto=veto, reasons=reasons, risks=risks, summary="清算委员复核清算密集区和爆仓风险。")


def run_whale_member(data: dict[str, Any]) -> dict[str, Any]:
    whale = data.get("whale") or {}
    dealer = data.get("dealer") or {}
    score = _to_int(whale.get("score"), 50)
    net_flow = _to_float(whale.get("net_flow_15m"), _to_float(whale.get("net_flow_5m"), 0))
    state = str(dealer.get("state", "无明显行为"))
    reasons = [str(dealer.get("explanation") or f"庄家行为状态：{state}。")]
    risks: list[str] = []
    veto = False
    direction = "neutral"
    vote = "建议观望"
    support = False
    if net_flow > 0 and score >= 60:
        direction, vote, support = "long", "支持做多", True
        reasons.append("大单净流入增强，短线资金更偏向主动买入。")
    elif net_flow < 0 and score >= 60:
        direction, vote, support = "short", "支持做空", True
        reasons.append("大单净流出增强，短线资金更偏向主动卖出。")
    else:
        risks.append("大单方向不够连续，暂不适合单独跟随。")
    if "派发" in state or "诱多" in state:
        risks.append("疑似派发或诱多，不建议追多。")
        if direction == "long":
            vote, support = "反对交易", False
    if "诱空" in state and direction == "short":
        risks.append("疑似诱空，不建议追空。")
        vote, support = "反对交易", False
    return _member("大单 / 庄家委员", direction, score, 100 - min(score, 80), vote, support_trade=support, veto=veto, reasons=reasons, risks=risks, summary="大单委员复核主动买卖和庄家行为初判。")


def run_risk_member(data: dict[str, Any]) -> dict[str, Any]:
    strategy = data.get("local_strategy") or {}
    radar = data.get("radar") or {}
    liquidation = data.get("liquidation") or {}
    derivatives = data.get("derivatives") or {}
    quality = (strategy.get("data_quality") or {}).get("level", "poor")
    risk_score = _to_int(radar.get("overall_score"), _to_int(strategy.get("risk_score"), 85))
    trade_safety = str(radar.get("trade_safety", "谨慎交易"))
    funding_rate = _to_float(((derivatives.get("funding") or {}).get("rate")), 0)
    account_ratio = _to_float(((derivatives.get("long_short") or {}).get("account_ratio")), 1)
    liquidation_score = _to_int(liquidation.get("risk_score"), 50)
    reasons = [str(radar.get("market_explanation") or "风险委员已读取综合风险雷达。")]
    risks: list[str] = []
    veto = False
    if risk_score >= 85:
        risks.append("综合风险评分达到极高区间。")
        veto = True
    if "禁止" in trade_safety:
        risks.append("交易安全等级已提示禁止开仓。")
        veto = True
    if quality == "poor":
        risks.append("本地策略数据质量不足。")
        veto = True
    if liquidation_score >= 85:
        risks.append("清算风险极高。")
        veto = True
    if abs(funding_rate) >= 0.0015:
        risks.append("Funding处于极端区间。")
        veto = True
    if account_ratio >= 2.8 or account_ratio <= 0.35:
        risks.append("多空比处于极端拥挤状态。")
        veto = True
    if not risks:
        risks.append("未触发强制风险否决，但仍需控制仓位。")
    vote = "否决交易" if veto else ("建议观望" if risk_score >= 65 else "支持交易")
    return _member("风险委员", "neutral", max(20, 100 - risk_score), risk_score, vote, support_trade=(vote == "支持交易"), veto=veto, reasons=reasons, risks=risks, summary="风险委员只判断能不能交易，拥有最高否决权。")


def run_watchlist_member(data: dict[str, Any]) -> dict[str, Any]:
    item = data.get("watchlist_item") or {}
    if not item:
        member = _member("观察池委员", "neutral", 45, 50, "建议观望", reasons=["当前交易对象尚未进入观察池。"], risks=["缺少持续跟踪历史，委员会只做当前快照复核。"], summary="观察池委员等待更多跟踪记录。")
        member["shadow"] = True
        return member
    tracking = item.get("tracking") or {}
    status = str(tracking.get("status", "持续观察"))
    category = str(item.get("category", "manual"))
    watch_score = _to_int(item.get("watch_score"), 0)
    strategy = item.get("local_strategy") or {}
    direction = str(strategy.get("direction", "neutral"))
    reasons = [str(item.get("watch_explanation") or tracking.get("status_explanation") or f"观察池状态：{status}。")]
    risks: list[str] = []
    vote = "建议观望"
    support = False
    if category == "key_tracking" and status in {"机会增强", "持续观察"} and watch_score >= 65:
        vote, support = ("支持做多" if direction == "long" else "支持做空" if direction == "short" else "建议观望"), direction in {"long", "short"}
        reasons.append("该对象属于重点跟踪池，观察评分较高。")
    elif status == "风险升高" or category == "high_risk":
        vote = "反对交易"
        risks.append("观察池显示风险升高，需要等待新确认。")
    elif status == "信号失效" or category == "expired":
        vote = "反对交易"
        risks.append("观察池信号已失效，不适合进入交易候选。")
    else:
        risks.append("观察池尚未形成足够强的持续跟踪信号。")
    member = _member("观察池委员", direction, watch_score, max(30, 100 - watch_score), vote, support_trade=support, reasons=reasons, risks=risks, summary=f"观察池状态：{status}。")
    member["shadow"] = True
    return member


def run_strategy_validation_member(data: dict[str, Any]) -> dict[str, Any]:
    local = data.get("local_strategy") or {}
    strategy_name = str(local.get("strategy_name") or "本地策略")
    reasons: list[str] = []
    risks: list[str] = []
    confidence = 45
    vote = "建议观望"
    support = False
    grade = "暂无评级"
    simulation_status = "验证不足"
    try:
        candidates = list(get_strategy_candidates() if get_strategy_candidates else [])
    except Exception:
        candidates = []
    try:
        stats = calculate_sim_stats() if calculate_sim_stats else {}
    except Exception:
        stats = {}
    try:
        replay = analyze_replay_learning() if analyze_replay_learning else {}
    except Exception:
        replay = {}
    matched = None
    for item in candidates:
        if str(item.get("strategy_name", "")) == strategy_name or str(item.get("strategy_id", "")) in str(local.get("strategy_id", "")):
            matched = item
            break
    total_trades = _to_int(stats.get("total_trades"), 0)
    profit_factor = _to_float(stats.get("profit_factor"), 0)
    max_drawdown = _to_float(stats.get("max_drawdown"), 100)
    if matched:
        grade = str(matched.get("grade", "暂无评级"))
        reasons.append(f"策略工厂存在候选记录，评级为 {grade}。")
    else:
        risks.append("策略工厂暂未找到完全匹配的候选策略记录。")
    if total_trades >= 30 and profit_factor >= 1.2 and max_drawdown <= 15:
        simulation_status = "已验证"
        confidence = 68
        vote = "支持"
        support = True
        reasons.append(f"模拟交易样本 {total_trades} 笔，Profit Factor {profit_factor:.2f}，最大回撤 {max_drawdown:.2f}%。")
    elif total_trades:
        risks.append(f"模拟交易样本不足或表现未达标：{total_trades} 笔，Profit Factor {profit_factor:.2f}。")
        confidence = 48
    else:
        risks.append("尚无足够模拟交易样本，策略验证委员不能直接支持开仓。")
    if grade in {"D", "E"}:
        vote = "反对"
        support = False
        simulation_status = "高风险"
        risks.append("策略评级偏低，不建议进入交易执行。")
    replay_summary = replay.get("summary", {}) if isinstance(replay, dict) else {}
    if replay_summary.get("data_quality") == "poor":
        risks.append("复盘学习样本不足，策略验证仅供参考。")
    member = _member(
        "策略验证委员",
        str(local.get("direction") or _direction_from_action(str(local.get("action", "")))),
        confidence,
        45 if support else 62,
        vote,
        support_trade=support,
        reasons=reasons or ["策略验证委员已读取策略工厂、模拟交易和复盘学习摘要。"],
        risks=risks or ["历史验证通过也不代表未来收益，仍需风控限制。"],
        summary=f"策略验证状态：{simulation_status}，评级：{grade}。",
    )
    member["strategy_grade"] = grade
    member["simulation_status"] = simulation_status
    member["shadow"] = True
    return member


def run_live_safety_member(data: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    risks: list[str] = []
    veto = False
    risk_level = 45
    try:
        status = get_live_safety_status() if get_live_safety_status else {}
    except Exception as exc:
        status = {}
        veto = True
        risks.append(f"实盘安全中心读取失败，保守触发安全否决：{exc!r}")
    settings = status.get("settings") or {}
    permission = status.get("permission") or {}
    withdraw = status.get("withdraw") or {}
    mode = str(settings.get("mode", "read_only"))
    if settings.get("kill_switch_enabled"):
        veto = True
        risk_level = 95
        risks.append("实盘安全锁已开启，禁止进入任何交易执行流程。")
    if withdraw.get("status") == "高危开启":
        veto = True
        risk_level = 90
        risks.append("API 提现权限存在高危状态，实盘安全委员触发否决。")
    if mode in {"testnet", "live_manual"} and not permission.get("ok"):
        veto = True
        risk_level = 85
        risks.append("当前模式需要更完整的 API 权限检查，但检查未通过。")
    if mode == "live_manual" and not settings.get("live_manual_enabled"):
        veto = True
        risk_level = 88
        risks.append("Live Manual 在本版本默认禁用。")
    if not risks:
        reasons.append("实盘安全中心未发现硬阻断项；当前仍不会执行真实订单。")
        if mode == "read_only":
            risks.append("当前为只读模式，不能进入真实执行，只允许安全审查和订单预览。")
    vote = "否决" if veto else ("警告" if mode == "read_only" else "允许")
    member = _member(
        "实盘安全委员",
        "neutral",
        40 if veto else 64,
        risk_level,
        vote,
        support_trade=False,
        veto=veto,
        reasons=reasons,
        risks=risks,
        summary="实盘安全委员负责 API 权限、安全锁、交易所规则和实盘准入，拥有硬否决权。",
    )
    member["live_mode"] = mode
    member["live_safety_status"] = {
        "kill_switch_enabled": bool(settings.get("kill_switch_enabled")),
        "permission_status": permission.get("permission_status"),
        "withdraw_status": withdraw.get("status"),
        "allow_live_candidate": bool(status.get("allow_live_candidate")),
    }
    return member


def calculate_committee_vote(data: dict[str, Any]) -> dict[str, Any]:
    votes = list(data.get("member_votes") or [])
    long_score = short_score = risk_penalty = 0.0
    support_weight = observe_weight = oppose_weight = shadow_weight = veto_weight = soft_warning_weight = 0.0
    total_weight = 0.0
    shadow_notes: list[str] = []
    official_names: list[str] = []
    supporting_names: list[str] = []
    opposing_names: list[str] = []
    for vote in votes:
        name = str(vote.get("member_name"))
        weight = float(vote.get("weight", COMMITTEE_WEIGHTS.get(name, 0)))
        vote_code = str(vote.get("vote_code") or _normalize_vote(vote.get("vote"), veto=bool(vote.get("veto"))))
        direction = str(vote.get("direction", "neutral"))
        confidence = _to_float(vote.get("confidence"), 50) / 100
        if name in SHADOW_MEMBERS or vote.get("shadow") or vote.get("member_type") == "shadow":
            shadow_weight += weight
            shadow_notes.append(f"{name} 当前为影子模式，不参与正式加权投票。")
            continue
        official_names.append(name)
        confidence = _to_float(vote.get("confidence"), 50) / 100
        total_weight += weight
        if vote_code == "veto" or vote.get("veto"):
            veto_weight += weight
            risk_penalty += weight * 1.8
            opposing_names.append(name)
            continue
        if vote.get("soft_warning") or vote.get("soft_veto"):
            soft_warning_weight += weight
        if vote_code in {"strong_support", "support", "weak_support", "neutral_support"}:
            support_weight += weight
            supporting_names.append(name)
            score = weight * float(VOTE_STRENGTH.get(vote_code, 0)) * confidence
            if direction == "long":
                long_score += score
            elif direction == "short":
                short_score += score
        elif vote_code == "observe":
            observe_weight += weight
        else:
            oppose_weight += weight
            risk_penalty += weight * abs(float(VOTE_STRENGTH.get(vote_code, -0.5))) * confidence
            opposing_names.append(name)
    direction_gap = abs(long_score - short_score)
    if direction_gap < 10:
        direction = "neutral"
        direction_strength = "方向不明确"
    elif direction_gap < 20:
        direction = "long" if long_score > short_score else "short"
        direction_strength = "轻微偏向"
    elif direction_gap < 35:
        direction = "long" if long_score > short_score else "short"
        direction_strength = "中等偏向"
    else:
        direction = "long" if long_score > short_score else "short"
        direction_strength = "强偏向"
    if direction == "long":
        base_confidence = min(95, long_score / max(total_weight, 1) * 100 + support_weight * 0.25)
    elif direction == "short":
        base_confidence = min(95, short_score / max(total_weight, 1) * 100 + support_weight * 0.25)
    else:
        base_confidence = max(20, 60 - (observe_weight + oppose_weight) * 0.35)
    confidence = _clamp(base_confidence - risk_penalty * 0.4)
    resonance_level = "no_resonance"
    if veto_weight > 0:
        resonance_level = "blocked"
    elif support_weight >= 60 and direction != "neutral" and oppose_weight <= 18:
        resonance_level = "strong_resonance"
    elif support_weight >= 42 and direction != "neutral" and oppose_weight <= 30:
        resonance_level = "medium_resonance"
    elif support_weight >= 25 and direction != "neutral":
        resonance_level = "weak_resonance"
    if long_score > short_score:
        direction = "long"
    elif short_score > long_score:
        direction = "short"
    if direction_gap < 10:
        direction = "neutral"
    support_pct = support_weight / max(total_weight, 1) * 100
    observe_pct = observe_weight / max(total_weight, 1) * 100
    oppose_pct = (oppose_weight + veto_weight) / max(total_weight, 1) * 100
    return {
        "direction": direction,
        "long_score": round(long_score, 2),
        "short_score": round(short_score, 2),
        "direction_gap": round(direction_gap, 2),
        "direction_strength": direction_strength,
        "risk_penalty": round(risk_penalty, 2),
        "confidence": confidence,
        "committee_weights": COMMITTEE_WEIGHTS,
        "shadow_notes": shadow_notes,
        "support_weight": round(support_weight, 2),
        "observe_weight": round(observe_weight, 2),
        "oppose_weight": round(oppose_weight, 2),
        "veto_weight": round(veto_weight, 2),
        "soft_warning_weight": round(soft_warning_weight, 2),
        "shadow_weight": round(shadow_weight, 2),
        "formal_weight": round(total_weight, 2),
        "support_pct": round(support_pct, 2),
        "observe_pct": round(observe_pct, 2),
        "oppose_pct": round(oppose_pct, 2),
        "resonance_level": resonance_level,
        "official_members": official_names,
        "supporting_members": supporting_names,
        "opposing_members": opposing_names,
        "majority_support": bool(support_weight > (observe_weight + oppose_weight + veto_weight) and direction in {"long", "short"}),
    }


def apply_risk_veto(data: dict[str, Any]) -> dict[str, Any]:
    votes = list(data.get("member_votes") or [])
    veto_members = [vote for vote in votes if vote.get("veto")]
    local = data.get("local_strategy") or {}
    if str(local.get("trade_permission")) == "blocked":
        if not any(v.get("member_name") == "本地策略委员" for v in veto_members):
            veto_members.append({"member_name": "本地策略委员", "risks": ["本地策略已禁止开仓。"]})
    reasons = [risk for member in veto_members for risk in list(member.get("risks") or [])[:2]]
    hard_sources = [str(member.get("member_name")) for member in veto_members]
    return {
        "blocked": bool(veto_members),
        "veto_members": veto_members,
        "reasons": reasons,
        "hard_sources": hard_sources,
    }


def _max_position_from_decision(permission: str, position: str, risk_score: int, quality: str, veto_blocked: bool) -> str:
    if permission in {"blocked", "observe_only", "watch_candidate", "no_auto_trade", "rejected"} or veto_blocked or quality == "poor":
        return "0%"
    high = 0.0
    raw = str(position or "0%").replace("%", "")
    try:
        high = float(raw.split("-")[-1])
    except (TypeError, ValueError):
        high = 0.0
    if risk_score >= 70:
        high = min(high or 3, 3)
    elif risk_score >= 55:
        high = min(high or 5, 5)
    else:
        high = min(max(high, 3), 10)
    if quality == "partial":
        high = min(high, 5)
    return f"{high:g}%"


def generate_chairman_decision(data: dict[str, Any]) -> dict[str, Any]:
    local = data.get("local_strategy") or {}
    vote_result = calculate_committee_vote(data)
    veto = apply_risk_veto(data)
    member_votes = list(data.get("member_votes") or [])
    risk_score = _to_int((data.get("radar") or {}).get("overall_score"), _to_int(local.get("risk_score"), 75))
    quality = (local.get("data_quality") or {}).get("level", "poor")
    rr = local.get("risk_reward_ratio")
    rr_value = 0.0
    if isinstance(rr, str) and ":" in rr:
        rr_value = _to_float(rr.split(":", 1)[1], 0)
    elif rr is not None:
        rr_value = _to_float(rr, 0)
    direction = vote_result["direction"]
    confidence = vote_result["confidence"]
    majority_support = bool(vote_result.get("majority_support"))
    support_weight = _to_float(vote_result.get("support_weight"), 0)
    observe_weight = _to_float(vote_result.get("observe_weight"), 0)
    oppose_weight = _to_float(vote_result.get("oppose_weight"), 0)
    veto_weight = _to_float(vote_result.get("veto_weight"), 0)
    resonance_level = str(vote_result.get("resonance_level", "no_resonance"))
    permission = "rejected"
    action = "继续观察"
    approved = False
    warnings: list[str] = []
    if veto["blocked"] or quality == "poor":
        resonance_level = "blocked"
        direction = "neutral"
        action = "禁止开仓"
        permission = "blocked"
        approved = False
        warnings.extend(veto["reasons"] or ["风险否决已触发。"])
    elif risk_score >= 85:
        resonance_level = "blocked"
        action = "禁止开仓"
        permission = "blocked"
        approved = False
        warnings.append("风险评分达到85以上，触发禁止开仓。")
    elif risk_score >= 80:
        action = "高风险观察"
        permission = "no_auto_trade"
        approved = False
        warnings.append("风险评分达到80以上，禁止自动交易，只允许人工观察或重新复核。")
    elif resonance_level == "no_resonance":
        action = "继续观察"
        permission = "observe_only"
        warnings.append("正式委员未形成有效共振，暂不进入交易候选。")
    elif resonance_level == "weak_resonance":
        action = "通过观察"
        permission = "watch_candidate"
        warnings.append("正式委员只有弱共振，仅进入观察候选。")
    elif rr_value and rr_value < 1.2:
        if majority_support and direction != "neutral":
            action = "轻仓试多" if direction == "long" else "轻仓试空"
            permission = "simulation_or_approval"
            approved = True
            warnings.append("当前风险收益比不足 1:1.2，但正式委员权重多数支持；本轮仅允许轻仓候选，需后续交易规则细化。")
        else:
            action = "继续观察"
            permission = "observe_only"
            warnings.append("当前风险收益比不足 1:1.2，且正式投票未形成多数支持。")
    elif not majority_support or direction == "neutral":
        action = "继续观察"
        permission = "observe_only"
        warnings.append(f"正式委员未形成权重多数支持：支持{support_weight:.0f}% / 观望{observe_weight:.0f}% / 反对{oppose_weight + veto_weight:.0f}%。")
    elif resonance_level == "medium_resonance" and risk_score < 65:
        action = "轻仓试多" if direction == "long" else "轻仓试空"
        permission = "simulation_or_approval"
        approved = True
        warnings.append("正式委员形成中等共振，允许进入模拟或自动交易候选。")
    elif resonance_level == "strong_resonance" and risk_score < 55:
        action = "顺势交易候选"
        permission = "candidate"
        approved = True
    else:
        action = "轻仓试多" if direction == "long" else "轻仓试空"
        permission = "simulation_or_approval"
        approved = True
    if quality == "partial" and permission == "approved":
        permission = "simulation_or_approval"
        action = "轻仓试多" if direction == "long" else "轻仓试空" if direction == "short" else "继续观察"
        warnings.append("部分数据缺失，委员会自动降级为谨慎通过。")
    position = "0%"
    if permission == "candidate":
        position = "5%-10%" if confidence >= 75 and risk_score <= 45 else "3%-5%"
    elif permission == "simulation_or_approval":
        position = "1%-3%" if risk_score >= 60 else "3%-5%"
    if quality == "partial" and position not in {"0%", "1%-3%"}:
        position = "3%-5%"
    supporting = [v["member_name"] for v in member_votes if v.get("vote_code") in {"strong_support", "support", "weak_support", "neutral_support"} and not v.get("shadow")]
    opposing = [v["member_name"] for v in member_votes if v.get("vote_code") in {"weak_oppose", "oppose", "observe"} and not v.get("shadow")]
    veto_names = [str(v.get("member_name")) for v in veto["veto_members"]]
    main_reasons = [reason for v in member_votes if v.get("support_trade") for reason in list(v.get("reasons") or [])[:1]][:6]
    main_risks = [risk for v in member_votes for risk in list(v.get("risks") or [])[:1]][:6]
    if not main_reasons:
        main_reasons = ["委员会未形成足够一致的支持理由。"]
    if not main_risks:
        main_risks = ["当前暂无强制风险，但仍需控制仓位。"]
    deepseek_vote = next((v for v in member_votes if v.get("member_name") == "DeepSeek委员"), {})
    gemini_vote = next((v for v in member_votes if v.get("member_name") == "Gemini委员"), {})
    external_ai_summary = f"{_external_ai_status_text(deepseek_vote, 'DeepSeek')}；{_external_ai_status_text(gemini_vote, 'Gemini')}。"
    shadow_summary = "；".join(str(v.get("summary", "")) for v in member_votes if v.get("shadow")) or "观察池与策略验证当前为影子委员，仅作参考。"
    mode_action = {
        "candidate": "当前可进入顺势交易候选；实盘仍需订单预览、Test Order 和人工确认。",
        "simulation_or_approval": "当前可进入自动模拟或自动交易候选。",
        "watch_candidate": "当前只进入观察候选。",
        "observe_only": "当前只观察，不创建订单。",
        "no_auto_trade": "当前禁止自动交易，只允许人工观察。",
        "blocked": "当前禁止开仓。",
    }.get(permission, "当前继续观察。")
    if permission in {"candidate", "simulation_or_approval", "watch_candidate"}:
        summary = (
            f"正式委员支持权重{support_weight:.0f}%，观望权重{observe_weight:.0f}%，反对权重{oppose_weight + veto_weight:.0f}%。"
            f"{external_ai_summary}"
            f"影子委员参考：{shadow_summary}。硬否决：无。共振等级：{resonance_level}。最终动作：{action}。{mode_action}"
        )
    elif permission == "blocked":
        summary = (
            f"正式委员支持权重{support_weight:.0f}%，观望权重{observe_weight:.0f}%，反对权重{oppose_weight + veto_weight:.0f}%。"
            f"硬否决：已触发，来源：{', '.join(veto_names) or '风险/数据质量'}。共振等级：blocked。最终动作：禁止开仓。"
        )
    else:
        summary = (
            f"正式委员支持权重{support_weight:.0f}%，观望权重{observe_weight:.0f}%，反对权重{oppose_weight + veto_weight:.0f}%。"
            f"{external_ai_summary}"
            f"影子委员参考：{shadow_summary}。共振等级：{resonance_level}。最终动作：{action}。{mode_action}"
        )
    soft_veto_members = [v for v in member_votes if v.get("soft_veto")]
    risk_max_position = _max_position_from_decision(permission, position, risk_score, quality, veto["blocked"])
    consensus = build_external_ai_consensus(deepseek_vote, gemini_vote, direction) if build_external_ai_consensus else {}
    return {
        "symbol": data.get("symbol"),
        "timestamp": _now(),
        "final_direction": direction,
        "final_direction_text": _direction_text(direction),
        "final_action": action,
        "committee_confidence": confidence,
        "committee_risk_score": risk_score,
        "trade_permission": permission,
        "resonance_level": resonance_level,
        "resonance_text": {
            "no_resonance": "无共振",
            "weak_resonance": "弱共振",
            "medium_resonance": "中等共振",
            "strong_resonance": "强共振",
            "blocked": "被否决",
        }.get(resonance_level, "无共振"),
        "position_suggestion": position,
        "system_position_suggestion": position,
        "risk_max_position": risk_max_position,
        "user_selected_position": None,
        "manual_override_allowed": bool(permission in {"candidate", "simulation_or_approval"} and not veto["blocked"] and quality != "poor"),
        "manual_override_required_confirm": False,
        "manual_override_risk_note": "用户只能在风控允许最大仓位内调整，不能绕过风险委员或实盘安全委员硬否决。",
        "approved_for_simulation": bool(approved and permission in {"candidate", "simulation_or_approval"} and not veto["blocked"] and quality != "poor"),
        "strategy_source": "本地策略 + 委员会复核",
        "entry_zone": local.get("entry_zone") or {},
        "stop_loss": local.get("stop_loss") or {},
        "take_profit_1": local.get("take_profit_1") or {},
        "take_profit_2": local.get("take_profit_2") or {},
        "risk_reward_ratio": local.get("risk_reward_ratio"),
        "invalid_condition": local.get("invalid_condition", "等待结构确认"),
        "chairman_summary": summary,
        "supporting_members": supporting,
        "opposing_members": opposing,
        "veto_members": veto_names,
        "main_reasons": main_reasons,
        "main_risks": main_risks,
        "final_warnings": (warnings or ["所有通过建议都需要严格止损，不代表确定性收益。"])[:6],
        "member_votes": member_votes,
        "vote_detail": vote_result,
        "committee_weights": COMMITTEE_WEIGHTS,
        "majority_rule": {
            "enabled": True,
            "support_weight": support_weight,
            "observe_weight": observe_weight,
            "oppose_weight": oppose_weight,
            "veto_weight": veto_weight,
            "shadow_weight": _to_float(vote_result.get("shadow_weight"), 0),
            "formal_weight": _to_float(vote_result.get("formal_weight"), 0),
            "majority_support": majority_support,
            "principle": "无硬否决时按正式委员权重少数服从多数；实盘仍需人工确认。",
        },
        "external_ai": {
            "deepseek": deepseek_vote,
            "gemini": gemini_vote,
            "external_ai_consensus": consensus,
        },
        "external_ai_snapshot": {
            "deepseek": deepseek_vote,
            "gemini": gemini_vote,
            "external_ai_consensus": consensus,
        },
        "hard_veto_status": {
            "blocked": bool(veto["blocked"]),
            "members": veto_names,
            "reasons": veto["reasons"],
        },
        "soft_veto_status": {
            "triggered": bool(soft_veto_members),
            "members": [str(v.get("member_name")) for v in soft_veto_members],
            "suggestions": [str(v.get("suggested_adjustment")) for v in soft_veto_members if v.get("suggested_adjustment")],
        },
        "data_quality": local.get("data_quality") or {"level": "poor", "missing_fields": ["本地策略"]},
    }


def generate_committee_explanation(data: dict[str, Any]) -> dict[str, Any]:
    decision = data
    if decision.get("trade_permission") in {"approved", "cautious"}:
        why = "本地策略和部分委员方向形成共振，且风险未触发强制否决，因此委员会允许进入谨慎候选。"
    elif decision.get("trade_permission") == "blocked":
        why = "风险否决或本地策略禁止开仓已触发，因此委员会禁止开仓。"
    else:
        why = "当前委员意见分歧或置信度不足，因此委员会只建议继续观察。"
    return {
        "why_pass_or_not": why,
        "max_risk": (decision.get("main_risks") or ["当前最大风险仍需等待更多数据确认。"])[0],
        "next_condition": "下一步观察价格是否站稳关键均线，同时确认盘口和大单方向是否继续支持。",
        "invalid_condition": decision.get("invalid_condition") or "等待本地策略给出失效条件。",
    }


def run_committee_meeting(symbol: str, **kwargs: Any) -> dict[str, Any]:
    """运行单个交易对象的委员会会议。"""
    data = collect_committee_inputs(symbol, **kwargs)
    if not data.get("market_cognition") and build_market_cognition:
        try:
            data["market_cognition"] = build_market_cognition(
                symbol=data.get("symbol") or symbol,
                ticker=data.get("ticker"),
                rows=data.get("rows"),
                derivatives=data.get("derivatives"),
                orderbook_analysis=data.get("orderbook_analysis"),
                whale=data.get("whale"),
                signal_analysis=data.get("signal_analysis"),
                local_strategy=data.get("local_strategy"),
            )
        except Exception:
            data["market_cognition"] = {}
    member_functions = [
        run_local_strategy_member,
        run_trend_member,
        run_capital_member,
        run_orderbook_member,
        run_liquidation_member,
        run_whale_member,
        run_watchlist_member,
        run_strategy_validation_member,
        run_risk_member,
        run_live_safety_member,
    ]
    votes: list[dict[str, Any]] = []
    for func in member_functions:
        try:
            votes.append(func(data))
        except Exception as exc:
            name = getattr(func, "__name__", "unknown")
            votes.append(_member("委员异常", "neutral", 20, 85, "反对交易", risks=[f"{name} 分析失败，委员会已降级保守处理：{_safe_committee_text(exc)}"], summary="部分委员分析失败。"))
    for name, func in [("DeepSeek委员", run_deepseek_shadow_member), ("Gemini委员", run_gemini_shadow_member)]:
        try:
            if func:
                votes.append(func(data))
            else:
                votes.append(_member(name, "neutral", 0, 50, "观望", reasons=["外部AI接入口不可用。"], risks=["主系统继续使用本地委员会判断。"], summary=f"{name} 未启用。"))
        except Exception as exc:
            votes.append(_member(name, "neutral", 0, 65, "观望", reasons=["外部AI调用失败，已自动降级。"], risks=[f"{name} 失败：{_safe_committee_text(exc)}"], summary=f"{name} 暂不可用，不影响主系统。"))
    data["member_votes"] = _normalize_members(votes, data)
    decision = generate_chairman_decision(data)
    if data.get("market_cognition"):
        decision["market_cognition"] = data.get("market_cognition")
    decision["explanation"] = generate_committee_explanation(decision)
    if attach_trading_committee_v91:
        try:
            decision = attach_trading_committee_v91(data, decision)
        except Exception as exc:
            decision["trading_committee_v91"] = {
                "version": "AI模型 9.2",
                "final_action": "WAIT",
                "final_reason": f"9.1交易委员会聚合失败，已保留旧委员会结果：{exc!r}",
                "members": [],
                "risk_judge": {"risk_verdict": "WARNING", "blocked": False, "warnings": ["9.1聚合失败，使用旧委员会兼容结果。"]},
                "position_plan": {"allow_position": False, "reason": "9.1聚合失败。"},
                "execution_plan": {"execution_allowed": False, "execution_type": "WAIT", "reason": "9.1聚合失败。"},
            }
    save_committee_decision(decision)
    return decision


def get_committee_decision(symbol: str) -> dict[str, Any]:
    """无页面输入时的安全降级接口。"""
    return run_committee_meeting(symbol)


def get_committee_candidates() -> list[dict[str, Any]]:
    if not get_watchlist_candidates_for_committee:
        return []
    try:
        return list(get_watchlist_candidates_for_committee())
    except Exception:
        return []


def get_committee_decision_for_watchlist(limit: int = 5) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for item in get_committee_candidates()[:limit]:
        decisions.append(get_committee_decision(str(item.get("symbol", ""))))
    return decisions


def get_committee_approved_signals(limit: int = 20) -> list[dict[str, Any]]:
    approved: list[dict[str, Any]] = []
    for row in load_committee_history()[:100]:
        if row.get("approved_for_simulation") and row.get("trade_permission") in {"candidate", "simulation_or_approval", "approved", "cautious"} and not row.get("veto_members") and _to_int(row.get("committee_confidence"), 0) >= 50:
            approved.append(
                {
                    "symbol": row.get("symbol"),
                    "direction": row.get("final_direction"),
                    "action": row.get("final_action"),
                    "trade_permission": row.get("trade_permission"),
                    "approved_for_simulation": row.get("approved_for_simulation"),
                    "veto_members": row.get("veto_members"),
                    "committee_confidence": row.get("committee_confidence"),
                    "risk_score": row.get("committee_risk_score"),
                    "position_suggestion": row.get("position_suggestion"),
                    "system_position_suggestion": row.get("system_position_suggestion") or row.get("position_suggestion"),
                    "risk_max_position": row.get("risk_max_position"),
                    "manual_override_allowed": row.get("manual_override_allowed"),
                    "entry_zone": row.get("entry_zone"),
                    "stop_loss": row.get("stop_loss"),
                    "take_profit_1": row.get("take_profit_1"),
                    "take_profit_2": row.get("take_profit_2"),
                    "risk_reward_ratio": row.get("risk_reward_ratio"),
                    "invalid_condition": row.get("invalid_condition"),
                    "chairman_summary": row.get("chairman_summary"),
                    "approved_time": row.get("timestamp"),
                }
            )
    return approved[:limit]


def save_committee_decision(data: dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        history = load_committee_history()
        compact = {
            "timestamp": data.get("timestamp"),
            "symbol": data.get("symbol"),
            "current_price": ((data.get("ticker") or {}).get("last_price") if data.get("ticker") else None),
            "local_strategy_action": ((data.get("local_strategy") or {}).get("action") if data.get("local_strategy") else None),
            "final_direction": data.get("final_direction"),
            "final_action": data.get("final_action"),
            "committee_confidence": data.get("committee_confidence"),
            "committee_risk_score": data.get("committee_risk_score"),
            "trade_permission": data.get("trade_permission"),
            "approved_for_simulation": data.get("approved_for_simulation"),
            "position_suggestion": data.get("position_suggestion"),
            "system_position_suggestion": data.get("system_position_suggestion"),
            "risk_max_position": data.get("risk_max_position"),
            "manual_override_allowed": data.get("manual_override_allowed"),
            "committee_weights": data.get("committee_weights"),
            "hard_veto_status": data.get("hard_veto_status"),
            "soft_veto_status": data.get("soft_veto_status"),
            "external_ai": data.get("external_ai"),
            "external_ai_snapshot": data.get("external_ai_snapshot"),
            "entry_zone": data.get("entry_zone"),
            "stop_loss": data.get("stop_loss"),
            "take_profit_1": data.get("take_profit_1"),
            "take_profit_2": data.get("take_profit_2"),
            "risk_reward_ratio": data.get("risk_reward_ratio"),
            "invalid_condition": data.get("invalid_condition"),
            "supporting_members": data.get("supporting_members"),
            "opposing_members": data.get("opposing_members"),
            "veto_members": data.get("veto_members"),
            "chairman_summary": data.get("chairman_summary"),
            "main_risks": data.get("main_risks"),
            "data_quality": (data.get("data_quality") or {}).get("level"),
        }
        history.insert(0, compact)
        COMMITTEE_LOG_PATH.write_text(json.dumps(history[:300], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[AI交易委员会] 写入日志失败 error={repr(exc)}")


def load_committee_history() -> list[dict[str, Any]]:
    try:
        if not COMMITTEE_LOG_PATH.exists():
            return []
        data = json.loads(COMMITTEE_LOG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"[AI交易委员会] 读取日志失败 error={repr(exc)}")
        return []

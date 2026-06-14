"""大单监控与庄家行为初判引擎。

使用 Binance USDT-M Futures AggTrades 公共接口识别大额成交。
本模块只读取公开成交数据，不包含任何账户、下单或交易接口。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from services.system_diagnostics import safe_binance_rest_get


BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"
BINANCE_SPOT_BASE_URL = "https://api.binance.com"
REQUEST_TIMEOUT = 10


def _request_futures(path: str, params: dict[str, Any] | None = None) -> Any:
    """请求 Binance Futures 公共接口。"""
    try:
        return safe_binance_rest_get(path, params, base_url=BINANCE_FUTURES_BASE_URL, fallback_base_url=None, timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        print(f"[Binance大单监控] Futures请求失败 path={path} params={params} error={repr(exc)}")
        raise


def _request_spot(path: str, params: dict[str, Any] | None = None) -> Any:
    """请求 Binance Spot 公共接口，用于 Futures 不支持该币种时降级。"""
    try:
        return safe_binance_rest_get(path, params, base_url=BINANCE_SPOT_BASE_URL, timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        print(f"[Binance大单监控] Spot请求失败 path={path} params={params} error={repr(exc)}")
        raise


def _to_float(value: Any, default: float = 0.0) -> float:
    """安全转换为浮点数。"""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_price(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    if abs(value) >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _format_amount(value: float) -> str:
    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.2f}K"
    return f"{value:.2f}"


def format_amount(value: float) -> str:
    """给前端调试和兼容层复用的金额格式化。"""
    return _format_amount(value)


def _threshold(symbol: str) -> float:
    """按币种动态设置大单阈值，单位 USDT。"""
    normalized = str(symbol or "").upper().strip()
    if normalized == "BTCUSDT":
        return 100_000.0
    if normalized == "ETHUSDT":
        return 50_000.0
    return 20_000.0


def _threshold_from_ticker(symbol: str, ticker: dict[str, Any] | None = None) -> float:
    """根据币种与成交额微调大单阈值，避免小币种永远无大单。"""
    base = _threshold(symbol)
    quote_volume = _to_float((ticker or {}).get("quote_volume"))
    if quote_volume <= 0:
        return base
    dynamic = quote_volume * 0.00002
    floor = 5_000.0 if symbol not in {"BTCUSDT", "ETHUSDT"} else base * 0.5
    return max(floor, min(base, dynamic if dynamic > 0 else base))


def _whale_level(score: int) -> str:
    if score <= 20:
        return "大单冷清"
    if score <= 40:
        return "大单偏弱"
    if score <= 60:
        return "大单中性"
    if score <= 80:
        return "大单活跃"
    return "大单极强"


def _empty_stats() -> dict[str, Any]:
    return {
        "count": 0,
        "buy_count": 0,
        "sell_count": 0,
        "buy_amount": 0.0,
        "sell_amount": 0.0,
        "net_amount": 0.0,
        "trade_count": 0,
    }


def _stats_for_window(trades: list[dict[str, Any]], now_ms: int, minutes: int, whale_only: bool = True) -> dict[str, Any]:
    """统计指定分钟窗口内的大单或全部成交。"""
    start_ms = now_ms - minutes * 60 * 1000
    stats = _empty_stats()
    for trade in trades:
        if int(trade["timestamp"]) < start_ms:
            continue
        stats["trade_count"] += 1
        if whale_only and not trade.get("is_whale"):
            continue
        stats["count"] += 1
        if trade["direction"] == "主动买入":
            stats["buy_count"] += 1
            stats["buy_amount"] += trade["amount"]
        else:
            stats["sell_count"] += 1
            stats["sell_amount"] += trade["amount"]
    stats["net_amount"] = stats["buy_amount"] - stats["sell_amount"]
    return stats


def _load_public_agg_trades(symbol: str) -> tuple[list[dict[str, Any]], str, str]:
    """优先使用U本位成交，失败时降级到现货成交。"""
    futures_error = ""
    try:
        raw = _request_futures("/fapi/v1/aggTrades", {"symbol": symbol, "limit": 1000})
        if isinstance(raw, list):
            return raw, "Binance Futures aggTrades", ""
    except Exception as exc:
        futures_error = repr(exc)
    try:
        raw = _request_spot("/api/v3/aggTrades", {"symbol": symbol, "limit": 1000})
        if isinstance(raw, list):
            return raw, "Binance Spot aggTrades REST fallback", futures_error
    except Exception as spot_exc:
        try:
            raw = _request_spot("/api/v3/trades", {"symbol": symbol, "limit": 1000})
            if isinstance(raw, list):
                return raw, "Binance Spot recentTrades REST fallback", f"futures={futures_error}; spot_agg={spot_exc!r}"
        except Exception as trades_exc:
            raise RuntimeError(f"大单公共成交获取失败 symbol={symbol} futures={futures_error} spot_agg={spot_exc!r} spot_trades={trades_exc!r}") from trades_exc
    return [], "Binance public trades", futures_error


def _direction_status(stats_5m: dict[str, Any]) -> tuple[str, str]:
    """判断大单方向状态。"""
    count = int(stats_5m.get("count", 0))
    buy_amount = float(stats_5m.get("buy_amount", 0))
    sell_amount = float(stats_5m.get("sell_amount", 0))
    total = buy_amount + sell_amount
    if count == 0 or total <= 0:
        return "暂无明显大单", "最近5分钟没有达到阈值的大额成交。"
    buy_ratio = buy_amount / total * 100
    if count >= 8 and buy_ratio >= 65:
        return "连续买入", "最近5分钟出现多笔主动买入大单，短线资金正在积极进场。"
    if count >= 8 and buy_ratio <= 35:
        return "连续卖出", "最近5分钟出现多笔主动卖出大单，短线抛压较重。"
    if count >= 12:
        return "大单异常活跃", "最近5分钟大单成交频繁，多空资金正在激烈博弈。"
    if buy_ratio >= 60:
        return "主动买入大单", "主动买入金额占优，短线资金偏向进攻。"
    if buy_ratio <= 40:
        return "主动卖出大单", "主动卖出金额占优，短线资金偏向撤退。"
    return "买卖对冲", "大单买卖金额接近，资金方向暂不明确。"


def _whale_score(stats_15m: dict[str, Any], price_change_percent: float, oi_change_1h: float) -> int:
    """计算大单强度评分。"""
    total_amount = float(stats_15m.get("buy_amount", 0)) + float(stats_15m.get("sell_amount", 0))
    count = int(stats_15m.get("count", 0))
    net_amount = float(stats_15m.get("net_amount", 0))
    score = 20
    if count >= 3:
        score += 15
    if count >= 8:
        score += 15
    if total_amount >= 500_000:
        score += 15
    if total_amount >= 2_000_000:
        score += 15
    if total_amount > 0:
        imbalance = abs(net_amount) / total_amount * 100
        if imbalance >= 35:
            score += 12
    if price_change_percent * net_amount > 0:
        score += 8
    if oi_change_1h * net_amount > 0:
        score += 8
    return max(0, min(100, int(round(score))))


def get_whale_snapshot(symbol: str, ticker: dict[str, Any] | None = None, derivatives: dict[str, Any] | None = None) -> dict[str, Any]:
    """获取并分析当前交易对象的大单成交。"""
    normalized = str(symbol or "").upper().strip()
    threshold = _threshold_from_ticker(normalized, ticker)
    raw, data_source, fallback_error = _load_public_agg_trades(normalized)
    now_ms = int(datetime.now().timestamp() * 1000)
    trades: list[dict[str, Any]] = []
    whales: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        price = _to_float(item.get("p"))
        quantity = _to_float(item.get("q"))
        amount = price * quantity
        timestamp = int(item.get("T") or item.get("time") or now_ms)
        # buyerMaker=True 代表买方是挂单方，主动方为卖方。
        direction = "主动卖出" if item.get("m") else "主动买入"
        trade = {
            "time": datetime.fromtimestamp(timestamp / 1000).strftime("%H:%M:%S"),
            "timestamp": timestamp,
            "price": price,
            "price_text": _format_price(price),
            "quantity": quantity,
            "quantity_text": f"{quantity:.8f}".rstrip("0").rstrip("."),
            "amount": amount,
            "amount_text": _format_amount(amount),
            "direction": direction,
            "is_whale": amount >= threshold,
        }
        trades.append(trade)
        if trade["is_whale"]:
            whales.append(trade)
    whales.sort(key=lambda trade: int(trade["timestamp"]), reverse=True)
    trades.sort(key=lambda trade: int(trade["timestamp"]), reverse=True)
    stats = {
        "1m": _stats_for_window(trades, now_ms, 1),
        "5m": _stats_for_window(trades, now_ms, 5),
        "15m": _stats_for_window(trades, now_ms, 15),
    }
    status, explanation = _direction_status(stats["5m"])
    price_change = _to_float((ticker or {}).get("price_change_percent"))
    oi_change = _to_float((((derivatives or {}).get("oi") or {}).get("changes") or {}).get("1h"))
    score = _whale_score(stats["15m"], price_change, oi_change)
    latest_buy = [trade for trade in whales if trade["direction"] == "主动买入"]
    latest_sell = [trade for trade in whales if trade["direction"] == "主动卖出"]
    recent_buy = [trade for trade in trades if trade["direction"] == "主动买入"]
    recent_sell = [trade for trade in trades if trade["direction"] == "主动卖出"]
    largest_buy = max(latest_buy, key=lambda trade: trade["amount"], default=None)
    largest_sell = max(latest_sell, key=lambda trade: trade["amount"], default=None)
    largest_recent_buy = max(recent_buy, key=lambda trade: trade["amount"], default=None)
    largest_recent_sell = max(recent_sell, key=lambda trade: trade["amount"], default=None)
    stats_5m = stats["5m"]
    stats_15m = stats["15m"]
    total_5m = _to_float(stats_5m.get("buy_amount")) + _to_float(stats_5m.get("sell_amount"))
    buy_sell_ratio = _to_float(stats_5m.get("buy_amount")) / total_5m * 100 if total_5m else 0.0
    data_quality = "good" if isinstance(raw, list) and raw else "poor"
    if data_quality == "good" and not whales:
        data_quality = "partial"
    risk_tip = "当前暂无明显大单，但成交数据正常。"
    if stats_15m["net_amount"] > 0:
        risk_tip = "大单净流入为正，注意是否与盘口买盘形成共振。"
    elif stats_15m["net_amount"] < 0:
        risk_tip = "大单净流出为负，注意主动卖出压力是否延续。"
    dealer_behavior = "无明显行为"
    if score >= 60 and stats_15m["net_amount"] > 0:
        dealer_behavior = "疑似吸筹 / 疑似拉升"
    elif score >= 60 and stats_15m["net_amount"] < 0:
        dealer_behavior = "疑似派发 / 卖盘压制"
    return {
        "symbol": normalized,
        "threshold": threshold,
        "threshold_text": _format_amount(threshold),
        "raw_trade_count": len(raw) if isinstance(raw, list) else 0,
        "trade_count": len(trades),
        "latest": whales[:8],
        "recent_trades": trades[:8],
        "stats": stats,
        "status": status,
        "explanation": explanation,
        "score": score,
        "level": _whale_level(score),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "whale_score": score,
        "whale_score_text": _whale_level(score),
        "whale_direction": status,
        "dealer_behavior": dealer_behavior,
        "risk_tip": risk_tip,
        "net_inflow_5m": stats_5m["net_amount"],
        "net_inflow_15m": stats_15m["net_amount"],
        "active_buy_amount": stats_5m["buy_amount"],
        "active_sell_amount": stats_5m["sell_amount"],
        "largest_buy_order": largest_buy or largest_recent_buy or {},
        "largest_sell_order": largest_sell or largest_recent_sell or {},
        "buy_whale_count": stats_5m["buy_count"],
        "sell_whale_count": stats_5m["sell_count"],
        "buy_sell_count_text": f"买入 {stats_5m['buy_count']} 笔 / 卖出 {stats_5m['sell_count']} 笔",
        "buy_sell_ratio": buy_sell_ratio,
        "data_quality": data_quality,
        "error": None if raw else "成交数据为空",
        "debug": {
            "symbol": normalized,
            "data_source": data_source,
            "raw_trade_count": len(raw) if isinstance(raw, list) else 0,
            "threshold": threshold,
            "stats_5m_trade_count": stats_5m.get("trade_count", 0),
            "stats_15m_trade_count": stats_15m.get("trade_count", 0),
            "active_buy_amount": stats_5m["buy_amount"],
            "active_sell_amount": stats_5m["sell_amount"],
            "buy_whale_count": stats_5m["buy_count"],
            "sell_whale_count": stats_5m["sell_count"],
            "data_quality": data_quality,
            "error": fallback_error or None,
        },
    }


def analyze_dealer_behavior(
    whale: dict[str, Any] | None,
    derivatives: dict[str, Any] | None,
    orderbook_analysis: dict[str, Any] | None,
    signal_analysis: dict[str, Any] | None,
    liquidation: dict[str, Any] | None,
) -> dict[str, Any]:
    """结合大单、盘口、衍生品和结构数据初判庄家行为。"""
    whale = whale or {}
    stats_5m = (whale.get("stats") or {}).get("5m") or _empty_stats()
    net_amount = float(stats_5m.get("net_amount", 0))
    total_amount = float(stats_5m.get("buy_amount", 0)) + float(stats_5m.get("sell_amount", 0))
    whale_score = int(_to_float(whale.get("score")))
    ob = orderbook_analysis or {}
    buy_ratio = _to_float(ob.get("buy_ratio"))
    sell_ratio = _to_float(ob.get("sell_ratio"))
    derivatives = derivatives or {}
    oi_change = _to_float((((derivatives.get("oi") or {}).get("changes") or {}).get("1h")))
    funding_percent = _to_float((derivatives.get("funding") or {}).get("rate")) * 100
    structure = str((signal_analysis or {}).get("market_structure", "等待数据"))
    trend_score = int(_to_float((signal_analysis or {}).get("trend_score"), 50))
    liq_state = str((liquidation or {}).get("squeeze_state", "正常"))

    accumulation = 20
    wash = 15
    markup = 20
    distribution = 15

    if total_amount > 0 and net_amount > 0:
        accumulation += 22
        markup += 14
    if total_amount > 0 and net_amount < 0:
        distribution += 24
        wash += 8
    if whale_score >= 70:
        accumulation += 10
        markup += 10
        distribution += 10
    if buy_ratio >= 58:
        accumulation += 12
        markup += 8
    if sell_ratio >= 58:
        distribution += 12
        wash += 8
    if oi_change > 2 and abs(funding_percent) <= 0.03:
        accumulation += 14
    if oi_change > 2 and funding_percent > 0.03:
        markup += 10
        distribution += 8
    if structure in {"横盘震荡", "回踩确认"} and net_amount > 0:
        accumulation += 18
    if structure in {"突破", "加速上涨"} and net_amount > 0:
        markup += 22
    if structure in {"假突破", "跌破"}:
        wash += 18
    if liq_state in {"多头踩踏风险", "高风险双向震荡"}:
        wash += 12
    if trend_score >= 75 and net_amount < 0:
        distribution += 18

    probabilities = {
        "accumulation": max(0, min(100, int(round(accumulation)))),
        "wash": max(0, min(100, int(round(wash)))),
        "markup": max(0, min(100, int(round(markup)))),
        "distribution": max(0, min(100, int(round(distribution)))),
    }
    max_key = max(probabilities, key=probabilities.get)
    state_map = {
        "accumulation": "疑似吸筹",
        "wash": "疑似洗盘",
        "markup": "疑似拉升",
        "distribution": "疑似派发",
    }
    state = state_map[max_key] if probabilities[max_key] >= 45 else "无明显行为"
    if funding_percent > 0.08 and trend_score >= 70:
        state = "高风险诱多"
    if funding_percent < -0.08 and trend_score <= 30:
        state = "高风险诱空"

    if state == "疑似吸筹":
        explanation = "价格结构未明显失控，同时大单净流入或盘口买盘增强，可能存在资金吸筹。"
    elif state == "疑似洗盘":
        explanation = "清算或假突破风险上升，价格可能通过快速波动清理短线筹码。"
    elif state == "疑似拉升":
        explanation = "大单主动买入、趋势结构和资金变化形成共振，短线存在拉升迹象。"
    elif state == "疑似派发":
        explanation = "主动卖出大单或盘口抛压增强，强势区需要警惕资金派发。"
    elif state == "高风险诱多":
        explanation = "Funding过热且趋势偏强，容易出现诱多后回落。"
    elif state == "高风险诱空":
        explanation = "Funding过低且趋势偏弱，容易出现诱空后反抽。"
    else:
        explanation = "当前大单、盘口和衍生品数据未形成明确庄家行为信号。"

    return {
        "state": state,
        "explanation": explanation,
        "accumulation_probability": probabilities["accumulation"],
        "wash_probability": probabilities["wash"],
        "markup_probability": probabilities["markup"],
        "distribution_probability": probabilities["distribution"],
    }

"""策略工厂回测引擎。

安全边界：
- 只做历史K线回测与策略研究。
- 不执行真实订单，不读取真实账户，不修改生产策略。
"""

from __future__ import annotations

import csv
import itertools
import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from services import kline_service
from services.strategy_factory import get_strategy_config, load_strategy_registry


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
KLINE_CACHE_DIR = DATA_DIR / "historical_klines"
RESULTS_PATH = DATA_DIR / "backtest_results.json"
TRADES_CSV_PATH = DATA_DIR / "backtest_trades.csv"
OPT_PATH = DATA_DIR / "parameter_optimization_results.json"
REPORT_DIR = DATA_DIR / "strategy_reports"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_json(path: Path, default: Any) -> Any:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            _write_json(path, default)
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[回测中心] 读取文件失败 {path.name} error={exc!r}")
        _write_json(path, default)
        return default


def _write_json(path: Path, data: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _strategy(strategy_id: str) -> dict[str, Any]:
    return next((s for s in load_strategy_registry() if s.get("strategy_id") == strategy_id), {})


def _period_limit(interval: str, period_days: int) -> int:
    minutes = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}.get(interval, 15)
    return max(120, min(1000, int(period_days * 24 * 60 / minutes)))


def _cache_path(symbol: str, interval: str, period_days: int) -> Path:
    KLINE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return KLINE_CACHE_DIR / f"{symbol.upper()}_{interval}_{period_days}d.csv"


def load_historical_klines(symbol: str, interval: str, period_days: int = 30, force_refresh: bool = False) -> list[dict[str, Any]]:
    """加载历史K线。优先用本地缓存，缓存缺失时请求 Binance 公共K线。"""
    path = _cache_path(symbol, interval, period_days)
    if path.exists() and not force_refresh:
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append({**row, "open": _to_float(row.get("open")), "high": _to_float(row.get("high")), "low": _to_float(row.get("low")), "close": _to_float(row.get("close")), "volume": _to_float(row.get("volume")), "open_time": int(_to_float(row.get("open_time")))})
        if rows:
            return rows
    rows = kline_service.get_klines(symbol, interval, _period_limit(interval, period_days))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["open_time", "open_datetime", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "open_time": row.get("open_time"),
                    "open_datetime": str(row.get("open_datetime")),
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "volume": row.get("volume"),
                }
            )
    return rows


def _sma(values: list[float], period: int, idx: int) -> float | None:
    if idx + 1 < period:
        return None
    window = values[idx + 1 - period : idx + 1]
    return sum(window) / period


def _rsi(values: list[float], period: int, idx: int) -> float | None:
    if idx < period:
        return None
    gains = []
    losses = []
    for i in range(idx + 1 - period, idx + 1):
        diff = values[i] - values[i - 1]
        gains.append(max(0.0, diff))
        losses.append(abs(min(0.0, diff)))
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = (sum(gains) / period) / avg_loss
    return 100 - 100 / (1 + rs)


def _atr(rows: list[dict[str, Any]], period: int, idx: int) -> float | None:
    if idx < period:
        return None
    trs = []
    for i in range(idx + 1 - period, idx + 1):
        prev_close = _to_float(rows[i - 1].get("close")) if i > 0 else _to_float(rows[i].get("close"))
        high = _to_float(rows[i].get("high"))
        low = _to_float(rows[i].get("low"))
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs) / period


def generate_signal(strategy_id: str, rows: list[dict[str, Any]], idx: int, config: dict[str, Any]) -> dict[str, Any]:
    closes = [_to_float(r.get("close")) for r in rows]
    volumes = [_to_float(r.get("volume")) for r in rows]
    close = closes[idx]
    rsi = _rsi(closes, 14, idx)
    atr = _atr(rows, 14, idx)
    if atr is None or rsi is None:
        return {"direction": "neutral", "action": "wait", "reason": "指标样本不足。"}
    stype = _strategy(strategy_id).get("strategy_type")
    ma_short = _sma(closes, int(config.get("ma_short", config.get("ma_period", 20))), idx)
    ma_long = _sma(closes, int(config.get("ma_long", 60)), idx)
    direction = "neutral"
    reason = "当前没有满足策略条件。"
    if stype in {"trend", "committee"} and ma_short and ma_long:
        if close > ma_short > ma_long and rsi >= _to_float(config.get("rsi_min_long", 45)) and rsi <= _to_float(config.get("rsi_max_long", 75)):
            direction, reason = "long", "价格位于短长均线上方，趋势偏多且RSI处于健康区。"
        elif close < ma_short < ma_long and rsi <= _to_float(config.get("rsi_max_short", 55)) and rsi >= _to_float(config.get("rsi_min_short", 25)):
            direction, reason = "short", "价格位于短长均线下方，趋势偏空且RSI未过度极端。"
    elif stype == "pullback":
        ma = _sma(closes, int(config.get("ma_period", 20)), idx)
        tolerance = _to_float(config.get("pullback_pct", 1.0)) / 100
        ma60 = _sma(closes, 60, idx)
        if ma and ma60 and close > ma60 and abs(close - ma) / close <= tolerance and rsi >= _to_float(config.get("rsi_low", 40)):
            direction, reason = "long", "大方向偏多，价格回踩均线附近，具备轻仓观察条件。"
        elif ma and ma60 and close < ma60 and abs(close - ma) / close <= tolerance and rsi <= _to_float(config.get("rsi_high", 65)):
            direction, reason = "short", "大方向偏空，价格反弹到均线附近，具备轻仓观察条件。"
    elif stype == "reversal":
        lookback = int(config.get("lookback", 30))
        if idx > lookback:
            prev_high = max(_to_float(r.get("high")) for r in rows[idx - lookback : idx])
            prev_low = min(_to_float(r.get("low")) for r in rows[idx - lookback : idx])
            if _to_float(rows[idx].get("high")) > prev_high and close < prev_high * (1 - _to_float(config.get("failure_pct", 0.4)) / 100):
                direction, reason = "short", "上破失败后收回，疑似假突破反打。"
            elif _to_float(rows[idx].get("low")) < prev_low and close > prev_low * (1 + _to_float(config.get("failure_pct", 0.4)) / 100):
                direction, reason = "long", "下破失败后收回，疑似诱空反打。"
    elif stype == "whale":
        lookback = int(config.get("breakout_lookback", 24))
        if idx > lookback:
            avg_vol = sum(volumes[idx - lookback : idx]) / lookback
            high = max(_to_float(r.get("high")) for r in rows[idx - lookback : idx])
            low = min(_to_float(r.get("low")) for r in rows[idx - lookback : idx])
            if volumes[idx] > avg_vol * _to_float(config.get("volume_mult", 1.8)) and close > high:
                direction, reason = "long", "放量突破近端高点，模拟大单跟随偏多。"
            elif volumes[idx] > avg_vol * _to_float(config.get("volume_mult", 1.8)) and close < low:
                direction, reason = "short", "放量跌破近端低点，模拟大单跟随偏空。"
    elif stype == "range":
        lookback = int(config.get("lookback", 60))
        if idx > lookback:
            high = max(_to_float(r.get("high")) for r in rows[idx - lookback : idx])
            low = min(_to_float(r.get("low")) for r in rows[idx - lookback : idx])
            pos = (close - low) / (high - low) * 100 if high > low else 50
            if pos <= _to_float(config.get("edge_pct", 18)) and rsi <= _to_float(config.get("rsi_low", 35)):
                direction, reason = "long", "价格接近区间支撑且RSI偏低，观察区间反弹。"
            elif pos >= 100 - _to_float(config.get("edge_pct", 18)) and rsi >= _to_float(config.get("rsi_high", 65)):
                direction, reason = "short", "价格接近区间压力且RSI偏高，观察区间回落。"
    elif stype in {"liquidation", "filter"}:
        volatility = atr / close * 100 if close else 0
        if volatility <= _to_float(config.get("max_volatility_pct", 4.0)):
            direction, reason = "neutral", "风险过滤通过，但该模板默认只作为过滤器，不单独开仓。"
    if direction == "neutral":
        return {"direction": "neutral", "action": "wait", "confidence": 0, "reason": reason, "risk_note": "未产生交易信号。", "data_quality": "good"}
    stop = close - atr * _to_float(config.get("atr_mult", 1.5)) if direction == "long" else close + atr * _to_float(config.get("atr_mult", 1.5))
    risk = abs(close - stop)
    rr = max(_to_float(config.get("rr_min", 1.2)), 1.0)
    tp1 = close + risk * rr if direction == "long" else close - risk * rr
    tp2 = close + risk * rr * 1.8 if direction == "long" else close - risk * rr * 1.8
    return {
        "timestamp": str(rows[idx].get("open_datetime", rows[idx].get("open_time", ""))),
        "direction": direction,
        "action": "open",
        "confidence": 60 + min(30, abs((rsi or 50) - 50)),
        "entry_price": close,
        "stop_loss": stop,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "risk_reward_ratio": rr,
        "reason": reason,
        "risk_note": "历史信号仅用于回测，不代表未来表现。",
        "data_quality": "good",
    }


def _close_trade(trade: dict[str, Any], candle: dict[str, Any], exit_price: float, reason: str, fee_rate: float, slippage: float) -> dict[str, Any]:
    direction = trade["direction"]
    exit_exec = exit_price * (1 - slippage) if direction == "long" else exit_price * (1 + slippage)
    qty = trade["quantity"]
    gross = (exit_exec - trade["entry_price"]) * qty if direction == "long" else (trade["entry_price"] - exit_exec) * qty
    fee = (trade["entry_price"] * qty + exit_exec * qty) * fee_rate
    pnl = gross - fee
    risk_per_unit = abs(trade["entry_price"] - trade["stop_loss"])
    trade.update({"close_time": str(candle.get("open_datetime", candle.get("open_time"))), "exit_price": exit_exec, "pnl": pnl, "pnl_pct": pnl / trade["margin"] * 100 if trade["margin"] else 0, "r_multiple": pnl / (risk_per_unit * qty) if risk_per_unit and qty else 0, "close_reason": reason, "fee": fee, "is_win": pnl > 0})
    return trade


def run_backtest(strategy_id: str, config: dict[str, Any] | None, symbol: str, timeframe: str, period_days: int = 30, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    config = {**get_strategy_config(strategy_id), **(config or {})}
    settings = {
        "initial_balance": 1000.0,
        "position_pct": 10.0,
        "allow_long": True,
        "allow_short": True,
        "leverage": 1.0,
        "fee_rate": 0.0004,
        "slippage": 0.0002,
        "max_positions": 1,
        **(settings or {}),
    }
    strategy = _strategy(strategy_id)
    rows = load_historical_klines(symbol, timeframe, period_days)
    if len(rows) < 120:
        return _empty_result(strategy_id, strategy, config, symbol, timeframe, period_days, settings, "历史数据不足，无法生成可靠回测结果。")
    equity = _to_float(settings.get("initial_balance"), 1000)
    balance = equity
    open_trade: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    fee_rate = _to_float(settings.get("fee_rate"), 0.0004)
    slippage = _to_float(settings.get("slippage"), 0.0002)
    for i in range(80, len(rows) - 1):
        candle = rows[i]
        next_candle = rows[i + 1]
        if open_trade:
            high = _to_float(candle.get("high"))
            low = _to_float(candle.get("low"))
            hit_stop = low <= open_trade["stop_loss"] if open_trade["direction"] == "long" else high >= open_trade["stop_loss"]
            hit_tp2 = high >= open_trade["take_profit_2"] if open_trade["direction"] == "long" else low <= open_trade["take_profit_2"]
            hit_tp1 = high >= open_trade["take_profit_1"] if open_trade["direction"] == "long" else low <= open_trade["take_profit_1"]
            if hit_stop:
                closed = _close_trade(open_trade, candle, open_trade["stop_loss"], "触发止损", fee_rate, slippage)
                balance += closed["pnl"]
                trades.append(closed)
                open_trade = None
            elif hit_tp2 or hit_tp1:
                target = open_trade["take_profit_2"] if hit_tp2 else open_trade["take_profit_1"]
                closed = _close_trade(open_trade, candle, target, "触发止盈2" if hit_tp2 else "触发止盈1", fee_rate, slippage)
                balance += closed["pnl"]
                trades.append(closed)
                open_trade = None
        signal = generate_signal(strategy_id, rows, i, config)
        if not open_trade and signal.get("action") == "open":
            direction = signal["direction"]
            if (direction == "long" and not settings.get("allow_long", True)) or (direction == "short" and not settings.get("allow_short", True)):
                continue
            entry = _to_float(next_candle.get("open")) * (1 + slippage if direction == "long" else 1 - slippage)
            margin = balance * _to_float(settings.get("position_pct"), 10) / 100
            notional = margin * _to_float(settings.get("leverage"), 1)
            qty = notional / entry if entry else 0
            risk = abs(entry - _to_float(signal.get("stop_loss")))
            if risk <= 0 or qty <= 0:
                continue
            open_trade = {
                "trade_id": f"bt_{len(trades)+1}",
                "symbol": symbol,
                "timeframe": timeframe,
                "strategy_id": strategy_id,
                "strategy_name": strategy.get("strategy_name"),
                "direction": direction,
                "open_time": str(next_candle.get("open_datetime", next_candle.get("open_time"))),
                "entry_price": entry,
                "quantity": qty,
                "margin": margin,
                "notional_usdt": notional,
                "stop_loss": _to_float(signal.get("stop_loss")),
                "take_profit_1": _to_float(signal.get("take_profit_1")),
                "take_profit_2": _to_float(signal.get("take_profit_2")),
                "signal_reason": signal.get("reason"),
                "risk_note": signal.get("risk_note"),
            }
        unrealized = 0.0
        if open_trade:
            close = _to_float(candle.get("close"))
            unrealized = (close - open_trade["entry_price"]) * open_trade["quantity"] if open_trade["direction"] == "long" else (open_trade["entry_price"] - close) * open_trade["quantity"]
        curve.append({"time": str(candle.get("open_datetime", candle.get("open_time"))), "equity": balance + unrealized, "realized_equity": balance})
    if open_trade:
        closed = _close_trade(open_trade, rows[-1], _to_float(rows[-1].get("close")), "回测结束平仓", fee_rate, slippage)
        balance += closed["pnl"]
        trades.append(closed)
    result = {
        "result_id": f"backtest_{int(time.time() * 1000)}",
        "created_time": _now(),
        "strategy_id": strategy_id,
        "strategy_name": strategy.get("strategy_name", strategy_id),
        "config": config,
        "symbol": symbol,
        "timeframe": timeframe,
        "period_days": period_days,
        "settings": settings,
        "data_count": len(rows),
        "trades": trades,
        "equity_curve": curve,
        "metrics": calculate_backtest_metrics(trades, curve, settings),
        "warning": "回测已尽量避免未来函数，但仍需人工验证策略逻辑。历史回测结果不代表未来收益。",
    }
    result["grade"] = grade_strategy_result(result)
    result["overfit_risk"] = detect_overfitting_risk(result)
    result["report"] = generate_strategy_report(result)
    save_backtest_result(result)
    return result


def _empty_result(strategy_id: str, strategy: dict[str, Any], config: dict[str, Any], symbol: str, timeframe: str, period_days: int, settings: dict[str, Any], message: str) -> dict[str, Any]:
    result = {"result_id": f"backtest_{int(time.time() * 1000)}", "created_time": _now(), "strategy_id": strategy_id, "strategy_name": strategy.get("strategy_name", strategy_id), "config": config, "symbol": symbol, "timeframe": timeframe, "period_days": period_days, "settings": settings, "data_count": 0, "trades": [], "equity_curve": [], "metrics": calculate_backtest_metrics([], [], settings), "grade": "E", "overfit_risk": {"level": "高", "reasons": [message]}, "warning": message}
    result["report"] = generate_strategy_report(result)
    return result


def calculate_backtest_metrics(trades: list[dict[str, Any]], curve: list[dict[str, Any]], settings: dict[str, Any]) -> dict[str, Any]:
    initial = _to_float(settings.get("initial_balance"), 1000)
    pnls = [_to_float(t.get("pnl")) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    final_equity = initial + sum(pnls)
    max_equity = initial
    max_dd = 0.0
    for point in curve:
        eq = _to_float(point.get("equity"), initial)
        max_equity = max(max_equity, eq)
        max_dd = max(max_dd, (max_equity - eq) / max_equity * 100 if max_equity else 0)
    total_profit = sum(wins)
    total_loss = abs(sum(losses))
    r_values = [_to_float(t.get("r_multiple")) for t in trades]
    return {"final_equity": final_equity, "total_pnl": sum(pnls), "return_pct": (final_equity - initial) / initial * 100 if initial else 0, "max_drawdown_pct": max_dd, "total_trades": len(trades), "win_rate": len(wins) / len(trades) * 100 if trades else 0, "profit_factor": total_profit / total_loss if total_loss else (total_profit if total_profit else 0), "avg_r": sum(r_values) / len(r_values) if r_values else 0, "max_win": max(pnls) if pnls else 0, "max_loss": min(pnls) if pnls else 0, "avg_win": sum(wins) / len(wins) if wins else 0, "avg_loss": sum(losses) / len(losses) if losses else 0}


def grade_strategy_result(result: dict[str, Any]) -> str:
    m = result.get("metrics") or {}
    trades = int(m.get("total_trades", 0) or 0)
    pf = _to_float(m.get("profit_factor"))
    dd = _to_float(m.get("max_drawdown_pct"))
    avg_r = _to_float(m.get("avg_r"))
    ret = _to_float(m.get("return_pct"))
    if trades < 10:
        return "D" if ret > 0 else "E"
    if trades < 30:
        return "B" if pf >= 1.5 and avg_r > 0 and dd <= 12 else "C" if ret > 0 else "D"
    if pf > 1.5 and dd <= 15 and avg_r > 0 and ret > 0:
        return "A"
    if pf >= 1.2 and dd <= 20 and avg_r >= 0:
        return "B"
    if ret > 0:
        return "C"
    return "D"


def detect_overfitting_risk(result: dict[str, Any]) -> dict[str, Any]:
    m = result.get("metrics") or {}
    reasons: list[str] = []
    level = "低"
    if int(m.get("total_trades", 0) or 0) < 30:
        reasons.append("交易样本不足30笔，评级仅供参考。")
        level = "高"
    if _to_float(m.get("max_drawdown_pct")) > 20:
        reasons.append("最大回撤偏大，策略稳定性不足。")
        level = "高"
    if _to_float(m.get("profit_factor")) > 3 and int(m.get("total_trades", 0) or 0) < 50:
        reasons.append("Profit Factor 很高但样本较少，可能由少数交易贡献。")
        level = "中" if level == "低" else level
    if not reasons:
        reasons.append("未发现明显过拟合信号，但仍需样本外和模拟交易验证。")
    return {"level": level, "reasons": reasons}


def save_backtest_result(result: dict[str, Any]) -> None:
    results = load_backtest_results()
    compact = {k: v for k, v in result.items() if k != "trades"}
    compact["trades_preview"] = result.get("trades", [])[:50]
    results.insert(0, compact)
    _write_json(RESULTS_PATH, results[:100])
    trades = result.get("trades", [])
    if trades:
        with TRADES_CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
            keys = sorted({k for row in trades for k in row.keys()})
            writer = csv.DictWriter(handle, fieldnames=keys)
            writer.writeheader()
            writer.writerows(trades)


def load_backtest_results() -> list[dict[str, Any]]:
    data = _read_json(RESULTS_PATH, [])
    return data if isinstance(data, list) else []


def generate_strategy_report(result: dict[str, Any]) -> str:
    m = result.get("metrics") or {}
    overfit = result.get("overfit_risk") or {}
    return "\n".join(
        [
            f"# {result.get('strategy_name')} 回测报告",
            "",
            f"- 交易对象：{result.get('symbol')}",
            f"- 周期：{result.get('timeframe')}",
            f"- 交易次数：{m.get('total_trades', 0)}",
            f"- 收益率：{_to_float(m.get('return_pct')):.2f}%",
            f"- 最大回撤：{_to_float(m.get('max_drawdown_pct')):.2f}%",
            f"- 胜率：{_to_float(m.get('win_rate')):.2f}%",
            f"- Profit Factor：{_to_float(m.get('profit_factor')):.2f}",
            f"- 平均R：{_to_float(m.get('avg_r')):.2f}",
            f"- 策略评级：{result.get('grade', 'E')}",
            f"- 过拟合风险：{overfit.get('level', '高')}",
            "",
            "## 风险说明",
            "回测结果不代表未来表现，候选策略只能进入模拟验证，不能直接进入真实交易。",
        ]
    )


def export_strategy_report(result: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"backtest_report_{result.get('result_id', int(time.time()))}.md"
    path.write_text(result.get("report") or generate_strategy_report(result), encoding="utf-8")
    return path


def run_batch_backtest(strategy_id: str, config: dict[str, Any], symbols: list[str], timeframes: list[str], period_days: int, settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [run_backtest(strategy_id, config, symbol, timeframe, period_days, settings) for symbol in symbols for timeframe in timeframes]


def run_parameter_grid_search(strategy_id: str, base_config: dict[str, Any], symbol: str, timeframe: str, period_days: int, param_grid: dict[str, list[Any]], settings: dict[str, Any] | None = None, limit: int = 24) -> list[dict[str, Any]]:
    keys = list(param_grid.keys())
    combos = list(itertools.product(*[param_grid[k] for k in keys]))[:limit]
    results = []
    for combo in combos:
        config = dict(base_config)
        config.update({key: value for key, value in zip(keys, combo)})
        result = run_backtest(strategy_id, config, symbol, timeframe, period_days, settings)
        results.append({"config": config, "metrics": result.get("metrics"), "grade": result.get("grade"), "overfit_risk": result.get("overfit_risk"), "result_id": result.get("result_id")})
    _write_json(OPT_PATH, results[:200])
    return results


def compare_strategy_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        m = result.get("metrics") or {}
        rows.append({"策略": result.get("strategy_name"), "交易对象": result.get("symbol"), "周期": result.get("timeframe"), "收益率": _to_float(m.get("return_pct")), "最大回撤": _to_float(m.get("max_drawdown_pct")), "胜率": _to_float(m.get("win_rate")), "Profit Factor": _to_float(m.get("profit_factor")), "平均R": _to_float(m.get("avg_r")), "交易次数": m.get("total_trades", 0), "评级": result.get("grade", "E"), "过拟合": (result.get("overfit_risk") or {}).get("level", "高")})
    return rows

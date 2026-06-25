"""Background auto simulation runner.

It converts opportunity-board candidates into local simulated signals only when
the simulated account is running and simulation mode is set to auto. No real
exchange order API is used here.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from services import market_cache
from services.binance_public import get_24hr_ticker
from services.fast_opportunity_engine import collect_top10_opportunities, run_committee_top10_precheck
from services.sim_trade_engine import (
    append_sim_diagnostic,
    get_open_positions,
    get_pending_early_exit_shadow_symbols,
    get_pending_orders,
    load_settings,
    load_sim_account,
    load_sim_trade_history,
    log_sim_event,
    update_simulation,
)
from services.sim_calibration_engine import evaluate_signal_ev, extract_calibration_tags
from services.sim_observation_engine import (
    get_pending_observation_symbols,
    record_observation_signal,
    update_observation_signals,
)


_LAST_RUN_AT = 0.0
_MIN_INTERVAL_SECONDS = 3.0
LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
POSITION_PRICE_LOG = LOG_DIR / "position_price_debug.log"
_LAST_PRICE_STATUS: dict[str, str] = {}
MIN_AUTO_SIM_DIRECTION_GAP = 20
MIN_AUTO_SIM_CONSENSUS_SUPPORT = 3
MIN_AUTO_SIM_MARKET_ALIGNMENT = 80
MIN_AUTO_SIM_PROFESSIONAL_SCORE = 75
MIN_AUTO_SIM_SIMULATION_SCORE = 70
MIN_AUTO_SIM_BASE_QUALITY = 70
MIN_AUTO_SIM_LIQUIDITY_QUALITY = 70
MIN_AUTO_SIM_PORTFOLIO_FIT = 60
MAX_AUTO_SIM_SAMPLING_RISK = 60
SHORT_NO_CHASE_CHANGE_PCT = -8.0
LONG_NO_CHASE_CHANGE_PCT = 8.0
LONG_CONFIRMED_ENTRY_STATES = {"pullback_confirmed", "breakout_confirmed"}
SHORT_CONFIRMED_ENTRY_STATES = {"failed_retest_confirmed", "breakdown_confirmed"}


def _debug_log(message: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with POSITION_PRICE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    return int(round(_to_float(value, default)))


def _direction(row: dict[str, Any], precheck: dict[str, Any] | None = None) -> str:
    text = " ".join(
        str(value)
        for value in [
            (precheck or {}).get("direction"),
            row.get("direction"),
            row.get("advice"),
            row.get("opportunity_status"),
            row.get("current_market_state"),
        ]
        if value
    )
    lowered = text.lower()
    if "short" in lowered or "空" in text:
        return "short"
    return "long"


def _price(row: dict[str, Any]) -> float:
    symbol = str(row.get("symbol") or "").upper()
    ticker = market_cache.get_ticker(symbol) or {}
    return (
        _to_float(ticker.get("last_price"), 0)
        or _to_float(row.get("current_price"), 0)
        or _to_float(row.get("last_price"), 0)
        or _to_float(row.get("price"), 0)
    )


def _fetch_live_price(symbol: str) -> float:
    try:
        ticker = get_24hr_ticker(symbol)
        market_cache.set_ticker(symbol, ticker)
        return _to_float(ticker.get("last_price"), 0)
    except Exception as exc:
        _debug_log(f"direct_price_fetch_failed symbol={symbol} error={repr(exc)}")
        return 0.0


def _build_price_map(opportunities: list[dict[str, Any]]) -> dict[str, float]:
    symbols = {str(item.get("symbol") or "").upper() for item in opportunities}
    symbols.update(str(item.get("symbol") or "").upper() for item in get_open_positions())
    symbols.update(str(item.get("symbol") or "").upper() for item in get_pending_orders())
    symbols.update(get_pending_early_exit_shadow_symbols())
    symbols.update(get_pending_observation_symbols())
    price_map: dict[str, float] = {}
    statuses: dict[str, str] = {}
    by_symbol = {str(item.get("symbol") or "").upper(): item for item in opportunities}
    for symbol in symbols:
        if not symbol:
            continue
        ticker = market_cache.get_ticker(symbol) or {}
        price = _to_float(ticker.get("last_price"), 0)
        status = "live" if price > 0 else "missing"
        if price <= 0:
            price = _fetch_live_price(symbol)
            status = "live" if price > 0 else "missing"
        if price <= 0:
            price = _price(by_symbol.get(symbol, {}))
            status = "ranking" if price > 0 else "missing"
        if price <= 0:
            for position in get_open_positions():
                if str(position.get("symbol") or "").upper() == symbol:
                    position_price = _to_float(position.get("current_price"))
                    if position_price > 0:
                        price = position_price
                        status = "stale"
                    break
        price_map[symbol] = max(price, 0)
        statuses[symbol] = status
    global _LAST_PRICE_STATUS
    _LAST_PRICE_STATUS = statuses
    live_count = len([s for s in statuses.values() if s in {"live", "ranking"}])
    missing_count = len([s for s in statuses.values() if s == "missing"])
    stale_count = len([s for s in statuses.values() if s == "stale"])
    _debug_log(f"build_price_map symbols={len(price_map)} live={live_count} stale={stale_count} missing={missing_count}")
    return price_map


def _risk_reward_prices(price: float, direction: str) -> tuple[float, float, float]:
    stop_pct = 0.0125
    tp1_pct = stop_pct * 1.0
    tp2_pct = stop_pct * 2.4
    if direction == "short":
        return price * (1 + stop_pct), price * (1 - tp1_pct), price * (1 - tp2_pct)
    return price * (1 - stop_pct), price * (1 + tp1_pct), price * (1 + tp2_pct)


def _sampling_reject_reasons(
    symbol: str,
    price: float,
    score: int,
    simulation_score: int,
    base_quality: int,
    liquidity_quality: int,
    portfolio_fit: int,
    risk: int,
) -> list[str]:
    reasons: list[str] = []
    if not symbol:
        reasons.append("交易对象缺失。")
    if price <= 0:
        reasons.append("价格不可用。")
    if score < MIN_AUTO_SIM_PROFESSIONAL_SCORE:
        reasons.append(f"专业分 {score} 低于自动模拟开仓阈值 {MIN_AUTO_SIM_PROFESSIONAL_SCORE}。")
    if simulation_score < MIN_AUTO_SIM_SIMULATION_SCORE:
        reasons.append(f"模拟适配分 {simulation_score} 低于自动模拟开仓阈值 {MIN_AUTO_SIM_SIMULATION_SCORE}。")
    if base_quality < MIN_AUTO_SIM_BASE_QUALITY:
        reasons.append(f"基础质量分 {base_quality} 低于{MIN_AUTO_SIM_BASE_QUALITY}。")
    if liquidity_quality < MIN_AUTO_SIM_LIQUIDITY_QUALITY:
        reasons.append(f"流动性质量 {liquidity_quality} 低于{MIN_AUTO_SIM_LIQUIDITY_QUALITY}。")
    if portfolio_fit < MIN_AUTO_SIM_PORTFOLIO_FIT:
        reasons.append(f"组合适配分 {portfolio_fit} 低于{MIN_AUTO_SIM_PORTFOLIO_FIT}。")
    if risk >= MAX_AUTO_SIM_SAMPLING_RISK:
        reasons.append(f"风险分 {risk} 达到或超过 {MAX_AUTO_SIM_SAMPLING_RISK}。")
    return reasons


def _market_bias(row: dict[str, Any], precheck: dict[str, Any]) -> str:
    bias = str(row.get("direction_bias") or precheck.get("direction_bias") or "").lower()
    if bias in {"long", "short", "neutral"}:
        return bias
    regime = str(row.get("market_regime") or precheck.get("market_regime") or "").lower()
    if regime in {"bullish", "rebound"}:
        return "long"
    if regime in {"bearish", "weak"}:
        return "short"
    return "neutral"


def _same_direction(value: Any, direction: str) -> bool:
    text = str(value or "").lower()
    if direction == "long":
        return text in {"long", "多头", "buy", "bullish"}
    if direction == "short":
        return text in {"short", "空头", "sell", "bearish"}
    return False


def _strict_auto_sim_reject_reasons(row: dict[str, Any], precheck: dict[str, Any], direction: str) -> list[str]:
    """Hard gate for auto-simulation entries.

    Opportunity-board rank and score are only candidate signals. New simulated
    positions require direction confirmation, confirmed entry structure and
    market alignment so the runner does not chase extended one-way moves.
    """
    reasons: list[str] = []
    direction_gap = _to_float(row.get("direction_gap"), _to_float(precheck.get("direction_gap"), 0))
    market_alignment = _to_float(row.get("market_alignment_score"), _to_float(precheck.get("market_alignment_score"), 0))
    consensus_count = _to_int(row.get("consensus_support_count"), _to_int(precheck.get("consensus_support_count"), 0))
    entry_state = str(row.get("entry_state") or precheck.get("entry_state") or "")
    change_pct = _to_float(row.get("price_change_percent"), _to_float(row.get("change_percent"), 0))
    risk_flags = [str(item) for item in row.get("risk_flags", []) or []]
    block_reasons = [str(item) for item in row.get("trade_block_reasons", []) or precheck.get("block_reasons", []) or []]
    kline = row.get("kline_signal") or {}
    whale = row.get("whale_signal") or {}
    orderbook = row.get("orderbook_signal") or {}

    if precheck.get("allowed_candidate") is False:
        reasons.append("委员会快速预判未允许进入候选。")
    if row.get("tradable_now") is not True:
        reasons.append("专业预审未给出 tradable_now。")
    if str(row.get("action_gate") or precheck.get("action_gate") or "") != "open_now":
        reasons.append("专业预审未进入 open_now。")
    if block_reasons:
        reasons.append("专业预审仍存在阻断原因。")
    if direction_gap < MIN_AUTO_SIM_DIRECTION_GAP:
        reasons.append(f"方向分差 {direction_gap:.0f} 低于自动模拟硬门槛 {MIN_AUTO_SIM_DIRECTION_GAP}。")
    if consensus_count < MIN_AUTO_SIM_CONSENSUS_SUPPORT:
        reasons.append(f"方向共识 {consensus_count}/5 不足，禁止仅凭机会榜开仓。")
    if market_alignment < MIN_AUTO_SIM_MARKET_ALIGNMENT or _market_bias(row, precheck) != direction:
        reasons.append("大盘未与开仓方向同向。")
    if kline and not _same_direction(kline.get("direction"), direction):
        reasons.append("K线方向未确认同向。")
    whale_same = _same_direction(whale.get("direction"), direction) and whale.get("confirming") is not False
    orderbook_same = _same_direction(orderbook.get("direction"), direction) and orderbook.get("confirming") is not False
    if not whale_same and not orderbook_same:
        reasons.append("大单或盘口没有至少一个同向确认。")
    if orderbook and orderbook.get("direction") in {"long", "short"} and not _same_direction(orderbook.get("direction"), direction):
        reasons.append("盘口方向与开仓方向冲突。")

    if direction == "short":
        if entry_state not in SHORT_CONFIRMED_ENTRY_STATES:
            reasons.append("空单未完成反抽失败/跌破确认。")
        if change_pct <= SHORT_NO_CHASE_CHANGE_PCT or any("追空" in item or "跌幅较大" in item for item in risk_flags + block_reasons):
            reasons.append("24小时跌幅较大，禁止直接追空。")
    elif direction == "long":
        if entry_state not in LONG_CONFIRMED_ENTRY_STATES:
            reasons.append("多单未完成回踩/突破确认。")
        if change_pct >= LONG_NO_CHASE_CHANGE_PCT or any("追多" in item or "涨幅较大" in item for item in risk_flags + block_reasons):
            reasons.append("24小时涨幅较大，禁止直接追多。")
    else:
        reasons.append("方向不是 long/short。")
    return reasons


def _signal_from_precheck(precheck: dict[str, Any]) -> dict[str, Any] | None:
    row = precheck.get("opportunity") or {}
    symbol = str(precheck.get("symbol") or row.get("symbol") or "").upper()
    price = _price(row)
    score = _to_int(row.get("professional_trade_score", row.get("final_opportunity_score", row.get("opportunity_score"))), _to_int(precheck.get("professional_trade_score", precheck.get("score")), 0))
    risk = _to_int(row.get("risk_score"), _to_int(precheck.get("risk_score"), 50))
    simulation_score = _to_int(row.get("simulation_score"), max(score, MIN_AUTO_SIM_SIMULATION_SCORE))
    base_quality = _to_int(row.get("base_quality_score"), max(65, min(80, simulation_score)))
    liquidity_quality = _to_int(row.get("liquidity_quality_score"), 70)
    relative_strength = _to_int(row.get("relative_strength_score"), 65)
    signal_freshness = _to_int(row.get("signal_freshness_score"), 65)
    historical_tradability = _to_int(row.get("historical_tradability_score"), 60)
    portfolio_fit = _to_int(row.get("portfolio_fit_score"), 75)
    if _sampling_reject_reasons(symbol, price, score, simulation_score, base_quality, liquidity_quality, portfolio_fit, risk):
        return None
    direction = str(precheck.get("direction") or row.get("trade_direction") or _direction(row, precheck))
    if direction not in {"long", "short"}:
        return None
    if _strict_auto_sim_reject_reasons(row, precheck, direction):
        return None
    preliminary_signal = {
        **row,
        "symbol": symbol,
        "direction": direction,
        "risk_score": risk,
        "professional_trade_score": score,
        "simulation_score": simulation_score,
        "base_quality_score": base_quality,
        "liquidity_quality_score": liquidity_quality,
        "portfolio_fit_score": portfolio_fit,
    }
    ev_check = evaluate_signal_ev(preliminary_signal, load_sim_trade_history())
    if not ev_check.get("allowed"):
        return None
    stop, tp1, tp2 = _risk_reward_prices(price, direction)
    action = "顺势做多" if direction == "long" and score >= 88 else "轻仓试多" if direction == "long" else "顺势做空" if score >= 88 else "轻仓试空"
    if simulation_score < 66 or liquidity_quality < 55 or portfolio_fit < 50 or risk >= 60:
        position_suggestion = "1%-3%"
    elif simulation_score >= 82 and liquidity_quality >= 72 and portfolio_fit >= 70 and risk <= 45:
        position_suggestion = "5%-10%"
    else:
        position_suggestion = "3%-5%"
    rank = int(precheck.get("rank", 0) or 0)
    return {
        "symbol": symbol,
        "direction": direction,
        "action": action,
        "trade_permission": "approved",
        "approved_for_simulation": True,
        "veto_members": [],
        "committee_confidence": max(60, min(95, score)),
        "risk_score": risk,
        "position_suggestion": position_suggestion,
        "system_position_suggestion": position_suggestion,
        "entry_zone": {"low": price * 0.999, "high": price * 1.001},
        "stop_loss": {"price": stop},
        "take_profit_1": {"price": tp1},
        "take_profit_2": {"price": tp2},
        "risk_reward_ratio": 2.4,
        "invalid_condition": "机会榜信号失效、风险升高或委员会后续否决。",
        "chairman_summary": (
            f"后台自动模拟：机会榜TOP{rank or '-'}候选，专业分{score}，模拟分{simulation_score}，"
            f"基础分{base_quality}，流动性{liquidity_quality}，新鲜度{signal_freshness}，"
            f"历史{historical_tradability}，组合{portfolio_fit}，风险{risk}。仅执行本地模拟订单。"
        ),
        "professional_trade_score": score,
        "simulation_score": simulation_score,
        "base_quality_score": base_quality,
        "liquidity_quality_score": liquidity_quality,
        "relative_strength_score": relative_strength,
        "signal_freshness_score": signal_freshness,
        "historical_tradability_score": historical_tradability,
        "portfolio_fit_score": portfolio_fit,
        "base_score_breakdown": row.get("base_score_breakdown"),
        "entry_state": row.get("entry_state"),
        "action_gate": "open_now",
        "tradable_now": True,
        "sampling_override": not bool(precheck.get("allowed_candidate")) or not bool(row.get("tradable_now")) or row.get("action_gate") != "open_now",
        "original_allowed_candidate": bool(precheck.get("allowed_candidate")),
        "original_action_gate": row.get("action_gate"),
        "original_tradable_now": bool(row.get("tradable_now")),
        "market_regime": row.get("market_regime"),
        "market_alignment_score": row.get("market_alignment_score"),
        "direction_gap": row.get("direction_gap"),
        "risk_flags": row.get("risk_flags", []),
        "source_opportunity_id": row.get("opportunity_id") or precheck.get("opportunity_id") or f"{symbol}_{direction}",
        "source_board_rank": rank,
        "current_market_state": row.get("current_market_state") or row.get("opportunity_status") or "机会榜自动模拟候选",
        "opportunity_status": row.get("opportunity_status"),
        "historical_ev": ev_check.get("ev"),
        "historical_ev_sample_size": ev_check.get("sample_size"),
        "historical_ev_win_rate": ev_check.get("win_rate"),
        "historical_ev_reason": ev_check.get("reason"),
        "calibration_tags": extract_calibration_tags(preliminary_signal),
    }


def run_auto_simulation_cycle(rankings: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Run one background simulation cycle.

    Existing positions and pending simulated orders are updated even when auto
    mode is disabled. New simulated orders require account=running and mode=auto.
    """
    global _LAST_RUN_AT
    now = time.time()
    if now - _LAST_RUN_AT < _MIN_INTERVAL_SECONDS:
        return {"ok": True, "skipped": True, "reason": "自动模拟刷新冷却中。"}
    _LAST_RUN_AT = now
    opportunities = collect_top10_opportunities(rankings, limit=10)
    price_map = _build_price_map(opportunities)
    settings = load_settings()
    account = load_sim_account()
    signals: list[dict[str, Any]] = []
    if account.get("status") == "running" and settings.get("mode") == "auto":
        prechecks = run_committee_top10_precheck(rankings, limit=3)
        for precheck in prechecks:
            signal = _signal_from_precheck(precheck)
            if signal:
                signals.append(signal)
            else:
                row = precheck.get("opportunity") or {}
                symbol = str(precheck.get("symbol") or row.get("symbol") or "").upper()
                score = _to_int(row.get("professional_trade_score", row.get("final_opportunity_score", row.get("opportunity_score"))), _to_int(precheck.get("professional_trade_score", precheck.get("score")), 0))
                risk = _to_int(row.get("risk_score"), _to_int(precheck.get("risk_score"), 50))
                simulation_score = _to_int(row.get("simulation_score"), max(score, MIN_AUTO_SIM_SIMULATION_SCORE))
                base_quality = _to_int(row.get("base_quality_score"), max(65, min(80, simulation_score)))
                liquidity_quality = _to_int(row.get("liquidity_quality_score"), 70)
                portfolio_fit = _to_int(row.get("portfolio_fit_score"), 75)
                price = _price(row)
                direction = str(precheck.get("direction") or row.get("trade_direction") or _direction(row, precheck))
                ev_check: dict[str, Any] = {}
                reasons = _sampling_reject_reasons(symbol, price, score, simulation_score, base_quality, liquidity_quality, portfolio_fit, risk)
                if direction not in {"long", "short"}:
                    reasons.append("方向不是 long/short。")
                else:
                    reasons.extend(_strict_auto_sim_reject_reasons(row, precheck, direction))
                    ev_check = evaluate_signal_ev(
                        {
                            **row,
                            "symbol": symbol,
                            "direction": direction,
                            "risk_score": risk,
                            "professional_trade_score": score,
                            "simulation_score": simulation_score,
                            "base_quality_score": base_quality,
                            "liquidity_quality_score": liquidity_quality,
                            "portfolio_fit_score": portfolio_fit,
                        },
                        load_sim_trade_history(),
                    )
                    if not ev_check.get("allowed"):
                        reasons.append(ev_check.get("reason") or "同类历史EV不支持开仓。")
                record_observation_signal(
                    symbol=symbol,
                    direction=direction,
                    entry_price=price,
                    rank=int(precheck.get("rank", 0) or 0),
                    reasons=reasons,
                    row=row,
                    precheck=precheck,
                    ev_check=ev_check,
                    scores={
                        "professional_trade_score": score,
                        "simulation_score": simulation_score,
                        "base_quality_score": base_quality,
                        "liquidity_quality_score": liquidity_quality,
                        "portfolio_fit_score": portfolio_fit,
                        "risk_score": risk,
                    },
                )
                append_sim_diagnostic(
                    "自动模拟候选拒绝",
                    symbol,
                    "rejected",
                    "；".join(reasons or ["未通过自动模拟采样。"]),
                    {
                        "rank": precheck.get("rank"),
                        "price": price,
                        "direction": direction,
                        "professional_trade_score": score,
                        "simulation_score": simulation_score,
                        "base_quality_score": base_quality,
                        "liquidity_quality_score": liquidity_quality,
                        "portfolio_fit_score": portfolio_fit,
                        "risk_score": risk,
                        "entry_state": row.get("entry_state"),
                        "action_gate": row.get("action_gate"),
                    },
                )
    summary = update_simulation(price_map, signals, _LAST_PRICE_STATUS)
    observation_summary = update_observation_signals(price_map)
    _debug_log(f"update_simulation signals={len(signals)} prices={len(price_map)}")
    if signals:
        log_sim_event("后台自动模拟扫描", content=f"本轮候选 {len(signals)} 个，已交给模拟风控执行。")
    elif account.get("status") == "running" and settings.get("mode") == "auto":
        append_sim_diagnostic(
            "后台自动模拟扫描",
            status="no_signal",
            reason="本轮无可执行模拟信号。",
            details={"opportunities": len(opportunities), "prices": len(price_map)},
        )
    return {"ok": True, "signals": len(signals), "prices": len(price_map), "summary": summary, "observations": observation_summary}

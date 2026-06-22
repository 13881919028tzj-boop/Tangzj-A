"""Signal-analysis orchestration and market-structure panels."""

from __future__ import annotations

from html import escape
from typing import Any, Callable

import streamlit as st

from components.ui import render_metric_grid
from services import market_cache
from services.ai_committee_engine import run_committee_meeting
from services.capital_structure_engine import analyze_capital_structure
from services.liquidation_engine import analyze_liquidation_risk
from services.local_strategy_engine import append_strategy_log, build_local_strategy
from services.market_risk_radar import analyze_market_risk_radar
from services.orderbook_analyzer import analyze_orderbook
from services.signal_engine import build_signal_analysis
from services.watchlist_manager import is_watched, update_watchlist_item
from services.whale_monitor import analyze_dealer_behavior
from utils.formatters import format_compact, format_price


def _signal_color(value: str) -> str:
    if value in {"强多", "偏多", "顺势做多", "轻仓试多", "上升趋势", "突破", "回踩确认", "加速上涨", "金叉", "多头延续", "极强", "偏强", "资金流入", "健康上涨", "健康下跌", "空头回补", "安全", "较安全", "低", "低风险", "偏低风险", "可交易", "轻仓可试", "支持交易", "轻仓支持"}:
        return "green"
    if value in {"强空", "偏空", "顺势做空", "轻仓试空", "下降趋势", "跌破", "加速下跌", "极高风险", "高风险", "死叉", "空头延续", "极弱", "偏弱", "资金恐慌", "危险上涨", "多头拥挤", "空头拥挤", "恐慌下跌", "空头挤压风险", "多头踩踏风险", "高风险双向震荡", "高风险上涨", "高风险下跌", "多空双杀风险", "疑似诱多", "疑似诱空", "极端风险", "高", "极高", "不建议开仓", "禁止开仓", "反对交易"}:
        return "red"
    if value in {"中等风险", "横盘震荡", "假突破", "观望", "不建议追多", "不建议追空", "资金观望", "资金过热", "高风险震荡", "中性", "正常", "中", "震荡观望", "谨慎交易"}:
        return "yellow"
    return "blue"


def _safe_text(value: Any, limit: int = 260) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    return text[:limit].rstrip() + "..." if len(text) > limit else text


def _render_numbered(items: list[str]) -> str:
    return "".join(f"<li>{escape(_safe_text(item))}</li>" for item in items)

def _format_signed_percent(value: Any) -> str:
    """格式化可为空的百分比变化。"""
    if value is None:
        return "待确认"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "待确认"
    return f"{number:+.2f}%"


def _format_funding(value: Any) -> str:
    """格式化 Funding 资金费率。"""
    if value is None:
        return "待确认"
    try:
        return f"{float(value) * 100:+.4f}%"
    except (TypeError, ValueError):
        return "待确认"


def _format_ratio(value: Any) -> str:
    """格式化多空比。"""
    if value is None:
        return "待确认"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "待确认"


def _format_amount_short(value: Any) -> str:
    """格式化金额，适合手机端显示。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "待确认"
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 1_000_000_000:
        return f"{sign}{number / 1_000_000_000:.2f}B"
    if number >= 1_000_000:
        return f"{sign}{number / 1_000_000:.2f}M"
    if number >= 1_000:
        return f"{sign}{number / 1_000:.2f}K"
    return f"{sign}{number:.2f}"



def render_signal_analysis(
    symbol: str,
    ticker: dict[str, Any] | None,
    *,
    get_effective_ticker: Callable[[str], dict[str, Any] | None],
    append_signal_debug: Callable[[str], None],
    render_local_strategy_decision: Callable[[dict[str, Any]], None],
    render_ai_committee_decision: Callable[[dict[str, Any]], None],
    render_sim_signal_linkage: Callable[[dict[str, Any]], None],
    render_manual_position_override_panel: Callable[[dict[str, Any]], None],
    render_committee_candidates: Callable[[], None],
) -> None:
    """渲染市场结构、评分、方向建议与止盈止损建议。"""
    symbol = st.session_state.get("current_symbol", symbol)
    interval = market_cache.get_kline_interval()
    rows = market_cache.get_klines(symbol, interval)
    live_ticker = get_effective_ticker(symbol) or ticker
    current_price = live_ticker.get("last_price") if live_ticker else None
    orderbook = market_cache.get_orderbook(symbol)
    orderbook_analysis = analyze_orderbook(orderbook, current_price)
    analysis = build_signal_analysis(live_ticker, rows, orderbook_analysis)
    derivatives = market_cache.get_derivatives(symbol)
    capital = analyze_capital_structure(derivatives, live_ticker, analysis)
    liquidation = analyze_liquidation_risk(live_ticker, rows, derivatives, orderbook_analysis, analysis)
    whale = market_cache.get_whales(symbol)
    signal_missing = []
    if not live_ticker:
        signal_missing.append("实时价格等待刷新")
    if len(rows) < 80:
        signal_missing.append(f"K线数据不足：当前 {len(rows)} 根 / 需要 80 根")
    if not orderbook:
        signal_missing.append("盘口等待刷新")
    if not whale:
        signal_missing.append("暂无明显大单")
    if signal_missing:
        append_signal_debug(f"signal_chain_missing symbol={symbol} reasons={' | '.join(signal_missing)}")
    dealer = analyze_dealer_behavior(whale, derivatives, orderbook_analysis, analysis, liquidation)
    radar = analyze_market_risk_radar(live_ticker, rows, derivatives, capital, liquidation, whale, dealer, orderbook_analysis, analysis)
    strategy = build_local_strategy(
        symbol=symbol,
        ticker=live_ticker,
        rows=rows,
        signal_analysis=analysis,
        orderbook_analysis=orderbook_analysis,
        derivatives=derivatives,
        capital=capital,
        liquidation=liquidation,
        whale=whale,
        dealer=dealer,
        radar=radar,
        primary_timeframe=interval,
    )
    if is_watched(symbol):
        try:
            update_watchlist_item(symbol, strategy, live_ticker)
        except Exception as exc:
            print(f"[观察池] 更新 {symbol} 失败 error={repr(exc)}")
    strategy_key = f"{strategy.get('symbol')}|{strategy.get('timestamp')[:16]}|{strategy.get('action')}|{strategy.get('confidence')}"
    if st.session_state.get("last_strategy_log_key") != strategy_key:
        append_strategy_log(strategy)
        st.session_state.last_strategy_log_key = strategy_key
    snapshot = market_cache.snapshot()

    rsi_text = f"{analysis['rsi']:.2f}" if analysis.get("rsi") is not None else "等待数据"
    ma20_text = format_price(analysis["ma20"]) if analysis.get("ma20") else "等待数据"
    ma60_text = format_price(analysis["ma60"]) if analysis.get("ma60") else "等待数据"
    support_text = format_price(analysis["support"]) if analysis.get("support") else "等待数据"
    resistance_text = format_price(analysis["resistance"]) if analysis.get("resistance") else "等待数据"
    volume_change = float(analysis.get("volume_change") or 0)
    structure = str(analysis["market_structure"])
    suggestion = str(analysis["suggestion"])
    trend_level = str(analysis["trend_level"])
    risk_level = str(analysis["risk_level"])
    oi = (derivatives or {}).get("oi") or {}
    funding = (derivatives or {}).get("funding") or {}
    long_short = (derivatives or {}).get("long_short") or {}
    oi_changes = oi.get("changes") or {}
    oi_text = format_compact(oi.get("current_oi")) if oi.get("current_oi") is not None else "待确认"
    funding_text = _format_funding(funding.get("rate"))
    account_ratio_text = _format_ratio(long_short.get("account_ratio"))
    position_ratio_text = _format_ratio(long_short.get("position_ratio"))
    derivatives_error = snapshot.get("derivatives_last_error") or ""
    derivatives_error_html = ""
    if derivatives_error:
        derivatives_error_html = f'<div class="status-card" style="margin-top:8px;color:#F0B90B;">{escape(str(derivatives_error))}</div>'
    capital_score_text = f'{capital["score"]} / 100 · {escape(str(capital["level"]))}'
    capital_state_text = escape(str(capital["state"]))
    capital_explanation_text = escape(str(capital["explanation"]))
    derivatives_update_time = escape(str(snapshot.get("derivatives_last_update_time", "初始化中")))

    render_metric_grid(
        [
            ("市场结构", structure, _signal_color(structure)),
            ("趋势评分", f"{analysis['trend_score']} / 100", _signal_color(trend_level)),
            ("趋势等级", trend_level, _signal_color(trend_level)),
            ("交易建议", suggestion, _signal_color(suggestion)),
            ("风险评分", f"{analysis['risk_score']} / 100", _signal_color(risk_level)),
            ("风险等级", risk_level, _signal_color(risk_level)),
            ("综合风险", f"{radar['overall_score']} / 100", _signal_color(str(radar["risk_level"]))),
            ("交易安全", str(radar["trade_safety"]), _signal_color(str(radar["trade_safety"]))),
            ("RSI", rsi_text, "blue"),
            ("MACD", str(analysis["macd_signal"]), _signal_color(str(analysis["macd_signal"]))),
            ("支撑位", support_text, "green"),
            ("压力位", resistance_text, "red"),
            ("资金结构评分", f"{capital['score']} / 100", _signal_color(str(capital["level"]))),
            ("衍生品状态", str(capital["market_state"]), _signal_color(str(capital["market_state"]))),
            ("爆仓风险评分", f"{liquidation['risk_score']} / 100", _signal_color(str(liquidation["risk_level"]))),
            ("挤仓风险", str(liquidation["squeeze_state"]), _signal_color(str(liquidation["squeeze_state"]))),
            ("大单强度", f"{(whale or {}).get('score', 0)} / 100", _signal_color(str((whale or {}).get("level", "大单中性")))),
            ("庄家行为", str(dealer["state"]), _signal_color(str(dealer["state"]))),
            ("本地策略", str(strategy["action"]), _signal_color(str(strategy["action"]))),
            ("策略置信度", f"{strategy['confidence']} / 100", _signal_color(str(strategy["action"]))),
            ("本地投票", f"{strategy.get('local_vote_score', 0)} / 100 · {strategy.get('local_vote_grade', 'D')}", _signal_color(str(strategy.get("local_vote_decision", "只观察")))),
            ("投票决议", str(strategy.get("local_vote_decision", "只观察")), _signal_color(str(strategy.get("local_vote_decision", "只观察")))),
        ]
    )

    data_status = ""
    if signal_missing:
        data_status = f'<div class="status-card" style="margin-bottom:8px;color:#F0B90B;">{"<br>".join(escape(str(item)) for item in signal_missing)}</div>'
    elif not analysis.get("ready"):
        data_status = f'<div class="status-card" style="margin-bottom:8px;color:#F0B90B;">{escape(str(analysis["message"]))}</div>'

    render_local_strategy_decision(strategy)
    committee_decision = run_committee_meeting(
        symbol,
        ticker=live_ticker,
        rows=rows,
        signal_analysis=analysis,
        orderbook_analysis=orderbook_analysis,
        derivatives=derivatives,
        capital=capital,
        liquidation=liquidation,
        whale=whale,
        dealer=dealer,
        radar=radar,
        local_strategy=strategy,
    )
    render_ai_committee_decision(committee_decision)
    render_sim_signal_linkage(committee_decision)
    render_manual_position_override_panel(committee_decision)
    render_committee_candidates()

    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">衍生品市场结构</div>
            <div class="metric-value {_signal_color(str(capital["market_state"]))}">{escape(str(capital["market_state"]))}</div>
            <div class="module-desc">{escape(str(capital["market_explanation"]))}</div>
            <div class="status-card" style="margin-top:8px;">
              当前OI：{oi_text}<br>
              OI 5分钟变化：{_format_signed_percent(oi_changes.get("5m"))}<br>
              OI 15分钟变化：{_format_signed_percent(oi_changes.get("15m"))}<br>
              OI 1小时变化：{_format_signed_percent(oi_changes.get("1h"))}<br>
              OI 4小时变化：{_format_signed_percent(oi_changes.get("4h"))}<br>
              OI 24小时变化：{_format_signed_percent(oi_changes.get("24h"))}<br>
              OI状态：{escape(str(oi.get("status", "等待数据")))}
            </div>
            <div class="status-card" style="margin-top:8px;">
              Funding：{funding_text}<br>
              Funding状态：{escape(str(funding.get("status", "等待数据")))}｜趋势：{escape(str(funding.get("trend", "等待数据")))}<br>
              账户多空比：{account_ratio_text}<br>
              持仓多空比：{position_ratio_text}<br>
              多空状态：{escape(str(long_short.get("status", "等待数据")))}
            </div>
            <div class="status-card" style="margin-top:8px;">
              资金结构评分：{capital_score_text}<br>
              资金结构状态：{capital_state_text}<br>
              中文解释：{capital_explanation_text}<br>
              更新时间：{derivatives_update_time}
            </div>
            {derivatives_error_html}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">清算热力区 / 爆仓风险分析</div>
            <div class="metric-value {_signal_color(str(liquidation["risk_level"]))}">{liquidation["risk_score"]} / 100 · {escape(str(liquidation["risk_level"]))}</div>
            <div class="module-desc">{escape(str(liquidation["explanation"]))}</div>
            <div class="status-card" style="margin-top:8px;">
              当前价格：{format_price(liquidation["current_price"]) if liquidation.get("current_price") else "等待数据"}<br>
              最近清算区：{escape(str(liquidation["nearest_zone"]))}<br>
              上方清算区：{escape(str(liquidation["upper_zone"]))}<br>
              下方清算区：{escape(str(liquidation["lower_zone"]))}
            </div>
            <div class="status-card" style="margin-top:8px;">
              上方距离：{escape(str(liquidation["upper_distance"]))}｜预计触发强度：{escape(str(liquidation["upper_strength"]))}<br>
              下方距离：{escape(str(liquidation["lower_distance"]))}｜预计触发强度：{escape(str(liquidation["lower_strength"]))}<br>
              当前挤仓风险：{escape(str(liquidation["squeeze_state"]))}
            </div>
            <div class="status-card" style="margin-top:8px;">
              猎杀止损概率：{escape(str(liquidation["hunt_probability"]))}<br>
              中文解释：{escape(str(liquidation["hunt_explanation"]))}
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    whale = whale or {}

    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">庄家行为初判</div>
            <div class="metric-value {_signal_color(str(dealer["state"]))}">{escape(str(dealer["state"]))}</div>
            <div class="module-desc">{escape(str(dealer["explanation"]))}</div>
            <div class="status-card" style="margin-top:8px;">
              吸筹概率：{dealer["accumulation_probability"]}%<br>
              洗盘概率：{dealer["wash_probability"]}%<br>
              拉升概率：{dealer["markup_probability"]}%<br>
              派发概率：{dealer["distribution_probability"]}%
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    risk_components_html = []
    for component in radar.get("components", []):
        risk_components_html.append(
            f"""<div class="status-card" style="margin-top:8px;">
              <b>{escape(str(component.get("name", "风险分项")))}</b>：{component.get("score", 0)} / 100 · {escape(str(component.get("level", "中")))}<br>
              原因：{escape(str(component.get("reason", "等待数据")))}
            </div>"""
        )
    alerts_html = "".join(
        f'<div class="status-card" style="margin-top:8px;color:#F0B90B;">{escape(str(alert))}</div>'
        for alert in radar.get("alerts", [])
    )

    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">市场风险雷达</div>
            <div class="metric-value {_signal_color(str(radar["risk_level"]))}">{radar["overall_score"]} / 100 · {escape(str(radar["risk_level"]))}</div>
            <div class="module-desc">{escape(str(radar["market_explanation"]))}</div>
            <div class="status-card" style="margin-top:8px;">
              市场状态：{escape(str(radar["market_state"]))}<br>
              交易安全等级：{escape(str(radar["trade_safety"]))}<br>
              建议仓位：{escape(str(radar["position_size"]))}<br>
              仓位解释：{escape(str(radar["position_explanation"]))}
            </div>
            <div class="module-title" style="margin-top:10px;">风险来源拆解</div>
            {''.join(risk_components_html)}
            <div class="module-title" style="margin-top:10px;">风险警报</div>
            {alerts_html}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="app-shell">
          {data_status}
          <div class="module-grid">
            <div class="module-card">
              <div class="module-title">市场结构分析</div>
              <div class="metric-value {_signal_color(structure)}">{escape(structure)}</div>
              <div class="module-desc">{escape(str(analysis["structure_explanation"]))}</div>
              <div class="status-card" style="margin-top:8px;">
                当前交易对象：{escape(symbol)}<br>
                当前周期：{escape(interval)}<br>
                当前价格：{format_price(current_price) if current_price else "等待数据"}
              </div>
            </div>
            <div class="module-card">
              <div class="module-title">趋势评分</div>
              <div class="metric-value {_signal_color(trend_level)}">{analysis["trend_score"]} / 100 · {escape(trend_level)}</div>
              <div class="module-desc">{escape(str(analysis["trend_explanation"]))}</div>
              <div class="status-card" style="margin-top:8px;">
                MA20：{ma20_text}<br>
                MA60：{ma60_text}<br>
                成交量变化：{volume_change:.1f}%
              </div>
            </div>
            <div class="module-card">
              <div class="module-title">风险评分</div>
              <div class="metric-value {_signal_color(risk_level)}">{analysis["risk_score"]} / 100 · {escape(risk_level)}</div>
              <div class="module-desc">{escape(str(analysis["risk_explanation"]))}</div>
              <div class="status-card" style="margin-top:8px;">
                支撑位：{support_text}<br>
                压力位：{resistance_text}<br>
                盘口倾向：{escape(str(orderbook_analysis.get("bias", "等待数据")))}
              </div>
            </div>
            <div class="module-card">
              <div class="module-title">交易方向与止盈止损</div>
              <div class="metric-value {_signal_color(suggestion)}">{escape(suggestion)}</div>
              <div class="module-desc">该建议只用于行情分析与学习，不代表真实下单指令。</div>
              <div class="status-card" style="margin-top:8px;">
                入场参考：{escape(str(analysis["entry_zone"]))}<br>
                止损位：{escape(str(analysis["stop_loss"]))}<br>
                止盈1：{escape(str(analysis["take_profit_1"]))}<br>
                止盈2：{escape(str(analysis["take_profit_2"]))}<br>
                风险收益比：{escape(str(analysis["risk_reward"]))}
              </div>
            </div>
            <div class="module-card">
              <div class="module-title">当前信号来源</div>
              <ol class="module-desc" style="padding-left:18px;margin:8px 0 0 0;">{_render_numbered(analysis["reasons"])}</ol>
            </div>
            <div class="module-card">
              <div class="module-title">当前风险提示</div>
              <ol class="module-desc" style="padding-left:18px;margin:8px 0 0 0;">{_render_numbered(analysis["risks"])}</ol>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


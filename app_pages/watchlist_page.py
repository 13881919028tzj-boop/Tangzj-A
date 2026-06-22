"""Watchlist panel used by the market page."""

from __future__ import annotations

from html import escape
from typing import Any, Callable

import streamlit as st

from components.market_widgets import render_watchlist_quick_controls
from components.ui import kline_symbol_link, signal_color
from services.watchlist_manager import (
    clear_expired_watchlist,
    get_watchlist,
    get_watchlist_alerts,
    get_watchlist_candidates_for_committee,
    get_watchlist_summary,
    remove_from_watchlist,
    set_watchlist_category,
)
from utils.formatters import format_percent, format_price


def render_watchlist(
    rankings: dict[str, list[dict[str, Any]]],
    *,
    set_current_symbol: Callable[[str], None],
) -> None:
    """Render the professional watchlist panel."""
    current_symbol = st.session_state.get("current_symbol", "BTCUSDT")
    summary = get_watchlist_summary()
    items = sorted(get_watchlist(), key=lambda item: (item.get("category") != "key_tracking", -(item.get("watch_score") or 0), item.get("symbol", "")))[:50]
    alerts = get_watchlist_alerts(8)
    candidates = get_watchlist_candidates_for_committee()[:8]

    st.markdown('<div class="list-card"><div class="module-title">观察池 / 重点币种跟踪系统</div><div class="module-desc">观察池只跟踪本地策略变化，不替代本地策略最终信号。</div>', unsafe_allow_html=True)
    render_watchlist_quick_controls(current_symbol, "market_watchlist", source="manual")
    cols = st.columns(3)
    summary_cards = [
        ("总观察", summary["total"]),
        ("手动观察", summary["manual"]),
        ("AI观察", summary["ai"]),
        ("重点跟踪", summary["key_tracking"]),
        ("高风险", summary["high_risk"]),
        ("信号失效", summary["expired"]),
    ]
    for index, (label, value) in enumerate(summary_cards):
        with cols[index % 3]:
            st.markdown(f'<div class="metric-box"><div class="metric-label">{label}</div><div class="metric-value yellow">{value}</div></div>', unsafe_allow_html=True)
    if st.button("清除非手动失效观察对象", key="watchlist_clear_expired", width="stretch"):
        clear_expired_watchlist()
        st.success("已清理非手动来源的失效观察对象")

    if not items:
        st.markdown('<div class="pending">暂无观察对象。可以从当前交易对象或机会榜加入。</div></div>', unsafe_allow_html=True)
        return

    category_text = {"manual": "手动观察", "ai": "AI观察", "key_tracking": "重点跟踪", "high_risk": "高风险观察", "expired": "已失效观察"}
    for index, item in enumerate(items, start=1):
        symbol = item.get("symbol", "-")
        strategy = item.get("local_strategy") or {}
        tracking = item.get("tracking") or {}
        latest_alert = (item.get("alerts") or [{}])[0]
        data_quality = item.get("data_quality") or {}
        status = str(tracking.get("status", "持续观察"))
        status_color = signal_color(status)
        source = escape(str(item.get("source", "manual")))
        category = category_text.get(str(item.get("category", "manual")), "手动观察")
        st.markdown(
            f"""
            <div class="module-card" style="margin-top:8px;">
              <div class="module-title">{kline_symbol_link(symbol, f"#{index} {symbol}")} <span class="{status_color}">· {escape(status)}</span></div>
              <div class="module-desc">点击币种可直接跳转到K线图区域。</div>
              <div class="module-desc">来源：{source}｜分类：{escape(category)}｜加入：{escape(str(item.get("added_time", "-")))}｜更新：{escape(str(item.get("last_update_time", "-")))}</div>
              <div class="watch-info-grid">
                <div class="watch-info-cell"><div class="watch-info-label">价格</div><div class="watch-info-value">{format_price(item.get("current_price"))}</div></div>
                <div class="watch-info-cell"><div class="watch-info-label">24h涨跌</div><div class="watch-info-value {signal_color(format_percent(item.get("price_change_24h")))}">{format_percent(item.get("price_change_24h"))}</div></div>
                <div class="watch-info-cell"><div class="watch-info-label">本地策略</div><div class="watch-info-value {signal_color(str(strategy.get("action", "观望")))}">{escape(str(strategy.get("action", "等待策略")))}</div></div>
                <div class="watch-info-cell"><div class="watch-info-label">策略类型</div><div class="watch-info-value yellow">{escape(str(strategy.get("strategy_name", "等待策略")))}</div></div>
                <div class="watch-info-cell"><div class="watch-info-label">置信度</div><div class="watch-info-value blue">{strategy.get("confidence", 0)} / 100</div></div>
                <div class="watch-info-cell"><div class="watch-info-label">风险</div><div class="watch-info-value yellow">{strategy.get("risk_score", 0)} / 100</div></div>
                <div class="watch-info-cell"><div class="watch-info-label">机会</div><div class="watch-info-value green">{strategy.get("opportunity_score", 0)} / 100</div></div>
                <div class="watch-info-cell"><div class="watch-info-label">观察评分</div><div class="watch-info-value green">{item.get("watch_score", 0)} / 100</div></div>
              </div>
              <div class="status-card" style="margin-top:8px;">
                状态：{escape(str(tracking.get("status_explanation", "等待策略同步。")).replace("等待下一轮策略跟踪", "等待策略同步"))}<br>
                等级：{escape(str(item.get("watch_level", "普通观察")))}｜{escape(str(item.get("watch_explanation", "本地策略数据同步中。")).replace("等待本地策略数据同步。", "本地策略数据同步中。"))}<br>
                提醒：{escape(str(latest_alert.get("content", "当前暂无提醒")))}<br>
                数据质量：{escape(str(data_quality.get("level", "poor")))}｜信号失效：{escape(str(strategy.get("invalid_condition", "等待确认")))}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown('<div class="watch-action-grid">', unsafe_allow_html=True)
        b1, b2, b3, b4 = st.columns(4)
        with b1:
            if st.button("切换", key=f"watch_switch_{symbol}_{index}", width="stretch"):
                set_current_symbol(str(symbol))
        with b2:
            if st.button("标重点", key=f"watch_key_{symbol}_{index}", width="stretch"):
                set_watchlist_category(str(symbol), "key_tracking")
                st.success(f"{symbol} 已标记为重点跟踪")
        with b3:
            if st.button("高风险", key=f"watch_risk_{symbol}_{index}", width="stretch"):
                set_watchlist_category(str(symbol), "high_risk")
                st.warning(f"{symbol} 已移入高风险观察")
        with b4:
            if st.button("移除", key=f"watch_remove_{symbol}_{index}", width="stretch"):
                remove_from_watchlist(str(symbol))
                st.warning(f"{symbol} 已移出观察池")
        st.markdown("</div>", unsafe_allow_html=True)
        with st.expander(f"{symbol} 观察详情", expanded=False):
            st.write("最近历史：")
            st.json((item.get("history") or [])[-8:])

    if alerts:
        st.markdown('<div class="module-title" style="margin-top:10px;">观察池提醒</div>', unsafe_allow_html=True)
        for alert in alerts:
            color = "red" if alert.get("level") == "高级提醒" else "yellow" if alert.get("level") == "中级提醒" else "blue"
            st.markdown(
                f'<div class="status-card" style="margin-top:6px;"><b class="{color}">{escape(str(alert.get("level", "提醒")))}</b>｜{escape(str(alert.get("time", "-")))}｜{escape(str(alert.get("symbol", "-")))}<br>{escape(str(alert.get("content", "")))}<br>原因：{escape(str(alert.get("reason", "")))}</div>',
                unsafe_allow_html=True,
            )
    if candidates:
        st.markdown('<div class="module-title" style="margin-top:10px;">交易委员会候选</div>', unsafe_allow_html=True)
        for row in candidates:
            st.markdown(
                f'<div class="status-card" style="margin-top:6px;"><b>{escape(str(row.get("symbol", "-")))}</b>｜观察评分 {row.get("watch_score", 0)}｜{escape(str(row.get("local_strategy_action", "-")))}｜{escape(str(row.get("strategy_name", "-")))}<br>{escape(str(row.get("main_reason", "")))}<br>主要风险：{escape(str(row.get("main_risk", "")))}</div>',
                unsafe_allow_html=True,
            )
    st.markdown("</div>", unsafe_allow_html=True)

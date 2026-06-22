"""Reusable market-board widgets."""

from __future__ import annotations

from html import escape
from typing import Any

import streamlit as st

from components.ui import kline_href
from services.fast_opportunity_engine import get_fast_opportunity_status
from services.watchlist_manager import add_to_watchlist, is_watched, remove_from_watchlist
from utils.formatters import format_compact, format_percent, format_price, format_score, safe_number, safe_score


def _safe_compare_gte(value: Any, threshold: float) -> bool:
    number = safe_score(value)
    return False if number is None else number >= threshold


def render_watchlist_quick_controls(symbol: str, key_prefix: str, source: str = "manual") -> None:
    """Current-symbol watchlist quick controls."""
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return
    c1, c2 = st.columns(2)
    watched = is_watched(normalized)
    with c1:
        if watched:
            st.button(f"{normalized} 已在观察池", key=f"{key_prefix}_watch_exists", disabled=True, width="stretch")
        elif st.button(f"加入观察池：{normalized}", key=f"{key_prefix}_watch_add", width="stretch"):
            add_to_watchlist(normalized, source=source)
            st.success(f"{normalized} 已加入观察池")
    with c2:
        if watched and st.button(f"移出观察池：{normalized}", key=f"{key_prefix}_watch_remove", width="stretch"):
            remove_from_watchlist(normalized)
            st.warning(f"{normalized} 已移出观察池")


def watch_action_html(symbol: str, page: str, source: str) -> str:
    """Inline watchlist action used by market rows."""
    if is_watched(symbol):
        return '<span class="watch-pill done">已观察</span>'
    return f'<a class="watch-pill" href="?page={page}&symbol={symbol}&watch_add={symbol}" target="_self">加入观察池</a>'


def render_rank_list(title: str, rows: list[dict[str, Any]], mode: str) -> None:
    """Render a compact market ranking list."""
    st.markdown(f'<div class="list-card"><div class="module-title">{title}</div>', unsafe_allow_html=True)
    st.markdown('<div class="opp-row compact-five rank-head"><div>交易对象</div><div>价格</div><div>观察</div><div>涨跌</div><div>成交额</div></div>', unsafe_allow_html=True)
    if not rows:
        st.markdown('<div class="pending">正在获取行情</div>', unsafe_allow_html=True)
    active_page = st.session_state.active_page
    for index, row in enumerate(rows[:10], start=1):
        medal_class = "gold" if index == 1 else "silver" if index == 2 else "bronze" if index == 3 else ""
        change_class = "green" if row["price_change_percent"] >= 0 else "red"
        symbol = row["symbol"]
        href = kline_href(symbol)
        st.markdown(
            f"""
            <div class="opp-row compact-five">
              <div>
                <a class="rank-link" href="{href}" target="_self"><div class="opp-symbol"><span class="rank-index {medal_class}">#{index}</span> {symbol}</div></a>
                <div class="opp-meta">点击查看K线图</div>
              </div>
              <div class="opp-symbol">{format_price(row["last_price"])}</div>
              <div>{watch_action_html(symbol, active_page, title)}</div>
              <div class="{change_class}" style="font-weight:900;">{format_percent(row["price_change_percent"])}</div>
              <div class="rank-volume">{format_compact(row["quote_volume"])}</div>
            </div>""",
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


def render_score_sources(row: dict[str, Any]) -> str:
    risk = row.get("risk_breakdown") or {}
    opportunity = row.get("opportunity_breakdown") or {}
    risk_sources = risk.get("main_risk_sources") or []
    opportunity_sources = opportunity.get("main_opportunity_sources") or []
    risk_text = "；".join(escape(str(item)) for item in risk_sources[:3]) if risk_sources else "暂无主要风险来源。"
    opportunity_text = "；".join(escape(str(item)) for item in opportunity_sources[:4]) if opportunity_sources else "暂无机会来源拆解。"
    risk_rows = "".join(
        f"<div class=\"opp-meta\">{label}：{int(float(risk.get(key, 0) or 0))}</div>"
        for key, label in [
            ("volatility_risk", "波动"),
            ("overheat_risk", "过热"),
            ("funding_risk", "Funding"),
            ("crowding_risk", "拥挤"),
            ("liquidation_risk", "清算"),
            ("orderflow_risk", "盘口大单"),
            ("data_quality_risk", "数据质量"),
            ("combo_risk_boost", "组合放大"),
        ]
    )
    opp_rows = "".join(
        f"<div class=\"opp-meta\">{label}：{int(float(opportunity.get(key, 0) or 0))}</div>"
        for key, label in [
            ("trend_opportunity", "趋势"),
            ("capital_opportunity", "资金"),
            ("structure_opportunity", "结构"),
            ("orderflow_opportunity", "盘口大单"),
            ("liquidity_opportunity", "流动性"),
            ("tradeability_opportunity", "可交易性"),
        ]
    )
    diagnostic = str(row.get("risk_model_diagnostic") or "normal")
    diagnostic_text = {
        "too_sensitive": "风险模型可能过度敏感，请检查阈值设置。",
        "too_loose": "风险模型可能过于宽松，请检查阈值设置。",
        "insufficient_data": "样本或字段不足，当前统计仅供观察。",
    }.get(diagnostic, "风险模型状态正常。")
    return f"""
      <details class="opp-meta" style="margin-top:6px;">
        <summary>评分拆解 / 风险来源</summary>
        <div class="committee-grid" style="margin-top:6px;">
          <div class="status-card"><b>机会来源</b><br>{opportunity_text}<br>{opp_rows}</div>
          <div class="status-card"><b>风险来源</b><br>{risk_text}<br>{risk_rows}</div>
        </div>
        <div class="opp-meta">诊断：{escape(diagnostic_text)}</div>
      </details>
    """


def render_opportunity_list(title: str, rows: list[dict[str, Any]], mode: str) -> None:
    """Render a compact opportunity list."""
    active_page = st.session_state.active_page
    fast_status = get_fast_opportunity_status()
    fast_target = str(fast_status.get("current_target") or "")
    st.markdown(f'<div class="list-card"><div class="module-title">{title}</div>', unsafe_allow_html=True)
    if not rows:
        st.markdown('<div class="pending">市场机会榜暂不可用，请稍后重试</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
        return
    st.markdown(
        '<div class="opp-row compact-five rank-head"><div>交易对象</div><div>价格</div><div>观察</div><div>涨跌</div><div>机会/风险</div></div>',
        unsafe_allow_html=True,
    )
    for index, row in enumerate(rows[:10], start=1):
        symbol = row.get("symbol", "")
        opportunity_score = safe_score(row.get("final_opportunity_score", row.get("opportunity_score")))
        raw_score = safe_score(row.get("raw_opportunity_score"), opportunity_score)
        risk_score = safe_score(row.get("risk_score"))
        risk_penalty = safe_score(row.get("risk_penalty"), 0)
        score_cap = safe_score(row.get("score_cap"), 100)
        status = str(row.get("opportunity_status", row.get("advice", "观察")))
        fast_badges = []
        if index == 1:
            fast_badges.append("TOP1")
        if str(symbol).upper() == fast_target:
            fast_badges.append("快速预判目标")
        if _safe_compare_gte(opportunity_score, 80):
            fast_badges.append("80分候选")
        fast_text = " · ".join(fast_badges) if fast_badges else "等待触发"
        change = safe_number(row.get("price_change_percent"))
        change_class = "green" if (change is not None and change >= 0) else "red" if change is not None else "yellow"
        href = kline_href(symbol)
        st.markdown(
            f"""
            <div class="opp-row compact-five">
              <div>
                <a class="rank-link" href="{href}" target="_self"><div class="opp-symbol">#{index} {symbol}</div></a>
                <div class="opp-meta">{row.get("current_market_state", "观察")} · {row.get("advice", "观察等待")}</div>
                <div class="opp-meta">{escape(fast_text)}</div>
              </div>
              <div class="opp-symbol">{format_price(row.get("last_price", 0))}</div>
              <div>{watch_action_html(symbol, active_page, title)}</div>
              <div class="{change_class}" style="font-weight:900;">{format_percent(change)}</div>
              <div>
                <div class="score-pill">终{format_score(opportunity_score)} / 风{format_score(risk_score)}</div>
                <div class="opp-meta">原始{format_score(raw_score)}｜扣{format_score(risk_penalty)}｜封顶{format_score(score_cap)}</div>
                <div class="opp-meta">{escape(status)}｜额 {format_compact(row.get("quote_volume", 0))}</div>
              </div>
            </div>""",
            unsafe_allow_html=True,
        )
        st.markdown(render_score_sources(row), unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

"""Market and opportunity-board page shell."""

from __future__ import annotations

from html import escape
from typing import Any, Callable

import streamlit as st

from components.ui import render_page_head
from services import market_cache
from services.fast_opportunity_engine import get_fast_opportunity_status, save_fast_opportunity_settings
from services.watchlist_manager import auto_add_from_rankings


def render_fast_opportunity_panel() -> None:
    """Show TOP1 fast capture and committee precheck status."""
    status = get_fast_opportunity_status()
    capture = status.get("latest_capture") or {}
    precheck = status.get("latest_precheck") or {}
    candidate = status.get("latest_candidate") or {}
    settings = status.get("settings") or {}
    symbol = capture.get("symbol") or status.get("current_target") or "-"
    capture_state = "通过" if capture.get("trigger_committee_precheck") else "观察"
    precheck_state = precheck.get("fast_action") or "等待"
    candidate_state = candidate.get("message") or "暂无候选"
    st.markdown(
        f"""
        <div class="module-card">
          <div class="module-title">TOP1 三秒快速捕捉</div>
          <div class="metric-grid">
            <div class="metric-box"><div class="metric-label">当前目标</div><div class="metric-value blue">{escape(str(symbol))}</div></div>
            <div class="metric-box"><div class="metric-label">快速评分</div><div class="metric-value green">{int(capture.get("fast_score", status.get("target_score", 0)) or 0)}</div></div>
            <div class="metric-box"><div class="metric-label">快速捕捉</div><div class="metric-value {'green' if capture_state == '通过' else 'yellow'}">{capture_state}</div></div>
            <div class="metric-box"><div class="metric-label">委员会预判</div><div class="metric-value {'green' if precheck_state == '进入候选' else 'yellow'}">{escape(str(precheck_state))}</div></div>
            <div class="metric-box"><div class="metric-label">完整复核</div><div class="metric-value yellow">{int(settings.get("COMMITTEE_FULL_REVIEW_SECONDS", 15))}秒周期</div></div>
            <div class="metric-box"><div class="metric-label">触发阈值</div><div class="metric-value blue">{int(settings.get("OPPORTUNITY_TRIGGER_SCORE", 80))}分</div></div>
          </div>
          <div class="module-desc" style="margin-top:8px;">
            {escape(str(candidate_state))}<br>
            机会评分达到80分仅代表进入交易候选，真实交易仍需完整委员会、风控、实盘安全和人工确认。DeepSeek/Gemini 不参与高频快速调用。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.expander("机会发现设置", expanded=False):
        s1, s2 = st.columns(2)
        new_settings = dict(settings)
        new_settings["ENABLE_FAST_OPPORTUNITY_CAPTURE"] = s1.checkbox("启用TOP1快速捕捉", value=bool(settings.get("ENABLE_FAST_OPPORTUNITY_CAPTURE", True)))
        new_settings["ENABLE_FAST_COMMITTEE_PRECHECK"] = s2.checkbox("启用委员会快速预判", value=bool(settings.get("ENABLE_FAST_COMMITTEE_PRECHECK", True)))
        new_settings["ENABLE_COMMITTEE_ANCHOR_TOP1"] = st.checkbox("委员会当前交易对象自动锚定TOP1", value=bool(settings.get("ENABLE_COMMITTEE_ANCHOR_TOP1", True)))
        c1, c2, c3 = st.columns(3)
        new_settings["TOP10_OPPORTUNITY_REFRESH_SECONDS"] = c1.number_input("TOP10刷新秒数", min_value=3, max_value=30, value=int(settings.get("TOP10_OPPORTUNITY_REFRESH_SECONDS", 3)), step=1)
        new_settings["TOP1_FAST_CAPTURE_SECONDS"] = c2.number_input("TOP1捕捉秒数", min_value=3, max_value=30, value=int(settings.get("TOP1_FAST_CAPTURE_SECONDS", 3)), step=1)
        new_settings["COMMITTEE_FAST_PRECHECK_SECONDS"] = c3.number_input("快速预判秒数", min_value=3, max_value=30, value=int(settings.get("COMMITTEE_FAST_PRECHECK_SECONDS", 3)), step=1)
        c4, c5, c6 = st.columns(3)
        new_settings["COMMITTEE_FULL_REVIEW_SECONDS"] = c4.number_input("完整复核秒数", min_value=10, max_value=120, value=int(settings.get("COMMITTEE_FULL_REVIEW_SECONDS", 15)), step=5)
        new_settings["OPPORTUNITY_TRIGGER_SCORE"] = c5.number_input("机会触发分数", min_value=60, max_value=95, value=int(settings.get("OPPORTUNITY_TRIGGER_SCORE", 80)), step=1)
        new_settings["OPPORTUNITY_DUPLICATE_COOLDOWN_SECONDS"] = c6.number_input("重复候选冷却秒数", min_value=60, max_value=1800, value=int(settings.get("OPPORTUNITY_DUPLICATE_COOLDOWN_SECONDS", 120)), step=30)
        c7, c8, c9 = st.columns(3)
        new_settings["COMMITTEE_TARGET_MIN_STABLE_CYCLES"] = c7.number_input("TOP1稳定确认次数", min_value=1, max_value=5, value=int(settings.get("COMMITTEE_TARGET_MIN_STABLE_CYCLES", 2)), step=1)
        new_settings["COMMITTEE_TARGET_SWITCH_SCORE_GAP"] = c8.number_input("切换分差", min_value=1, max_value=20, value=int(settings.get("COMMITTEE_TARGET_SWITCH_SCORE_GAP", 5)), step=1)
        new_settings["COMMITTEE_TARGET_COOLDOWN_SECONDS"] = c9.number_input("切换冷却秒数", min_value=10, max_value=300, value=int(settings.get("COMMITTEE_TARGET_COOLDOWN_SECONDS", 30)), step=5)
        c10, c11, c12 = st.columns(3)
        new_settings["COMMITTEE_REVIEW_TOP_N"] = c10.number_input("快速候选评审TOP N", min_value=1, max_value=10, value=int(settings.get("COMMITTEE_REVIEW_TOP_N", 5)), step=1)
        new_settings["COMMITTEE_LIGHT_TRACK_TOP_N"] = c11.number_input("轻量跟踪TOP N", min_value=5, max_value=20, value=int(settings.get("COMMITTEE_LIGHT_TRACK_TOP_N", 10)), step=1)
        new_settings["TOP2_TO_TOP5_PRECHECK_SECONDS"] = c12.number_input("TOP2-TOP5预判秒数", min_value=3, max_value=30, value=int(settings.get("TOP2_TO_TOP5_PRECHECK_SECONDS", 5)), step=1)
        c13, c14 = st.columns(2)
        new_settings["FULL_REVIEW_TOP_N"] = c13.number_input("完整复核TOP N", min_value=1, max_value=10, value=int(settings.get("FULL_REVIEW_TOP_N", 10)), step=1)
        new_settings["FULL_REVIEW_INTERVAL_SECONDS"] = c14.number_input("完整复核间隔秒数", min_value=10, max_value=120, value=int(settings.get("FULL_REVIEW_INTERVAL_SECONDS", 15)), step=5)
        if st.button("保存机会发现设置", width="stretch"):
            save_fast_opportunity_settings(new_settings)
            st.success("机会发现设置已保存，后台刷新将在下一轮读取。")


def render_market_page(
    rankings: dict[str, list[dict[str, Any]]] | None,
    page_titles: dict[str, tuple[str, str]],
    version: str,
    render_trade_opportunity_board: Callable[[dict[str, list[dict[str, Any]]] | None], None],
    render_rank_list: Callable[[str, list[dict[str, Any]], str], None],
    render_watchlist: Callable[[dict[str, list[dict[str, Any]]]], None],
    render_opportunity_list: Callable[[str, list[dict[str, Any]], str], None],
) -> None:
    """Market page shell; detailed board renderers remain injected callbacks."""
    rankings = market_cache.get_rankings() or rankings or {}
    render_page_head("market", page_titles, version)
    try:
        auto_add_from_rankings(rankings)
    except Exception as exc:
        print(f"[观察池] AI自动加入候选失败 error={repr(exc)}")
    render_fast_opportunity_panel()
    render_trade_opportunity_board(rankings)
    st.markdown('<div class="app-shell">', unsafe_allow_html=True)
    basic_tabs = st.tabs(["涨幅榜", "跌幅榜", "成交量榜", "观察池"])
    with basic_tabs[0]:
        render_rank_list("涨幅榜 TOP10", rankings.get("gainers", []), "gainers")
    with basic_tabs[1]:
        render_rank_list("跌幅榜 TOP10", rankings.get("losers", []), "losers")
    with basic_tabs[2]:
        render_rank_list("成交量榜 TOP10", rankings.get("volume", []), "volume")
    with basic_tabs[3]:
        render_watchlist(rankings)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="app-shell">', unsafe_allow_html=True)
    tabs = st.tabs(["强势币榜", "弱势币榜", "多头机会榜", "空头机会榜", "异动币榜", "高风险榜"])
    with tabs[0]:
        render_opportunity_list("强势币榜 TOP10", rankings.get("strong", []), "strong")
    with tabs[1]:
        render_opportunity_list("弱势币榜 TOP10", rankings.get("weak", []), "weak")
    with tabs[2]:
        render_opportunity_list("多头机会榜 TOP10", rankings.get("long_opportunities", []), "long_opportunities")
    with tabs[3]:
        render_opportunity_list("空头机会榜 TOP10", rankings.get("short_opportunities", []), "short_opportunities")
    with tabs[4]:
        render_opportunity_list("异动币榜 TOP10", rankings.get("abnormal", []), "abnormal")
    with tabs[5]:
        render_opportunity_list("高风险榜 TOP10", rankings.get("high_risk", []), "high_risk")
    st.markdown("</div>", unsafe_allow_html=True)

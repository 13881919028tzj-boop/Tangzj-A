"""Detailed trade-opportunity board widgets."""

from __future__ import annotations

import time
from html import escape
from typing import Any, Callable

import streamlit as st

from components.market_widgets import render_score_sources, watch_action_html
from components.ui import kline_href, render_metric_grid
from services import market_cache
from services.fast_opportunity_engine import get_fast_opportunity_status, run_committee_top10_precheck
from services.watchlist_manager import get_watchlist_candidates_for_committee
from utils.formatters import format_compact, format_percent, format_price, format_score, safe_number, safe_score


def _safe_compare_gte(value: Any, threshold: float) -> bool:
    number = safe_score(value)
    return False if number is None else number >= threshold


def _risk_class(risk_score: Any) -> str:
    risk = safe_score(risk_score)
    if risk is None:
        return "yellow"
    if risk < 35:
        return "green"
    if risk < 65:
        return "yellow"
    return "red"


def _opportunity_class(opportunity_score: Any) -> str:
    score = safe_score(opportunity_score)
    if score is None:
        return "yellow"
    if score >= 80:
        return "green"
    if score >= 60:
        return "yellow"
    return "blue"


def _combined_trade_opportunities(rankings: dict[str, list[dict[str, Any]]] | None, limit: int = 10) -> list[dict[str, Any]]:
    rankings = rankings or {}
    by_symbol: dict[str, dict[str, Any]] = {}
    for key, label in [
        ("long_opportunities", "多头机会榜"),
        ("short_opportunities", "空头机会榜"),
        ("strong", "强势币榜"),
        ("abnormal", "异动币榜"),
    ]:
        for row in rankings.get(key, []) or []:
            symbol = str(row.get("symbol", "")).upper()
            if not symbol:
                continue
            item = dict(row)
            item["opportunity_source"] = label
            old = by_symbol.get(symbol)
            if old is None or safe_score(item.get("final_opportunity_score", item.get("opportunity_score")), -1) > safe_score(old.get("final_opportunity_score", old.get("opportunity_score")), -1):
                by_symbol[symbol] = item
    for item in get_watchlist_candidates_for_committee():
        symbol = str(item.get("symbol", "")).upper()
        if not symbol:
            continue
        watch_score = safe_score(item.get("watch_score"), 0) or 0
        confidence = safe_score(item.get("confidence"), watch_score) or watch_score
        risk = safe_score(item.get("risk_score"), 50)
        opportunity_score = max(watch_score, min(100, confidence))
        if opportunity_score < 61 or _safe_compare_gte(risk, 80):
            continue
        ticker = market_cache.get_ticker(symbol) or {}
        row = {
            "symbol": symbol,
            "last_price": ticker.get("last_price"),
            "current_price": ticker.get("last_price"),
            "price_change_percent": ticker.get("price_change_percent"),
            "quote_volume": ticker.get("quote_volume", 0),
            "final_opportunity_score": opportunity_score,
            "raw_opportunity_score": opportunity_score,
            "opportunity_score": opportunity_score,
            "risk_score": risk,
            "risk_penalty": 0,
            "score_cap": 100,
            "direction": item.get("local_strategy_action") or "观察",
            "advice": item.get("local_strategy_action") or "观察池候选",
            "opportunity_status": item.get("status") or "观察池候选",
            "current_market_state": item.get("strategy_name") or "观察池重点跟踪",
            "opportunity_source": "观察池候选",
            "watch_score": watch_score,
            "watchlist_candidate": True,
            "watchlist_reason": item.get("main_reason"),
        }
        old = by_symbol.get(symbol)
        if old is None or safe_score(row.get("final_opportunity_score"), -1) > safe_score(old.get("final_opportunity_score", old.get("opportunity_score")), -1):
            by_symbol[symbol] = row
    return sorted(by_symbol.values(), key=lambda row: safe_score(row.get("final_opportunity_score", row.get("opportunity_score")), -1), reverse=True)[:limit]


def _top10_precheck_map(rankings: dict[str, list[dict[str, Any]]] | None) -> dict[str, dict[str, Any]]:
    try:
        results = run_committee_top10_precheck(rankings, 10)
    except Exception:
        results = list((get_fast_opportunity_status().get("latest_top10_precheck") or []))
    return {str(item.get("symbol", "")).upper(): item for item in results}


def render_top10_committee_precheck_summary(precheck_by_symbol: dict[str, dict[str, Any]]) -> None:
    if not precheck_by_symbol:
        return
    results = list(precheck_by_symbol.values())
    allowed = [item for item in results if item.get("allowed_candidate")]
    blocked = [item for item in results if not item.get("allowed_candidate")]
    watch = [item for item in blocked if str(item.get("fast_action")) == "观察复核"]
    top_allowed = "、".join(str(item.get("symbol")) for item in allowed[:5]) or "暂无"
    main_blocks = []
    for item in blocked[:4]:
        reason = "；".join(str(r) for r in list(item.get("block_reasons") or [])[:2]) or "等待完整复核"
        main_blocks.append(f"{item.get('symbol')}：{reason}")
    block_text = "｜".join(main_blocks) if main_blocks else "暂无明显阻断。"
    st.markdown(
        f"""
        <div class="status-card" style="margin-top:8px;">
          <b>机会榜TOP10委员会快速判断</b><br>
          可进候选：{len(allowed)} 个｜观察复核：{len(watch)} 个｜阻断/等待：{len(blocked)} 个<br>
          候选对象：{escape(top_allowed)}<br>
          主要阻断：{escape(block_text)}<br>
          该判断为轻量预判，不调用DeepSeek/Gemini，不替代完整委员会复核。
        </div>
        """,
        unsafe_allow_html=True,
    )
    multi_review = list((get_fast_opportunity_status().get("latest_multi_review") or []))
    if multi_review:
        rows_html = ""
        for item in multi_review[:10]:
            status = str(item.get("review_status", "pending"))
            symbol = str(item.get("symbol", "-"))
            created = "已生成候选" if item.get("candidate_created") else "未生成候选"
            reason = str(item.get("block_reason") or item.get("candidate_id") or "等待下一轮复核")
            review_count = int(safe_score(item.get("review_count"), 0) or 0)
            reject_count = int(safe_score(item.get("reject_count"), 0) or 0)
            opportunity_round = int(safe_score(item.get("opportunity_round"), 1) or 1)
            rows_html += (
                f'<div class="opp-meta">#{escape(str(item.get("rank", "-")))} {escape(symbol)}｜'
                f'第{opportunity_round}轮｜审查{review_count}次｜否决{reject_count}次｜'
                f'{escape(status)}｜{escape(created)}｜{escape(reason)}</div>'
            )
        st.markdown(
            f"""
            <div class="status-card" style="margin-top:8px;">
              <b>多机会并行评审队列</b><br>
              {rows_html}
            </div>
            """,
            unsafe_allow_html=True,
        )


def _multi_review_map() -> dict[str, dict[str, Any]]:
    return {str(item.get("symbol", "")).upper(): item for item in (get_fast_opportunity_status().get("latest_multi_review") or [])}


def _opportunity_committee_summary(symbol: str, row: dict[str, Any], precheck: dict[str, Any], multi: dict[str, Any]) -> dict[str, Any]:
    allowed = bool(precheck.get("allowed_candidate"))
    review_status = str(multi.get("review_status") or precheck.get("review_status") or "pending")
    candidate_created = bool(multi.get("candidate_created"))
    block_reason = str(multi.get("block_reason") or "；".join(precheck.get("block_reasons") or []) or "")
    if candidate_created:
        candidate_status = "自动候选"
    elif review_status == "blocked":
        candidate_status = "阻止"
    elif allowed:
        candidate_status = "候选池"
    elif review_status in {"watching", "fast_checked"}:
        candidate_status = "观察池"
    else:
        candidate_status = "未进入"

    action = str(precheck.get("fast_action") or ("轻仓试单" if allowed else "等待复核"))
    decision = "支持做多" if allowed and precheck.get("direction") == "long" else "支持做空" if allowed and precheck.get("direction") == "short" else "建议观察" if review_status != "blocked" else "禁止开仓"
    review_count = int(safe_score(multi.get("review_count", precheck.get("review_count")), 0) or 0)
    reject_count = int(safe_score(multi.get("reject_count", row.get("reject_count")), 0) or 0)
    opportunity_round = int(safe_score(multi.get("opportunity_round", row.get("opportunity_round")), 1) or 1)
    lifecycle_status = str(multi.get("status") or row.get("status") or "")
    removed_reason = str(multi.get("removed_reason") or row.get("removed_reason") or "")
    cooldown = {"has_cooldown": False, "cooldown_type": "", "remaining_seconds": 0, "reason": ""}
    cooldown_until = safe_score(multi.get("cooldown_until", row.get("cooldown_until")), 0) or 0
    remaining = max(0, int(cooldown_until - time.time())) if cooldown_until else 0
    if "冷却" in block_reason or remaining > 0:
        mm, ss = divmod(remaining, 60)
        reason = f"第{opportunity_round}轮｜否决{reject_count}次｜冷却中 {mm:02d}:{ss:02d}｜审查{review_count}次"
        if remaining <= 0:
            reason = block_reason if "审查" in block_reason else f"{block_reason} 审查 {review_count} 次。"
        cooldown = {"has_cooldown": True, "cooldown_type": "candidate_or_reject_cooldown", "remaining_seconds": remaining, "reason": reason}
    return {
        "review_status": review_status,
        "committee_decision": decision,
        "final_action": action,
        "trade_permission": "candidate" if allowed else "blocked" if review_status == "blocked" else "observe_only",
        "resonance_level": "等待完整复核" if review_status in {"pending", "fast_checked"} else "被否决" if review_status == "blocked" else "中等共振",
        "support_weight": 0,
        "observe_weight": 0,
        "oppose_weight": 0,
        "hard_veto": review_status == "blocked" and _safe_compare_gte(precheck.get("risk_score"), 85),
        "soft_warning_count": len(precheck.get("warnings") or []),
        "review_count": review_count,
        "reject_count": reject_count,
        "opportunity_round": opportunity_round,
        "lifecycle_status": lifecycle_status,
        "removed_reason": removed_reason,
        "deepseek_status": "外部AI待补充",
        "gemini_status": "外部AI待补充",
        "cooldown_status": cooldown,
        "candidate_status": candidate_status,
        "next_action": "等待自动交易检查" if candidate_created else "进入候选检查" if allowed else block_reason or "继续观察",
        "last_review_time": str(multi.get("last_review_time") or precheck.get("timestamp") or ""),
        "review_age_seconds": 0,
    }


def render_trade_opportunity_board(
    rankings: dict[str, list[dict[str, Any]]] | None,
    compact: bool = False,
    set_current_symbol: Callable[[str, str], None] | None = None,
) -> None:
    """Render detailed live trade-opportunity board."""
    rankings = market_cache.get_rankings() or rankings or {}
    rows = _combined_trade_opportunities(rankings, 10)
    precheck_by_symbol = _top10_precheck_map(rankings)
    multi_by_symbol = _multi_review_map()
    fast_status = get_fast_opportunity_status()
    fast_target = str(fast_status.get("current_target") or "").upper()
    committee_symbol = str(st.session_state.get("committee_active_symbol") or st.session_state.get("current_symbol", "")).upper()
    queue_symbol = str(st.session_state.get("committee_review_queue_symbol") or "").upper()
    settings = fast_status.get("settings") or {}
    trigger_score = int(settings.get("OPPORTUNITY_TRIGGER_SCORE", 80) or 80)
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">实时交易机会榜单</div>
            <div class="module-desc">交易机会榜是系统所有交易机会的总入口。每个币都会经过交易委员会复核；评分达到{trigger_score}分只进入候选，不代表允许真实下单。榜单会持续显示入榜价、现价、委员会判断、冷却和候选状态。</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not rows:
        st.info("交易机会榜单正在等待市场扫描数据。")
        return
    top1 = rows[0]
    top1_symbol = str(top1.get("symbol", "")).upper()
    top1_score = safe_score(top1.get("final_opportunity_score", top1.get("opportunity_score")))
    top1_risk = safe_score(top1.get("risk_score"))
    anchor_text = "当前查看" if committee_symbol == top1_symbol else "后台复核" if queue_symbol == top1_symbol else "未锚定"
    fast_text = "快速捕捉目标" if fast_target == top1_symbol else "等待快速确认"
    render_metric_grid(
        [
            ("榜首交易对象", top1_symbol, "green"),
            ("TOP1最终机会分", format_score(top1_score), "green" if _safe_compare_gte(top1_score, trigger_score) else "yellow"),
            ("TOP1风险分", format_score(top1_risk), _risk_class(top1_risk)),
            ("当前委员会对象", committee_symbol or "-", "blue"),
            ("锚定状态", anchor_text, "green" if anchor_text == "当前查看" else "yellow"),
            ("快速捕捉", fast_text, "green" if fast_text == "快速捕捉目标" else "yellow"),
            ("候选阈值", f"{trigger_score}分", "blue"),
        ]
    )
    render_top10_committee_precheck_summary(precheck_by_symbol)
    if committee_symbol != top1_symbol and set_current_symbol is not None:
        if st.button(f"切换当前交易对象到 TOP1：{top1_symbol}", key=f"anchor_top1_{top1_symbol}_{compact}", width="stretch"):
            set_current_symbol(top1_symbol, source="opportunity_board_click")
            st.rerun()
    st.markdown(
        '<div class="list-card"><div class="module-title">交易机会榜 TOP10</div>'
        '<div class="opp-row compact-five rank-head"><div>交易对象</div><div>入榜/现价</div><div>观察</div><div>涨跌/数据</div><div>机会/委员会</div></div>',
        unsafe_allow_html=True,
    )
    for index, row in enumerate(rows, start=1):
        medal_class = "gold" if index == 1 else "silver" if index == 2 else "bronze" if index == 3 else ""
        score = safe_score(row.get("final_opportunity_score", row.get("opportunity_score")))
        raw_score = safe_score(row.get("raw_opportunity_score"), score)
        risk = safe_score(row.get("risk_score"))
        risk_penalty = safe_score(row.get("risk_penalty"), 0)
        score_cap = safe_score(row.get("score_cap"), 100)
        status = str(row.get("opportunity_status", row.get("advice", "观察")))
        symbol = str(row.get("symbol", "")).upper()
        precheck = precheck_by_symbol.get(symbol, {})
        multi = multi_by_symbol.get(symbol, {})
        committee_summary = _opportunity_committee_summary(symbol, row, precheck, multi)
        row["committee_summary"] = committee_summary
        committee_action = str(committee_summary.get("final_action") or "等待判断")
        candidate_status = str(committee_summary.get("candidate_status") or "未进入")
        review_status = str(committee_summary.get("review_status") or "pending")
        reject_count = int(safe_score(committee_summary.get("reject_count"), 0) or 0)
        opportunity_round = int(safe_score(committee_summary.get("opportunity_round"), 1) or 1)
        removed_reason = str(committee_summary.get("removed_reason") or "")
        cooldown = committee_summary.get("cooldown_status") or {}
        cooldown_text = cooldown.get("reason") if cooldown.get("has_cooldown") else "无"
        ticker = market_cache.get_ticker(symbol) or {}
        entry_snapshot = row.get("entry_snapshot") if isinstance(row.get("entry_snapshot"), dict) else {}
        entry_price = safe_number(row.get("entry_price") or entry_snapshot.get("entry_price"))
        if entry_price is None:
            entry_price = safe_number(row.get("last_price") or row.get("current_price"))
        live_price = safe_number(ticker.get("last_price"), safe_number(row.get("last_price") or row.get("current_price")))
        change = safe_number(ticker.get("price_change_percent"), safe_number(row.get("price_change_percent")))
        live_change = ((live_price - entry_price) / entry_price * 100) if live_price is not None and entry_price not in {None, 0} else None
        change_class = "green" if (change is not None and change >= 0) else "red" if change is not None else "yellow"
        state = "当前查看" if symbol == committee_symbol else ("后台TOP1" if symbol == queue_symbol else "复核队列")
        selected_class = " selected-row" if symbol == committee_symbol else ""
        href = kline_href(symbol)
        st.markdown(
            f"""
            <div class="opp-row compact-five{selected_class}">
              <div>
                <a class="rank-link" href="{href}" target="_self"><div class="opp-symbol"><span class="rank-index {medal_class}">#{index}</span> {symbol}</div></a>
                <div class="opp-meta">{escape(str(row.get("current_market_state", "-")))} · {escape(str(row.get("opportunity_source", "-")))}</div>
                <div class="opp-meta">{escape(state)}</div>
              </div>
              <div>
                <div class="opp-meta">入榜价 {format_price(entry_price)}</div>
                <div class="opp-symbol">{format_price(live_price)}</div>
                <div class="opp-meta">入榜后 {format_percent(live_change)}</div>
              </div>
              <div>{watch_action_html(symbol, st.session_state.active_page, "实时交易机会榜")}</div>
              <div>
                <div class="{change_class}" style="font-weight:900;">24h {format_percent(change)}</div>
                <div class="opp-meta">复核：{escape(review_status)}</div>
                <div class="opp-meta">第{opportunity_round}轮｜否决{reject_count}次</div>
                <div class="opp-meta">冷却：{escape(str(cooldown_text))}</div>
                <div class="opp-meta">移除原因：{escape(removed_reason or "无")}</div>
                <div class="opp-meta">DeepSeek/Gemini：外部AI待补充</div>
              </div>
              <div>
                <div class="score-pill">终{format_score(score)} / 风{format_score(risk)}</div>
                <div class="opp-meta">原始{format_score(raw_score)}｜扣{format_score(risk_penalty)}｜封顶{format_score(score_cap)}</div>
                <div class="opp-meta">委员会：<span class="{_opportunity_class(score)}">{escape(committee_action)}</span></div>
                <div class="opp-meta">候选：{escape(candidate_status)}｜{escape(status)}</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(render_score_sources(row), unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

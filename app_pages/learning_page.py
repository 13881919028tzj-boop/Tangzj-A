"""Replay learning page."""

from __future__ import annotations

from html import escape
from typing import Any

import streamlit as st

from components.ui import render_metric_grid, render_page_head, safe_committee_text, signal_color
from services.external_ai_center import get_external_ai_performance_summary
from services.replay_learning_engine import analyze_replay_learning


def render_learning(page_titles: dict[str, tuple[str, str]], version: str) -> None:
    """Render the replay learning center."""
    render_page_head("learning", page_titles, version)
    replay = analyze_replay_learning()
    summary = replay.get("summary", {})
    win_loss = replay.get("win_loss", {})
    weaknesses = replay.get("weaknesses", [])
    members = replay.get("member_performance", [])
    suggestions = replay.get("strategy_factory_suggestions", [])
    history = replay.get("history", [])

    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">复盘学习中心</div>
            <div class="module-desc">基于本地模拟交易历史，分析输赢原因、策略弱点、委员表现，并为策略工厂提供优化依据。</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_metric_grid(
        [
            ("复盘样本", f"{summary.get('total_trades', 0)} 笔", "blue"),
            ("胜率", f"{float(summary.get('win_rate', 0) or 0):.2f}%", "green" if float(summary.get("win_rate", 0) or 0) >= 50 else "yellow"),
            ("累计模拟盈亏", f"{float(summary.get('total_pnl', 0) or 0):+.2f} USDT", "green" if float(summary.get("total_pnl", 0) or 0) >= 0 else "red"),
            ("平均每笔盈亏", f"{float(summary.get('avg_pnl', 0) or 0):+.2f} USDT", "green" if float(summary.get("avg_pnl", 0) or 0) >= 0 else "red"),
            ("盈利 / 亏损", f"{summary.get('wins', 0)} / {summary.get('losses', 0)}", ""),
            ("人工干预", f"{summary.get('manual_override_count', 0)} 次", "yellow" if summary.get("manual_override_count", 0) else ""),
            ("最大盈利", f"{float(summary.get('best_trade', 0) or 0):+.2f} USDT", "green"),
            ("最大亏损", f"{float(summary.get('worst_trade', 0) or 0):+.2f} USDT", "red"),
            ("数据质量", str(summary.get("data_quality", "poor")), "green" if summary.get("data_quality") == "good" else "yellow" if summary.get("data_quality") == "partial" else "red"),
        ]
    )

    st.markdown(
        f"""
        <div class="app-shell"><div class="module-card">
          <div class="module-title">复盘结论</div>
          <div class="status-card">{escape(str(summary.get("sample_warning", "暂无复盘样本。")))}</div>
          <div class="status-card" style="margin-top:8px;">
            <b>盈利原因：</b>{escape(str(win_loss.get("win_summary", "暂无盈利样本。")))}<br>
            <b>亏损原因：</b>{escape(str(win_loss.get("loss_summary", "暂无亏损样本。")))}<br>
            <b>主要亏损来源：</b>{escape(str(win_loss.get("main_loss_reason", "暂无")))}
          </div>
        </div></div>
        """,
        unsafe_allow_html=True,
    )

    tabs = st.tabs(["输赢原因", "策略弱点", "委员表现", "外部AI表现", "人工干预", "策略工厂建议", "交易复盘记录"])

    with tabs[0]:
        reasons = win_loss.get("close_reason_breakdown") or []
        if not reasons:
            st.info("暂无足够交易记录，完成模拟交易后会生成输赢原因拆解。")
        for item in reasons[:12]:
            avg_pnl = float(item.get("avg_pnl", 0) or 0)
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(item.get("reason", "未知原因")))}</b><br>
                  出现次数：{item.get("count", 0)}｜亏损次数：{item.get("loss_count", 0)}｜
                  平均盈亏：<span class="{signal_color("支持交易" if avg_pnl >= 0 else "反对交易")}">{avg_pnl:+.2f} USDT</span><br>
                  解释：该平仓原因用于判断当前模拟系统的主要收益来源或亏损来源。
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[1]:
        if not weaknesses:
            st.success("当前没有发现明显策略弱点。若样本较少，该结论仅作保守参考。")
        for item in weaknesses:
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(item.get("dimension", "")))}：{escape(str(item.get("name", "")))}</b><br>
                  交易数：{item.get("trade_count", 0)}｜胜率：{float(item.get("win_rate", 0) or 0):.2f}%｜
                  平均盈亏：{float(item.get("avg_pnl", 0) or 0):+.2f} USDT｜平均R：{float(item.get("avg_r", 0) or 0):+.2f}R<br>
                  {escape(str(item.get("explanation", "")))}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[2]:
        if not members:
            st.info("当前历史交易中缺少委员投票快照。新平仓交易会保存委员会快照，后续可评估委员表现。")
        for member in members:
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(member.get("member_name", "未知委员")))}</b><br>
                  支持交易次数：{member.get("support_count", 0)}｜判断正确：{member.get("correct_count", 0)}｜判断错误：{member.get("wrong_count", 0)}｜否决次数：{member.get("veto_count", 0)}<br>
                  支持后胜率：{float(member.get("accuracy", 0) or 0):.2f}%｜支持后平均盈亏：{float(member.get("avg_pnl_when_supported", 0) or 0):+.2f} USDT<br>
                  {escape(str(member.get("explanation", "")))}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[3]:
        perf = get_external_ai_performance_summary()
        st.markdown("DeepSeek/Gemini 当前为正式投票委员，参与委员会权重统计；仍不能直接执行交易或绕过风控。")
        for provider, label in [("deepseek", "DeepSeek"), ("gemini", "Gemini")]:
            item = perf.get(provider) or {}
            dirs = item.get("direction_counts") or {}
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{label} 表现卡</b>｜升级建议：<span class="yellow">{escape(str(item.get("upgrade_suggestion", "继续影子模式")))}</span><br>
                  总分析：{item.get("total_calls", 0)}｜有效：{item.get("valid_calls", 0)}｜失败：{item.get("failed_calls", 0)}｜失败率：{float(item.get("failure_rate", 0) or 0):.2f}%｜平均耗时：{float(item.get("avg_duration_ms", 0) or 0):.0f} ms<br>
                  方向统计：多 {dirs.get("long", 0)} / 空 {dirs.get("short", 0)} / 中性 {dirs.get("neutral", 0)}｜软否决：{item.get("soft_veto_count", 0)}<br>
                  方向准确率：{float(item.get("direction_accuracy", 0) or 0):.2f}%｜风险识别有效率：{float(item.get("risk_identification_effective_rate", 0) or 0):.2f}%｜软否决有效率：{float(item.get("soft_veto_effective_rate", 0) or 0):.2f}%<br>
                  过度保守：{item.get("over_conservative_count", 0)}｜过度激进：{item.get("over_aggressive_count", 0)}｜{escape(str(item.get("sample_warning", "样本数量不足，暂不评估外部AI准确率。")))}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[4]:
        overrides = replay.get("manual_overrides") or []
        if not overrides:
            st.info("暂无人工仓位干预记录。")
        for row in overrides[:50]:
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(row.get("symbol", "")))}</b>｜{escape(str(row.get("mode", "模拟")))}｜{escape(str(row.get("result", "")))}<br>
                  系统建议：{escape(str(row.get("system_position_suggestion", "")))}｜风控最大：{escape(str(row.get("risk_max_position", "")))}%｜用户选择：{escape(str(row.get("user_selected_position", "")))}%<br>
                  委员会动作：{escape(str(row.get("committee_final_action", "")))}｜风险评分：{escape(str(row.get("risk_score", "")))}｜原因：{escape(str(row.get("reason", "")))}<br>
                  复盘标记：该记录存在人工仓位干预，需要单独评估系统原始建议与用户干预后表现。
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[5]:
        for item in suggestions:
            priority = str(item.get("priority", "中"))
            color = "red" if priority == "高" else "yellow"
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(item.get("title", "")))}</b>｜<span class="{color}">优先级：{escape(priority)}</span><br>
                  {escape(str(item.get("suggestion", "")))}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[6]:
        if not history:
            st.info("暂无模拟交易历史。请先在交易页运行模拟交易，完成平仓后这里会自动生成复盘记录。")
        for row in history[:50]:
            pnl = float(row.get("pnl", 0) or 0)
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(row.get("symbol", "")))}</b>｜{escape("空单" if row.get("direction") == "short" else "多单")}｜{escape(str(row.get("close_reason", "")))}｜
                  <span class="{signal_color("支持交易" if pnl >= 0 else "反对交易")}">{pnl:+.2f} USDT</span><br>
                  委员会动作：{escape(str(row.get("committee_action", "暂无")))}｜策略：{escape(str(row.get("strategy_name", "暂无")))}｜风险收益比：{escape(str(row.get("risk_reward_ratio", "暂无")))}<br>
                  主席总结：{escape(safe_committee_text(row.get("chairman_summary", "暂无"), 420))}
                </div>
                """,
                unsafe_allow_html=True,
            )

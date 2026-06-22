"""Data dashboard page rendering."""

from __future__ import annotations

from typing import Any

import streamlit as st

from components.ui import render_metric_grid, render_page_head
from services.dashboard_center import (
    collect_all_metrics,
    export_report_csv,
    export_report_json,
    export_report_markdown,
    generate_daily_report,
    generate_monthly_report,
    generate_weekly_report,
    load_recent_reports,
)


def _render_dashboard_dict(data: dict[str, Any], skip: set[str] | None = None) -> None:
    """把指标字典安全渲染为窄表，跳过复杂嵌套字段。"""
    skip = skip or set()
    rows = []
    for key, value in data.items():
        if key in skip or isinstance(value, (dict, list)):
            continue
        rows.append({"指标": key, "数值": value})
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info("暂无可展示明细。")


def render_data_dashboard_page(page_titles: dict[str, tuple[str, str]], version: str) -> None:
    """数据看板与经营分析报告中心。"""
    render_page_head("dashboard", page_titles, version)
    metrics = collect_all_metrics()
    overview = metrics.get("overview") or {}
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>数据看板只统计，不执行交易</b><br>
            本页面用于汇总模拟、实盘、审批、委员、外部AI、风控、服务器与通知数据。样本不足时只给保守观察结论，不会自动扩大额度或修改策略。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("系统运行天数", f"{float(overview.get('system_runtime_days', 0) or 0):.1f}", ""),
            ("自动模拟交易", str(overview.get("simulation_trades", 0)), ""),
            ("模拟总收益", f"{float(overview.get('simulation_pnl', 0) or 0):+.2f} USDT", "green" if float(overview.get("simulation_pnl", 0) or 0) >= 0 else "red"),
            ("模拟最大回撤", f"{float(overview.get('simulation_max_drawdown', 0) or 0):.2f}%", "yellow"),
            ("实盘订单", str(overview.get("live_orders", 0)), ""),
            ("实盘总盈亏", f"{float(overview.get('live_pnl', 0) or 0):+.2f} USDT", "green" if float(overview.get("live_pnl", 0) or 0) >= 0 else "red"),
            ("审批单", str(overview.get("approvals", 0)), ""),
            ("风控拦截", str(overview.get("risk_blocks", 0)), "yellow"),
            ("熔断次数", str(overview.get("circuit_breakers", 0)), "red" if int(overview.get("circuit_breakers", 0) or 0) else ""),
            ("外部AI样本", str(overview.get("deepseek_gemini_samples", 0)), ""),
            ("样本是否足够", "是" if overview.get("sample_enough") else "否", "green" if overview.get("sample_enough") else "yellow"),
            ("当前建议", str(overview.get("recommendation", "继续观察")), "yellow"),
        ]
    )

    tabs = st.tabs(["总览", "模拟交易", "实盘交易", "自动实盘", "审批流", "委员表现", "外部AI", "策略", "风控", "服务器", "通知远控", "报告", "数据质量"])
    with tabs[0]:
        st.markdown("**平台数据总览**")
        st.info(str(overview.get("recommendation", "继续观察，不建议扩大额度")))
        _render_dashboard_dict(overview)

    with tabs[1]:
        sim = metrics.get("simulation") or {}
        st.markdown("**模拟交易看板**")
        st.info(str(sim.get("sample_warning", "模拟交易样本不足，统计结果仅供参考。")))
        render_metric_grid(
            [
                ("交易次数", str(sim.get("trade_count", 0)), ""),
                ("胜率", f"{float(sim.get('win_rate', 0) or 0):.1f}%", "green"),
                ("总盈亏", f"{float(sim.get('total_pnl', 0) or 0):+.2f}", "green" if float(sim.get("total_pnl", 0) or 0) >= 0 else "red"),
                ("Profit Factor", f"{float(sim.get('profit_factor', 0) or 0):.2f}", ""),
                ("最大回撤", f"{float(sim.get('max_drawdown', 0) or 0):.2f}%", "yellow"),
                ("最佳交易", f"{float(sim.get('best_trade', 0) or 0):+.2f}", "green"),
                ("最差交易", f"{float(sim.get('worst_trade', 0) or 0):+.2f}", "red"),
                ("常见币种", str(sim.get("most_common_symbol", "-")), ""),
            ]
        )
        st.caption(f"数据源：{sim.get('source')}｜权益曲线：{sim.get('equity_source')}")
        _render_dashboard_dict(sim, {"sample_warning"})

    with tabs[2]:
        live = metrics.get("live") or {}
        st.markdown("**实盘交易看板**")
        st.warning("实盘样本较少时，不建议据此扩大资金。")
        st.info(str(live.get("sample_warning", "实盘样本不足。")))
        render_metric_grid(
            [
                ("实盘订单数", str(live.get("trade_count", 0)), ""),
                ("成交数", str(live.get("filled_count", 0)), "green"),
                ("撤单数", str(live.get("cancelled_count", 0)), "yellow"),
                ("总投入", f"{float(live.get('total_invested', 0) or 0):.2f} USDT", ""),
                ("综合盈亏", f"{float(live.get('combined_pnl', 0) or 0):+.2f} USDT", "green" if float(live.get("combined_pnl", 0) or 0) >= 0 else "red"),
                ("手续费估算", f"{float(live.get('estimated_fee', 0) or 0):.2f}", ""),
                ("人工干预", str(live.get("manual_override_count", 0)), "yellow"),
                ("胜率", f"{float(live.get('win_rate', 0) or 0):.1f}%", ""),
            ]
        )
        st.caption(f"数据源：{live.get('source')}｜持仓源：{live.get('position_source')}")
        _render_dashboard_dict(live, {"sample_warning"})

    with tabs[3]:
        auto_live = metrics.get("auto_live") or {}
        st.markdown("**自动实盘试运行看板**")
        st.info(str(auto_live.get("sample_warning", "自动实盘样本不足。")))
        render_metric_grid(
            [
                ("事件数", str(auto_live.get("event_count", 0)), ""),
                ("开启次数", str(auto_live.get("enabled_count", 0)), ""),
                ("成功数", str(auto_live.get("success_count", 0)), "green"),
                ("失败数", str(auto_live.get("failure_count", 0)), "red"),
                ("熔断次数", str(auto_live.get("circuit_breaker_count", 0)), "red"),
                ("冷却次数", str(auto_live.get("cooldown_count", 0)), "yellow"),
            ]
        )
        st.warning(str(auto_live.get("recommendation", "继续关闭自动实盘")))
        _render_dashboard_dict(auto_live, {"sample_warning"})

    with tabs[4]:
        approval = metrics.get("approval") or {}
        st.markdown("**审批流表现看板**")
        st.info(str(approval.get("sample_warning", "审批流样本不足。")))
        render_metric_grid(
            [
                ("审批总数", str(approval.get("total", 0)), ""),
                ("待审批", str(approval.get("pending", 0)), "yellow"),
                ("已批准", str(approval.get("approved", 0)), "green"),
                ("已拒绝", str(approval.get("rejected", 0)), "red"),
                ("已修改", str(approval.get("modified", 0)), ""),
                ("已过期", str(approval.get("expired", 0)), "yellow"),
                ("执行成功", str(approval.get("executed", 0)), "green"),
                ("执行失败", str(approval.get("failed", 0)), "red"),
            ]
        )
        _render_dashboard_dict(approval, {"sample_warning"})

    with tabs[5]:
        committee = metrics.get("committee") or {}
        st.markdown("**委员表现看板**")
        st.info(str(committee.get("sample_warning", "委员投票样本不足，暂不建议调整权重。")))
        members = committee.get("members") or {}
        if not members:
            st.info("暂无委员投票数据。")
        else:
            st.dataframe([{"委员": name, **value} for name, value in members.items()], width="stretch", hide_index=True)
        st.caption(f"数据源：{committee.get('source')}")

    with tabs[6]:
        external_ai = metrics.get("external_ai") or {}
        st.markdown("**DeepSeek / Gemini 表现看板**")
        st.info(str(external_ai.get("sample_warning", "外部AI样本不足。")))
        st.warning(str(external_ai.get("upgrade_suggestion", "外部AI样本不足，暂不建议提高权重。")))
        ai_stats = external_ai.get("ai_stats") or {}
        if not ai_stats:
            st.info("暂无外部AI审计数据。")
        else:
            st.dataframe([{"AI": name, **value} for name, value in ai_stats.items()], width="stretch", hide_index=True)
        st.caption(f"数据源：{external_ai.get('source')}")

    with tabs[7]:
        strategy = metrics.get("strategy") or {}
        st.markdown("**策略表现看板**")
        st.info(str(strategy.get("sample_warning", "策略样本不足。")))
        render_metric_grid([("策略样本", str(strategy.get("strategy_count", 0)), ""), ("数据质量", str(strategy.get("data_quality", "poor")), "yellow")])
        st.markdown("评级分布")
        st.json(strategy.get("grade_summary", {}))
        st.markdown("过拟合风险分布")
        st.json(strategy.get("overfit_summary", {}))
        st.caption(f"数据源：{strategy.get('source')}")

    with tabs[8]:
        risk = metrics.get("risk") or {}
        st.markdown("**风控表现看板**")
        st.info(str(risk.get("conclusion", "风控样本不足，建议维持保守设置。")))
        render_metric_grid(
            [
                ("风险事件", str(risk.get("risk_event_count", 0)), ""),
                ("否决次数", str(risk.get("risk_veto_count", 0)), "red"),
                ("数据质量拦截", str(risk.get("data_quality_block_count", 0)), "yellow"),
                ("API异常拦截", str(risk.get("api_error_block_count", 0)), "red"),
                ("熔断次数", str(risk.get("circuit_breaker_count", 0)), "red"),
                ("冷却次数", str(risk.get("cooldown_count", 0)), "yellow"),
            ]
        )
        st.caption(f"数据源：{risk.get('source')}")

    with tabs[9]:
        server = metrics.get("server") or {}
        st.markdown("**服务器运行看板**")
        st.info(str(server.get("stability", "暂无服务器运行日志。")))
        render_metric_grid(
            [
                ("运行事件", str(server.get("runtime_events", 0)), ""),
                ("估算运行天数", f"{float(server.get('estimated_runtime_days', 0) or 0):.1f}", ""),
                ("错误数", str(server.get("error_count", 0)), "red" if int(server.get("error_count", 0) or 0) else ""),
                ("重启数", str(server.get("restart_count", 0)), "yellow"),
            ]
        )
        st.caption(f"数据源：{server.get('source')}")

    with tabs[10]:
        notification = metrics.get("notification") or {}
        st.markdown("**通知与远程操作看板**")
        render_metric_grid(
            [
                ("通知总数", str(notification.get("notification_count", 0)), ""),
                ("未读通知", str(notification.get("unread_count", 0)), "yellow"),
                ("紧急通知", str(notification.get("urgent_count", 0)), "red"),
                ("审批通知", str(notification.get("approval_notice_count", 0)), ""),
                ("风险通知", str(notification.get("risk_notice_count", 0)), "yellow"),
                ("远程操作", str(notification.get("remote_action_count", 0)), ""),
                ("登录失败", str(notification.get("login_failure_count", 0)), "red"),
            ]
        )
        st.caption(f"数据源：{notification.get('source')}")

    with tabs[11]:
        st.markdown("**日报 / 周报 / 月报生成器**")
        c1, c2, c3 = st.columns(3)
        generated_report = None
        if c1.button("生成今日报告", width="stretch"):
            generated_report = generate_daily_report()
        if c2.button("生成本周报告", width="stretch"):
            generated_report = generate_weekly_report()
        if c3.button("生成本月报告", width="stretch"):
            generated_report = generate_monthly_report()
        if generated_report:
            st.success("报告已生成。")
            st.json(generated_report.get("files", {}))
            st.download_button("下载 Markdown", export_report_markdown(generated_report), file_name=f"{generated_report.get('kind')}_report.md", mime="text/markdown", width="stretch")
            st.download_button("下载 JSON", export_report_json(generated_report), file_name=f"{generated_report.get('kind')}_report.json", mime="application/json", width="stretch")
            st.download_button("下载 CSV 指标摘要", export_report_csv(generated_report), file_name=f"{generated_report.get('kind')}_metrics.csv", mime="text/csv", width="stretch")
        reports = load_recent_reports()
        if reports:
            st.dataframe(reports, width="stretch", hide_index=True)
        else:
            st.info("暂无已生成报告。")

    with tabs[12]:
        quality = metrics.get("quality") or {}
        st.markdown("**数据质量检查**")
        if quality.get("checks"):
            st.dataframe(quality.get("checks"), width="stretch", hide_index=True)
        issues = quality.get("issues") or []
        if issues:
            st.warning(f"检测到 {quality.get('issue_count', 0)} 条历史数据质量记录，已跳过异常数据。")
            st.dataframe(issues, width="stretch", hide_index=True)
        else:
            st.info("暂无数据质量异常记录。")

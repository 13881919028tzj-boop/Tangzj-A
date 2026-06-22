"""Simulation trading page."""

from __future__ import annotations

import time
from html import escape
from typing import Any, Callable

import streamlit as st

from components.ui import kline_symbol_link, render_metric_grid, render_page_head, safe_committee_text, signal_color
from services import market_cache
from services.sim_observability import build_sim_diagnostic_rows, build_sim_score_feedback_rows
from services.sim_trade_engine import (
    cancel_order,
    calculate_position_holding_time,
    calculate_position_r_multiple,
    calculate_sim_score_feedback,
    clear_sim_history,
    close_sim_position,
    create_pending_sim_order,
    get_sim_account_summary,
    get_sim_equity_curve,
    load_sim_diagnostics,
    load_settings,
    move_stop_to_breakeven,
    reset_sim_account,
    save_settings,
    set_sim_status,
    update_simulation,
    validate_signal_for_simulation,
)
from utils.formatters import direction_text, format_price, format_waiting_price, money_text, pct_text, seconds_text, valid_price


def committee_decision_to_sim_signal(decision: dict[str, Any]) -> dict[str, Any]:
    """Convert the current committee decision into a simulation signal."""
    if not decision:
        return {}
    return {
        "symbol": decision.get("symbol"),
        "direction": decision.get("final_direction"),
        "action": decision.get("final_action"),
        "trade_permission": decision.get("trade_permission"),
        "approved_for_simulation": decision.get("approved_for_simulation"),
        "veto_members": decision.get("veto_members"),
        "committee_confidence": decision.get("committee_confidence"),
        "risk_score": decision.get("committee_risk_score"),
        "position_suggestion": decision.get("position_suggestion"),
        "entry_zone": decision.get("entry_zone"),
        "stop_loss": decision.get("stop_loss"),
        "take_profit_1": decision.get("take_profit_1"),
        "take_profit_2": decision.get("take_profit_2"),
        "risk_reward_ratio": decision.get("risk_reward_ratio"),
        "invalid_condition": decision.get("invalid_condition"),
        "chairman_summary": decision.get("chairman_summary"),
        "approved_time": decision.get("timestamp"),
        "main_reasons": decision.get("main_reasons"),
        "main_risks": decision.get("main_risks"),
        "member_votes": decision.get("member_votes"),
        "external_ai": decision.get("external_ai"),
        "external_ai_snapshot": decision.get("external_ai_snapshot") or decision.get("external_ai"),
        "committee_weights": decision.get("committee_weights"),
        "hard_veto_status": decision.get("hard_veto_status"),
        "soft_veto_status": decision.get("soft_veto_status"),
        "system_position_suggestion": decision.get("system_position_suggestion"),
        "risk_max_position": decision.get("risk_max_position"),
        "manual_override_allowed": decision.get("manual_override_allowed"),
    }


def build_sim_price_map(current_symbol: str, summary: dict[str, Any] | None = None) -> dict[str, float]:
    """Collect the current prices needed by simulated positions and orders."""
    symbols = {str(current_symbol or st.session_state.get("current_symbol", "BTCUSDT")).upper()}
    summary = summary or get_sim_account_summary()
    rows = list(summary.get("positions") or []) + list(summary.get("orders") or [])
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        if symbol:
            symbols.add(symbol)
    prices: dict[str, float] = {}
    for symbol in symbols:
        ticker = market_cache.get_ticker(symbol) or {}
        price = valid_price(ticker.get("last_price"))
        if price is None:
            old = next((row for row in rows if str(row.get("symbol") or "").upper() == symbol), {})
            price = valid_price(old.get("current_price"))
        if price is not None:
            prices[symbol] = price
    return prices


def render_trading(build_current_committee_decision: Callable[[str, dict[str, Any] | None], dict[str, Any]], page_titles: dict[str, tuple[str, str]], version: str) -> None:
    """交易页。"""
    render_page_head("trading", page_titles, version)
    symbol = st.session_state.get("current_symbol", "BTCUSDT")
    ticker = market_cache.get_ticker(symbol)
    price = valid_price((ticker or {}).get("last_price"))
    decision = build_current_committee_decision(symbol, ticker)
    signal = committee_decision_to_sim_signal(decision)
    price_map = build_sim_price_map(symbol)
    if price is not None:
        price_map[symbol] = price
    settings = load_settings()
    if settings.get("mode") == "auto":
        summary = update_simulation(price_map, [signal])
    else:
        summary = update_simulation(price_map, [])
    account = summary.get("account", {})
    positions = [p for p in summary.get("positions", []) if p.get("status") in {"open", "partially_closed"}]
    orders = [o for o in summary.get("orders", []) if o.get("status") == "pending"]
    history = summary.get("history", [])
    events = summary.get("events", [])
    stats = summary.get("stats", {})
    ok, reject_reasons = validate_signal_for_simulation(signal, {symbol: price or 0})

    money = money_text
    pct = pct_text

    def remaining_text(order: dict[str, Any]) -> str:
        remaining = int(order.get("expired_ts", 0) or 0) - int(time.time())
        if remaining <= 0:
            return "已到期"
        return seconds_text(remaining)

    def distance_to_entry(order: dict[str, Any], current_price: Any) -> str:
        price_value = valid_price(current_price)
        low = valid_price(order.get("entry_zone_low"))
        high = valid_price(order.get("entry_zone_high"))
        if price_value is None or low is None or high is None:
            return "等待价格刷新"
        if low <= price_value <= high:
            return "已进入入场区"
        target = low if price_value < low else high
        return f"{abs(price_value - target) / price_value * 100:.2f}%"

    st.markdown(
        '<div class="app-shell"><div class="module-card warning-box"><b>模拟交易安全提示</b><br>当前为模拟交易，不会使用真实资金，不会执行真实订单。所有订单、持仓和盈亏均为本地模拟数据。</div></div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    if c1.button("开启模拟交易", width="stretch"):
        settings["mode"] = "auto"
        save_settings(settings)
        set_sim_status("running")
        st.rerun()
    if c2.button("暂停模拟交易", width="stretch"):
        set_sim_status("paused")
        st.rerun()
    if c3.button("停止模拟交易", width="stretch"):
        set_sim_status("stopped")
        st.rerun()
    if c4.button("重置模拟账户", width="stretch"):
        reset_sim_account(float(settings.get("initial_balance", 1000)))
        st.rerun()

    status_color = "green" if account.get("status") == "running" else "yellow" if account.get("status") == "paused" else "red"
    render_metric_grid(
        [
            ("模拟交易状态", str(account.get("status", "stopped")), status_color),
            ("当前模式", "自动模拟" if settings.get("mode") == "auto" else "仅观察" if settings.get("mode") == "observe" else "手动确认", "blue"),
            ("当前权益", money(account.get("equity")), "green" if float(account.get("equity", 0) or 0) >= float(account.get("initial_balance", 0) or 0) else "red"),
            ("可用余额", money(account.get("available_balance")), ""),
            ("占用保证金", money(account.get("used_margin")), "yellow"),
            ("总名义仓位", money(account.get("total_exposure")), "blue"),
            ("浮动盈亏", money(account.get("unrealized_pnl")), "green" if float(account.get("unrealized_pnl", 0) or 0) >= 0 else "red"),
            ("累计收益率", pct(account.get("return_pct")), "green" if float(account.get("return_pct", 0) or 0) >= 0 else "red"),
            ("当前回撤", f"{float(account.get('current_drawdown', 0) or 0):.2f}%", "yellow"),
            ("最大回撤", f"{float(account.get('max_drawdown', 0) or 0):.2f}%", "yellow"),
            ("当前持仓", str(account.get("open_position_count", len(positions))), ""),
            ("待触发订单", str(account.get("pending_order_count", len(orders))), ""),
        ]
    )

    tabs = st.tabs(["账户总览", "当前持仓", "待触发订单", "交易历史", "统计分析", "参数设置", "事件日志", "诊断追踪", "评分反馈"])

    with tabs[0]:
        risk = summary.get("risk_summary") or {}
        st.markdown(
            f"""
            <div class="app-shell"><div class="module-card">
              <div class="module-title">模拟账户中心</div>
              <div class="module-desc">当前为模拟交易，不会使用真实资金，不会执行真实订单。</div>
              <div class="committee-grid">
                <div class="summary-card"><div class="summary-label">初始资金</div><div class="summary-value">{money(account.get("initial_balance"))}</div></div>
                <div class="summary-card"><div class="summary-label">当前权益</div><div class="summary-value {signal_color("支持交易" if float(account.get("equity", 0) or 0) >= float(account.get("initial_balance", 0) or 0) else "反对交易")}">{money(account.get("equity"))}</div></div>
                <div class="summary-card"><div class="summary-label">可用余额</div><div class="summary-value">{money(account.get("available_balance"))}</div></div>
                <div class="summary-card"><div class="summary-label">占用保证金</div><div class="summary-value yellow">{money(account.get("used_margin"))}</div></div>
                <div class="summary-card"><div class="summary-label">多头暴露</div><div class="summary-value green">{money(account.get("long_exposure"))}</div></div>
                <div class="summary-card"><div class="summary-label">空头暴露</div><div class="summary-value red">{money(account.get("short_exposure"))}</div></div>
                <div class="summary-card"><div class="summary-label">已实现盈亏</div><div class="summary-value {signal_color("支持交易" if float(account.get("realized_pnl", 0) or 0) >= 0 else "反对交易")}">{money(account.get("realized_pnl"))}</div></div>
                <div class="summary-card"><div class="summary-label">今日盈亏</div><div class="summary-value {signal_color("支持交易" if float(account.get("daily_pnl", 0) or 0) >= 0 else "反对交易")}">{money(account.get("daily_pnl"))}</div></div>
              </div>
              <div class="status-card" style="margin-top:8px;">
                账户风险状态：<b>{escape(str(risk.get("status", "正常")))}</b><br>
                当前持仓风险：{float(risk.get("total_risk_pct", 0) or 0):.2f}%｜预计最大止损亏损：{money(risk.get("total_risk_usdt"))}｜仍允许新开仓：{"是" if risk.get("allow_new_position") else "否"}<br>
                {escape(str(risk.get("explanation", "当前模拟账户风险等待计算。")))}
              </div>
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        curve_rows = summary.get("equity_curve") or get_sim_equity_curve(300)
        if curve_rows:
            chart_values = {
                "权益": [float(row.get("equity", 0) or 0) for row in curve_rows],
                "累计盈亏": [float(row.get("total_pnl", 0) or 0) for row in curve_rows],
                "回撤": [float(row.get("current_drawdown", 0) or 0) for row in curve_rows],
            }
            st.line_chart(chart_values)
        else:
            st.info("当前暂无权益曲线，模拟账户变化后会自动生成。")

        st.markdown('<div class="app-shell"><div class="module-card"><div class="module-title">委员会通过信号</div>', unsafe_allow_html=True)
        st.markdown(
            f"""
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">当前交易对象</div><div class="summary-value yellow">{escape(symbol)}</div></div>
              <div class="summary-card"><div class="summary-label">委员会动作</div><div class="summary-value {signal_color(str(signal.get("action", "")))}">{escape(str(signal.get("action", "继续观察")))}</div></div>
              <div class="summary-card"><div class="summary-label">方向</div><div class="summary-value">{escape(str(decision.get("final_direction_text", "中性")))}</div></div>
              <div class="summary-card"><div class="summary-label">置信度</div><div class="summary-value blue">{signal.get("committee_confidence", 0)} / 100</div></div>
              <div class="summary-card"><div class="summary-label">风险评分</div><div class="summary-value yellow">{signal.get("risk_score", 0)} / 100</div></div>
              <div class="summary-card"><div class="summary-label">风险收益比</div><div class="summary-value">{signal.get("risk_reward_ratio") or "待确认"}</div></div>
              <div class="summary-card"><div class="summary-label">模拟订单状态</div><div class="summary-value">{escape("已有待触发订单" if any(o.get("symbol") == symbol for o in orders) else "未创建订单")}</div></div>
              <div class="summary-card"><div class="summary-label">模拟持仓状态</div><div class="summary-value">{escape("已有同币种持仓" if any(p.get("symbol") == symbol for p in positions) else "无同币种持仓")}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">AI主席总结：{escape(safe_committee_text(signal.get("chairman_summary", "等待委员会总结。"), 420))}</div>
            """,
            unsafe_allow_html=True,
        )
        if ok:
            st.success("该委员会信号当前满足模拟交易风控条件。")
            if price is None:
                st.warning("等待价格刷新，暂不创建模拟订单。")
            elif st.button("加入模拟候选 / 创建模拟订单", width="stretch"):
                order = create_pending_sim_order(signal, price)
                if order:
                    st.success("已创建本地模拟订单。")
                else:
                    st.warning("未创建模拟订单，请查看模拟事件日志。")
                st.rerun()
        else:
            st.warning("当前未创建模拟订单：" + "；".join(reject_reasons))
        st.markdown("</div></div>", unsafe_allow_html=True)

    with tabs[1]:
        st.markdown('<div class="app-shell"><div class="module-card"><div class="module-title">当前模拟持仓中心</div>', unsafe_allow_html=True)
        if not positions:
            st.markdown('<div class="status-card">当前暂无模拟持仓。</div>', unsafe_allow_html=True)
        for pos in positions:
            pos_price = valid_price(price_map.get(str(pos.get("symbol", "")))) or valid_price(pos.get("current_price"))
            pnl = float(pos.get("unrealized_pnl", 0) or 0)
            pnl_class = "green" if pnl >= 0 else "red"
            r_value = calculate_position_r_multiple(pos, pos_price)
            holding = calculate_position_holding_time(pos)
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{kline_symbol_link(pos.get("symbol"), str(pos.get("symbol")))}</b>｜{direction_text(pos.get("direction"))}｜模拟持仓｜{pos.get("status")}<br>
                  开仓：{format_waiting_price(pos.get("entry_price"))}　当前：{format_waiting_price(pos_price)}　数量：{float(pos.get("quantity", 0) or 0):.6f}<br>
                  模拟保证金：{money(pos.get("margin_usdt"))}　名义仓位：{money(pos.get("notional_usdt"))}　模拟杠杆：{pos.get("leverage", 1)}x<br>
                  浮动盈亏：<span class="{pnl_class}">{pnl:+.2f} USDT / {float(pos.get("unrealized_pnl_pct", 0) or 0):+.2f}%</span>　已实现：{money(pos.get("realized_pnl"))}<br>
                  止损：{format_waiting_price(pos.get("stop_loss"))}　止盈1：{format_waiting_price(pos.get("take_profit_1"))}　止盈2：{format_waiting_price(pos.get("take_profit_2"))}<br>
                  R倍数：{f"{r_value:+.2f}R" if r_value is not None else "暂无"}　持仓：{escape(str(holding.get("text", "0分钟")))}　止盈1：{"已触发" if pos.get("tp1_hit") else "未触发"}　保本止损：{"已移动" if pos.get("moved_stop_to_breakeven") else "未移动"}<br>
                  委员会：{escape(str(pos.get("committee_action", "等待")))} / 置信度{pos.get("committee_confidence", 0)} / 风险{pos.get("committee_risk_score", 0)}<br>
                  AI主席摘要：{escape(safe_committee_text((pos.get("committee_snapshot") or {}).get("chairman_summary", pos.get("open_reason", "暂无摘要")), 420))}
                </div>
                """,
                unsafe_allow_html=True,
            )
            pc1, pc2, pc3, pc4 = st.columns(4)
            if pc1.button("全部平仓", key=f"close_all_{pos.get('position_id')}", width="stretch", disabled=pos_price is None):
                close_sim_position(str(pos.get("position_id")), "用户手动平仓", pos_price)
                st.rerun()
            if pc2.button("平仓50%", key=f"close_half_{pos.get('position_id')}", width="stretch", disabled=pos_price is None):
                close_sim_position(str(pos.get("position_id")), "用户手动平仓50%", pos_price, ratio=0.5)
                st.rerun()
            if pc3.button("止损到保本", key=f"breakeven_{pos.get('position_id')}", width="stretch"):
                move_stop_to_breakeven(str(pos.get("position_id")))
                st.rerun()
            with pc4:
                st.write("详情")
            with st.expander(f"{pos.get('symbol')} 持仓详情与事件时间线", expanded=False):
                st.markdown(f"开仓原因：{pos.get('open_reason') or '暂无'}")
                st.markdown(f"信号失效条件：{pos.get('invalid_condition') or '暂无'}")
                st.markdown(f"本地策略动作：{pos.get('local_strategy_action') or '暂无'}")
                related_events = [e for e in events if e.get("symbol") == pos.get("symbol")]
                if not related_events:
                    st.info("当前暂无该持仓相关事件。")
                for event in related_events[:10]:
                    st.caption(f"{event.get('time')}｜{event.get('event_type')}｜{event.get('content') or event.get('reason')}")
        st.markdown("</div></div>", unsafe_allow_html=True)

    with tabs[2]:
        st.markdown('<div class="app-shell"><div class="module-card"><div class="module-title">待触发模拟订单中心</div>', unsafe_allow_html=True)
        if not orders:
            st.markdown('<div class="status-card">当前暂无待触发模拟订单。</div>', unsafe_allow_html=True)
        for order in orders[:50]:
            order_price = valid_price(price_map.get(str(order.get("symbol", ""))))
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{kline_symbol_link(order.get("symbol"), str(order.get("symbol")))}</b>｜{direction_text(order.get("direction"))}｜{escape(str(order.get("action", "")))}｜待触发模拟订单<br>
                  当前价格：{format_waiting_price(order_price)}　入场区间：{format_waiting_price(order.get("entry_zone_low"))} - {format_waiting_price(order.get("entry_zone_high"))}　距离入场区：{escape(distance_to_entry(order, order_price))}<br>
                  止损：{format_waiting_price(order.get("stop_loss"))}　止盈1：{format_waiting_price(order.get("take_profit_1"))}　止盈2：{format_waiting_price(order.get("take_profit_2"))}<br>
                  建议仓位：{escape(str(order.get("position_pct", "0%")))}　预计保证金：{money(order.get("margin_usdt"))}　名义仓位：{money(order.get("notional_usdt"))}<br>
                  创建：{escape(str(order.get("created_time", "")))}　剩余有效期：{escape(remaining_text(order))}　来源：{escape(str(order.get("source", "AI交易委员会")))}
                </div>
                """,
                unsafe_allow_html=True,
            )
            oc1, oc2 = st.columns([1, 3])
            if oc1.button("取消订单", key=f"cancel_order_{order.get('order_id')}", width="stretch"):
                cancel_order(str(order.get("order_id")))
                st.rerun()
            with oc2.expander("查看委员会信号摘要", expanded=False):
                snapshot = order.get("committee_snapshot") or {}
                st.markdown(f"主席总结：{escape(safe_committee_text(snapshot.get('chairman_summary') or order.get('reason') or '暂无', 420))}", unsafe_allow_html=True)
                st.markdown(f"置信度：{snapshot.get('committee_confidence', 0)}｜风险：{snapshot.get('risk_score', 0)}｜风险收益比：{snapshot.get('risk_reward_ratio')}")
        st.markdown("</div></div>", unsafe_allow_html=True)

    with tabs[3]:
        if not history:
            st.info("当前暂无模拟交易历史。")
        else:
            filter_col1, filter_col2, filter_col3 = st.columns(3)
            result_filter = filter_col1.selectbox("结果筛选", ["全部", "盈利", "亏损"], key="sim_history_result_filter")
            dir_filter = filter_col2.selectbox("方向筛选", ["全部", "多单", "空单"], key="sim_history_dir_filter")
            reason_options = ["全部"] + sorted({str(row.get("close_reason") or "未知") for row in history})
            reason_filter = filter_col3.selectbox("原因筛选", reason_options, key="sim_history_reason_filter")
            rows = history
            if result_filter == "盈利":
                rows = [row for row in rows if row.get("is_win")]
            elif result_filter == "亏损":
                rows = [row for row in rows if not row.get("is_win")]
            if dir_filter == "多单":
                rows = [row for row in rows if row.get("direction") == "long"]
            elif dir_filter == "空单":
                rows = [row for row in rows if row.get("direction") == "short"]
            if reason_filter != "全部":
                rows = [row for row in rows if str(row.get("close_reason") or "未知") == reason_filter]
            for row in rows[:50]:
                pnl = float(row.get("pnl", 0) or 0)
                klass = "green" if pnl >= 0 else "red"
                st.markdown(
                    f"""
                    <div class="status-card">
                      <b>{kline_symbol_link(row.get("symbol"), str(row.get("symbol")))}</b>｜{direction_text(row.get("direction"))}｜{escape(str(row.get("close_reason", "")))}｜<span class="{klass}">{pnl:+.2f} USDT / {float(row.get("pnl_pct", 0) or 0):+.2f}%</span><br>
                      开仓：{format_price(row.get("entry_price"))}　平仓：{format_price(row.get("exit_price"))}　数量：{float(row.get("quantity", 0) or 0):.6f}　仓位：{money(row.get("notional_usdt"))}<br>
                      R倍数：{row.get("r_multiple") if row.get("r_multiple") not in {"", None} else "暂无"}　持仓：{seconds_text(row.get("holding_seconds"))}　委员会：{escape(str(row.get("committee_action", "")))} / 置信度{row.get("committee_confidence", 0)} / 风险{row.get("committee_risk_score", 0)}<br>
                      开仓时间：{escape(str(row.get("open_time", "")))}　平仓时间：{escape(str(row.get("close_time", "")))}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        if st.button("清空模拟历史", width="stretch"):
            clear_sim_history()
            st.rerun()

    with tabs[4]:
        render_metric_grid(
            [
                ("总交易次数", str(stats.get("total_trades", 0)), ""),
                ("当前持仓", str(len(positions)), ""),
                ("待触发订单", str(len(orders)), ""),
                ("盈利 / 亏损", f"{stats.get('wins', 0)} / {stats.get('losses', 0)}", ""),
                ("胜率", f"{float(stats.get('win_rate', 0)):.2f}%", "green" if float(stats.get("win_rate", 0)) >= 50 else "yellow"),
                ("累计盈亏", money(stats.get("total_pnl")), "green" if float(stats.get("total_pnl", 0) or 0) >= 0 else "red"),
                ("总收益率", pct(stats.get("return_pct", account.get("return_pct", 0))), "green" if float(stats.get("return_pct", account.get("return_pct", 0)) or 0) >= 0 else "red"),
                ("今日盈亏", money(stats.get("daily_pnl", account.get("daily_pnl", 0))), "green" if float(stats.get("daily_pnl", account.get("daily_pnl", 0)) or 0) >= 0 else "red"),
                ("当前浮盈", money(stats.get("current_unrealized_pnl", account.get("unrealized_pnl", 0))), "green" if float(stats.get("current_unrealized_pnl", 0) or 0) >= 0 else "red"),
                ("最大单笔盈利", money(stats.get("max_win")), "green"),
                ("最大单笔亏损", money(stats.get("max_loss")), "red"),
                ("Profit Factor", f"{float(stats.get('profit_factor', 0) or 0):.2f}", "blue"),
                ("平均R倍数", f"{float(stats.get('avg_r_multiple', 0) or 0):+.2f}R", ""),
                ("多单胜率", f"{float(stats.get('long_win_rate', 0) or 0):.2f}%", ""),
                ("空单胜率", f"{float(stats.get('short_win_rate', 0) or 0):.2f}%", ""),
                ("常见平仓原因", str(stats.get("common_close_reason", "暂无")), "yellow"),
                ("最佳交易对象", str(stats.get("best_symbol", "暂无")), "green"),
                ("最差交易对象", str(stats.get("worst_symbol", "暂无")), "red"),
                ("连续盈利", str(stats.get("consecutive_wins", 0)), "green"),
                ("连续亏损", str(stats.get("consecutive_losses", 0)), "red"),
            ]
        )
        if not history:
            st.info("暂无模拟交易历史，统计数据将在交易完成后生成。")

    with tabs[5]:
        with st.form("sim_settings_form"):
            new_settings = dict(settings)
            new_settings["initial_balance"] = st.number_input("初始模拟资金 USDT", min_value=100.0, max_value=1000000.0, value=float(settings.get("initial_balance", 1000)), step=100.0)
            new_settings["max_position_pct"] = st.slider("单笔最大仓位比例", 1, 50, int(settings.get("max_position_pct", 10)))
            new_settings["max_risk_pct"] = st.slider("单笔最大风险比例", 1, 10, int(settings.get("max_risk_pct", 1)))
            st.info("10.0 本地策略版已关闭模拟持仓数量上限：只要账户有可用模拟余额，后台会持续按候选信号开仓。")
            new_settings["max_positions"] = 0
            new_settings["max_same_symbol_positions"] = 0
            new_settings["allow_long"] = st.checkbox("允许模拟做多", value=bool(settings.get("allow_long", True)))
            new_settings["allow_short"] = st.checkbox("允许模拟做空", value=bool(settings.get("allow_short", True)))
            st.info("模拟交易已锁定为 U本位永续合约模拟，杠杆固定 5x，并保持自动持续运行。")
            new_settings["market_type"] = "futures"
            new_settings["futures_leverage_locked"] = True
            new_settings["continuous_run"] = True
            new_settings["ignore_loss_limits"] = True
            new_settings["leverage"] = 5
            new_settings["entry_mode"] = "立即按当前价模拟开仓"
            new_settings["mode"] = "auto"
            new_settings["tp1_close_pct"] = st.slider("止盈1平仓比例", 10, 90, int(settings.get("tp1_close_pct", 50)))
            new_settings["move_sl_to_breakeven"] = st.checkbox("止盈1后移动止损到保本", value=bool(settings.get("move_sl_to_breakeven", True)))
            new_settings["dynamic_stop_loss_base_pct"] = st.number_input("动态止损基准 %", min_value=0.2, max_value=5.0, value=float(settings.get("dynamic_stop_loss_base_pct", 1.25)), step=0.05)
            new_settings["dynamic_stop_loss_high_risk_pct"] = st.number_input("高风险动态止损 %", min_value=0.2, max_value=5.0, value=float(settings.get("dynamic_stop_loss_high_risk_pct", 0.85)), step=0.05)
            new_settings["dynamic_stop_loss_low_risk_pct"] = st.number_input("低风险动态止损 %", min_value=0.2, max_value=5.0, value=float(settings.get("dynamic_stop_loss_low_risk_pct", 1.55)), step=0.05)
            new_settings["dynamic_take_profit_1_r"] = st.number_input("止盈1 R倍数", min_value=0.5, max_value=5.0, value=float(settings.get("dynamic_take_profit_1_r", 1.4)), step=0.1)
            new_settings["dynamic_take_profit_2_r"] = st.number_input("止盈2 R倍数", min_value=1.0, max_value=8.0, value=float(settings.get("dynamic_take_profit_2_r", 2.8)), step=0.1)
            new_settings["daily_loss_limit_pct"] = st.slider("每日最大亏损限制", 1, 20, int(settings.get("daily_loss_limit_pct", 3)))
            new_settings["max_drawdown_limit_pct"] = st.slider("最大回撤限制", 1, 30, int(settings.get("max_drawdown_limit_pct", 8)))
            new_settings["consecutive_loss_pause"] = st.slider("连续亏损暂停次数", 1, 10, int(settings.get("consecutive_loss_pause", 3)))
            new_settings["signal_ttl_minutes"] = st.slider("信号有效期分钟", 5, 240, int(settings.get("signal_ttl_minutes", 60)))
            new_settings["cooldown_minutes"] = st.slider("新开仓冷却时间分钟", 1, 120, int(settings.get("cooldown_minutes", 15)))
            if st.form_submit_button("保存模拟交易参数", width="stretch"):
                save_settings(new_settings)
                st.success("模拟交易参数已保存。")
                st.rerun()

    with tabs[6]:
        if not events:
            st.info("当前暂无模拟事件。")
        for event in events[:80]:
            st.caption(f"{event.get('time')}｜{event.get('event_type')}｜{event.get('symbol')}｜{event.get('content') or event.get('reason')}")

    with tabs[7]:
        diagnostics = summary.get("diagnostics") or load_sim_diagnostics(80)
        process_results = summary.get("process_results") or []
        if process_results:
            st.markdown("**本轮模拟处理结果**")
            st.dataframe(process_results, width="stretch", hide_index=True)
        if not diagnostics:
            st.info("当前暂无模拟诊断记录。后台扫描、候选拒绝和模拟风控拒绝会写入这里。")
        else:
            st.dataframe(build_sim_diagnostic_rows(diagnostics), width="stretch", hide_index=True)

    with tabs[8]:
        feedback = summary.get("score_feedback") or calculate_sim_score_feedback(history)
        sample_warning = str(feedback.get("sample_warning") or "")
        if sample_warning:
            st.info(sample_warning)
        st.markdown("**基础评分与模拟结果反馈**")
        feedback_rows = build_sim_score_feedback_rows(feedback)
        if feedback_rows:
            st.dataframe(feedback_rows, width="stretch", hide_index=True)
        else:
            st.info("暂无可用于评分反馈的平仓样本。")
        for suggestion in feedback.get("suggestions", []) or []:
            st.caption(str(suggestion))


"""Auto trading page rendering and legacy approval card helpers."""

from __future__ import annotations

from html import escape
from typing import Any, Callable

import streamlit as st

from app_pages.simulation_page import committee_decision_to_sim_signal
from components.ui import kline_symbol_link, render_metric_grid, render_page_head
from services import market_cache
from services.approval_center import (
    approve_approval,
    execute_approved_approval,
    modify_approval,
    reject_approval,
    run_approval_preflight,
)
from services.live_auto_pilot import (
    create_live_auto_order_plan,
    disable_live_auto_pilot,
    enable_live_auto_pilot,
    execute_live_auto_spot_order,
    filter_live_auto_signal,
    get_live_auto_status,
    load_live_auto_audit_log,
    load_live_auto_config,
    pause_live_auto_pilot,
    release_live_auto_circuit_breaker,
    resume_live_auto_pilot,
    run_live_auto_admission_check,
    run_live_auto_exit_check,
    save_live_auto_config,
    trigger_live_auto_circuit_breaker,
)
from services.live_trading_center import (
    create_live_order_plan,
    get_live_account_snapshot,
    load_live_order_records,
    run_exit_spot_test_order,
    run_spot_test_order,
)
from services.secure_api_vault import get_secure_api_status, write_secure_api_values
from utils.formatters import format_price, format_score, pct_text, safe_number


def safe_compare_lt(value: Any, threshold: float) -> bool:
    number = safe_number(value, None)
    return False if number is None else number < threshold


def _fold_panel(key: str, title: str, summary: str = "", *, expanded: bool = False) -> bool:
    state_key = f"fold_{key}_expanded"
    if state_key not in st.session_state:
        st.session_state[state_key] = expanded
    label = f"{'▾' if st.session_state[state_key] else '▸'} {title}"
    if summary:
        label = f"{label}｜{summary}"
    if st.button(label, key=f"fold_btn_{key}", width="stretch"):
        st.session_state[state_key] = not st.session_state[state_key]
        st.rerun()
    return bool(st.session_state[state_key])


def _close_fold(key: str) -> None:
    st.session_state[f"fold_{key}_expanded"] = False


def _money(value: Any) -> str:
    return f"{float(value or 0):.2f} USDT"


@st.cache_data(ttl=20, show_spinner=False)
def _cached_live_account_snapshot(market_type: str) -> dict[str, Any]:
    return get_live_account_snapshot(False, market_type)


@st.cache_data(ttl=10, show_spinner=False)
def _cached_live_auto_status(price_items: tuple[tuple[str, float], ...] = ()) -> dict[str, Any]:
    return get_live_auto_status(dict(price_items))


def _live_balance_value(row: dict[str, Any]) -> float:
    free = safe_number(row.get("free"), 0) or 0
    locked = safe_number(row.get("locked"), 0) or 0
    wallet = safe_number(row.get("wallet"), None)
    return float(wallet if wallet is not None else free + locked)


def _live_auto_order_history(limit: int = 500) -> list[dict[str, Any]]:
    rows = load_live_order_records(limit)
    auto_rows = [row for row in rows if str(row.get("source") or "") == "LIVE_AUTO_PILOT"]
    return auto_rows or rows[:limit]


def _live_auto_stats(orders: list[dict[str, Any]], positions: list[dict[str, Any]], config: dict[str, Any], balances: list[dict[str, Any]]) -> dict[str, Any]:
    pnl_values = [safe_number(pos.get("unrealized_pnl"), 0) or 0 for pos in positions]
    total_unrealized = sum(float(v) for v in pnl_values)
    total_notional = sum(float(safe_number(pos.get("notional"), safe_number(pos.get("quote_amount"), 0)) or 0) for pos in positions)
    total_margin = sum(float(safe_number(pos.get("quote_amount"), 0) or 0) for pos in positions)
    submitted = [row for row in orders if str(row.get("order_status") or row.get("raw_status_summary") or "").upper() not in {"REJECTED", "FAILED"}]
    failed = [row for row in orders if str(row.get("order_status") or row.get("raw_status_summary") or "").upper() in {"REJECTED", "FAILED"}]
    wallet_total = sum(_live_balance_value(row) for row in balances)
    futures_usdt = sum(_live_balance_value(row) for row in balances if str(row.get("asset") or "").upper() == "USDT")
    principal = float(safe_number(config.get("principal_usdt"), 0) or 0)
    return {
        "wallet_total": wallet_total,
        "usdt_balance": futures_usdt,
        "principal": principal,
        "return_pct": (total_unrealized / principal * 100) if principal else 0,
        "open_position_count": len(positions),
        "total_unrealized": total_unrealized,
        "total_notional": total_notional,
        "total_margin": total_margin,
        "order_count": len(orders),
        "submitted_count": len(submitted),
        "failed_count": len(failed),
        "avg_order_notional": sum(float(safe_number(row.get("notional"), 0) or 0) for row in orders) / len(orders) if orders else 0,
        "max_unrealized_win": max(pnl_values) if pnl_values else 0,
        "max_unrealized_loss": min(pnl_values) if pnl_values else 0,
    }


def _render_live_trade_ledger(
    *,
    account_snapshot: dict[str, Any],
    auto_status: dict[str, Any],
    auto_config: dict[str, Any],
    auto_review: dict[str, Any],
    positions: list[dict[str, Any]],
    market_text: str,
    status_text: str,
    build_committee_decision: Callable[[str, dict[str, Any] | None], dict[str, Any]],
) -> None:
    balances = account_snapshot.get("balances") or []
    orders = _live_auto_order_history(500)
    audit = load_live_auto_audit_log(200)
    live_stats = _live_auto_stats(orders, positions, auto_config, balances)
    current_symbol = st.session_state.get("current_symbol", "BTCUSDT")
    current_ticker = market_cache.get_ticker(current_symbol) or {}
    decision = build_committee_decision(current_symbol, current_ticker)
    signal = committee_decision_to_sim_signal(decision)
    signal["symbol"] = current_symbol
    signal["current_price"] = float(current_ticker.get("last_price") or 0)
    signal["data_quality"] = "good" if current_ticker else "poor"
    ok, reasons = filter_live_auto_signal(signal)
    plan = st.session_state.get("live_auto_order_plan")

    tab_options = ["账户总览", "当前持仓", "真实订单计划", "交易历史", "统计分析", "参数设置", "事件日志"]
    active_tab = st.radio("自动交易页面", tab_options, horizontal=True, label_visibility="collapsed", key="live_auto_ledger_active_tab")

    if active_tab == "账户总览":
        if account_snapshot.get("ok"):
            st.success("Binance API 已接入，账户只读检查通过。")
        else:
            st.warning(account_snapshot.get("message", "Binance 账户状态暂不可用。"))
        render_metric_grid(
            [
                ("本金设置", _money(live_stats.get("principal")), "blue"),
                ("账户USDT", _money(live_stats.get("usdt_balance")), "green" if live_stats.get("usdt_balance", 0) else "yellow"),
                ("钱包资产合计", _money(live_stats.get("wallet_total")), ""),
                ("当前持仓", str(live_stats.get("open_position_count", 0)), ""),
                ("占用保证金", _money(live_stats.get("total_margin")), "yellow"),
                ("总名义仓位", _money(live_stats.get("total_notional")), "blue"),
                ("浮动盈亏", _money(live_stats.get("total_unrealized")), "green" if float(live_stats.get("total_unrealized", 0) or 0) >= 0 else "red"),
                ("试运行收益率", pct_text(live_stats.get("return_pct")), "green" if float(live_stats.get("return_pct", 0) or 0) >= 0 else "red"),
            ]
        )
        st.markdown(
            f"""
            <div class="app-shell"><div class="module-card">
              <div class="module-title">实盘账户中心</div>
              <div class="module-desc">这里记录自动实盘的账户快照、持仓、真实订单和事件日志，后续统计分析都以这些记录为准。</div>
              <div class="status-card">
                自动交易状态：<b>{escape(status_text)}</b>｜自动下单：{"开启" if auto_status.get("order_enabled") else "关闭"}｜自动止盈止损：{"开启" if auto_status.get("exit_enabled") else "关闭"}<br>
                当前市场：{escape(market_text)}｜默认杠杆：{int(auto_config.get("default_leverage", 1) or 1)}x｜单笔保证金：{_money(auto_config.get("max_order_usdt"))}<br>
                今日额度：{_money(auto_status.get("daily_used_usdt"))} / {_money(auto_config.get("daily_limit_usdt"))}｜熔断：{"已触发" if auto_status.get("circuit_breaker_enabled") else "未触发"}｜原因：{escape(str(auto_status.get("circuit_breaker_reason") or "无"))}
              </div>
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        if balances:
            with st.expander(f"真实账户余额摘要｜{len(balances[:30])} 条资产", expanded=False):
                st.dataframe(balances[:30], width="stretch", hide_index=True)
        else:
            st.info("当前没有读取到账户余额明细。")

        st.markdown("**自动交易控制**")
        confirm_phrase = st.text_input("开启/恢复确认短句", value="", type="password", placeholder="我确认开启小资金自动实盘试运行")
        c1, c2, c3, c4 = st.columns(4)
        if c1.button("开启自动交易", width="stretch"):
            result = enable_live_auto_pilot(confirm_phrase)
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()
        if c2.button("暂停自动交易", width="stretch"):
            pause_live_auto_pilot("用户在自动交易页暂停。")
            st.rerun()
        if c3.button("恢复自动交易", width="stretch"):
            result = resume_live_auto_pilot(confirm_phrase)
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()
        if c4.button("停止自动交易", width="stretch"):
            disable_live_auto_pilot("用户在自动交易页停止。")
            st.rerun()
        b1, b2 = st.columns(2)
        breaker_reason = b1.text_input("熔断原因", value="用户手动触发自动交易熔断。")
        if b1.button("触发熔断", width="stretch"):
            trigger_live_auto_circuit_breaker(breaker_reason)
            st.rerun()
        breaker_phrase = b2.text_input("解除熔断确认短句", value="", type="password", placeholder="我确认解除自动实盘熔断")
        if b2.button("解除熔断", width="stretch"):
            result = release_live_auto_circuit_breaker(breaker_phrase)
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()

        with st.expander("自动交易准入检查", expanded=False):
            admission = run_live_auto_admission_check(user_confirmed=True)
            for item in admission.get("checks", []):
                (st.success if item.get("ok") else st.error)(f"{item.get('name')}：{item.get('message')}")

    elif active_tab == "当前持仓":
        st.markdown('<div class="app-shell"><div class="module-card"><div class="module-title">当前实盘自动持仓中心</div>', unsafe_allow_html=True)
        if not positions:
            st.markdown('<div class="status-card">当前暂无自动实盘持仓。</div>', unsafe_allow_html=True)
        for pos in positions:
            pnl = float(pos.get("unrealized_pnl", 0) or 0)
            pnl_class = "green" if pnl >= 0 else "red"
            exit_check = run_live_auto_exit_check(pos)
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{kline_symbol_link(pos.get("symbol"), str(pos.get("symbol")))}</b>｜{escape(str(pos.get("market_type", "spot")))}｜{escape(str(pos.get("status", "")))}｜真实自动持仓<br>
                  开仓：{format_price(pos.get("entry_price"))}　当前：{format_price(pos.get("current_price"))}　数量：{float(pos.get("quantity", 0) or 0):.8f}<br>
                  保证金：{_money(pos.get("quote_amount"))}　名义仓位：{_money(pos.get("notional", pos.get("quote_amount")))}　杠杆：{int(pos.get("leverage", 1) or 1)}x<br>
                  浮动盈亏：<span class="{pnl_class}">{pnl:+.4f} USDT / {float(pos.get("unrealized_pnl_pct", 0) or 0):+.2f}%</span><br>
                  止盈：{float((pos.get("exit_rule") or {}).get("take_profit_pct", auto_config.get("take_profit_pct", 0)) or 0):.2f}%　止损：{float((pos.get("exit_rule") or {}).get("stop_loss_pct", auto_config.get("stop_loss_pct", 0)) or 0):.2f}%　退出检查：{escape(str(exit_check.get("action", "-")))}<br>
                  开仓时间：{escape(str(pos.get("entry_time", "")))}　最近复核：{escape(str(pos.get("last_review_time", "")))}　持仓ID：{escape(str(pos.get("auto_position_id", "")))}
                </div>
                """,
                unsafe_allow_html=True,
            )
            with st.expander(f"{pos.get('symbol')} 持仓详情与事件时间线", expanded=False):
                related_events = [e for e in audit if e.get("symbol") == pos.get("symbol")]
                if not related_events:
                    st.info("当前暂无该持仓相关事件。")
                for event in related_events[:20]:
                    st.caption(f"{event.get('time')}｜{event.get('event')}｜{event.get('result')}｜{event.get('reason')}")
        st.markdown("</div></div>", unsafe_allow_html=True)

    elif active_tab == "真实订单计划":
        st.markdown("**当前自动信号**")
        render_metric_grid(
            [
                ("交易对象", current_symbol, "yellow"),
                ("当前价格", format_price(signal.get("current_price")), ""),
                ("委员会动作", str(signal.get("action", "-")), ""),
                ("方向", str(signal.get("direction", "-")), ""),
                ("置信度", str(signal.get("committee_confidence", 0)), "green" if float(signal.get("committee_confidence") or 0) >= 70 else "yellow"),
                ("风险评分", format_score(signal.get("risk_score")), "green" if safe_compare_lt(signal.get("risk_score"), 55.01) else "red"),
                ("信号过滤", "通过" if ok else "拒绝", "green" if ok else "red"),
                ("交易对象限制", "已放开", "green"),
            ]
        )
        for reason in reasons:
            st.warning(reason)
        if st.button("按当前信号生成真实自动订单计划", disabled=not ok, width="stretch"):
            st.session_state["live_auto_order_plan"] = create_live_auto_order_plan(signal)
            st.rerun()
        st.markdown("**真实订单计划**")
        if not plan:
            st.info("当前暂无真实自动订单计划。通过当前信号生成后，可在这里执行真实下单。")
        else:
            render_metric_grid(
                [
                    ("计划ID", str(plan.get("auto_plan_id", "-")), ""),
                    ("交易对象", str(plan.get("symbol", "-")), "yellow"),
                    ("市场", "U本位永续" if plan.get("market_type") == "futures" else "现货", "blue"),
                    ("方向", str(plan.get("side", "-")), "green" if plan.get("side") == "BUY" else "red"),
                    ("杠杆", f"{int(plan.get('leverage', 1) or 1)}x", "yellow"),
                    ("保证金", _money(plan.get("quote_amount")), "yellow"),
                    ("名义金额", _money(plan.get("notional")), "blue"),
                    ("数量", f"{float(plan.get('quantity', 0) or 0):.8f}", ""),
                ]
            )
            if st.button("执行真实自动订单", width="stretch"):
                st.session_state["live_auto_execute_result"] = execute_live_auto_spot_order(plan)
                st.rerun()
            result = st.session_state.get("live_auto_execute_result") or {}
            if result:
                (st.success if result.get("ok") else st.error)(result.get("message"))
                preflight = result.get("preflight") or {}
                for item in preflight.get("checks", []):
                    st.caption(f"{'通过' if item.get('ok') else '失败'}｜{item.get('name')}｜{item.get('message')}")

    elif active_tab == "交易历史":
        if not orders:
            st.info("当前暂无真实自动交易历史。真实下单提交后会记录在这里。")
        else:
            f1, f2, f3 = st.columns(3)
            market_filter = f1.selectbox("市场筛选", ["全部"] + sorted({str(row.get("market_type") or "未知") for row in orders}), key="live_auto_history_market")
            side_filter = f2.selectbox("方向筛选", ["全部"] + sorted({str(row.get("side") or "未知") for row in orders}), key="live_auto_history_side")
            status_filter = f3.selectbox("状态筛选", ["全部"] + sorted({str(row.get("order_status") or row.get("raw_status_summary") or "未知") for row in orders}), key="live_auto_history_status")
            rows = orders
            if market_filter != "全部":
                rows = [row for row in rows if str(row.get("market_type") or "未知") == market_filter]
            if side_filter != "全部":
                rows = [row for row in rows if str(row.get("side") or "未知") == side_filter]
            if status_filter != "全部":
                rows = [row for row in rows if str(row.get("order_status") or row.get("raw_status_summary") or "未知") == status_filter]
            for row in rows[:80]:
                side = str(row.get("side") or "")
                klass = "green" if side.upper() == "BUY" else "red"
                st.markdown(
                    f"""
                    <div class="status-card">
                      <b>{kline_symbol_link(row.get("symbol"), str(row.get("symbol")))}</b>｜{escape(str(row.get("market_type", "")))}｜<span class="{klass}">{escape(side)}</span>｜{escape(str(row.get("order_status") or row.get("raw_status_summary") or ""))}<br>
                      价格：{format_price(row.get("price") or row.get("avg_price"))}　数量：{float(row.get("quantity", 0) or 0):.8f}　名义金额：{_money(row.get("notional"))}　保证金：{_money(row.get("margin_usdt", row.get("notional")))}<br>
                      杠杆：{row.get("leverage", "-")}　订单类型：{escape(str(row.get("order_type", "")))}　订单ID：{escape(str(row.get("order_id", "")))}<br>
                      委员会：{escape(str(row.get("committee_action", "")))}　风险：{escape(str(row.get("risk_score", "")))}　时间：{escape(str(row.get("time", "")))}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    elif active_tab == "统计分析":
        render_metric_grid(
            [
                ("真实订单数", str(live_stats.get("order_count", 0)), ""),
                ("已提交订单", str(live_stats.get("submitted_count", 0)), "green"),
                ("失败订单", str(live_stats.get("failed_count", 0)), "red"),
                ("当前持仓", str(live_stats.get("open_position_count", 0)), ""),
                ("占用保证金", _money(live_stats.get("total_margin")), "yellow"),
                ("名义仓位", _money(live_stats.get("total_notional")), "blue"),
                ("当前浮盈", _money(live_stats.get("total_unrealized")), "green" if float(live_stats.get("total_unrealized", 0) or 0) >= 0 else "red"),
                ("试运行收益率", pct_text(live_stats.get("return_pct")), "green" if float(live_stats.get("return_pct", 0) or 0) >= 0 else "red"),
                ("平均订单额", _money(live_stats.get("avg_order_notional")), ""),
                ("最大浮盈", _money(live_stats.get("max_unrealized_win")), "green"),
                ("最大浮亏", _money(live_stats.get("max_unrealized_loss")), "red"),
                ("事件日志", str(len(audit)), ""),
            ]
        )
        if not orders:
            st.info("暂无真实订单样本，胜率、EV、Profit Factor 等统计需要平仓样本后再计算。")
        else:
            st.dataframe(orders[:100], width="stretch", hide_index=True)

    elif active_tab == "参数设置":
        param_summary = f"{market_text}｜{float(auto_config.get('position_pct', 5) or 5):.2f}%｜{int(auto_config.get('default_leverage', 5) or 5)}x｜{_money(auto_config.get('max_order_usdt'))}"
        if _fold_panel("live_params", "真实订单参数", param_summary, expanded=False):
            with st.form("auto_trade_settings_form"):
                new_config = dict(auto_config)
                new_config["principal_usdt"] = st.number_input("自动交易本金 USDT", min_value=1.0, max_value=10_000_000.0, value=float(auto_config.get("principal_usdt", 100) or 100), step=10.0)
                new_config["position_pct"] = st.slider("开仓比例（占本金）", min_value=0.1, max_value=40.0, value=float(auto_config.get("position_pct", 5) or 5), step=0.1)
                st.info(f"按当前设置，单笔保证金约为 {float(new_config['principal_usdt']) * float(new_config['position_pct']) / 100:.2f} USDT。")
                new_config["daily_limit_usdt"] = st.number_input("单日自动交易额度 USDT", min_value=1.0, max_value=float(new_config["principal_usdt"]), value=min(float(auto_config.get("daily_limit_usdt", 20) or 20), float(new_config["principal_usdt"])), step=10.0)
                new_config["max_positions"] = st.number_input("最大同时持仓", min_value=1, max_value=5, value=int(auto_config.get("max_positions", 1) or 1), step=1)
                new_config["enforce_allowed_symbols"] = False
                new_config["allowed_symbols"] = auto_config.get("allowed_symbols") or []
                st.info("币种白名单已关闭，所有交易对只按交易所规则、权限、额度和风控检查。")
                c1, c2 = st.columns(2)
                new_config["allow_spot"] = c1.checkbox("允许现货自动交易", value=bool(auto_config.get("allow_spot", True)))
                new_config["allow_futures"] = c2.checkbox("允许U本位永续自动交易", value=bool(auto_config.get("allow_futures", True)))
                market_options = [m for m, enabled in [("spot", new_config["allow_spot"]), ("futures", new_config["allow_futures"])] if enabled] or ["spot"]
                current_market = str(auto_config.get("default_market_type", "futures"))
                new_config["default_market_type"] = st.selectbox("默认市场", market_options, index=market_options.index(current_market) if current_market in market_options else 0, format_func=lambda x: "现货 Spot" if x == "spot" else "U本位永续合约")
                new_config["max_leverage"] = st.slider("最大允许杠杆", 1, 125, int(auto_config.get("max_leverage", 20) or 20))
                new_config["default_leverage"] = st.slider("指定执行杠杆", 1, int(new_config["max_leverage"]), min(int(auto_config.get("default_leverage", 5) or 5), int(new_config["max_leverage"])))
                new_config["take_profit_pct"] = st.number_input("止盈阈值 %（默认约为模拟交易2/3）", min_value=0.1, max_value=20.0, value=float(auto_config.get("take_profit_pct", 2.13) or 2.13), step=0.1)
                new_config["stop_loss_pct"] = st.number_input("止损阈值 %（默认约为模拟交易2/3）", min_value=-20.0, max_value=-0.1, value=float(auto_config.get("stop_loss_pct", -1.07) or -1.07), step=0.1)
                new_config["allow_market_order"] = st.checkbox("允许市价单", value=bool(auto_config.get("allow_market_order", False)))
                new_config["live_auto_order_enabled"] = st.checkbox("允许自动真实下单", value=bool(auto_config.get("live_auto_order_enabled")))
                new_config["live_auto_exit_enabled"] = st.checkbox("允许自动止盈止损", value=bool(auto_config.get("live_auto_exit_enabled")))
                if st.form_submit_button("保存自动交易参数", width="stretch"):
                    save_live_auto_config(new_config)
                    st.success("自动交易参数已保存。")
                    _close_fold("live_params")
                    st.rerun()

    elif active_tab == "事件日志":
        render_metric_grid(
            [
                ("自动订单数", str(auto_review.get("auto_order_count", 0)), ""),
                ("成功数", str(auto_review.get("auto_success_count", 0)), "green"),
                ("失败数", str(auto_review.get("auto_failure_count", 0)), "red"),
                ("熔断次数", str(auto_review.get("circuit_breaker_count", 0)), "red"),
            ]
        )
        if not audit:
            st.info("暂无自动交易事件日志。")
        for event in audit:
            st.caption(f"{event.get('time')}｜{event.get('event')}｜{event.get('symbol')}｜{event.get('result')}｜{event.get('reason')}")


def render_approval_center_page(page_titles: dict[str, tuple[str, str]], version: str, build_committee_decision: Callable[[str, dict[str, Any] | None], dict[str, Any]], safe_committee_text: Callable[[Any, int], str]) -> None:
    """自动交易控制台。保留函数名仅用于兼容旧 approval 路由。"""
    render_page_head("auto_trade", page_titles, version)
    secure_status = get_secure_api_status()
    binance_ready = bool((secure_status.get("BINANCE_API_KEY") or {}).get("configured")) and bool((secure_status.get("BINANCE_API_SECRET") or {}).get("configured"))
    auto_config = load_live_auto_config()
    auto_symbols = set(auto_config.get("allowed_symbols") or [])
    initial_auto_status = _cached_live_auto_status()
    for pos in (initial_auto_status.get("open_positions") or []):
        if pos.get("symbol"):
            auto_symbols.add(str(pos.get("symbol")).upper())
    auto_prices = {sym: float((market_cache.get_ticker(sym) or {}).get("last_price") or 0) for sym in sorted(auto_symbols)}
    auto_status = _cached_live_auto_status(tuple(sorted(auto_prices.items())))
    auto_config = auto_status.get("config") or auto_config
    auto_review = auto_status.get("review") or {}
    account_snapshot = _cached_live_account_snapshot("futures" if auto_config.get("allow_futures") else "spot") if binance_ready else {}
    positions = auto_status.get("open_positions") or []
    market_text = " / ".join(
        name
        for name, enabled in [
            ("现货", auto_config.get("allow_spot")),
            ("U本位永续", auto_config.get("allow_futures")),
        ]
        if enabled
    ) or "未启用"
    status_text = "运行中" if auto_status.get("enabled") and not auto_status.get("paused") else "暂停" if auto_status.get("paused") else "关闭"

    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>自动实盘交易</b><br>
            本页只负责真实交易接口、真实订单参数、运行控制、订单计划和实盘持仓。点击执行按钮前不会提交真实订单；开启自动下单后，系统会按这里保存的参数连接 Binance 执行。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("Binance API", "已接入" if binance_ready else "待接入", "green" if binance_ready else "yellow"),
            ("自动交易状态", status_text, "green" if status_text == "运行中" else "yellow"),
            ("自动下单", "开启" if auto_status.get("order_enabled") else "关闭", "green" if auto_status.get("order_enabled") else "yellow"),
            ("自动止盈止损", "开启" if auto_status.get("exit_enabled") else "关闭", "green" if auto_status.get("exit_enabled") else "yellow"),
            ("市场", market_text, "blue"),
            ("默认杠杆", f"{int(auto_config.get('default_leverage', 5) or 5)}x", "yellow"),
            ("开仓比例", f"{float(auto_config.get('position_pct', 5) or 5):.2f}%", "yellow"),
            ("单笔保证金", _money(auto_config.get("max_order_usdt")), "yellow"),
            ("今日额度", f"{_money(auto_status.get('daily_used_usdt'))} / {_money(auto_config.get('daily_limit_usdt'))}", "yellow"),
            ("当前持仓", f"{len(positions)} / {auto_config.get('max_positions', 1)}", "yellow"),
            ("熔断", "已触发" if auto_status.get("circuit_breaker_enabled") else "未触发", "red" if auto_status.get("circuit_breaker_enabled") else "green"),
            ("账户检查", "通过" if account_snapshot.get("ok") else "待检查" if binance_ready else "未接入", "green" if account_snapshot.get("ok") else "yellow"),
        ]
    )

    if _fold_panel("live_api", "Binance API 填写", "已接入" if binance_ready else "待接入", expanded=not binance_ready):
        with st.form("auto_trade_binance_api_form"):
            b1, b2 = st.columns(2)
            binance_key = b1.text_input("Binance API Key", value="", type="password", placeholder="留空则不修改")
            binance_secret = b2.text_input("Binance API Secret", value="", type="password", placeholder="留空则不修改")
            st.caption("API Key 必须关闭提现权限；页面只显示脱敏状态，不展示 Secret。")
            if st.form_submit_button("保存 Binance API", width="stretch"):
                result = write_secure_api_values({"BINANCE_API_KEY": binance_key, "BINANCE_API_SECRET": binance_secret})
                (st.success if result.get("ok") else st.warning)(result.get("message"))
                _close_fold("live_api")
                st.rerun()

    if not binance_ready:
        st.warning("Binance API 待接入：自动交易不会运行。请先保存 Binance API Key / Secret。")
        return

    _render_live_trade_ledger(
        account_snapshot=account_snapshot,
        auto_status=auto_status,
        auto_config=auto_config,
        auto_review=auto_review,
        positions=positions,
        market_text=market_text,
        status_text=status_text,
        build_committee_decision=build_committee_decision,
    )
    return

    st.markdown("### 实盘运行状态")
    if account_snapshot.get("ok"):
        st.success("Binance API 已接入，账户只读检查通过。")
    else:
        st.warning(account_snapshot.get("message", "Binance 账户状态暂不可用。"))
    balances = account_snapshot.get("balances") or []
    if balances:
        with st.expander(f"账户余额｜{len(balances[:20])} 条资产", expanded=False):
            st.dataframe(balances[:20], width="stretch", hide_index=True)

    st.markdown("### 自动交易控制")
    confirm_phrase = st.text_input("开启/恢复确认短句", value="", type="password", placeholder="我确认开启小资金自动实盘试运行")
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("开启自动交易", width="stretch"):
        result = enable_live_auto_pilot(confirm_phrase)
        (st.success if result.get("ok") else st.error)(result.get("message"))
        st.rerun()
    if c2.button("暂停自动交易", width="stretch"):
        pause_live_auto_pilot("用户在自动交易页暂停。")
        st.rerun()
    if c3.button("恢复自动交易", width="stretch"):
        result = resume_live_auto_pilot(confirm_phrase)
        (st.success if result.get("ok") else st.error)(result.get("message"))
        st.rerun()
    if c4.button("停止自动交易", width="stretch"):
        disable_live_auto_pilot("用户在自动交易页停止。")
        st.rerun()
    b1, b2 = st.columns(2)
    breaker_reason = b1.text_input("熔断原因", value="用户手动触发自动交易熔断。")
    if b1.button("触发熔断", width="stretch"):
        trigger_live_auto_circuit_breaker(breaker_reason)
        st.rerun()
    breaker_phrase = b2.text_input("解除熔断确认短句", value="", type="password", placeholder="我确认解除自动实盘熔断")
    if b2.button("解除熔断", width="stretch"):
        result = release_live_auto_circuit_breaker(breaker_phrase)
        (st.success if result.get("ok") else st.error)(result.get("message"))
        st.rerun()

    if _fold_panel("live_admission", "自动交易准入检查", "展开查看每项检查", expanded=False):
        admission = run_live_auto_admission_check(user_confirmed=True)
        for item in admission.get("checks", []):
            (st.success if item.get("ok") else st.error)(f"{item.get('name')}：{item.get('message')}")

    current_symbol = st.session_state.get("current_symbol", "BTCUSDT")
    current_ticker = market_cache.get_ticker(current_symbol) or {}
    decision = build_committee_decision(current_symbol, current_ticker)
    signal = committee_decision_to_sim_signal(decision)
    signal["symbol"] = current_symbol
    signal["current_price"] = float(current_ticker.get("last_price") or 0)
    signal["data_quality"] = "good" if current_ticker else "poor"
    ok, reasons = filter_live_auto_signal(signal)

    st.markdown("### 当前自动信号")
    render_metric_grid(
        [
            ("交易对象", current_symbol, "yellow"),
            ("当前价格", format_price(signal.get("current_price")), ""),
            ("委员会动作", str(signal.get("action", "-")), ""),
            ("方向", str(signal.get("direction", "-")), ""),
            ("置信度", str(signal.get("committee_confidence", 0)), "green" if float(signal.get("committee_confidence") or 0) >= 70 else "yellow"),
            ("风险评分", format_score(signal.get("risk_score")), "green" if safe_compare_lt(signal.get("risk_score"), 55.01) else "red"),
            ("信号过滤", "通过" if ok else "拒绝", "green" if ok else "red"),
            ("交易对象限制", "已放开", "green"),
        ]
    )
    for reason in reasons:
        st.warning(reason)
    if st.button("按当前信号生成真实自动订单计划", disabled=not ok, width="stretch"):
        st.session_state["live_auto_order_plan"] = create_live_auto_order_plan(signal)
        st.rerun()

    plan = st.session_state.get("live_auto_order_plan")
    st.markdown("### 真实订单计划")
    if not plan:
        st.info("当前暂无真实自动订单计划。通过当前信号生成后，可在这里执行真实下单。")
    else:
        render_metric_grid(
            [
                ("计划ID", str(plan.get("auto_plan_id", "-")), ""),
                ("交易对象", str(plan.get("symbol", "-")), "yellow"),
                ("市场", "U本位永续" if plan.get("market_type") == "futures" else "现货", "blue"),
                ("方向", str(plan.get("side", "-")), "green" if plan.get("side") == "BUY" else "red"),
                ("杠杆", f"{int(plan.get('leverage', 1) or 1)}x", "yellow"),
                ("保证金", _money(plan.get("quote_amount")), "yellow"),
                ("名义金额", _money(plan.get("notional")), "blue"),
                ("数量", f"{float(plan.get('quantity', 0) or 0):.8f}", ""),
            ]
        )
        if st.button("执行真实自动订单", width="stretch"):
            st.session_state["live_auto_execute_result"] = execute_live_auto_spot_order(plan)
            st.rerun()
        result = st.session_state.get("live_auto_execute_result") or {}
        if result:
            (st.success if result.get("ok") else st.error)(result.get("message"))
            preflight = result.get("preflight") or {}
            for item in preflight.get("checks", []):
                st.caption(f"{'通过' if item.get('ok') else '失败'}｜{item.get('name')}｜{item.get('message')}")

    st.markdown("### 自动持仓")
    if not positions:
        st.info("当前暂无自动交易持仓。")
    for pos in positions:
        with st.expander(f"{pos.get('symbol')}｜{pos.get('market_type', 'spot')}｜{pos.get('status')}｜浮盈 {float(pos.get('unrealized_pnl', 0) or 0):+.4f} USDT / {float(pos.get('unrealized_pnl_pct', 0) or 0):+.2f}%", expanded=False):
            st.markdown(kline_symbol_link(pos.get("symbol"), f"查看 {pos.get('symbol')} K线", "watch-pill"), unsafe_allow_html=True)
            exit_check = run_live_auto_exit_check(pos)
            render_metric_grid(
                [
                    ("入场价", f"{float(pos.get('entry_price', 0) or 0):.8f}", ""),
                    ("当前价", f"{float(pos.get('current_price', 0) or 0):.8f}", ""),
                    ("数量", f"{float(pos.get('quantity', 0) or 0):.8f}", ""),
                    ("保证金", _money(pos.get("quote_amount")), "yellow"),
                    ("杠杆", f"{int(pos.get('leverage', 1) or 1)}x", "yellow"),
                    ("退出检查", str(exit_check.get("action", "-")), "red" if exit_check.get("ok") else "yellow"),
                ]
            )

    param_summary = f"{market_text}｜{float(auto_config.get('position_pct', 5) or 5):.2f}%｜{int(auto_config.get('default_leverage', 5) or 5)}x｜{_money(auto_config.get('max_order_usdt'))}"
    if _fold_panel("live_params", "真实订单参数", param_summary, expanded=False):
        with st.form("auto_trade_settings_form"):
            new_config = dict(auto_config)
            new_config["principal_usdt"] = st.number_input("自动交易本金 USDT", min_value=1.0, max_value=10_000_000.0, value=float(auto_config.get("principal_usdt", 100) or 100), step=10.0)
            new_config["position_pct"] = st.slider("开仓比例（占本金）", min_value=0.1, max_value=40.0, value=float(auto_config.get("position_pct", 5) or 5), step=0.1)
            st.info(f"按当前设置，单笔保证金约为 {float(new_config['principal_usdt']) * float(new_config['position_pct']) / 100:.2f} USDT。")
            new_config["daily_limit_usdt"] = st.number_input("单日自动交易额度 USDT", min_value=1.0, max_value=float(new_config["principal_usdt"]), value=min(float(auto_config.get("daily_limit_usdt", 20) or 20), float(new_config["principal_usdt"])), step=10.0)
            new_config["max_positions"] = st.number_input("最大同时持仓", min_value=1, max_value=5, value=int(auto_config.get("max_positions", 1) or 1), step=1)
            new_config["enforce_allowed_symbols"] = False
            new_config["allowed_symbols"] = auto_config.get("allowed_symbols") or []
            st.info("币种白名单已关闭，所有交易对只按交易所规则、权限、额度和风控检查。")
            c1, c2 = st.columns(2)
            new_config["allow_spot"] = c1.checkbox("允许现货自动交易", value=bool(auto_config.get("allow_spot", True)))
            new_config["allow_futures"] = c2.checkbox("允许U本位永续自动交易", value=bool(auto_config.get("allow_futures", True)))
            market_options = [m for m, enabled in [("spot", new_config["allow_spot"]), ("futures", new_config["allow_futures"])] if enabled] or ["spot"]
            current_market = str(auto_config.get("default_market_type", "futures"))
            new_config["default_market_type"] = st.selectbox("默认市场", market_options, index=market_options.index(current_market) if current_market in market_options else 0, format_func=lambda x: "现货 Spot" if x == "spot" else "U本位永续合约")
            new_config["max_leverage"] = st.slider("最大允许杠杆", 1, 125, int(auto_config.get("max_leverage", 20) or 20))
            new_config["default_leverage"] = st.slider("指定执行杠杆", 1, int(new_config["max_leverage"]), min(int(auto_config.get("default_leverage", 5) or 5), int(new_config["max_leverage"])))
            new_config["take_profit_pct"] = st.number_input("止盈阈值 %（默认约为模拟交易2/3）", min_value=0.1, max_value=20.0, value=float(auto_config.get("take_profit_pct", 2.13) or 2.13), step=0.1)
            new_config["stop_loss_pct"] = st.number_input("止损阈值 %（默认约为模拟交易2/3）", min_value=-20.0, max_value=-0.1, value=float(auto_config.get("stop_loss_pct", -1.07) or -1.07), step=0.1)
            new_config["allow_market_order"] = st.checkbox("允许市价单", value=bool(auto_config.get("allow_market_order", False)))
            new_config["live_auto_order_enabled"] = st.checkbox("允许自动真实下单", value=bool(auto_config.get("live_auto_order_enabled")))
            new_config["live_auto_exit_enabled"] = st.checkbox("允许自动止盈止损", value=bool(auto_config.get("live_auto_exit_enabled")))
            if st.form_submit_button("保存自动交易参数", width="stretch"):
                save_live_auto_config(new_config)
                st.success("自动交易参数已保存。")
                _close_fold("live_params")
                st.rerun()

    if _fold_panel("live_logs", "事件日志与统计", f"订单 {auto_review.get('auto_order_count', 0)}｜失败 {auto_review.get('auto_failure_count', 0)}", expanded=False):
        render_metric_grid(
            [
                ("自动订单数", str(auto_review.get("auto_order_count", 0)), ""),
                ("成功数", str(auto_review.get("auto_success_count", 0)), "green"),
                ("失败数", str(auto_review.get("auto_failure_count", 0)), "red"),
                ("熔断次数", str(auto_review.get("circuit_breaker_count", 0)), "red"),
            ]
        )
        audit = load_live_auto_audit_log(100)
        if not audit:
            st.info("暂无自动交易事件日志。")
        for event in audit:
            st.caption(f"{event.get('time')}｜{event.get('event')}｜{event.get('symbol')}｜{event.get('result')}｜{event.get('reason')}")







def _render_approval_card(approval: dict[str, Any], compact: bool = False) -> None:
    approval_id = str(approval.get("approval_id", ""))
    symbol = str(approval.get("symbol", "-"))
    status = str(approval.get("status", "-"))
    deep = approval.get("deepseek_snapshot") or {}
    gemini = approval.get("gemini_snapshot") or {}
    soft_warning = bool(deep.get("soft_veto") or gemini.get("soft_veto"))
    title = f"{symbol}｜{approval.get('approval_type')}｜{approval.get('side')}｜{status}｜{approval_id}"
    with st.expander(title, expanded=not compact):
        render_metric_grid(
            [
                ("状态", status, "yellow" if status == "pending" else "green" if status in {"approved", "executed"} else "red"),
                ("模式", str(approval.get("mode", "-")), "yellow"),
                ("来源", str(approval.get("source", "-")), ""),
                ("优先级", str(approval.get("priority", "-")), "red" if approval.get("priority") in {"高", "紧急"} else "yellow"),
                ("创建价格", f"{float(approval.get('price_at_create', 0) or 0):.8f}", ""),
                ("当前价格", f"{float(approval.get('current_price', 0) or 0):.8f}", ""),
                ("建议金额", f"{float(approval.get('system_suggested_amount', 0) or 0):.2f} USDT", ""),
                ("风控最大", f"{float(approval.get('risk_max_amount', 0) or 0):.2f} USDT", "red"),
            ]
        )
        st.markdown(
            f"""
            <div class="status-card">
              有效期：{escape(str(approval.get("expires_at", "-")))}｜
              用户选择金额：{escape(str(approval.get("user_selected_amount") or "未选择"))}<br>
              DeepSeek：{escape(str(deep.get("vote", "暂无")))}｜风险 {escape(str(deep.get("risk_level", "-")))}｜{escape(safe_committee_text(deep.get("summary", "暂无")))}<br>
              Gemini：{escape(str(gemini.get("vote", "暂无")))}｜风险 {escape(str(gemini.get("risk_level", "-")))}｜{escape(safe_committee_text(gemini.get("summary", "暂无")))}
            </div>
            """,
            unsafe_allow_html=True,
        )
        if soft_warning:
            st.warning("外部AI存在风险提醒。用户仍批准时，本次操作会记录为人工风险确认。")
        if status in {"pending", "modified"}:
            m1, m2, m3 = st.columns(3)
            new_amount = m1.number_input("修改用户金额", min_value=0.0, max_value=50.0, value=float(approval.get("user_selected_amount") or approval.get("system_suggested_amount") or 0), step=1.0, key=f"appr_amount_{approval_id}")
            new_price = m2.number_input("更新当前价格", min_value=0.0, value=float(approval.get("current_price") or approval.get("price_at_create") or 0), step=0.01, key=f"appr_price_{approval_id}")
            new_type = m3.selectbox("订单类型", ["LIMIT", "MARKET"], index=0 if approval.get("order_type") != "MARKET" else 1, key=f"appr_type_{approval_id}")
            c1, c2, c3 = st.columns(3)
            if c1.button("保存修改", key=f"modify_{approval_id}", width="stretch"):
                result = modify_approval(approval_id, {"user_selected_amount": new_amount, "current_price": new_price, "order_type": new_type})
                (st.success if result.get("ok") else st.error)(result.get("message"))
                st.rerun()
            if c2.button("批准审批单", key=f"approve_{approval_id}", width="stretch"):
                result = approve_approval(approval_id, {"user_selected_amount": new_amount, "reason": "用户在审批中心批准。"})
                (st.success if result.get("ok") else st.error)(result.get("message"))
                st.rerun()
            reject_reason = st.selectbox("拒绝原因", ["风险太高", "不想交易", "仓位太大", "信号不清晰", "外部AI反对", "手动取消", "其他"], key=f"reject_reason_{approval_id}")
            if c3.button("拒绝审批单", key=f"reject_{approval_id}", width="stretch"):
                result = reject_approval(approval_id, reject_reason)
                (st.success if result.get("ok") else st.error)(result.get("message"))
                st.rerun()
        elif status == "approved":
            required_phrase = "我确认执行小资金实盘平仓" if approval.get("approval_type") in {"exit", "partial_exit"} else "我确认执行小资金实盘订单"
            if approval.get("approval_type") == "cancel":
                required_phrase = "我确认撤销该真实订单"
            st.markdown(f"**确认短句：** `{required_phrase}`")
            phrase = st.text_input("执行确认短句", key=f"approval_phrase_{approval_id}")
            if st.button("运行审批执行前检查", key=f"preflight_{approval_id}", width="stretch"):
                current_price = float(approval.get("current_price") or approval.get("price_at_create") or 0)
                test_result = {}
                if approval.get("mode") == "LIVE_MANUAL" and approval.get("approval_type") == "entry":
                    plan = create_live_order_plan(approval.get("committee_snapshot") or {}, approval.get("entry_plan") or {})
                    test_result = run_spot_test_order(plan)
                elif approval.get("mode") == "LIVE_MANUAL" and approval.get("approval_type") in {"exit", "partial_exit"}:
                    test_result = run_exit_spot_test_order(approval.get("exit_plan") or {})
                st.session_state[f"approval_test_{approval_id}"] = test_result
                st.session_state[f"approval_preflight_{approval_id}"] = run_approval_preflight(approval, current_price, test_result, phrase)
            preflight = st.session_state.get(f"approval_preflight_{approval_id}") or {}
            if preflight:
                st.markdown(f"**检查结果：{preflight.get('message')}**")
                for item in preflight.get("checks", []):
                    (st.success if item.get("ok") else st.error)(f"{item.get('name')}：{item.get('message')}")
            if st.button("执行已批准审批单", key=f"execute_{approval_id}", disabled=not bool(preflight.get("ok")), width="stretch"):
                result = execute_approved_approval(approval, st.session_state.get(f"approval_test_{approval_id}") or {}, phrase, approval.get("current_price"))
                (st.success if result.get("ok") else st.error)(result.get("message"))
                st.rerun()
        else:
            st.caption("该审批单当前状态不能执行。")
        history_html = "".join(
            f"<div>{escape(str(event.get('time', '')))}｜{escape(str(event.get('action', '')))}｜{escape(str(event.get('reason', '')))}</div>"
            for event in approval.get("approval_history", [])
        )
        st.markdown(f"<details class='status-card'><summary>审批历史</summary>{history_html or '暂无审批历史。'}</details>", unsafe_allow_html=True)

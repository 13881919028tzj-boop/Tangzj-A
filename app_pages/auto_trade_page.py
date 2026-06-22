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
    run_exit_spot_test_order,
    run_spot_test_order,
)
from services.secure_api_vault import get_secure_api_status, write_secure_api_values
from utils.formatters import format_price, format_score, safe_number


def safe_compare_lt(value: Any, threshold: float) -> bool:
    number = safe_number(value, None)
    return False if number is None else number < threshold


def render_approval_center_page(page_titles: dict[str, tuple[str, str]], version: str, build_committee_decision: Callable[[str, dict[str, Any] | None], dict[str, Any]], safe_committee_text: Callable[[Any, int], str]) -> None:
    """自动交易控制台。保留函数名仅用于兼容旧 approval 路由。"""
    render_page_head("auto_trade", page_titles, version)
    secure_status = get_secure_api_status()
    binance_ready = bool((secure_status.get("BINANCE_API_KEY") or {}).get("configured")) and bool((secure_status.get("BINANCE_API_SECRET") or {}).get("configured"))
    auto_config = load_live_auto_config()
    auto_symbols = set(auto_config.get("allowed_symbols") or [])
    for pos in (get_live_auto_status().get("open_positions") or []):
        if pos.get("symbol"):
            auto_symbols.add(str(pos.get("symbol")).upper())
    auto_prices = {sym: float((market_cache.get_ticker(sym) or {}).get("last_price") or 0) for sym in sorted(auto_symbols)}
    auto_status = get_live_auto_status(auto_prices)
    auto_config = auto_status.get("config") or auto_config
    auto_review = auto_status.get("review") or {}

    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>自动交易栏</b><br>
            审批制度已取消。本栏用于自动实盘交易控制，支持现货 Spot 与 U本位永续合约。开启后系统会按用户设置的本金、开仓比例、杠杆、白名单、止盈止损和风控状态执行；未接入有效 Binance API 时只显示待接入，不会下单。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("Binance API", "已接入" if binance_ready else "待接入", "green" if binance_ready else "yellow"),
            ("自动交易状态", "运行中" if auto_status.get("enabled") and not auto_status.get("paused") else "暂停" if auto_status.get("paused") else "关闭", "green" if auto_status.get("enabled") and not auto_status.get("paused") else "yellow"),
            ("自动下单", "开启" if auto_status.get("order_enabled") else "关闭", "green" if auto_status.get("order_enabled") else "yellow"),
            ("自动止盈止损", "开启" if auto_status.get("exit_enabled") else "关闭", "green" if auto_status.get("exit_enabled") else "yellow"),
            ("市场", ("现货 " if auto_config.get("allow_spot") else "") + ("永续" if auto_config.get("allow_futures") else ""), "blue"),
            ("默认杠杆", f"{int(auto_config.get('default_leverage', 5) or 5)}x", "yellow"),
            ("开仓比例", f"{float(auto_config.get('position_pct', 5) or 5):.2f}%", "yellow"),
            ("单笔保证金", f"{float(auto_config.get('max_order_usdt', 0) or 0):.2f} USDT", "yellow"),
        ]
    )

    if not binance_ready:
        st.warning("Binance API 待接入：自动交易不会运行。请在下方填写并保存 Binance API Key / Secret。")
        with st.form("auto_trade_binance_api_form"):
            b1, b2 = st.columns(2)
            binance_key = b1.text_input("Binance API Key", value="", type="password", placeholder="留空则不修改")
            binance_secret = b2.text_input("Binance API Secret", value="", type="password", placeholder="留空则不修改")
            st.caption("密钥保存机制与 DeepSeek/Gemini 一致：写入本机 .env，页面只显示脱敏状态，不记录 Secret。")
            if st.form_submit_button("保存 Binance API", width="stretch"):
                result = write_secure_api_values({"BINANCE_API_KEY": binance_key, "BINANCE_API_SECRET": binance_secret})
                (st.success if result.get("ok") else st.warning)(result.get("message"))
                st.rerun()
        return

    account_snapshot = get_live_account_snapshot(False, "futures" if auto_config.get("allow_futures") else "spot")
    tabs = st.tabs(["账户总览", "自动开关", "当前信号", "订单计划", "自动持仓", "参数设置", "事件日志"])

    with tabs[0]:
        if account_snapshot.get("ok"):
            st.success("Binance API 已接入，账户只读检查通过。")
        else:
            st.warning(account_snapshot.get("message", "Binance账户状态暂不可用。"))
        render_metric_grid(
            [
                ("今日额度", f"{float(auto_status.get('daily_used_usdt', 0) or 0):.2f} / {float(auto_config.get('daily_limit_usdt', 0) or 0):.2f} USDT", "yellow"),
                ("本金设置", f"{float(auto_config.get('principal_usdt', 0) or 0):.2f} USDT", "blue"),
                ("最大持仓", str(auto_config.get("max_positions", 1)), ""),
                ("当前持仓", str(len(auto_status.get("open_positions") or [])), "yellow"),
                ("熔断", "已触发" if auto_status.get("circuit_breaker_enabled") else "未触发", "red" if auto_status.get("circuit_breaker_enabled") else "green"),
                ("熔断原因", str(auto_status.get("circuit_breaker_reason") or "无")[:32], "red" if auto_status.get("circuit_breaker_enabled") else ""),
            ]
        )
        balances = account_snapshot.get("balances") or []
        if balances:
            st.caption("账户余额只读摘要")
            st.dataframe(balances[:20], width="stretch", hide_index=True)

    with tabs[1]:
        c1, c2, c3, c4 = st.columns(4)
        if c1.button("开启自动交易", width="stretch"):
            result = enable_live_auto_pilot("我确认开启小资金自动实盘试运行")
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()
        if c2.button("暂停自动交易", width="stretch"):
            pause_live_auto_pilot("用户在自动交易栏暂停。")
            st.rerun()
        if c3.button("恢复自动交易", width="stretch"):
            result = resume_live_auto_pilot("我确认开启小资金自动实盘试运行")
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()
        if c4.button("停止自动交易", width="stretch"):
            disable_live_auto_pilot("用户在自动交易栏停止。")
            st.rerun()
        b1, b2 = st.columns(2)
        breaker_reason = b1.text_input("熔断原因", value="用户手动触发自动交易熔断。")
        if b1.button("触发熔断", width="stretch"):
            trigger_live_auto_circuit_breaker(breaker_reason)
            st.rerun()
        if b2.button("解除熔断", width="stretch"):
            result = release_live_auto_circuit_breaker("我确认解除自动实盘熔断")
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()
        st.markdown("**自动交易准入检查**")
        admission = run_live_auto_admission_check(user_confirmed=True)
        for item in admission.get("checks", []):
            (st.success if item.get("ok") else st.error)(f"{item.get('name')}：{item.get('message')}")

    with tabs[2]:
        current_symbol = st.session_state.get("current_symbol", "BTCUSDT")
        current_ticker = market_cache.get_ticker(current_symbol) or {}
        decision = build_committee_decision(current_symbol, current_ticker)
        signal = committee_decision_to_sim_signal(decision)
        signal["symbol"] = current_symbol
        signal["current_price"] = float(current_ticker.get("last_price") or 0)
        signal["data_quality"] = "good" if current_ticker else "poor"
        ok, reasons = filter_live_auto_signal(signal)
        render_metric_grid(
            [
                ("交易对象", current_symbol, "yellow"),
                ("当前价格", format_price(signal.get("current_price")), ""),
                ("委员会动作", str(signal.get("action", "-")), ""),
                ("置信度", str(signal.get("committee_confidence", 0)), "green" if float(signal.get("committee_confidence") or 0) >= 70 else "yellow"),
                ("风险评分", format_score(signal.get("risk_score")), "green" if safe_compare_lt(signal.get("risk_score"), 55.01) else "red"),
                ("信号过滤", "通过" if ok else "拒绝", "green" if ok else "red"),
            ]
        )
        for reason in reasons:
            st.warning(reason)
        if st.button("按当前信号生成自动订单计划", disabled=not ok, width="stretch"):
            st.session_state["live_auto_order_plan"] = create_live_auto_order_plan(signal)
            st.rerun()

    with tabs[3]:
        plan = st.session_state.get("live_auto_order_plan")
        if not plan:
            st.info("当前暂无自动订单计划。请在“当前信号”页生成。")
        else:
            render_metric_grid(
                [
                    ("计划ID", str(plan.get("auto_plan_id", "-")), ""),
                    ("交易对象", str(plan.get("symbol", "-")), "yellow"),
                    ("市场", "永续合约" if plan.get("market_type") == "futures" else "现货", "blue"),
                    ("方向", str(plan.get("side", "-")), "green" if plan.get("side") == "BUY" else "red"),
                    ("杠杆", f"{int(plan.get('leverage', 1) or 1)}x", "yellow"),
                    ("保证金", f"{float(plan.get('quote_amount', 0) or 0):.2f} USDT", "yellow"),
                    ("名义金额", f"{float(plan.get('notional', 0) or 0):.2f} USDT", "blue"),
                    ("数量", f"{float(plan.get('quantity', 0) or 0):.8f}", ""),
                ]
            )
            if st.button("执行自动订单", width="stretch"):
                st.session_state["live_auto_execute_result"] = execute_live_auto_spot_order(plan)
                st.rerun()
            result = st.session_state.get("live_auto_execute_result") or {}
            if result:
                (st.success if result.get("ok") else st.error)(result.get("message"))
                preflight = result.get("preflight") or {}
                for item in preflight.get("checks", []):
                    st.caption(f"{'通过' if item.get('ok') else '失败'}｜{item.get('name')}｜{item.get('message')}")

    with tabs[4]:
        positions = auto_status.get("open_positions") or []
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
                        ("保证金", f"{float(pos.get('quote_amount', 0) or 0):.2f} USDT", "yellow"),
                        ("杠杆", f"{int(pos.get('leverage', 1) or 1)}x", "yellow"),
                        ("退出检查", str(exit_check.get("action", "-")), "red" if exit_check.get("ok") else "yellow"),
                    ]
                )

    with tabs[5]:
        with st.form("auto_trade_settings_form"):
            new_config = dict(auto_config)
            new_config["principal_usdt"] = st.number_input("自动交易本金 USDT", min_value=1.0, max_value=10_000_000.0, value=float(auto_config.get("principal_usdt", 100) or 100), step=10.0)
            new_config["position_pct"] = st.slider("开仓比例（占本金）", min_value=0.1, max_value=40.0, value=float(auto_config.get("position_pct", 5) or 5), step=0.1)
            st.info(f"按当前设置，单笔保证金约为 {float(new_config['principal_usdt']) * float(new_config['position_pct']) / 100:.2f} USDT。")
            new_config["daily_limit_usdt"] = st.number_input("单日自动交易额度 USDT", min_value=1.0, max_value=float(new_config["principal_usdt"]), value=min(float(auto_config.get("daily_limit_usdt", 20) or 20), float(new_config["principal_usdt"])), step=10.0)
            new_config["max_positions"] = st.number_input("最大同时持仓", min_value=1, max_value=5, value=int(auto_config.get("max_positions", 1) or 1), step=1)
            new_config["allowed_symbols"] = [s.strip().upper() for s in st.text_input("自动交易白名单", value=",".join(auto_config.get("allowed_symbols") or ["BTCUSDT", "ETHUSDT"])).split(",") if s.strip()]
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
            new_config["live_auto_order_enabled"] = st.checkbox("允许自动真实下单", value=bool(auto_config.get("live_auto_order_enabled")))
            new_config["live_auto_exit_enabled"] = st.checkbox("允许自动止盈止损", value=bool(auto_config.get("live_auto_exit_enabled")))
            if st.form_submit_button("保存自动交易参数", width="stretch"):
                save_live_auto_config(new_config)
                st.success("自动交易参数已保存。")
                st.rerun()

    with tabs[6]:
        st.markdown("**自动交易统计**")
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

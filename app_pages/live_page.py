"""Live trading safety page rendering."""

from __future__ import annotations

from html import escape
from typing import Any, Callable

import streamlit as st

from app_pages.simulation_page import committee_decision_to_sim_signal
from components.ui import kline_symbol_link, render_metric_grid, render_page_head
from services import market_cache
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
    cancel_live_order,
    create_live_exit_plan,
    create_live_order_preview,
    create_live_order_plan,
    fetch_live_order_status,
    generate_live_position_review_snapshot,
    get_live_account_snapshot,
    get_live_manual_execution_status,
    get_live_position_summary,
    get_live_safety_status,
    load_live_position_audit_log,
    load_live_settings,
    preview_live_exit_order,
    release_live_kill_switch,
    run_exit_spot_test_order,
    run_futures_test_order,
    run_live_exit_preflight,
    run_live_futures_preflight,
    run_live_manual_preflight,
    run_live_preflight_check,
    run_spot_test_order,
    run_test_order_validation,
    run_testnet_order_flow,
    save_live_settings,
    submit_live_exit_order,
    submit_live_futures_order,
    submit_live_spot_order,
    trigger_live_kill_switch,
    validate_live_order_plan,
)
from utils.formatters import format_score, safe_number


def safe_compare_lt(value: Any, threshold: float) -> bool:
    number = safe_number(value, None)
    return False if number is None else number < threshold


def render_live_trading_center(page_titles: dict[str, tuple[str, str]], version: str, build_committee_decision: Callable[[str, dict[str, Any] | None], dict[str, Any]], safe_committee_text: Callable[[Any, int], str]) -> None:
    """实盘交易中心前置安全版。"""
    render_page_head("live", page_titles, version)
    status = get_live_safety_status()
    settings = status.get("settings") or load_live_settings()
    credentials = status.get("credentials") or {}
    connection = status.get("connection") or {}
    permission = status.get("permission") or {}
    withdraw = status.get("withdraw") or {}
    ip_status = status.get("ip_status") or {}
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>实盘前置安全提示</b><br>
            当前为小资金手动实盘执行功能。真实订单会使用真实资金，请谨慎操作。系统不会自动下单，任何真实订单都必须由你手动确认。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("当前模式", str(settings.get("mode", "read_only")), "yellow"),
            ("API连接", str(connection.get("status", "未检查")), "green" if connection.get("ok") else "red"),
            ("权限状态", str(permission.get("permission_status", "未配置")), "yellow"),
            ("提现权限", str(withdraw.get("status", "未知")), "red" if withdraw.get("status") == "高危开启" else "green"),
            ("IP白名单", str(ip_status.get("status", "未确认")), "green" if ip_status.get("ok") else "yellow"),
            ("安全锁", "已开启" if settings.get("kill_switch_enabled") else "未开启", "red" if settings.get("kill_switch_enabled") else "green"),
            ("Live Manual", "已启用" if settings.get("live_manual_enabled") else "默认禁用", "red" if settings.get("live_manual_enabled") else "yellow"),
            ("实盘候选", "允许审查" if status.get("allow_live_candidate") else "暂不允许", "green" if status.get("allow_live_candidate") else "red"),
        ]
    )

    tabs = st.tabs(["安全总览", "API设置", "账户只读", "订单预览", "执行前检查", "Dry-run / Testnet", "小资金实盘", "实盘持仓", "自动试运行", "实盘准入", "审计日志"])

    with tabs[0]:
        st.markdown(
            f"""
            <div class="app-shell"><div class="module-card">
              <div class="module-title">安全总览</div>
              <div class="status-card">
                安全说明：{escape(str(status.get("safety_notice", "")))}<br>
                API Key：{escape(str(credentials.get("masked_api_key", "未配置")))}｜Secret：{escape(str(credentials.get("secret_status", "未配置")))}<br>
                API连接：{escape(str(connection.get("message", "")))}<br>
                权限检查：{escape(str(permission.get("message", "")))}<br>
                提现权限：{escape(str(withdraw.get("message", "")))}<br>
                IP限制：{escape(str(ip_status.get("message", "")))}<br>
                安全锁原因：{escape(str(settings.get("kill_switch_reason", "") or "无"))}
              </div>
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2)
        if c1.button("触发实盘安全锁", width="stretch"):
            trigger_live_kill_switch("用户在实盘安全中心手动触发。")
            st.rerun()
        confirm_release = c2.checkbox("我确认解除实盘安全锁，并理解风险")
        if c2.button("解除安全锁", disabled=not confirm_release, width="stretch"):
            release_live_kill_switch("用户二次确认解除安全锁。")
            st.rerun()

    with tabs[1]:
        st.markdown(
            f"""
            <div class="app-shell"><div class="module-card">
              <div class="module-title">API密钥安全管理</div>
              <div class="status-card">
                API Key：{escape(str(credentials.get("masked_api_key", "未配置")))}<br>
                API Secret：已隐藏，页面不会显示，也不会写入日志。<br>
                来源：{escape(str(credentials.get("source", "未配置")))}<br>
                请不要把 API Secret 发送给任何人，也不要上传到任何公共仓库。
              </div>
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        with st.form("live_settings_form"):
            new_settings = dict(settings)
            new_settings["mode"] = st.selectbox("交易模式", ["read_only", "dry_run", "testnet", "live_manual"], index=["read_only", "dry_run", "testnet", "live_manual"].index(str(settings.get("mode", "read_only"))) if str(settings.get("mode", "read_only")) in ["read_only", "dry_run", "testnet", "live_manual"] else 0, format_func=lambda x: {"read_only": "只读模式", "dry_run": "Dry-run 本地预演", "testnet": "Testnet 测试网", "live_manual": "Live Manual 小资金手动"}[x])
            new_settings["market_type"] = st.selectbox("市场类型", ["spot", "futures"], index=0 if settings.get("market_type") == "spot" else 1, format_func=lambda x: "现货" if x == "spot" else "U本位合约")
            new_settings["live_manual_enabled"] = st.checkbox("手动开启 Live Manual 小资金实盘流程（仍需 LIVE_TRADING_ENABLED=true）", value=bool(settings.get("live_manual_enabled")))
            new_settings["ip_whitelist_confirmed"] = st.checkbox("我已开启 IP 白名单，或已理解未开启的风险", value=bool(settings.get("ip_whitelist_confirmed")))
            new_settings["max_live_notional_usdt"] = st.number_input("单笔真实名义金额上限 USDT", min_value=1.0, max_value=50.0, value=float(settings.get("max_live_notional_usdt", 10)), step=1.0)
            new_settings["daily_live_notional_limit_usdt"] = st.number_input("单日真实交易总额上限 USDT", min_value=10.0, max_value=500.0, value=float(settings.get("daily_live_notional_limit_usdt", 100)), step=10.0)
            new_settings["daily_live_loss_limit_usdt"] = st.number_input("单日真实亏损上限 USDT", min_value=1.0, max_value=100.0, value=float(settings.get("daily_live_loss_limit_usdt", 5)), step=1.0)
            new_settings["max_live_risk_pct"] = st.number_input("单笔真实风险上限 %", min_value=0.1, max_value=5.0, value=float(settings.get("max_live_risk_pct", 0.5)), step=0.1)
            symbols_text = st.text_input("允许实盘候选交易对象", value=",".join(settings.get("allowed_symbols", ["BTCUSDT", "ETHUSDT"])))
            new_settings["allowed_symbols"] = [s.strip().upper() for s in symbols_text.split(",") if s.strip()]
            if st.form_submit_button("保存安全配置", width="stretch"):
                save_live_settings(new_settings)
                st.success("安全配置已保存。真实提交仍要求 LIVE_TRADING_ENABLED=true、Spot Test Order 和确认短句。")
                st.rerun()

    with tabs[2]:
        snapshot = get_live_account_snapshot(settings.get("mode") == "testnet", settings.get("market_type", "spot"))
        label = snapshot.get("label", "真实账户只读数据")
        st.markdown(f"**{label}**｜更新时间：{snapshot.get('updated_time')}")
        if not snapshot.get("ok"):
            st.warning(snapshot.get("message"))
        balances = snapshot.get("balances") or []
        if not balances:
            st.info("当前暂无可显示余额，或 API 未配置。")
        for row in balances[:30]:
            st.markdown(f"{row.get('asset')}｜可用：{row.get('free')}｜锁定/钱包：{row.get('locked', row.get('wallet', '0'))}")
        positions = snapshot.get("positions") or []
        if positions:
            st.markdown("**真实持仓只读显示**")
            for pos in positions[:20]:
                st.caption(f"{pos.get('symbol')}｜数量：{pos.get('positionAmt')}｜未实现盈亏：{pos.get('unrealizedProfit')}")

    with tabs[3]:
        ticker = market_cache.get_ticker(st.session_state.get("current_symbol", "BTCUSDT")) or {}
        default_price = float(ticker.get("last_price") or 0)
        with st.form("live_preview_form"):
            p1, p2 = st.columns(2)
            plan_symbol = p1.text_input("交易对象", value=st.session_state.get("current_symbol", "BTCUSDT")).upper()
            side = p2.selectbox("方向", ["buy", "sell"], format_func=lambda x: "买入 / 做多" if x == "buy" else "卖出 / 做空")
            p3, p4 = st.columns(2)
            price = p3.number_input("计划价格", min_value=0.0, value=float(default_price or 0), step=0.01)
            quantity = p4.number_input("计划数量", min_value=0.0, value=0.001, step=0.001, format="%.6f")
            p5, p6 = st.columns(2)
            stop_loss = p5.number_input("止损价", min_value=0.0, value=0.0, step=0.01)
            take_profit = p6.number_input("止盈价", min_value=0.0, value=0.0, step=0.01)
            preview_clicked = st.form_submit_button("生成订单预览", width="stretch")
        if preview_clicked:
            preview = create_live_order_preview({"symbol": plan_symbol, "side": side, "price": price, "quantity": quantity, "stop_loss": stop_loss, "take_profit": take_profit, "market_type": settings.get("market_type")})
            st.session_state["live_order_plan"] = preview.get("plan")
            st.session_state["live_order_preview"] = preview
        preview = st.session_state.get("live_order_preview")
        if preview:
            plan = preview.get("plan", {})
            render_metric_grid(
                [
                    ("预览状态", "通过" if preview.get("ok") else "失败", "green" if preview.get("ok") else "red"),
                    ("名义金额", f"{float(plan.get('notional', 0) or 0):.2f} USDT", "yellow"),
                    ("预计手续费", f"{float(plan.get('estimated_fee', 0) or 0):.4f} USDT", ""),
                    ("预计滑点", f"{float(plan.get('estimated_slippage', 0) or 0):.4f} USDT", ""),
                    ("最大亏损", f"{float(plan.get('max_loss', 0) or 0):.4f} USDT", "red"),
                    ("风险收益比", f"{float(plan.get('risk_reward_ratio', 0) or 0):.2f}", ""),
                    ("交易规则", "通过" if preview.get("rule_check", {}).get("ok") else "失败", "green" if preview.get("rule_check", {}).get("ok") else "red"),
                    ("执行说明", "仅预览，不下单", "yellow"),
                ]
            )
            for err in preview.get("rule_check", {}).get("errors", []) + preview.get("risk_errors", []):
                st.error(err)
            for warn in preview.get("rule_check", {}).get("warnings", []):
                st.warning(warn)

    with tabs[4]:
        plan = st.session_state.get("live_order_plan") or {"symbol": st.session_state.get("current_symbol", "BTCUSDT"), "price": float((market_cache.get_ticker(st.session_state.get("current_symbol", "BTCUSDT")) or {}).get("last_price") or 0), "quantity": 0.001, "side": "buy", "market_type": settings.get("market_type")}
        confirmed = st.checkbox("我确认这只是执行前检查，不代表真实下单")
        if st.button("运行执行前检查清单", width="stretch"):
            st.session_state["live_preflight"] = run_live_preflight_check(plan, user_confirmed=confirmed)
        preflight = st.session_state.get("live_preflight")
        if preflight:
            st.markdown(f"**检查结果：{preflight.get('message')}**")
            for item in preflight.get("checklist", []):
                if item.get("status") == "通过":
                    st.success(f"{item.get('name')}：{item.get('message')}")
                elif item.get("status") == "警告":
                    st.warning(f"{item.get('name')}：{item.get('message')}")
                else:
                    st.error(f"{item.get('name')}：{item.get('message')}")

    with tabs[5]:
        plan = st.session_state.get("live_order_plan") or {"symbol": st.session_state.get("current_symbol", "BTCUSDT"), "price": float((market_cache.get_ticker(st.session_state.get("current_symbol", "BTCUSDT")) or {}).get("last_price") or 0), "quantity": 0.001, "side": "buy", "market_type": settings.get("market_type")}
        c1, c2 = st.columns(2)
        if c1.button("运行 Dry-run 验证", width="stretch"):
            st.session_state["dry_run_result"] = run_test_order_validation(plan)
        if c2.button("运行 Testnet 预检", width="stretch"):
            st.session_state["dry_run_result"] = run_testnet_order_flow(plan)
        result = st.session_state.get("dry_run_result")
        if result:
            if result.get("ok"):
                st.success(result.get("message"))
            else:
                st.error(result.get("message"))

    with tabs[6]:
        exec_status = get_live_manual_execution_status()
        st.markdown(
            """
            <div class="app-shell"><div class="module-card warning-box">
              <b>小资金手动实盘风险提示</b><br>
              当前功能可能提交真实 Spot 订单，真实订单会使用真实资金。系统不会自动下单，任何真实订单都必须由你手动确认并输入确认短句。
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        render_metric_grid(
            [
                ("LIVE_TRADING_ENABLED", "true" if exec_status.get("live_trading_enabled") else "false", "green" if exec_status.get("live_trading_enabled") else "red"),
                ("Live Manual", "已开启" if exec_status.get("live_manual_enabled") else "未开启", "green" if exec_status.get("live_manual_enabled") else "red"),
                ("当前模式", str(exec_status.get("mode", "read_only")), "yellow"),
                ("安全锁", "已开启" if exec_status.get("kill_switch_enabled") else "未开启", "red" if exec_status.get("kill_switch_enabled") else "green"),
                ("单笔上限", f"{float(exec_status.get('max_live_notional_usdt', 0) or 0):.2f} USDT", "yellow"),
                ("硬上限", f"{float(exec_status.get('hard_max_live_notional_usdt', 0) or 0):.2f} USDT", "red"),
                ("今日已用", f"{float(exec_status.get('daily_live_notional', 0) or 0):.2f} USDT", ""),
                ("今日上限", f"{float(exec_status.get('daily_limit', 0) or 0):.2f} USDT", ""),
            ]
        )
        ticker = market_cache.get_ticker(st.session_state.get("current_symbol", "BTCUSDT")) or {}
        current_price = float(ticker.get("last_price") or 0)
        decision = build_committee_decision(st.session_state.get("current_symbol", "BTCUSDT"), ticker)
        external_ai = decision.get("external_ai") or {}
        with st.form("live_manual_plan_form"):
            c1, c2 = st.columns(2)
            symbol = c1.text_input("交易对象", value=st.session_state.get("current_symbol", "BTCUSDT")).upper()
            side = c2.selectbox("买卖方向", ["BUY", "SELL"], format_func=lambda x: "买入 BUY" if x == "BUY" else "卖出 SELL")
            c0a, c0b = st.columns(2)
            market_type = c0a.selectbox("市场类型", ["spot", "futures"], index=0 if settings.get("market_type", "spot") == "spot" else 1, format_func=lambda x: "现货 Spot" if x == "spot" else "U本位永续合约")
            leverage = c0b.slider("合约杠杆", 1, 125, int(settings.get("max_leverage", 5) or 5), help="现货订单会忽略杠杆；合约订单提交前会同步 Binance 杠杆。")
            c3, c4 = st.columns(2)
            order_type = c3.selectbox("订单类型", ["LIMIT", "MARKET"], format_func=lambda x: "限价 LIMIT" if x == "LIMIT" else "市价 MARKET（需额外谨慎）")
            price = c4.number_input("计划价格", min_value=0.0, value=float(current_price or 0), step=0.01)
            c5, c6 = st.columns(2)
            quote_amount = c5.number_input("用户选择金额 USDT", min_value=0.0, max_value=50.0, value=min(float(settings.get("system_suggested_live_amount_usdt", 5) or 5), float(settings.get("max_live_notional_usdt", 10) or 10)), step=1.0)
            default_qty = quote_amount * (leverage if market_type == "futures" else 1) / price if price else 0.0
            quantity = c6.number_input("计划数量", min_value=0.0, value=default_qty, step=0.000001, format="%.8f")
            source = st.selectbox("订单来源", ["AI交易委员会", "手动订单"], index=0)
            submitted_plan = st.form_submit_button("生成小资金订单计划", width="stretch")
        if submitted_plan:
            signal = committee_decision_to_sim_signal(decision) if source == "AI交易委员会" else {"source": "手动订单", "symbol": symbol}
            plan = create_live_order_plan(signal, {"symbol": symbol, "market_type": market_type, "leverage": leverage, "side": side, "order_type": order_type, "price": price, "quantity": quantity, "quote_amount": quote_amount, "source": source})
            st.session_state["live_manual_plan"] = plan
            st.session_state.pop("live_manual_test_order", None)
            st.session_state.pop("live_manual_preflight", None)
            st.success("订单计划已生成。")

        plan = st.session_state.get("live_manual_plan")
        if plan:
            validation = validate_live_order_plan(plan)
            preview = create_live_order_preview(plan)
            st.session_state["live_manual_preview"] = preview
            render_metric_grid(
                [
                    ("计划状态", str(plan.get("status", "draft")), "yellow"),
                    ("交易对象", str(plan.get("symbol", "-")), ""),
                    ("市场", "U本位合约" if plan.get("market_type") == "futures" else "现货", "blue"),
                    ("杠杆", f"{int(plan.get('leverage', 1) or 1)}x", "yellow" if plan.get("market_type") == "futures" else ""),
                    ("方向", str(plan.get("side", "-")), "green" if plan.get("side") == "BUY" else "red"),
                    ("类型", str(plan.get("order_type", "-")), ""),
                    ("用户金额", f"{float(plan.get('user_selected_amount', 0) or 0):.2f} USDT", "yellow"),
                    ("系统建议", f"{float(plan.get('system_suggested_amount', 0) or 0):.2f} USDT", ""),
                    ("风控最大", f"{float(plan.get('risk_max_amount', 0) or 0):.2f} USDT", "red"),
                    ("人工干预", "是" if plan.get("manual_override") else "否", "yellow" if plan.get("manual_override") else "green"),
                ]
            )
            if plan.get("manual_override"):
                st.warning("你选择的真实订单金额高于系统建议金额。该行为将被记录为人工干预，并在复盘中单独标记。")
            for row_name, airow in [("DeepSeek", external_ai.get("deepseek") or {}), ("Gemini", external_ai.get("gemini") or {})]:
                st.caption(f"{row_name}影子意见：{airow.get('vote', '观望')}｜风险 {airow.get('risk_level', '中')}｜建议 {airow.get('suggested_adjustment', '不调整')}｜{safe_committee_text(airow.get('summary', '暂无'), 260)}")
            if any((external_ai.get(k) or {}).get("soft_veto") for k in ["deepseek", "gemini"]):
                st.warning("外部AI存在风险提醒，继续操作将被记录为人工风险确认。")
            if not validation.get("ok"):
                for err in validation.get("errors", []):
                    st.error(err)
            for warn in validation.get("warnings", []):
                st.warning(warn)
            st.markdown("**订单预览：这只是订单预览，尚未执行真实订单。**")
            if preview:
                p = preview.get("plan", {})
                st.markdown(f"名义金额：{float(p.get('notional',0) or 0):.2f} USDT｜预计手续费：{float(p.get('estimated_fee',0) or 0):.4f}｜滑点估算：{float(p.get('estimated_slippage',0) or 0):.4f}｜规则：{'通过' if preview.get('rule_check',{}).get('ok') else '失败'}")
            test_label = "执行 Futures Test Order 验证" if plan.get("market_type") == "futures" else "执行 Spot Test Order 验证"
            if st.button(test_label, width="stretch"):
                st.session_state["live_manual_test_order"] = run_futures_test_order(plan) if plan.get("market_type") == "futures" else run_spot_test_order(plan)
                st.rerun()
            test_order = st.session_state.get("live_manual_test_order") or {}
            if test_order:
                (st.success if test_order.get("ok") else st.error)(test_order.get("message"))
            st.markdown("**人工确认区**")
            manual_confirmed = st.checkbox("我确认这是小资金真实订单，并理解风险", key="live_manual_confirmed")
            phrase = st.text_input("请输入确认短句：我确认执行小资金实盘订单", key="live_manual_phrase")
            if st.button("运行小资金实盘执行前检查", width="stretch"):
                st.session_state["live_manual_preflight"] = run_live_futures_preflight(plan, test_order, manual_confirmed, phrase) if plan.get("market_type") == "futures" else run_live_manual_preflight(plan, test_order, manual_confirmed, phrase)
            preflight = st.session_state.get("live_manual_preflight") or {}
            if preflight:
                st.markdown(f"**检查结果：{preflight.get('message')}**")
                for item in preflight.get("checklist", []):
                    if item.get("status") == "通过":
                        st.success(f"{item.get('name')}：{item.get('message')}")
                    else:
                        st.error(f"{item.get('name')}：{item.get('message')}")
            can_submit = bool(preflight.get("ok"))
            submit_label = "提交真实 U本位合约小资金订单" if plan.get("market_type") == "futures" else "提交真实 Spot 小资金订单"
            if st.button(submit_label, disabled=not can_submit, width="stretch"):
                st.session_state["live_manual_submit_result"] = submit_live_futures_order(plan, test_order, manual_confirmed, phrase) if plan.get("market_type") == "futures" else submit_live_spot_order(plan, test_order, manual_confirmed, phrase)
                st.rerun()
            submit_result = st.session_state.get("live_manual_submit_result") or {}
            if submit_result:
                (st.success if submit_result.get("ok") else st.error)(submit_result.get("message"))

        st.markdown("**真实订单记录 / 状态回查 / 手动撤单**")
        for row in load_live_order_records(20):
            order_id = str(row.get("order_id", ""))
            symbol = str(row.get("symbol", ""))
            with st.expander(f"{row.get('time')}｜{symbol}｜{row.get('side')}｜{row.get('order_status')}｜{order_id}", expanded=False):
                st.markdown(kline_symbol_link(symbol, f"查看 {symbol} K线", "watch-pill"), unsafe_allow_html=True)
                st.markdown(f"金额：{row.get('notional')}｜数量：{row.get('quantity')}｜来源：{row.get('source')}｜人工干预：{row.get('manual_override')}")
                rc1, rc2 = st.columns(2)
                if rc1.button("回查订单状态", key=f"fetch_live_order_{order_id}", width="stretch", disabled=not order_id):
                    st.session_state[f"live_order_status_{order_id}"] = fetch_live_order_status(order_id, symbol)
                cancel_confirm = rc2.checkbox("我确认撤销该真实订单", key=f"cancel_confirm_{order_id}", disabled=not order_id)
                if rc2.button("撤销真实订单", key=f"cancel_live_order_{order_id}", width="stretch", disabled=not order_id or not cancel_confirm):
                    st.session_state[f"live_order_status_{order_id}"] = cancel_live_order(order_id, symbol, cancel_confirm)
                status_result = st.session_state.get(f"live_order_status_{order_id}")
                if status_result:
                    (st.success if status_result.get("ok") else st.warning)(status_result.get("message"))

    with tabs[7]:
        st.markdown(
            """
            <div class="app-shell"><div class="module-card warning-box">
              <b>小资金实盘持仓管理提示</b><br>
              当前为真实 Spot 持仓只读识别与人工确认平仓功能。系统不会自动卖出真实资产，任何平仓或减仓都必须由你手动确认并输入确认短句。
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        records = load_live_order_records(300)
        symbols_for_price = sorted({str(row.get("symbol", "")).upper() for row in records if row.get("symbol")})
        current_prices: dict[str, float] = {}
        for sym in symbols_for_price:
            ticker_row = market_cache.get_ticker(sym) or {}
            current_prices[sym] = float(ticker_row.get("last_price") or 0)
        live_pos_summary = get_live_position_summary(current_prices)
        render_metric_grid(
            [
                ("系统实盘持仓", str(live_pos_summary.get("system_position_count", 0)), "green" if live_pos_summary.get("system_position_count", 0) else "yellow"),
                ("外部资产", str(live_pos_summary.get("external_asset_count", 0)), "yellow"),
                ("浮动盈亏估算", f"{float(live_pos_summary.get('total_unrealized_pnl', 0) or 0):+.4f} USDT", "green" if float(live_pos_summary.get("total_unrealized_pnl", 0) or 0) >= 0 else "red"),
                ("安全锁", "已开启" if settings.get("kill_switch_enabled") else "未开启", "red" if settings.get("kill_switch_enabled") else "green"),
                ("Live Manual", "已开启" if settings.get("live_manual_enabled") else "未开启", "green" if settings.get("live_manual_enabled") else "red"),
                ("真实提交", "允许检查" if exec_status.get("live_trading_enabled") else "默认关闭", "green" if exec_status.get("live_trading_enabled") else "red"),
            ]
        )

        system_positions = live_pos_summary.get("open_system_positions") or []
        if not system_positions:
            st.info("当前暂无系统实盘持仓。外部资产会在下方只读显示，不纳入系统策略统计。")
        for pos in system_positions:
            pos_id = str(pos.get("live_position_id", ""))
            symbol = str(pos.get("symbol", ""))
            review = generate_live_position_review_snapshot(pos)
            pnl = float(pos.get("unrealized_pnl", 0) or 0)
            pnl_pct = float(pos.get("unrealized_pnl_pct", 0) or 0)
            with st.expander(f"{symbol} 实盘现货持仓｜{pos.get('status')}｜浮盈 {pnl:+.4f} USDT / {pnl_pct:+.2f}%", expanded=True):
                st.markdown(kline_symbol_link(symbol, f"查看 {symbol} K线", "watch-pill"), unsafe_allow_html=True)
                render_metric_grid(
                    [
                        ("来源", "系统订单", "green"),
                        ("买入均价", f"{float(pos.get('avg_entry_price', 0) or 0):.8f}", ""),
                        ("当前价格", f"{float(pos.get('current_price', 0) or 0):.8f}", ""),
                        ("原始数量", f"{float(pos.get('original_quantity', 0) or 0):.8f}", ""),
                        ("剩余数量", f"{float(pos.get('remaining_quantity', 0) or 0):.8f}", "yellow"),
                        ("成本", f"{float(pos.get('quote_cost', 0) or 0):.4f} {pos.get('quote_asset', 'USDT')}", ""),
                        ("已实现盈亏", f"{float(pos.get('realized_pnl', 0) or 0):+.4f}", "green" if float(pos.get("realized_pnl", 0) or 0) >= 0 else "red"),
                        ("风险", str(pos.get("risk_level", "低")), "red" if pos.get("risk_level") in {"高", "极高"} else "yellow"),
                    ]
                )
                st.markdown(
                    f"""
                    <div class="status-card">
                      <b>持仓复核</b><br>
                      AI建议：{escape(str(review.get("hold_decision", "继续观察")))}｜
                      风险等级：{escape(str(review.get("risk_level", "低")))}｜
                      自动动作：{escape(str(review.get("auto_action", "none")))}<br>
                      系统仅提醒，不会自动卖出真实资产。
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                c1, c2, c3, c4 = st.columns(4)
                ratio_label = c1.selectbox("平仓比例", ["25%", "50%", "75%", "100%"], index=1, key=f"exit_ratio_{pos_id}")
                order_type = c2.selectbox("平仓类型", ["LIMIT", "MARKET"], key=f"exit_type_{pos_id}", format_func=lambda x: "限价 LIMIT" if x == "LIMIT" else "市价 MARKET（注意滑点）")
                default_exit_price = float(pos.get("current_price", 0) or pos.get("avg_entry_price", 0) or 0)
                exit_price = c3.number_input("平仓价格", min_value=0.0, value=default_exit_price, step=0.01, key=f"exit_price_{pos_id}")
                exit_reason = c4.selectbox("平仓原因", ["用户手动", "建议止盈", "风险升高", "信号失效"], key=f"exit_reason_{pos_id}")
                if st.button("生成平仓预览", key=f"create_exit_plan_{pos_id}", width="stretch"):
                    ratio = float(ratio_label.strip("%")) / 100
                    st.session_state[f"live_exit_plan_{pos_id}"] = create_live_exit_plan(pos, {"exit_ratio": ratio, "order_type": order_type, "price": exit_price, "exit_reason": exit_reason})
                    st.session_state.pop(f"live_exit_test_{pos_id}", None)
                    st.session_state.pop(f"live_exit_preflight_{pos_id}", None)
                    st.rerun()

                exit_plan = st.session_state.get(f"live_exit_plan_{pos_id}")
                if exit_plan:
                    exit_preview = preview_live_exit_order(exit_plan)
                    ep = exit_preview.get("exit_plan", {})
                    st.markdown(
                        f"""
                        <div class="status-card">
                          <b>平仓预览：尚未执行真实卖出</b><br>
                          本次平仓：{float(ep.get("exit_ratio", 0) or 0) * 100:.0f}%｜
                          数量：{float(ep.get("exit_quantity", 0) or 0):.8f}｜
                          预计成交额：{float(ep.get("estimated_value", 0) or 0):.4f} USDT｜
                          预计手续费：{float(ep.get("estimated_fee", 0) or 0):.4f}｜
                          预计盈亏：{float(ep.get("estimated_pnl", 0) or 0):+.4f} USDT<br>
                          平仓后剩余数量：{float(exit_preview.get("remaining_after_exit", 0) or 0):.8f}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    validation = exit_preview.get("validation") or {}
                    for err in validation.get("errors", []):
                        st.error(err)
                    for warn in validation.get("warnings", []):
                        st.warning(warn)
                    if ep.get("order_type") == "MARKET":
                        st.warning("市价平仓可能产生滑点，实际成交价格可能与当前价格不同。")
                    if float(ep.get("exit_ratio", 0) or 0) >= 0.999:
                        st.warning("该操作将尝试卖出该系统持仓的全部剩余数量。")
                    if st.button("执行平仓 Spot Test Order 验证", key=f"exit_test_{pos_id}", width="stretch"):
                        st.session_state[f"live_exit_test_{pos_id}"] = run_exit_spot_test_order(exit_plan)
                        st.rerun()
                    test_result = st.session_state.get(f"live_exit_test_{pos_id}") or {}
                    if test_result:
                        (st.success if test_result.get("ok") else st.error)(test_result.get("message"))
                    st.markdown("**平仓人工确认区**")
                    confirmed = st.checkbox("我确认这是小资金真实平仓订单，并理解风险", key=f"exit_confirm_{pos_id}")
                    phrase = st.text_input("请输入确认短句：我确认执行小资金实盘平仓", key=f"exit_phrase_{pos_id}")
                    if st.button("运行平仓执行前检查", key=f"exit_preflight_{pos_id}", width="stretch"):
                        st.session_state[f"live_exit_preflight_{pos_id}"] = run_live_exit_preflight(exit_plan, test_result, confirmed, phrase)
                    exit_preflight = st.session_state.get(f"live_exit_preflight_{pos_id}") or {}
                    if exit_preflight:
                        st.markdown(f"**检查结果：{exit_preflight.get('message')}**")
                        for item in exit_preflight.get("checklist", []):
                            if item.get("status") == "通过":
                                st.success(f"{item.get('name')}：{item.get('message')}")
                            else:
                                st.error(f"{item.get('name')}：{item.get('message')}")
                    if st.button("提交真实 Spot 平仓订单", key=f"submit_exit_{pos_id}", disabled=not bool(exit_preflight.get("ok")), width="stretch"):
                        st.session_state[f"live_exit_submit_{pos_id}"] = submit_live_exit_order(exit_plan, test_result, confirmed, phrase)
                        st.rerun()
                    submit_exit = st.session_state.get(f"live_exit_submit_{pos_id}") or {}
                    if submit_exit:
                        (st.success if submit_exit.get("ok") else st.error)(submit_exit.get("message"))

        st.markdown("**外部持仓 / 不明资产（只读）**")
        external_assets = live_pos_summary.get("external_assets") or []
        if not external_assets:
            st.info("当前暂无外部持仓或不明来源资产。")
        for asset in external_assets[:30]:
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(asset.get("asset", "-")))}</b>｜来源：{escape(str(asset.get("source", "external")))}<br>
                  可用：{float(asset.get("free", 0) or 0):.8f}｜锁定：{float(asset.get("locked", 0) or 0):.8f}｜总量：{float(asset.get("total", 0) or 0):.8f}<br>
                  {escape(str(asset.get("message", "外部持仓只读显示。")))}
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("**实盘持仓审计日志**")
        position_audit = load_live_position_audit_log(30)
        if not position_audit:
            st.info("暂无实盘持仓审计日志。")
        for event in position_audit:
            st.caption(f"{event.get('time')}｜{event.get('event')}｜{event.get('symbol')}｜{event.get('result')}｜{event.get('reason')}")

    with tabs[8]:
        st.markdown(
            """
            <div class="app-shell"><div class="module-card warning-box">
              <b>小资金自动实盘试运行提示</b><br>
              LIVE_AUTO_PILOT 是小资金全自动试运行层。默认关闭，只有通过准入检查、白名单、额度限制、冷却、熔断、交易所规则和对应 Test Order 后，才允许提交极小资金真实 Spot 或 U本位合约订单。
            </div></div>
            """,
            unsafe_allow_html=True,
        )
        auto_config = load_live_auto_config()
        auto_symbols = set(auto_config.get("allowed_symbols") or [])
        auto_positions_snapshot = get_live_auto_status().get("open_positions") or []
        for pos in auto_positions_snapshot:
            if pos.get("symbol"):
                auto_symbols.add(str(pos.get("symbol")).upper())
        auto_prices: dict[str, float] = {}
        for sym in sorted(auto_symbols):
            ticker_row = market_cache.get_ticker(sym) or {}
            auto_prices[sym] = float(ticker_row.get("last_price") or 0)
        auto_status = get_live_auto_status(auto_prices)
        auto_config = auto_status.get("config") or auto_config
        auto_review = auto_status.get("review") or {}
        render_metric_grid(
            [
                ("自动模式", "开启" if auto_status.get("enabled") else "关闭", "green" if auto_status.get("enabled") else "red"),
                ("自动下单", "开启" if auto_status.get("order_enabled") else "关闭", "green" if auto_status.get("order_enabled") else "yellow"),
                ("自动止盈止损", "开启" if auto_status.get("exit_enabled") else "关闭", "green" if auto_status.get("exit_enabled") else "yellow"),
                ("暂停状态", "已暂停" if auto_status.get("paused") else "运行/待命", "yellow" if auto_status.get("paused") else "green"),
                ("熔断", "已触发" if auto_status.get("circuit_breaker_enabled") else "未触发", "red" if auto_status.get("circuit_breaker_enabled") else "green"),
                ("今日额度", f"{float(auto_status.get('daily_used_usdt', 0) or 0):.2f} / {float(auto_config.get('daily_limit_usdt', 20) or 20):.2f} USDT", "yellow"),
                ("单笔上限", f"{float(auto_config.get('max_order_usdt', 5) or 5):.2f} USDT", "yellow"),
                ("自动持仓", str(len(auto_status.get("open_positions") or [])), "yellow"),
            ]
        )
        if auto_status.get("circuit_breaker_reason"):
            st.error(f"自动实盘熔断原因：{auto_status.get('circuit_breaker_reason')}")

        cfg_col, ctl_col = st.columns(2)
        with cfg_col:
            st.markdown("**自动试运行配置**")
            with st.form("live_auto_config_form"):
                new_config = dict(auto_config)
                symbols_text = st.text_input("自动实盘白名单", value=",".join(auto_config.get("allowed_symbols") or ["BTCUSDT", "ETHUSDT"]))
                new_config["allowed_symbols"] = [s.strip().upper() for s in symbols_text.split(",") if s.strip()]
                new_config["max_order_usdt"] = st.number_input("单笔自动真实订单上限 USDT", min_value=1.0, max_value=50.0, value=float(auto_config.get("max_order_usdt", 5) or 5), step=1.0)
                new_config["daily_limit_usdt"] = st.number_input("单日自动真实额度上限 USDT", min_value=5.0, max_value=200.0, value=float(auto_config.get("daily_limit_usdt", 20) or 20), step=5.0)
                new_config["max_positions"] = st.number_input("最大同时自动真实持仓", min_value=1, max_value=5, value=int(auto_config.get("max_positions", 1) or 1), step=1)
                new_config["allow_futures"] = st.checkbox("允许 U本位永续合约自动实盘", value=bool(auto_config.get("allow_futures", True)))
                market_options = ["spot", "futures"]
                market_index = market_options.index(str(auto_config.get("default_market_type", "futures"))) if str(auto_config.get("default_market_type", "futures")) in market_options else 1
                new_config["default_market_type"] = st.selectbox("默认自动实盘市场", market_options, index=market_index, format_func=lambda x: "现货 Spot" if x == "spot" else "U本位永续合约")
                new_config["default_leverage"] = st.slider("实盘合约默认杠杆", 1, 20, int(auto_config.get("default_leverage", 5) or 5), help="系统会在下合约订单前同步 Binance 当前交易对杠杆。")
                new_config["max_leverage"] = st.slider("实盘合约最大允许杠杆", 1, 125, int(auto_config.get("max_leverage", 20) or 20))
                new_config["global_cooldown_minutes"] = st.number_input("全局冷却分钟", min_value=1, max_value=240, value=int(auto_config.get("global_cooldown_minutes", 15) or 15), step=1)
                new_config["symbol_cooldown_minutes"] = st.number_input("同币种冷却分钟", min_value=5, max_value=720, value=int(auto_config.get("symbol_cooldown_minutes", 60) or 60), step=5)
                new_config["allow_market_order"] = st.checkbox("允许自动市价单（默认不建议）", value=bool(auto_config.get("allow_market_order")))
                new_config["live_auto_order_enabled"] = st.checkbox("允许自动小资金真实下单", value=bool(auto_config.get("live_auto_order_enabled")))
                exit_phrase = st.text_input("开启自动止盈止损确认短句", placeholder="我确认开启小资金自动止盈止损")
                requested_exit = st.checkbox("允许自动止盈/止损试运行", value=bool(auto_config.get("live_auto_exit_enabled")))
                new_config["live_auto_exit_enabled"] = bool(requested_exit and exit_phrase.strip() == "我确认开启小资金自动止盈止损")
                new_config["take_profit_pct"] = st.number_input("止盈阈值 %", min_value=0.1, max_value=20.0, value=float(auto_config.get("take_profit_pct", 1.5) or 1.5), step=0.1)
                new_config["stop_loss_pct"] = st.number_input("止损阈值 %", min_value=-20.0, max_value=-0.1, value=float(auto_config.get("stop_loss_pct", -1.0) or -1.0), step=0.1)
                if st.form_submit_button("保存自动试运行配置", width="stretch"):
                    save_live_auto_config(new_config)
                    st.success("自动试运行配置已保存。自动实盘仍默认受准入检查和总开关限制。")
                    st.rerun()
                if requested_exit and exit_phrase.strip() != "我确认开启小资金自动止盈止损":
                    st.caption("自动止盈止损未开启：确认短句不匹配。")

        with ctl_col:
            st.markdown("**手机端控制台 / 总开关**")
            confirm_phrase = st.text_input("开启/恢复确认短句", placeholder="我确认开启小资金自动实盘试运行", key="live_auto_confirm_phrase")
            c1, c2 = st.columns(2)
            if c1.button("开启自动试运行", width="stretch"):
                result = enable_live_auto_pilot(confirm_phrase)
                (st.success if result.get("ok") else st.error)(result.get("message"))
                if not result.get("ok") and result.get("admission"):
                    st.session_state["live_auto_last_admission"] = result.get("admission")
                st.rerun()
            if c2.button("恢复自动试运行", width="stretch"):
                result = resume_live_auto_pilot(confirm_phrase)
                (st.success if result.get("ok") else st.error)(result.get("message"))
                st.rerun()
            p1, p2 = st.columns(2)
            if p1.button("暂停自动试运行", width="stretch"):
                pause_live_auto_pilot("用户在自动试运行控制台暂停。")
                st.rerun()
            if p2.button("关闭自动试运行", width="stretch"):
                disable_live_auto_pilot("用户在自动试运行控制台关闭。")
                st.rerun()
            breaker_reason = st.text_input("熔断/紧急停止原因", value="用户手动触发自动实盘熔断。")
            b1, b2 = st.columns(2)
            if b1.button("触发自动熔断", width="stretch"):
                trigger_live_auto_circuit_breaker(breaker_reason)
                st.rerun()
            release_phrase = b2.text_input("解除熔断短句", placeholder="我确认解除自动实盘熔断")
            if b2.button("解除自动熔断", width="stretch"):
                result = release_live_auto_circuit_breaker(release_phrase)
                (st.success if result.get("ok") else st.error)(result.get("message"))
                st.rerun()

        st.markdown("**自动实盘准入检查**")
        if st.button("运行准入检查", width="stretch"):
            st.session_state["live_auto_last_admission"] = run_live_auto_admission_check(user_confirmed=False)
        admission = st.session_state.get("live_auto_last_admission") or auto_status.get("admission") or {}
        for item in admission.get("checks", []):
            if item.get("ok"):
                st.success(f"{item.get('name')}：{item.get('message')}")
            else:
                st.error(f"{item.get('name')}：{item.get('message')}")

        st.markdown("**当前交易对象自动实盘信号检查**")
        current_symbol = st.session_state.get("current_symbol", "BTCUSDT")
        current_ticker = market_cache.get_ticker(current_symbol) or {}
        decision = build_committee_decision(current_symbol, current_ticker)
        live_auto_signal = committee_decision_to_sim_signal(decision)
        live_auto_signal["symbol"] = current_symbol
        live_auto_signal["current_price"] = float(current_ticker.get("last_price") or 0)
        live_auto_signal["data_quality"] = "good" if current_ticker else "poor"
        is_signal_ok, signal_reasons = filter_live_auto_signal(live_auto_signal)
        render_metric_grid(
            [
                ("交易对象", current_symbol, "yellow"),
                ("最终动作", str(live_auto_signal.get("action", "-")), ""),
                ("委员会置信度", str(live_auto_signal.get("committee_confidence", 0)), "green" if float(live_auto_signal.get("committee_confidence") or 0) >= 70 else "yellow"),
                ("风险评分", format_score(live_auto_signal.get("risk_score")), "green" if safe_compare_lt(live_auto_signal.get("risk_score"), 55.01) else "red"),
                ("数据质量", str(live_auto_signal.get("data_quality", "poor")), "green" if live_auto_signal.get("data_quality") == "good" else "red"),
                ("过滤结果", "可生成自动计划" if is_signal_ok else "拒绝自动实盘", "green" if is_signal_ok else "red"),
            ]
        )
        if signal_reasons:
            for reason in signal_reasons:
                st.warning(reason)
            st.caption("不满足自动交易条件的信号不会丢弃，会保留在观察/自动候选中等待下一轮复核。")
        sig_c1, sig_c2 = st.columns(2)
        if sig_c1.button("生成自动订单计划", disabled=not is_signal_ok, width="stretch"):
            st.session_state["live_auto_order_plan"] = create_live_auto_order_plan(live_auto_signal)
            st.rerun()
        if sig_c2.button("重新刷新自动信号", width="stretch"):
            st.session_state["live_auto_signal"] = live_auto_signal
            st.success("已刷新自动交易候选信号。")

        auto_plan = st.session_state.get("live_auto_order_plan")
        if auto_plan:
            st.markdown("**自动订单计划 / 执行前检查**")
            render_metric_grid(
                [
                    ("计划ID", str(auto_plan.get("auto_plan_id", "-")), ""),
                    ("交易对象", str(auto_plan.get("symbol", "-")), "yellow"),
                    ("方向", str(auto_plan.get("side", "-")), "green"),
                    ("类型", str(auto_plan.get("order_type", "-")), ""),
                    ("价格", f"{float(auto_plan.get('price', 0) or 0):.8f}", ""),
                    ("金额", f"{float(auto_plan.get('quote_amount', 0) or 0):.2f} USDT", "yellow"),
                ]
            )
            st.caption("该计划不是订单。点击执行前，仍需通过自动试运行开关、白名单、额度、交易所规则和对应 Test Order。")
            if st.button("执行自动试运行订单（极小资金）", width="stretch"):
                st.session_state["live_auto_execute_result"] = execute_live_auto_spot_order(auto_plan)
                st.rerun()
            auto_exec = st.session_state.get("live_auto_execute_result") or {}
            if auto_exec:
                (st.success if auto_exec.get("ok") else st.error)(auto_exec.get("message"))
                if auto_exec.get("converted_to_approval"):
                    st.warning("自动执行未通过，系统已保留信号等待下一轮复核。")
                preflight = auto_exec.get("preflight") or {}
                for item in preflight.get("checks", []):
                    st.caption(f"{'通过' if item.get('ok') else '失败'}｜{item.get('name')}｜{item.get('message')}")

        st.markdown("**自动实盘持仓监控**")
        open_auto_positions = auto_status.get("open_positions") or []
        if not open_auto_positions:
            st.info("当前暂无自动实盘持仓。")
        for pos in open_auto_positions:
            exit_check = run_live_auto_exit_check(pos)
            with st.expander(f"{pos.get('symbol')}｜{pos.get('status')}｜浮盈 {float(pos.get('unrealized_pnl', 0) or 0):+.4f} USDT / {float(pos.get('unrealized_pnl_pct', 0) or 0):+.2f}%", expanded=False):
                render_metric_grid(
                    [
                        ("入场价", f"{float(pos.get('entry_price', 0) or 0):.8f}", ""),
                        ("当前价", f"{float(pos.get('current_price', 0) or 0):.8f}", ""),
                        ("数量", f"{float(pos.get('quantity', 0) or 0):.8f}", ""),
                        ("金额", f"{float(pos.get('quote_amount', 0) or 0):.2f} USDT", "yellow"),
                        ("退出检查", str(exit_check.get("action", "-")), "red" if exit_check.get("ok") else "yellow"),
                        ("处理方式", str(exit_check.get("message", "")), ""),
                    ]
                )
                st.caption("自动退出默认关闭。即使开启，当前版本仍以极小仓位和审计优先；异常时只提醒不卖出。")

        st.markdown("**自动实盘复盘统计**")
        render_metric_grid(
            [
                ("自动订单数", str(auto_review.get("auto_order_count", 0)), ""),
                ("成功数", str(auto_review.get("auto_success_count", 0)), "green"),
                ("失败数", str(auto_review.get("auto_failure_count", 0)), "red"),
                ("熔断次数", str(auto_review.get("circuit_breaker_count", 0)), "red"),
            ]
        )
        st.info(auto_review.get("sample_warning", "自动实盘样本不足，暂不建议扩大额度或进入正式自动交易。"))

        st.markdown("**自动实盘审计日志**")
        auto_audit = load_live_auto_audit_log(50)
        if not auto_audit:
            st.info("暂无自动实盘审计日志。")
        for event in auto_audit:
            st.caption(f"{event.get('time')}｜{event.get('event')}｜{event.get('symbol')}｜{event.get('result')}｜{event.get('reason')}")

    with tabs[9]:
        sim_stats = status.get("sim_stats") or {}
        replay_summary = status.get("replay_summary") or {}
        st.markdown(f"模拟交易次数：{sim_stats.get('total_trades', 0)}｜模拟胜率：{float(sim_stats.get('win_rate', 0) or 0):.2f}%｜Profit Factor：{float(sim_stats.get('profit_factor', 0) or 0):.2f}｜最大回撤：{float(sim_stats.get('max_drawdown', 0) or 0):.2f}%")
        st.markdown(f"复盘数据质量：{replay_summary.get('data_quality', 'poor')}｜{replay_summary.get('sample_warning', '')}")
        allowed = status.get("allowed_strategy_candidates") or []
        all_candidates = status.get("strategy_candidates") or []
        if not all_candidates:
            st.info("暂无策略工厂候选策略。")
        for candidate in all_candidates[:30]:
            is_allowed = candidate in allowed
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(candidate.get("strategy_name")))}</b>｜评级：{escape(str(candidate.get("grade")))}｜过拟合：{escape(str(candidate.get("overfit_risk")))}<br>
                  实盘准入：{'允许进入后续审查' if is_allowed else '拒绝'}<br>
                  原因：{'候选策略评级和过拟合风险满足模拟验证入口。' if is_allowed else '该策略尚未完成足够模拟验证，或评级/过拟合风险不达标。'}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[10]:
        audit = status.get("recent_audit") or []
        if not audit:
            st.info("暂无实盘安全审计日志。")
        for event in audit[:100]:
            st.caption(f"{event.get('time')}｜{event.get('event')}｜{event.get('mode')}｜{event.get('symbol')}｜{event.get('result')}｜{event.get('reason')}")

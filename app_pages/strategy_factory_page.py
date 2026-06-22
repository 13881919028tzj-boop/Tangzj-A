"""Strategy factory and backtest page."""

from __future__ import annotations

from html import escape
from typing import Any

import streamlit as st

from components.ui import render_metric_grid, render_page_head
from services.backtest_engine import (
    compare_strategy_results,
    export_strategy_report,
    load_backtest_results,
    run_backtest,
    run_parameter_grid_search,
)
from services.strategy_factory import (
    create_strategy_candidate,
    get_available_strategies,
    get_replay_optimization_hints,
    get_strategy_candidates,
    get_strategy_config,
    reset_strategy_config,
    save_strategy_config,
)


def render_strategy_factory(page_titles: dict[str, tuple[str, str]], version: str, fallback_symbols: list[str]) -> None:
    """策略工厂 + 回测中心。"""
    render_page_head("strategy", page_titles, version)
    strategies = get_available_strategies()
    results = load_backtest_results()
    candidates = get_strategy_candidates()
    replay_hints = get_replay_optimization_hints()
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>策略研究安全提示</b><br>
            当前为策略研究与历史回测，不会执行真实订单。历史回测结果不代表未来收益，候选策略只能进入模拟验证。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    tabs = st.tabs(["策略库", "策略配置", "回测中心", "回测结果", "参数优化", "策略对比", "候选策略", "复盘建议"])

    strategy_names = {s["strategy_id"]: s["strategy_name"] for s in strategies}
    default_strategy_id = strategies[0]["strategy_id"] if strategies else ""

    with tabs[0]:
        if not strategies:
            st.warning("策略库暂不可用。")
        for strategy in strategies:
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(strategy.get("strategy_name")))}</b>｜{escape(str(strategy.get("strategy_type")))}｜风险：{escape(str(strategy.get("risk_profile")))}<br>
                  支持周期：{escape(" / ".join(strategy.get("supported_timeframes", [])))}｜市场：{escape(" / ".join(strategy.get("supported_markets", [])))}<br>
                  {escape(str(strategy.get("description", "")))}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[1]:
        strategy_id = st.selectbox("选择策略", list(strategy_names.keys()), format_func=lambda sid: strategy_names.get(sid, sid), key="factory_config_strategy") if strategies else default_strategy_id
        config = get_strategy_config(strategy_id) if strategy_id else {}
        with st.form("strategy_config_form"):
            st.caption("参数修改只保存为策略配置版本，不会自动修改生产策略。")
            new_config: dict[str, Any] = {}
            for key, value in config.items():
                label = key.replace("_", " ")
                if isinstance(value, bool):
                    new_config[key] = st.checkbox(label, value=value)
                elif isinstance(value, int):
                    new_config[key] = st.number_input(label, value=int(value), step=1)
                elif isinstance(value, float):
                    new_config[key] = st.number_input(label, value=float(value), step=0.1)
                else:
                    new_config[key] = st.text_input(label, value=str(value))
            c1, c2 = st.columns(2)
            if c1.form_submit_button("保存策略配置", width="stretch"):
                save_strategy_config(strategy_id, new_config)
                st.success("策略配置已保存，仅用于回测和候选策略。")
                st.rerun()
            if c2.form_submit_button("重置默认参数", width="stretch"):
                reset_strategy_config(strategy_id)
                st.success("已恢复默认参数。")
                st.rerun()

    with tabs[2]:
        with st.form("backtest_form"):
            c1, c2 = st.columns(2)
            bt_strategy = c1.selectbox("策略", list(strategy_names.keys()), format_func=lambda sid: strategy_names.get(sid, sid), key="bt_strategy") if strategies else default_strategy_id
            symbol = c2.selectbox("交易对象", fallback_symbols, index=0)
            c3, c4, c5 = st.columns(3)
            timeframe = c3.selectbox("周期", ["5m", "15m", "1h", "4h", "1d"], index=1)
            period_days = c4.selectbox("回测范围", [7, 30, 90, 180, 365], index=1, format_func=lambda d: f"最近{d}天")
            initial_balance = c5.number_input("初始资金 USDT", min_value=100.0, max_value=1000000.0, value=1000.0, step=100.0)
            c6, c7, c8 = st.columns(3)
            position_pct = c6.slider("单笔仓位比例", 1, 50, 10)
            fee_rate = c7.number_input("手续费率", min_value=0.0, max_value=0.01, value=0.0004, step=0.0001, format="%.4f")
            slippage = c8.number_input("滑点", min_value=0.0, max_value=0.01, value=0.0002, step=0.0001, format="%.4f")
            allow_long = st.checkbox("允许做多", value=True)
            allow_short = st.checkbox("允许做空", value=True)
            run_clicked = st.form_submit_button("运行回测", width="stretch")
        if run_clicked:
            try:
                result = run_backtest(
                    bt_strategy,
                    get_strategy_config(bt_strategy),
                    symbol,
                    timeframe,
                    int(period_days),
                    {"initial_balance": initial_balance, "position_pct": position_pct, "fee_rate": fee_rate, "slippage": slippage, "allow_long": allow_long, "allow_short": allow_short},
                )
                st.session_state["latest_backtest_result"] = result
                st.success("回测完成，已保存结果。")
            except Exception as exc:
                st.error(f"回测运行失败：{exc}")

        latest = st.session_state.get("latest_backtest_result")
        if latest:
            m = latest.get("metrics", {})
            render_metric_grid(
                [
                    ("策略评级", latest.get("grade", "E"), "green" if latest.get("grade") in {"A", "B"} else "yellow"),
                    ("交易次数", str(m.get("total_trades", 0)), ""),
                    ("总收益率", f"{float(m.get('return_pct', 0) or 0):+.2f}%", "green" if float(m.get("return_pct", 0) or 0) >= 0 else "red"),
                    ("最大回撤", f"{float(m.get('max_drawdown_pct', 0) or 0):.2f}%", "yellow"),
                    ("胜率", f"{float(m.get('win_rate', 0) or 0):.2f}%", ""),
                    ("Profit Factor", f"{float(m.get('profit_factor', 0) or 0):.2f}", "blue"),
                    ("平均R", f"{float(m.get('avg_r', 0) or 0):+.2f}R", ""),
                    ("过拟合风险", (latest.get("overfit_risk") or {}).get("level", "高"), "red" if (latest.get("overfit_risk") or {}).get("level") == "高" else "yellow"),
                ]
            )
            if latest.get("equity_curve"):
                st.line_chart({"权益": [float(p.get("equity", 0) or 0) for p in latest["equity_curve"]]})
            for reason in (latest.get("overfit_risk") or {}).get("reasons", []):
                st.warning(reason)

    with tabs[3]:
        if not results:
            st.info("暂无回测结果。请先在回测中心运行一次回测。")
        for result in results[:20]:
            m = result.get("metrics") or {}
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(result.get("strategy_name")))}</b>｜{escape(str(result.get("symbol")))}｜{escape(str(result.get("timeframe")))}｜评级：{escape(str(result.get("grade", "E")))}<br>
                  收益率：{float(m.get("return_pct", 0) or 0):+.2f}%｜最大回撤：{float(m.get("max_drawdown_pct", 0) or 0):.2f}%｜胜率：{float(m.get("win_rate", 0) or 0):.2f}%｜PF：{float(m.get("profit_factor", 0) or 0):.2f}<br>
                  过拟合风险：{escape(str((result.get("overfit_risk") or {}).get("level", "高")))}｜创建时间：{escape(str(result.get("created_time", "")))}
                </div>
                """,
                unsafe_allow_html=True,
            )
            c1, c2 = st.columns(2)
            if c1.button("加入候选策略", key=f"candidate_{result.get('result_id')}", width="stretch"):
                create_strategy_candidate(result)
                st.success("已加入候选策略库，仅用于模拟验证。")
                st.rerun()
            if c2.button("导出Markdown报告", key=f"report_{result.get('result_id')}", width="stretch"):
                path = export_strategy_report(result)
                st.success(f"报告已导出：{path}")

    with tabs[4]:
        st.caption("参数优化可能产生过拟合，必须结合样本外测试和模拟交易验证。")
        with st.form("optimizer_form"):
            opt_strategy = st.selectbox("优化策略", list(strategy_names.keys()), format_func=lambda sid: strategy_names.get(sid, sid), key="opt_strategy") if strategies else default_strategy_id
            opt_symbol = st.selectbox("优化交易对象", fallback_symbols, index=0, key="opt_symbol")
            opt_tf = st.selectbox("优化周期", ["5m", "15m", "1h"], index=1, key="opt_tf")
            opt_days = st.selectbox("优化范围", [7, 30, 90], index=1, format_func=lambda d: f"最近{d}天", key="opt_days")
            st.caption("默认扫描 ATR倍数 与 最小风险收益比。组合过多会变慢。")
            if st.form_submit_button("运行参数网格搜索", width="stretch"):
                base = get_strategy_config(opt_strategy)
                grid = {"atr_mult": [1.0, 1.3, 1.6, 2.0], "rr_min": [1.1, 1.3, 1.5]}
                try:
                    st.session_state["optimizer_results"] = run_parameter_grid_search(opt_strategy, base, opt_symbol, opt_tf, int(opt_days), grid, limit=12)
                    st.success("参数优化完成。")
                except Exception as exc:
                    st.error(f"参数优化失败：{exc}")
        for row in st.session_state.get("optimizer_results", [])[:20]:
            m = row.get("metrics") or {}
            st.markdown(f"参数：`{row.get('config')}`｜收益率 {float(m.get('return_pct',0) or 0):+.2f}%｜回撤 {float(m.get('max_drawdown_pct',0) or 0):.2f}%｜PF {float(m.get('profit_factor',0) or 0):.2f}｜评级 {row.get('grade')}｜过拟合 {(row.get('overfit_risk') or {}).get('level')}")

    with tabs[5]:
        compare_rows = compare_strategy_results(results[:8])
        if not compare_rows:
            st.info("暂无可对比回测结果。")
        for row in compare_rows:
            st.markdown(f"**{row['策略']}**｜{row['交易对象']} {row['周期']}｜收益率 {row['收益率']:+.2f}%｜回撤 {row['最大回撤']:.2f}%｜胜率 {row['胜率']:.2f}%｜PF {row['Profit Factor']:.2f}｜评级 {row['评级']}｜过拟合 {row['过拟合']}")

    with tabs[6]:
        if not candidates:
            st.info("暂无候选策略。达到条件的回测结果可手动加入候选库。")
        for candidate in candidates:
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(candidate.get("strategy_name")))}</b>｜评级：{escape(str(candidate.get("grade")))}｜状态：{escape(str(candidate.get("status", "待模拟验证")))}<br>
                  交易对象：{escape(" / ".join(candidate.get("symbols", [])))}｜周期：{escape(" / ".join(candidate.get("timeframes", [])))}<br>
                  收益率：{float(candidate.get("total_return", 0) or 0):+.2f}%｜回撤：{float(candidate.get("max_drawdown", 0) or 0):.2f}%｜胜率：{float(candidate.get("win_rate", 0) or 0):.2f}%｜PF：{float(candidate.get("profit_factor", 0) or 0):.2f}<br>
                  过拟合风险：{escape(str(candidate.get("overfit_risk", "高")))}｜说明：候选策略只能进入模拟验证，不能直接实盘。
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[7]:
        summary = replay_hints.get("summary", {})
        st.markdown(f"复盘样本：{summary.get('total_trades', 0)} 笔｜数据质量：{summary.get('data_quality', 'poor')}｜{summary.get('sample_warning', '')}")
        for item in replay_hints.get("suggestions", []):
            st.markdown(f"**{item.get('title')}**｜优先级：{item.get('priority')}  \n{item.get('suggestion')}")

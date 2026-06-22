"""Persistent simulated trade records page."""

from __future__ import annotations

import streamlit as st

from components.ui import kline_symbol_link, render_kline_jump_links, render_metric_grid, render_page_head
from services.trading_database import (
    get_sim_trade_stats as get_persistent_sim_trade_stats,
    init_database,
    query_review_records,
    query_sim_trades,
)


def render_trade_records_page(page_titles: dict[str, tuple[str, str]], version: str) -> None:
    """模拟交易持久化记录与统计中心。"""
    render_page_head("trade_records", page_titles, version)
    init_database()
    stats = get_persistent_sim_trade_stats()
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">模拟交易数据持久化系统</div>
            <div class="module-desc">开仓和平仓会写入 SQLite，本地程序重启后交易记录仍会保留。这里展示的是持久化数据库记录，不是临时页面缓存。</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("总交易数", str(stats.get("total_trades", 0)), "blue"),
            ("盈利次数", str(stats.get("win_count", 0)), "green"),
            ("亏损次数", str(stats.get("loss_count", 0)), "red"),
            ("胜率", f"{float(stats.get('win_rate', 0) or 0):.2f}%", "green" if float(stats.get("win_rate", 0) or 0) >= 50 else "yellow"),
            ("总收益", f"{float(stats.get('total_pnl', 0) or 0):+.2f} USDT", "green" if float(stats.get("total_pnl", 0) or 0) >= 0 else "red"),
            ("平均收益", f"{float(stats.get('average_pnl', 0) or 0):+.2f} USDT", ""),
            ("最大盈利", f"{float(stats.get('max_profit', 0) or 0):+.2f} USDT", "green"),
            ("最大亏损", f"{float(stats.get('max_loss', 0) or 0):+.2f} USDT", "red"),
            ("最大回撤", f"{float(stats.get('max_drawdown', 0) or 0):.2f} USDT", "yellow"),
            ("连续盈利", str(stats.get("max_win_streak", 0)), "green"),
            ("连续亏损", str(stats.get("max_loss_streak", 0)), "red"),
            ("当前持仓", str(stats.get("current_open_positions", 0)), "yellow"),
            ("累计开仓", str(stats.get("cumulative_opens", 0)), "blue"),
            ("数据库", "已连接", "green"),
        ]
    )
    tabs = st.tabs(["最近交易", "自动复盘", "数据库状态"])
    with tabs[0]:
        c1, c2, c3 = st.columns([2, 1, 1])
        search = c1.text_input("搜索交易记录", placeholder="输入币种、方向、策略或平仓原因")
        status = c2.selectbox("状态", ["全部", "OPEN", "CLOSED"])
        page_size = c3.selectbox("每页数量", [20, 50, 100], index=0)
        page_no = max(1, int(st.number_input("页码", min_value=1, value=1, step=1)))
        rows = query_sim_trades(limit=int(page_size), offset=(page_no - 1) * int(page_size), search=search, status="" if status == "全部" else status)
        table = [
            {
                "时间": row.get("close_time") or row.get("open_time"),
                "币种": row.get("symbol"),
                "方向": row.get("side"),
                "开仓价": row.get("entry_price"),
                "平仓价": row.get("exit_price") or "",
                "收益%": f"{float(row.get('pnl_percent', 0) or 0):+.2f}%" if row.get("status") == "CLOSED" else "",
                "盈亏USDT": f"{float(row.get('pnl', 0) or 0):+.4f}" if row.get("status") == "CLOSED" else "",
                "持仓时间": f"{float(row.get('holding_minutes', 0) or 0):.1f}分钟" if row.get("status") == "CLOSED" else "持仓中",
                "状态": row.get("status"),
                "策略": row.get("strategy") or "",
            }
            for row in rows
        ]
        if table:
            st.dataframe(table, width="stretch", hide_index=True)
            render_kline_jump_links([row.get("symbol") for row in rows], "本页交易对象K线")
        else:
            st.info("暂无符合条件的模拟交易记录。开启自动模拟并产生开/平仓后会写入这里。")
    with tabs[1]:
        reviews = query_review_records(100)
        if not reviews:
            st.info("暂无自动复盘记录。每次模拟完整平仓后会自动生成。")
        for row in reviews[:100]:
            with st.expander(f"{row.get('symbol')}｜{row.get('side')}｜{row.get('close_time')}｜{float(row.get('pnl', 0) or 0):+.4f} USDT"):
                st.markdown(kline_symbol_link(row.get("symbol"), f"查看 {row.get('symbol')} K线", "watch-pill"), unsafe_allow_html=True)
                st.markdown(f"**开仓原因**：{row.get('open_reason') or '暂无'}")
                st.markdown(f"**市场结构**：{row.get('market_structure') or '暂无'}")
                st.markdown(f"**AI评分**：{row.get('ai_score') or 0}")
                st.markdown(f"**交易逻辑**：{row.get('trade_logic') or '暂无'}")
                st.markdown(f"**持仓时间**：{float(row.get('holding_minutes', 0) or 0):.1f} 分钟")
                st.markdown(f"**平仓原因**：{row.get('close_reason') or '暂无'}")
                st.markdown(f"**最终收益**：{float(row.get('pnl', 0) or 0):+.4f} USDT / {float(row.get('pnl_percent', 0) or 0):+.2f}%")
    with tabs[2]:
        st.caption(f"SQLite 数据库路径：{stats.get('database_path')}")
        st.info("数据库会在程序启动或首次读写时自动创建。重置模拟账户不会删除 SQLite 历史记录。")


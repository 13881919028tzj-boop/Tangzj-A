"""Home overview page."""

from __future__ import annotations

from html import escape
from typing import Any, Callable

import streamlit as st

from app_pages.kline_page import build_ai_status, score_color, score_text
from app_pages.simulation_page import build_sim_price_map
from components.market_widgets import render_watchlist_quick_controls
from components.symbol_selector import render_symbol_search_panel
from components.ui import render_metric_grid, render_page_head, signal_color
from services import market_cache
from services.live_trading_center import get_live_safety_status
from services.notification_center import get_notification_summary
from services.server_runtime import get_server_health
from services.sim_trade_engine import update_simulation
from utils.formatters import format_compact, format_percent, format_price


def _direction_text(direction: str) -> str:
    if direction == "long":
        return "做多"
    if direction == "short":
        return "做空"
    return "中性"


def render_sim_overview_window(summary: dict[str, Any]) -> None:
    """Home-page simulation overview window."""
    account = summary.get("account") or {}
    positions = [p for p in summary.get("positions", []) if p.get("status") in {"open", "partially_closed"}]
    orders = [o for o in summary.get("orders", []) if o.get("status") == "pending"]
    history = summary.get("history") or []
    last_trade = history[0] if history else {}
    pnl = float(account.get("total_pnl", 0) or 0)
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">模拟交易概览</div>
            <div class="module-desc">当前为模拟交易，不会使用真实资金，不会执行真实订单。</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">模拟账户权益</div><div class="summary-value">{format_compact(float(account.get("equity", 0) or 0))} USDT</div></div>
              <div class="summary-card"><div class="summary-label">今日盈亏</div><div class="summary-value {signal_color("支持交易" if float(account.get("daily_pnl", 0) or 0) >= 0 else "反对交易")}">{float(account.get("daily_pnl", 0) or 0):+.2f} USDT</div></div>
              <div class="summary-card"><div class="summary-label">当前持仓</div><div class="summary-value blue">{len(positions)}</div></div>
              <div class="summary-card"><div class="summary-label">待触发订单</div><div class="summary-value yellow">{len(orders)}</div></div>
              <div class="summary-card"><div class="summary-label">累计盈亏</div><div class="summary-value {signal_color("支持交易" if pnl >= 0 else "反对交易")}">{pnl:+.2f} USDT</div></div>
              <div class="summary-card"><div class="summary-label">模拟状态</div><div class="summary-value yellow">{escape(str(account.get("status", "stopped")))}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">最近一笔交易：{escape(str(last_trade.get("symbol", "暂无")))}｜{escape(str(last_trade.get("close_reason", "暂无历史")))}｜盈亏 {float(last_trade.get("pnl", 0) or 0):+.2f} USDT</div>
            <a class="watch-pill" href="?page=trading" target="_self" style="margin-top:8px;">进入模拟交易中心</a>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_live_safety_overview_window(status: dict[str, Any]) -> None:
    """Home-page live safety overview window."""
    settings = status.get("settings") or {}
    connection = status.get("connection") or {}
    permission = status.get("permission") or {}
    withdraw = status.get("withdraw") or {}
    recent = (status.get("recent_audit") or [{}])[0] if status.get("recent_audit") else {}
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">实盘安全中心</div>
            <div class="module-desc">默认不会执行真实订单，当前仅用于安全检查、只读监控和订单预览。</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">当前模式</div><div class="summary-value yellow">{escape(str(settings.get("mode", "read_only")))}</div></div>
              <div class="summary-card"><div class="summary-label">API状态</div><div class="summary-value {signal_color("支持交易" if connection.get("ok") else "反对交易")}">{escape(str(connection.get("status", "未检查")))}</div></div>
              <div class="summary-card"><div class="summary-label">权限状态</div><div class="summary-value">{escape(str(permission.get("permission_status", "未配置")))}</div></div>
              <div class="summary-card"><div class="summary-label">提现权限</div><div class="summary-value {signal_color("反对交易" if withdraw.get("status") == "高危开启" else "支持交易")}">{escape(str(withdraw.get("status", "未知")))}</div></div>
              <div class="summary-card"><div class="summary-label">安全锁</div><div class="summary-value {signal_color("反对交易" if settings.get("kill_switch_enabled") else "支持交易")}">{'已触发' if settings.get("kill_switch_enabled") else '未触发'}</div></div>
              <div class="summary-card"><div class="summary-label">实盘候选</div><div class="summary-value {signal_color("支持交易" if status.get("allow_live_candidate") else "反对交易")}">{'允许审查' if status.get("allow_live_candidate") else '暂不允许'}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">最近安全事件：{escape(str(recent.get("event", "暂无")))}｜{escape(str(recent.get("result", "")))}｜{escape(str(recent.get("reason", "")))}</div>
            <a class="watch-pill" href="?page=live" target="_self" style="margin-top:8px;">进入实盘安全中心</a>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_server_overview_window() -> None:
    """Home-page server health window."""
    health = get_server_health()
    backup = health.get("backup") or {}
    metrics = health.get("metrics") or {}
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">服务器运行状态</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">状态</div><div class="summary-value {signal_color(str(health.get("status", "")))}">{escape(str(health.get("status", "运行中")))}</div></div>
              <div class="summary-card"><div class="summary-label">模式</div><div class="summary-value yellow">{escape(str(health.get("mode", "READ_ONLY")))}</div></div>
              <div class="summary-card"><div class="summary-label">运行时长</div><div class="summary-value blue">{escape(str(health.get("uptime", "-")))}</div></div>
              <div class="summary-card"><div class="summary-label">心跳</div><div class="summary-value green">{escape(str(health.get("last_heartbeat_time", "-")))}</div></div>
              <div class="summary-card"><div class="summary-label">日志</div><div class="summary-value {'green' if health.get("logs_writable") else 'red'}">{"正常" if health.get("logs_writable") else "不可写"}</div></div>
              <div class="summary-card"><div class="summary-label">数据目录</div><div class="summary-value {'green' if health.get("data_writable") else 'red'}">{"正常" if health.get("data_writable") else "不可写"}</div></div>
              <div class="summary-card"><div class="summary-label">磁盘剩余</div><div class="summary-value yellow">{float(metrics.get("disk_free_gb", 0) or 0):.1f} GB</div></div>
              <div class="summary-card"><div class="summary-label">最近备份</div><div class="summary-value {'green' if backup.get("latest_ok") else 'yellow'}">{escape(str(backup.get("latest_backup_time") or "未备份"))}</div></div>
            </div>
            <div class="module-desc" style="margin-top:8px;">服务器重启默认进入 READ_ONLY，真实交易和自动实盘不会自动恢复。</div>
            <a class="watch-pill" href="?page=server" target="_self" style="margin-top:8px;">查看服务器健康</a>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_notification_overview_window() -> None:
    """Home-page notification summary."""
    summary = get_notification_summary()
    latest = summary.get("latest") or []
    latest_text = "暂无未读通知"
    if latest:
        top = latest[0]
        latest_text = f"{top.get('priority')}｜{top.get('title')}｜{top.get('message', '')[:80]}"
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">通知中心</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">未读通知</div><div class="summary-value {'red' if summary.get("unread_count") else 'green'}">{summary.get("unread_count", 0)}</div></div>
              <div class="summary-card"><div class="summary-label">紧急通知</div><div class="summary-value {'red' if summary.get("urgent_count") else 'green'}">{summary.get("urgent_count", 0)}</div></div>
              <div class="summary-card"><div class="summary-label">待审批提醒</div><div class="summary-value yellow">{summary.get("approval_count", 0)}</div></div>
              <div class="summary-card"><div class="summary-label">风险提醒</div><div class="summary-value red">{summary.get("risk_count", 0)}</div></div>
              <div class="summary-card"><div class="summary-label">服务器提醒</div><div class="summary-value yellow">{summary.get("server_count", 0)}</div></div>
              <div class="summary-card"><div class="summary-label">实盘提醒</div><div class="summary-value yellow">{summary.get("live_count", 0)}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">最近提醒：{escape(str(latest_text))}</div>
            <a class="watch-pill" href="?page=remote" target="_self" style="margin-top:8px;">查看通知中心</a>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_strategy_committee_preview(strategy: dict[str, Any]) -> None:
    """Home-page strategy committee preview."""
    direction = _direction_text(str(strategy.get("direction", "neutral")))
    decision = str(strategy.get("local_vote_decision", "只观察"))
    grade = str(strategy.get("local_vote_grade", "D"))
    st.markdown(
        f"""
        <div class="app-shell">
            <div class="module-card">
              <div class="module-title">策略委员看板</div>
            <div class="module-desc">本地策略是基础提案层，DeepSeek/Gemini 当前为正式投票委员；观察池委员和策略验证委员暂为影子复核。</div>
            <div class="module-grid">
              <div class="status-card">
                <b>本地策略委员</b><br>
                投票分：<span class="{signal_color(decision)}">{strategy.get("local_vote_score", 0)} / 100 · {escape(grade)}级</span><br>
                投票决议：<span class="{signal_color(decision)}">{escape(decision)}</span><br>
                方向：{escape(direction)}｜建议：{escape(str(strategy.get("action", "观望")))}<br>
                策略：{escape(str(strategy.get("strategy_name", "无有效策略")))}｜仓位：{escape(str(strategy.get("position_suggestion", "0%")))}<br>
                理由：{escape(str(strategy.get("local_vote_reason", "等待策略数据同步")))}
              </div>
              <div class="status-card"><b>DeepSeek委员</b><br>正式投票<br>只读取脱敏摘要，不下单、不绕过风控。</div>
              <div class="status-card"><b>Gemini委员</b><br>正式投票<br>参与权重表决，不读取密钥、不硬否决。</div>
              <div class="status-card"><b>人工仓位干预</b><br>受限启用<br>只能低于风控最大仓位，超系统建议需确认。</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_home(
    ticker: dict[str, Any] | None,
    snapshot: dict[str, Any],
    scores: dict[str, Any],
    symbols: list[str],
    rankings: dict[str, list[dict[str, Any]]] | None = None,
    *,
    page_titles: dict[str, tuple[str, str]],
    version: str,
    fallback_symbols: list[str],
    set_current_symbol: Callable[[str, str], None],
    render_trade_opportunity_board: Callable[[dict[str, list[dict[str, Any]]] | None, bool], None],
    render_committee_overview: Callable[[dict[str, Any]], None],
    build_current_committee_decision: Callable[[str, dict[str, Any] | None], dict[str, Any]],
    build_current_local_strategy: Callable[[str, dict[str, Any] | None], dict[str, Any]],
) -> None:
    """Home overview page."""
    render_page_head("home", page_titles, version)
    render_symbol_search_panel(symbols, "home", fallback_symbols=fallback_symbols, set_symbol=set_current_symbol)
    render_watchlist_quick_controls(st.session_state.current_symbol, "home", source="manual")
    change_class = "green" if ticker and ticker["price_change_percent"] >= 0 else "red"
    ai_text, ai_color = build_ai_status(scores)
    render_metric_grid(
        [
            ("当前交易对象", snapshot.get("current_symbol", "-"), ""),
            ("当前价格", format_price(ticker["last_price"]) if ticker else "正在获取", ""),
            ("24小时涨跌幅", format_percent(ticker["price_change_percent"]) if ticker else "正在获取", change_class),
            ("市场状态", scores["trend_label"], "yellow"),
            ("趋势评分", score_text(scores.get("trend_score")), score_color(scores.get("trend_score"), 65)),
            ("风险评分", score_text(scores.get("risk_score")), "yellow" if scores.get("risk_score") is None or scores.get("risk_score", 0) < 65 else "red"),
            ("机会评分", score_text(scores.get("opportunity_score")), score_color(scores.get("opportunity_score"), 70)),
            ("AI建议", ai_text, ai_color),
        ]
    )
    if ticker:
        render_metric_grid(
            [
                ("24h最高价", format_price(ticker["high_price"]), ""),
                ("24h最低价", format_price(ticker["low_price"]), ""),
                ("24h成交额", format_compact(ticker["quote_volume"]), ""),
                ("更新时间", snapshot.get("last_update_time", "初始化中"), ""),
            ]
        )
    render_trade_opportunity_board(rankings, True)
    committee_symbol = str(st.session_state.get("committee_active_symbol") or st.session_state.current_symbol)
    render_committee_overview(build_current_committee_decision(committee_symbol, market_cache.get_ticker(committee_symbol)))
    sim_summary = update_simulation(build_sim_price_map(st.session_state.current_symbol), [])
    render_sim_overview_window(sim_summary)
    render_live_safety_overview_window(get_live_safety_status())
    render_server_overview_window()
    render_notification_overview_window()
    render_strategy_committee_preview(build_current_local_strategy(st.session_state.current_symbol, ticker))
    current = escape(str(st.session_state.get("current_symbol", "BTCUSDT")))
    st.markdown(
        f"""<div class="app-shell"><div class="module-card"><div class="module-title">快捷操作</div>
        <div class="quick-grid">
          <a class="quick-button rank-link" href="?page=positions&symbol={current}" target="_self">查看持仓与订单</a>
          <a class="quick-button rank-link" href="?page=market&symbol={current}" target="_self">查看机会榜</a>
          <a class="quick-button rank-link" href="?page=trade&symbol={current}" target="_self">模拟/实盘交易</a>
          <a class="quick-button rank-link" href="?page=server&symbol={current}" target="_self">运行与安全状态</a>
        </div></div></div>""",
        unsafe_allow_html=True,
    )

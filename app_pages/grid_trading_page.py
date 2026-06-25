"""Live grid trading page."""

from __future__ import annotations

from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from components.local_api import frontend_api_client_js
from components.ui import render_metric_grid, render_page_head
from services import market_cache
from services.background_refresher import refresh_klines_now, refresh_symbol_now
from services.grid_recommendation_engine import build_grid_recommendations
from services.live_grid_trade_engine import (
    build_live_grid_manual_order_plans,
    build_live_grid_recommendation_order_plans,
    get_live_grid_status,
    load_live_grid_audit,
    load_live_grid_settings,
    run_live_grid_plan_test_orders,
    save_live_grid_settings,
    submit_live_grid_plan_orders,
)
from services.live_trading_center import get_live_account_snapshot, get_live_position_summary, load_live_order_records
from utils.formatters import format_price, money_text


GRID_DIRECTION_LABELS = {
    "long_spot": "现货做多网格",
    "short_contract": "合约做空网格",
    "neutral_contract": "中性网格",
}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _price(symbol: str) -> float:
    symbol = str(symbol or "").upper().strip()
    ticker = market_cache.get_ticker(symbol) or {}
    price = _to_float(ticker.get("last_price"), 0)
    if price > 0:
        return price
    try:
        refresh_symbol_now(symbol)
    except Exception:
        pass
    ticker = market_cache.get_ticker(symbol) or {}
    return _to_float(ticker.get("last_price"), 0)


def _grid_range_suggestion(symbol: str, current_price: float) -> dict[str, Any]:
    symbol = str(symbol or "").upper().strip()
    price = _to_float(current_price)
    rows = market_cache.get_klines(symbol, "1m")
    if len(rows) < 80:
        try:
            refresh_klines_now(symbol, "1m")
            rows = market_cache.get_klines(symbol, "1m")
        except Exception:
            rows = rows or []
    if price <= 0:
        price = _to_float((rows[-1] if rows else {}).get("close"))
    if price <= 0:
        return {"direction": "long_spot", "lower": 0.0, "upper": 0.0, "quality": "等待行情", "reason": "当前价格不足，暂不能计算建议区间。"}
    window = rows[-120:] if len(rows) >= 20 else rows
    highs = [_to_float(row.get("high")) for row in window if _to_float(row.get("high")) > 0]
    lows = [_to_float(row.get("low")) for row in window if _to_float(row.get("low")) > 0]
    closes = [_to_float(row.get("close")) for row in window if _to_float(row.get("close")) > 0]
    recent_high = max(highs) if highs else price * 1.06
    recent_low = min(lows) if lows else price * 0.94
    ranges = [max(0.0, _to_float(row.get("high")) - _to_float(row.get("low"))) for row in window]
    atr = sum(ranges[-60:]) / max(len(ranges[-60:]), 1) if ranges else price * 0.003
    ref_close = closes[-60] if len(closes) >= 60 else closes[0] if closes else price
    trend_pct = (price - ref_close) / ref_close * 100 if ref_close else 0.0
    volatility_pct = atr / price * 100 if price else 0.0
    if trend_pct > 1.5:
        direction = "long_spot"
    elif trend_pct < -1.5:
        direction = "short_contract"
    else:
        direction = "neutral_contract"
    width = max(price * 0.035, atr * 12)
    lower = max(price * 0.5, min(price - width, recent_low * 1.002))
    upper = max(price * 1.01, max(price + width, recent_high * 0.998))
    return {
        "direction": direction,
        "lower": lower,
        "upper": upper,
        "quality": "高波动" if volatility_pct > 1.2 else "适合观察",
        "reason": f"近60分钟趋势 {trend_pct:+.2f}%，ATR约 {volatility_pct:.2f}%，参考近120根K线高低点。",
        "trend_pct": trend_pct,
        "volatility_pct": volatility_pct,
        "recent_low": recent_low,
        "recent_high": recent_high,
    }


def _live_price_html(symbol: str, initial_price: float) -> str:
    import json

    symbol_json = json.dumps(str(symbol or "").upper().strip())
    initial_price_json = json.dumps(float(initial_price or 0))
    client_js = frontend_api_client_js("fetchLiveGridPriceJson")
    return f"""
    <style>
      body {{ margin:0; background:transparent; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
      .box {{ border:1px solid rgba(51,65,85,.72); border-radius:8px; background:rgba(15,23,42,.72); padding:8px 10px; box-sizing:border-box; }}
      .label {{ color:#9CA3AF; font-size:12px; }}
      .value {{ color:#F0B90B; font-size:22px; line-height:1.3; font-weight:900; margin-top:3px; }}
      .meta {{ color:#9CA3AF; font-size:11px; margin-top:2px; }}
    </style>
    <div class="box">
      <div class="label">实时当前价</div>
      <div class="value" id="liveGridPrice">等待价格</div>
      <div class="meta" id="liveGridMeta">1秒刷新，不刷新页面</div>
    </div>
    <script>
      {client_js}
      const symbol = {symbol_json};
      let lastPrice = {initial_price_json};
      function num(v, d=0) {{ const n = Number(v); return Number.isFinite(n) ? n : d; }}
      function price(v) {{
        const n = num(v, NaN);
        if (!Number.isFinite(n) || n <= 0) return "等待价格";
        if (n >= 1000) return n.toLocaleString(undefined, {{maximumFractionDigits:2}});
        if (n >= 1) return n.toLocaleString(undefined, {{maximumFractionDigits:4}});
        return n.toFixed(8).replace(/0+$/,"").replace(/\\.$/,"");
      }}
      function render(source) {{
        document.getElementById("liveGridPrice").textContent = price(lastPrice);
        document.getElementById("liveGridMeta").textContent = `${{symbol}}｜${{source || "local"}}｜${{new Date().toLocaleTimeString()}}`;
      }}
      async function refreshPrice() {{
        try {{
          const ticker = await fetchLiveGridPriceJson(`/api/ticker?symbol=${{encodeURIComponent(symbol)}}`);
          const tickerPrice = num(ticker.last_price || ticker.price);
          if (tickerPrice > 0) {{
            lastPrice = tickerPrice;
            render(ticker.source || "ticker");
            return;
          }}
        }} catch (_) {{}}
        try {{
          const depth = await fetchLiveGridPriceJson(`/api/orderbook?symbol=${{encodeURIComponent(symbol)}}`);
          const bids = Array.isArray(depth.bids) ? depth.bids : [];
          const asks = Array.isArray(depth.asks) ? depth.asks : [];
          const bid = bids.length ? num(bids[0].price ?? bids[0][0]) : 0;
          const ask = asks.length ? num(asks[0].price ?? asks[0][0]) : 0;
          if (bid > 0 && ask > 0) lastPrice = (bid + ask) / 2;
          else if (bid > 0) lastPrice = bid;
          else if (ask > 0) lastPrice = ask;
          render(depth.source || "orderbook");
        }} catch (_) {{
          render("等待行情");
        }}
      }}
      render("初始化");
      refreshPrice();
      setInterval(refreshPrice, 1000);
    </script>
    """


def _ensure_fold_state(key: str, expanded: bool = False) -> bool:
    state_key = f"{key}_expanded"
    if state_key not in st.session_state:
        st.session_state[state_key] = expanded
    return bool(st.session_state.get(state_key))


def _collapsed_panel(key: str, title: str, summary: str) -> bool:
    state_key = f"{key}_expanded"
    expanded = _ensure_fold_state(key)
    if not expanded:
        c1, c2 = st.columns([4, 1])
        c1.markdown(f"**{title}**")
        c1.caption(summary)
        if c2.button("展开", key=f"{key}_expand", width="stretch"):
            st.session_state[state_key] = True
            st.rerun()
        return False
    c1, c2 = st.columns([4, 1])
    c1.markdown(f"**{title}**")
    if c2.button("收起", key=f"{key}_collapse", width="stretch"):
        st.session_state[state_key] = False
        st.rerun()
    return True


def _render_interface_summary(status: dict[str, Any], settings: dict[str, Any], symbol: str, current_price: float) -> None:
    permission = status.get("permission") or {}
    restrictions = status.get("restrictions") or {}
    summary = (
        f"接口 {'可检查' if status.get('ready_for_review') else '阻断'}｜"
        f"真实提交 {'可用' if status.get('real_submit_enabled') else '关闭'}｜"
        f"{symbol} {format_price(current_price)}｜"
        f"杠杆 {int(settings.get('futures_leverage', 3) or 3)}x｜"
        f"挂单上限 {money_text(settings.get('max_order_usdt'))}"
    )
    if not _collapsed_panel("live_grid_interface_summary", "接口与参数摘要", summary):
        return
    render_metric_grid(
        [
            ("接口状态", "可检查" if status.get("ready_for_review") else "阻断", "green" if status.get("ready_for_review") else "red"),
            ("真实提交", "可用" if status.get("real_submit_enabled") else "关闭", "green" if status.get("real_submit_enabled") else "yellow"),
            ("交易权限", "可交易" if permission.get("can_trade") else "不可交易", "green" if permission.get("can_trade") else "red"),
            ("提现权限", "关闭" if not permission.get("can_withdraw") and not restrictions.get("enableWithdrawals") else "异常", "green" if not permission.get("can_withdraw") and not restrictions.get("enableWithdrawals") else "red"),
            ("IP白名单", "已开启" if restrictions.get("ipRestrict") else "未开启", "green" if restrictions.get("ipRestrict") else "yellow"),
            ("全局实盘", "开启" if status.get("live_settings", {}).get("live_trading_enabled") else "关闭", "green" if status.get("live_settings", {}).get("live_trading_enabled") else "yellow"),
            ("交易对象", symbol, "blue"),
            ("当前价", format_price(current_price), "green" if current_price > 0 else "yellow"),
            ("合约杠杆", f"{int(settings.get('futures_leverage', 3) or 3)}x", "yellow"),
            ("单挂单上限", money_text(settings.get("max_order_usdt")), "blue"),
        ]
    )
    if status.get("blockers"):
        st.warning("；".join(str(item) for item in status.get("blockers", [])))


def _plan_summary(plan_result: dict[str, Any] | None) -> dict[str, Any]:
    plans = (plan_result or {}).get("plans") or []
    spot_count = 0
    futures_count = 0
    total_quote = 0.0
    symbols: list[str] = []
    for item in plans:
        plan = item.get("plan") or {}
        symbol = str(plan.get("symbol") or "").upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
        if str(plan.get("market_type")) == "futures":
            futures_count += 1
        else:
            spot_count += 1
        total_quote += _to_float(plan.get("quote_amount"))
    return {
        "count": len(plans),
        "spot_count": spot_count,
        "futures_count": futures_count,
        "total_quote": total_quote,
        "symbols": "、".join(symbols[:3]) if symbols else "-",
    }


def _is_grid_order(row: dict[str, Any]) -> bool:
    text = " ".join(str(row.get(key, "")) for key in ("source", "client_order_id", "order_id", "symbol"))
    return "网格" in text or "grid" in text.lower()


def _order_filled(row: dict[str, Any]) -> bool:
    status = str(row.get("order_status") or "").upper()
    return status in {"FILLED", "PARTIALLY_FILLED"} or _to_float(row.get("executed_qty")) > 0


def _grid_order_records(limit: int = 500) -> list[dict[str, Any]]:
    return [row for row in load_live_order_records(limit) if _is_grid_order(row)]


def _grid_context(symbol: str, current_price: float) -> dict[str, Any]:
    records = _grid_order_records(500)
    symbols = sorted({str(row.get("symbol") or "").upper() for row in records if row.get("symbol")})
    if symbol and symbol not in symbols:
        symbols.append(symbol)
    current_prices = {sym: _price(sym) for sym in symbols}
    if symbol and current_price > 0:
        current_prices[str(symbol).upper()] = current_price
    position_summary = get_live_position_summary(current_prices)
    open_positions = position_summary.get("open_system_positions") or []
    plan = _plan_summary(st.session_state.get("live_grid_plan_result") or {})
    filled_count = len([row for row in records if _order_filled(row)])
    submitted_count = len([row for row in records if str(row.get("order_status") or "").upper() not in {"REJECTED", "CANCELED", "EXPIRED"}])
    unrealized_pnl = _to_float(position_summary.get("total_unrealized_pnl"))
    realized_pnl = sum(_to_float(pos.get("realized_pnl")) for pos in open_positions)
    total_pnl = realized_pnl + unrealized_pnl
    return {
        "records": records,
        "symbols": symbols,
        "current_prices": current_prices,
        "position_summary": position_summary,
        "open_positions": open_positions,
        "plan": plan,
        "submitted_count": submitted_count,
        "filled_count": filled_count,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": total_pnl,
    }


def _render_grid_account_overview(status: dict[str, Any], settings: dict[str, Any], symbol: str, current_price: float, context: dict[str, Any]) -> None:
    snapshot = get_live_account_snapshot(False, "futures" if settings.get("allow_futures_grid") else "spot")
    balances = snapshot.get("balances") or []
    plan = context.get("plan") or {}
    open_positions = context.get("open_positions") or []
    records = context.get("records") or []
    if snapshot.get("ok"):
        st.success("Binance API 已接入，账户只读检查通过。")
    else:
        st.warning(snapshot.get("message", "Binance 账户状态暂不可用。"))
    render_metric_grid(
        [
            ("接口状态", "可检查" if status.get("ready_for_review") else "阻断", "green" if status.get("ready_for_review") else "red"),
            ("真实提交", "可用" if status.get("real_submit_enabled") else "关闭", "green" if status.get("real_submit_enabled") else "yellow"),
            ("运行网格", str(len(open_positions) or (1 if plan.get("count") else 0)), "green" if open_positions or plan.get("count") else "yellow"),
            ("计划挂单", str(plan.get("count", 0)), "blue" if plan.get("count") else "yellow"),
            ("真实订单", str(context.get("submitted_count", 0)), "green" if context.get("submitted_count") else "yellow"),
            ("成交", str(context.get("filled_count", 0)), "green" if context.get("filled_count") else "yellow"),
            ("浮动盈亏", f"{_to_float(context.get('unrealized_pnl')):+.4f} USDT", "green" if _to_float(context.get("unrealized_pnl")) >= 0 else "red"),
            ("总盈亏", f"{_to_float(context.get('total_pnl')):+.4f} USDT", "green" if _to_float(context.get("total_pnl")) >= 0 else "red"),
            ("当前币价", format_price(current_price), "green" if current_price > 0 else "yellow"),
            ("杠杆", f"{int(settings.get('futures_leverage', 3) or 3)}x", "yellow"),
            ("单挂单上限", money_text(settings.get("max_order_usdt")), "blue"),
            ("历史订单", str(len(records)), ""),
        ]
    )
    st.markdown(
        f"""
        <div class="app-shell"><div class="module-card">
          <div class="module-title">网格账户中心</div>
          <div class="module-desc">这里记录真实网格的账户快照、订单计划、真实订单、持仓和审计日志，后续统计分析都以这些记录为准。</div>
          <div class="status-card">
            当前对象：<b>{symbol}</b>｜当前价：{format_price(current_price)}｜现货网格：{"开启" if settings.get("allow_spot_long_grid") else "关闭"}｜合约网格：{"开启" if settings.get("allow_futures_grid") else "关闭"}<br>
            接口：{"开启" if settings.get("live_grid_interface_enabled") else "关闭"}｜Test Order：{"开启" if settings.get("allow_test_orders") else "关闭"}｜真实提交：{"开启" if settings.get("allow_real_order_submit") else "关闭"}<br>
            {status.get("message", "")}
          </div>
        </div></div>
        """,
        unsafe_allow_html=True,
    )
    if balances:
        with st.expander(f"真实账户余额摘要｜{len(balances[:30])} 条资产", expanded=False):
            st.dataframe(balances[:30], width="stretch", hide_index=True)
    if status.get("blockers"):
        with st.expander("接口阻断项", expanded=False):
            for item in status.get("blockers", []):
                st.warning(str(item))


def _render_grid_positions(context: dict[str, Any], settings: dict[str, Any], current_price: float) -> None:
    render_metric_grid(
        [
            ("运行网格", str(len(context.get("open_positions") or []) or (1 if (context.get("plan") or {}).get("count") else 0)), "green" if context.get("open_positions") or (context.get("plan") or {}).get("count") else "yellow"),
            ("计划挂单", str((context.get("plan") or {}).get("count", 0)), "blue" if (context.get("plan") or {}).get("count") else "yellow"),
            ("真实订单", str(context.get("submitted_count", 0)), "green" if context.get("submitted_count") else "yellow"),
            ("成交", str(context.get("filled_count", 0)), "green" if context.get("filled_count") else "yellow"),
            ("浮动盈亏", f"{_to_float(context.get('unrealized_pnl')):+.4f} USDT", "green" if _to_float(context.get("unrealized_pnl")) >= 0 else "red"),
            ("总盈亏", f"{_to_float(context.get('total_pnl')):+.4f} USDT", "green" if _to_float(context.get("total_pnl")) >= 0 else "red"),
            ("当前币价", format_price(current_price), "green" if current_price > 0 else "yellow"),
            ("杠杆", f"{int(settings.get('futures_leverage', 3) or 3)}x", "yellow"),
        ]
    )
    open_positions = context.get("open_positions") or []
    if not open_positions:
        st.info("当前暂无可识别的真实网格持仓。提交真实订单并成交后会显示在这里。")
        return
    for pos in open_positions:
        pnl = _to_float(pos.get("total_pnl"))
        pnl_class = "green" if pnl >= 0 else "red"
        st.markdown(
            f"""
            <div class="status-card">
              <b>{pos.get('symbol')}</b>｜{pos.get('status')}｜真实网格持仓<br>
              当前价：{format_price(pos.get('current_price'))}　成本均价：{format_price(pos.get('avg_entry_price'))}　剩余数量：{_to_float(pos.get('remaining_quantity')):.8f}<br>
              成本：{money_text(pos.get('quote_cost'))}　已实现：{_to_float(pos.get('realized_pnl')):+.4f} USDT　浮盈：{_to_float(pos.get('unrealized_pnl')):+.4f} USDT<br>
              <span class="{pnl_class}">总盈亏：{pnl:+.4f} USDT / {_to_float(pos.get('unrealized_pnl_pct')):+.2f}%</span>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_grid_history(records: list[dict[str, Any]]) -> None:
    if not records:
        st.info("当前暂无真实网格交易历史。提交真实订单后会记录在这里。")
        return
    f1, f2, f3 = st.columns(3)
    market_filter = f1.selectbox("市场筛选", ["全部"] + sorted({str(row.get("market_type") or "未知") for row in records}), key="grid_history_market")
    side_filter = f2.selectbox("方向筛选", ["全部"] + sorted({str(row.get("side") or "未知") for row in records}), key="grid_history_side")
    status_filter = f3.selectbox("状态筛选", ["全部"] + sorted({str(row.get("order_status") or row.get("raw_status_summary") or "未知") for row in records}), key="grid_history_status")
    rows = records
    if market_filter != "全部":
        rows = [row for row in rows if str(row.get("market_type") or "未知") == market_filter]
    if side_filter != "全部":
        rows = [row for row in rows if str(row.get("side") or "未知") == side_filter]
    if status_filter != "全部":
        rows = [row for row in rows if str(row.get("order_status") or row.get("raw_status_summary") or "未知") == status_filter]
    for row in rows[:100]:
        side = str(row.get("side") or "")
        klass = "green" if side.upper() == "BUY" else "red"
        st.markdown(
            f"""
            <div class="status-card">
              <b>{row.get('symbol')}</b>｜{row.get('market_type')}｜<span class="{klass}">{side}</span>｜{row.get('order_status') or row.get('raw_status_summary') or ''}<br>
              价格：{format_price(row.get('price') or row.get('avg_price'))}　数量：{_to_float(row.get('quantity')):.8f}　名义金额：{money_text(row.get('notional'))}　保证金：{money_text(row.get('margin_usdt', row.get('notional')))}<br>
              杠杆：{row.get('leverage', '-')}　类型：{row.get('order_type', '')}　订单ID：{row.get('order_id', '')}<br>
              来源：{row.get('source', '')}　时间：{row.get('time', '')}
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_grid_statistics(context: dict[str, Any], audit: list[dict[str, Any]]) -> None:
    records = context.get("records") or []
    notionals = [_to_float(row.get("notional")) for row in records]
    buy_count = len([row for row in records if str(row.get("side") or "").upper() == "BUY"])
    sell_count = len([row for row in records if str(row.get("side") or "").upper() == "SELL"])
    render_metric_grid(
        [
            ("真实订单数", str(len(records)), ""),
            ("买 / 卖", f"{buy_count} / {sell_count}", ""),
            ("已提交订单", str(context.get("submitted_count", 0)), "green"),
            ("成交订单", str(context.get("filled_count", 0)), "green" if context.get("filled_count") else "yellow"),
            ("当前持仓", str(len(context.get("open_positions") or [])), ""),
            ("计划挂单", str((context.get("plan") or {}).get("count", 0)), "blue"),
            ("累计名义金额", money_text(sum(notionals)), "blue"),
            ("平均订单额", money_text(sum(notionals) / len(notionals) if notionals else 0), ""),
            ("已实现盈亏", f"{_to_float(context.get('realized_pnl')):+.4f} USDT", "green" if _to_float(context.get("realized_pnl")) >= 0 else "red"),
            ("浮动盈亏", f"{_to_float(context.get('unrealized_pnl')):+.4f} USDT", "green" if _to_float(context.get("unrealized_pnl")) >= 0 else "red"),
            ("总盈亏", f"{_to_float(context.get('total_pnl')):+.4f} USDT", "green" if _to_float(context.get("total_pnl")) >= 0 else "red"),
            ("事件日志", str(len(audit)), ""),
        ]
    )
    if records:
        st.dataframe(records[:100], width="stretch", hide_index=True)
    else:
        st.info("暂无真实网格订单样本，成交率、EV、收益曲线需要成交记录后再计算。")


def _render_grid_runtime_status(status: dict[str, Any], settings: dict[str, Any], symbol: str, current_price: float) -> None:
    plan_result = st.session_state.get("live_grid_plan_result") or {}
    test_result = st.session_state.get("live_grid_test_result") or {}
    submit_result = st.session_state.get("live_grid_submit_result") or {}
    plan = _plan_summary(plan_result)
    records = [row for row in load_live_order_records(500) if _is_grid_order(row)]
    live_symbols = sorted({str(row.get("symbol") or "").upper() for row in records if row.get("symbol")})
    current_prices = {sym: _price(sym) for sym in live_symbols}
    position_summary = get_live_position_summary(current_prices)
    open_positions = position_summary.get("open_system_positions") or []
    grid_position_symbols = {str(pos.get("symbol") or "").upper() for pos in open_positions}
    running_grid_count = len(grid_position_symbols) or (1 if plan["count"] else 0)
    submitted_count = len([row for row in records if str(row.get("order_status") or "").upper() not in {"REJECTED", "CANCELED", "EXPIRED"}])
    filled_count = len([row for row in records if _order_filled(row)])
    realized_pnl = sum(_to_float(pos.get("realized_pnl")) for pos in open_positions)
    unrealized_pnl = _to_float(position_summary.get("total_unrealized_pnl"))
    total_pnl = realized_pnl + unrealized_pnl
    st.markdown("**网格运行状态**")
    render_metric_grid(
        [
            ("运行网格", str(running_grid_count), "green" if running_grid_count else "yellow"),
            ("计划挂单", str(plan["count"]), "blue" if plan["count"] else "yellow"),
            ("真实订单", str(submitted_count), "green" if submitted_count else "yellow"),
            ("成交", str(filled_count), "green" if filled_count else "yellow"),
            ("浮动盈亏", f"{unrealized_pnl:+.4f} USDT", "green" if unrealized_pnl >= 0 else "red"),
            ("总盈亏", f"{total_pnl:+.4f} USDT", "green" if total_pnl >= 0 else "red"),
            ("当前币价", format_price(current_price), "green" if current_price > 0 else "yellow"),
            ("杠杆", f"{int(settings.get('futures_leverage', 3) or 3)}x", "yellow"),
        ]
    )
    for pos in open_positions[:8]:
        pnl = _to_float(pos.get("total_pnl"))
        pnl_color = "#00C087" if pnl >= 0 else "#F6465D"
        st.markdown(
            f"""
            <div class="status-card" style="margin-top:8px;">
              <b>{pos.get('symbol')}</b>｜{pos.get('status')}｜风险 {pos.get('risk_level', '低')}<br>
              当前价：{format_price(pos.get('current_price'))}｜
              剩余数量：{_to_float(pos.get('remaining_quantity')):.8f}｜
              成本：{money_text(pos.get('quote_cost'))}<br>
              已实现：{_to_float(pos.get('realized_pnl')):+.4f} USDT｜
              浮盈：{_to_float(pos.get('unrealized_pnl')):+.4f} USDT｜
              <span style="color:{pnl_color};font-weight:800;">总盈亏：{pnl:+.4f} USDT</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    if not open_positions and plan["count"]:
        st.caption(f"当前已有订单计划：{plan['symbols']}｜现货 {plan['spot_count']}｜合约 {plan['futures_count']}｜计划金额 {money_text(plan['total_quote'])}。尚未识别到真实成交持仓。")
    elif not open_positions:
        st.caption("当前没有识别到运行中的真实网格。生成订单计划或提交真实订单后，这里会显示数量、成交和盈亏。")


def _render_settings(settings: dict[str, Any]) -> None:
    summary = (
        f"实盘接口 {'开' if settings.get('live_grid_interface_enabled') else '关'}｜"
        f"现货 {'开' if settings.get('allow_spot_long_grid') else '关'}｜"
        f"合约 {'开' if settings.get('allow_futures_grid') else '关'}｜"
        f"杠杆 {int(settings.get('futures_leverage', 3) or 3)}x｜"
        f"单挂单 {money_text(settings.get('max_order_usdt'))}"
    )
    if not _collapsed_panel("live_grid_settings", "实盘网格接口配置", summary):
        return
    with st.form("live_grid_settings_form"):
        c1, c2, c3, c4 = st.columns(4)
        live_enabled = c1.checkbox("开启实盘网格接口", value=bool(settings.get("live_grid_interface_enabled")))
        require_ip = c2.checkbox("要求IP白名单", value=bool(settings.get("require_ip_restrict")))
        max_orders = c3.number_input("最多初始挂单", min_value=1, max_value=5, value=int(settings.get("max_initial_orders", 2)), step=1)
        max_order_usdt = c4.number_input("单挂单上限USDT", min_value=1.0, max_value=50.0, value=float(settings.get("max_order_usdt", 5.0)), step=1.0)
        l1, l2 = st.columns(2)
        max_leverage = l1.slider("合约最大杠杆", 1, 125, int(settings.get("max_futures_leverage", 20) or 20))
        leverage = l2.slider("合约执行杠杆", 1, int(max_leverage), min(int(settings.get("futures_leverage", 3) or 3), int(max_leverage)))
        p1, p2, p3, p4 = st.columns(4)
        allow_reading = p1.checkbox("允许读取", value=bool(settings.get("allow_reading", True)))
        allow_spot = p2.checkbox("允许现货网格", value=bool(settings.get("allow_spot_long_grid", True)))
        allow_futures = p3.checkbox("允许合约网格", value=bool(settings.get("allow_futures_grid", False)))
        p4.checkbox("允许提现", value=False, disabled=True, help="提现权限永远禁止，不能在程序里放开。")
        q1, q2 = st.columns(2)
        allow_test = q1.checkbox("允许 Binance Test Order", value=bool(settings.get("allow_test_orders")))
        allow_real = q2.checkbox("允许真实提交", value=bool(settings.get("allow_real_order_submit", False)))
        st.caption("保存后本区域会自动折叠。真实提交仍需全局实盘开关、IP白名单、Test Order 和确认短句。")
        if st.form_submit_button("保存实盘网格接口配置", width="stretch"):
            save_live_grid_settings(
                {
                    **settings,
                    "live_grid_interface_enabled": live_enabled,
                    "require_ip_restrict": require_ip,
                    "max_initial_orders": int(max_orders),
                    "max_order_usdt": float(max_order_usdt),
                    "max_futures_leverage": int(max_leverage),
                    "futures_leverage": int(leverage),
                    "allow_reading": allow_reading,
                    "allow_spot_long_grid": allow_spot,
                    "allow_futures_grid": allow_futures,
                    "allow_test_orders": allow_test,
                    "allow_real_order_submit": allow_real,
                }
            )
            st.session_state["live_grid_settings_expanded"] = False
            st.session_state["live_grid_settings_saved"] = True
            st.rerun()


def _render_manual_plan_form(symbol: str, current_price: float, settings: dict[str, Any]) -> None:
    suggestion = _grid_range_suggestion(symbol, current_price)
    default_lower = _to_float(suggestion.get("lower"), current_price * 0.94 if current_price else 0.0)
    default_upper = _to_float(suggestion.get("upper"), current_price * 1.06 if current_price else 0.0)
    default_direction = str(suggestion.get("direction") or "long_spot")
    summary = f"{symbol}｜{GRID_DIRECTION_LABELS.get(default_direction, default_direction)}｜建议区间 {format_price(default_lower)} - {format_price(default_upper)}"
    if not _collapsed_panel("live_grid_manual", "真实网格订单参数", summary):
        return
    components.html(_live_price_html(symbol, current_price), height=86, scrolling=False)
    st.caption(f"{suggestion.get('quality', '-')}｜{suggestion.get('reason', '')}")
    with st.form("live_grid_manual_plan_form"):
        c1, c2 = st.columns(2)
        input_symbol = c1.text_input("交易对象", value=symbol)
        input_current = c2.number_input("当前价", min_value=0.0, value=float(current_price or 0), step=0.0001, format="%.8f")
        direction_keys = list(GRID_DIRECTION_LABELS.keys())
        direction_index = direction_keys.index(default_direction) if default_direction in direction_keys else 0
        direction = st.radio("网格方向", direction_keys, index=direction_index, format_func=lambda item: GRID_DIRECTION_LABELS.get(item, item), horizontal=True)
        c3, c4, c5 = st.columns(3)
        lower = c3.number_input("区间下限", min_value=0.0, value=float(default_lower), step=0.0001, format="%.8f")
        upper = c4.number_input("区间上限", min_value=0.0, value=float(default_upper), step=0.0001, format="%.8f")
        grid_count = c5.number_input("网格数量", min_value=2, max_value=200, value=20, step=1)
        c6, c7 = st.columns(2)
        quote_amount = c6.number_input("单格挂单金额USDT", min_value=1.0, max_value=float(settings.get("max_order_usdt", 5.0)), value=float(settings.get("max_order_usdt", 5.0)), step=1.0)
        c7.text_input("合约执行杠杆", value=f"{int(settings.get('futures_leverage', 3) or 3)}x", disabled=True)
        st.caption("杠杆在上方“实盘网格接口配置”中统一选择；生成计划后不会自动下单。")
        if st.form_submit_button("生成真实网格订单计划", width="stretch"):
            st.session_state["live_grid_plan_result"] = build_live_grid_manual_order_plans(
                {
                    "symbol": input_symbol,
                    "current_price": float(input_current),
                    "lower_price": float(lower),
                    "upper_price": float(upper),
                    "direction": direction,
                    "grid_count": int(grid_count),
                    "quote_amount": float(quote_amount),
                }
            )
            st.session_state["live_grid_manual_expanded"] = False
            st.rerun()


def _render_recommendations(settings: dict[str, Any]) -> None:
    recommendations = build_grid_recommendations(12)
    for item in recommendations:
        live_price = _price(str(item.get("symbol") or "").upper())
        if live_price > 0:
            item["last_price"] = live_price
    summary = f"候选 {len(recommendations)} 个｜使用当前配置杠杆 {int(settings.get('futures_leverage', 3) or 3)}x"
    if not _collapsed_panel("live_grid_recommendations", "网格推荐对象", summary):
        return
    if not recommendations:
        st.info("暂无网格推荐对象。")
        return
    st.caption("向下滑动查看候选对象，点击对应按钮直接生成真实网格计划。")
    for idx, item in enumerate(recommendations):
        symbol = str(item.get("symbol") or "-").upper()
        direction = str(item.get("suggested_direction") or "long_spot")
        reasons = "；".join(str(x) for x in item.get("reasons", [])[:3])
        st.markdown(
            f"""
            <div class="status-card" style="margin-top:8px;">
              <b>{symbol}</b>｜{GRID_DIRECTION_LABELS.get(direction, direction)}｜评分 {int(_to_float(item.get('grid_score')))}｜{item.get('quality', '-')}<br>
              当前价：{format_price(item.get('last_price'))}｜
              区间：{format_price(item.get('lower_price'))} - {format_price(item.get('upper_price'))}<br>
              ATR：{_to_float(item.get('atr_pct')):.2f}%｜趋势：{_to_float(item.get('trend_pct')):+.2f}%｜杠杆：{int(settings.get('futures_leverage', 3) or 3)}x<br>
              {reasons}
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button(f"选择 {symbol} 生成真实网格计划", key=f"live_grid_pick_recommendation_{idx}_{symbol}", width="stretch"):
            st.session_state["live_grid_plan_result"] = build_live_grid_recommendation_order_plans(item)
            st.session_state["live_grid_recommendations_expanded"] = False
            st.rerun()


def _render_plan_result() -> None:
    plan_result = st.session_state.get("live_grid_plan_result")
    if not plan_result:
        return
    st.markdown("**订单计划预览**")
    if plan_result.get("ok"):
        st.success(str(plan_result.get("message")))
    else:
        st.warning(str(plan_result.get("message")))
    rows = []
    for item in plan_result.get("plans") or []:
        plan = item.get("plan") or {}
        preview = item.get("preview") or {}
        rows.append(
            {
                "计划ID": plan.get("plan_id"),
                "币种": plan.get("symbol"),
                "市场": "合约" if plan.get("market_type") == "futures" else "现货",
                "方向": plan.get("side"),
                "杠杆": f"{int(_to_float(plan.get('leverage'), 1))}x" if plan.get("market_type") == "futures" else "-",
                "价格": format_price(plan.get("price")),
                "数量": f"{_to_float(plan.get('quantity')):.8f}",
                "金额": money_text(plan.get("quote_amount")),
                "预览": "通过" if preview.get("ok") else "失败",
                "错误": "；".join(str(x) for x in (preview.get("risk_errors") or [])),
            }
        )
    if not rows:
        return
    st.dataframe(rows, width="stretch", hide_index=True)
    c_test, c_submit = st.columns(2)
    if c_test.button("执行 Binance Test Order", width="stretch"):
        st.session_state["live_grid_test_result"] = run_live_grid_plan_test_orders(plan_result.get("plans") or [])
        st.rerun()
    phrase = st.text_input("真实提交确认短句", value="", type="password", placeholder="我确认执行小资金实盘订单")
    confirmed = st.checkbox("我确认提交当前计划列表中的真实订单，并理解可能产生真实亏损")
    if c_submit.button("提交真实网格初始订单", disabled=not confirmed, width="stretch"):
        st.session_state["live_grid_submit_result"] = submit_live_grid_plan_orders(plan_result.get("plans") or [], phrase)
        st.rerun()


def _render_action_results() -> None:
    test_result = st.session_state.get("live_grid_test_result")
    if test_result:
        st.markdown("**Test Order 结果**")
        if test_result.get("ok"):
            st.success(str(test_result.get("message")))
        else:
            st.warning(str(test_result.get("message")))
        st.json({"results": test_result.get("results", []), "blockers": (test_result.get("status") or {}).get("blockers", [])})
    submit_result = st.session_state.get("live_grid_submit_result")
    if submit_result:
        st.markdown("**真实提交结果**")
        if submit_result.get("ok"):
            st.success(str(submit_result.get("message")))
        else:
            st.warning(str(submit_result.get("message")))
        st.json({"results": submit_result.get("results", []), "blockers": (submit_result.get("status") or {}).get("blockers", [])})


def _render_audit() -> None:
    audit = load_live_grid_audit(30)
    if not audit:
        return
    with st.expander("实盘网格接口审计", expanded=False):
        for row in audit:
            st.caption(f"{row.get('time')}｜{row.get('event_type')}｜{row.get('symbol')}｜{row.get('result')}｜{row.get('reason')}")


def render_grid_trading_page(page_titles: dict[str, tuple[str, str]], version: str, current_symbol: str) -> None:
    render_page_head("grid_trading", page_titles, version)
    query_symbol = str(st.query_params.get("grid_symbol", "") or "").upper().strip()
    symbol = query_symbol or str(current_symbol or "BTCUSDT").upper().strip()
    current_price = _price(symbol)
    settings = load_live_grid_settings()
    status = get_live_grid_status()
    context = _grid_context(symbol, current_price)
    audit = load_live_grid_audit(200)

    st.markdown("**真实网格交易**")
    st.caption("本页只生成、测试、提交真实 Binance 订单计划；不会自动绕过 Test Order 和确认短句。")
    if st.session_state.pop("live_grid_settings_saved", False):
        st.success("实盘网格接口配置已保存。")

    tabs = st.tabs(["账户总览", "当前持仓", "真实订单计划", "交易历史", "统计分析", "参数设置", "事件日志"])

    with tabs[0]:
        _render_grid_account_overview(status, settings, symbol, current_price, context)
        _render_interface_summary(status, settings, symbol, current_price)

    with tabs[1]:
        _render_grid_positions(context, settings, current_price)

    with tabs[2]:
        _render_manual_plan_form(symbol, current_price, settings)
        _render_recommendations(settings)
        _render_plan_result()
        _render_action_results()

    with tabs[3]:
        _render_grid_history(context.get("records") or [])

    with tabs[4]:
        _render_grid_statistics(context, audit)

    with tabs[5]:
        _render_settings(settings)

    with tabs[6]:
        if not audit:
            st.info("当前暂无实盘网格事件日志。")
        for row in audit:
            st.caption(f"{row.get('time')}｜{row.get('event_type')}｜{row.get('symbol')}｜{row.get('result')}｜{row.get('reason')}")

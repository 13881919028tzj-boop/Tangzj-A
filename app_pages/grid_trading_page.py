"""Independent grid trading page."""

from __future__ import annotations

from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from components.local_api import frontend_api_client_js
from components.ui import render_metric_grid, render_page_head
from services import market_cache
from services.background_refresher import refresh_klines_now, refresh_symbol_now
from services.grid_trade_engine import (
    cancel_grid_orders,
    close_grid_position,
    create_grid_bot,
    get_grid_summary,
    load_grid_bots,
    pause_grid_bot,
    resume_grid_bot,
    stop_grid_bot,
    update_grid_bots,
    validate_grid_config,
)
from services.grid_recommendation_engine import auto_open_recommended_grids, build_grid_recommendations
from services.orderbook_service import get_orderbook
from utils.formatters import format_price, money_text


GRID_DIRECTION_LABELS = {
    "long_spot": "做多网格",
    "short_contract": "做空网格",
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


def _grid_price_map(current_symbol: str) -> dict[str, float]:
    symbols = {str(current_symbol or "").upper().strip()}
    for bot in load_grid_bots():
        symbol = str(bot.get("symbol") or "").upper().strip()
        if symbol:
            symbols.add(symbol)
    return {symbol: price for symbol in symbols if (price := _price(symbol)) > 0}


def _fallback_price_from_bots(symbol: str, bots: list[dict[str, Any]]) -> float:
    for bot in bots:
        if str(bot.get("symbol") or "").upper() == symbol:
            return _to_float(bot.get("mark_price"), _to_float(bot.get("last_price"), 0))
    return 0.0


def _grid_orderbook_map(bots: list[dict[str, Any]], limit: int = 3) -> dict[str, dict[str, Any]]:
    orderbooks: dict[str, dict[str, Any]] = {}
    symbols: list[str] = []
    for bot in bots:
        if bot.get("status") not in {"running", "paused"}:
            continue
        symbol = str(bot.get("symbol") or "").upper().strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    for symbol in symbols[:limit]:
        try:
            orderbook = get_orderbook(symbol, limit=20)
            market_cache.set_orderbook(symbol, orderbook)
            orderbooks[symbol] = {"ok": True, "source": "initial_live", **orderbook}
        except Exception as exc:
            cached = market_cache.get_orderbook(symbol)
            if cached:
                orderbooks[symbol] = {"ok": True, "source": "initial_cache", **cached}
            else:
                orderbooks[symbol] = {
                    "ok": False,
                    "symbol": symbol,
                    "bids": [],
                    "asks": [],
                    "source": "initial_unavailable",
                    "message": f"盘口暂不可用：{exc!r}",
                }
    return orderbooks


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
        return {
            "direction": "long_spot",
            "lower": 0.0,
            "upper": 0.0,
            "quality": "等待行情",
            "reason": "当前价格不足，暂不能计算建议区间。",
        }
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
    lower = min(price - width, recent_low * 1.002)
    upper = max(price + width, recent_high * 0.998)
    lower = max(price * 0.5, lower)
    upper = max(upper, price * 1.01)
    quality = "适合观察"
    if volatility_pct > 1.2:
        quality = "高波动"
    elif (upper - lower) / price < 0.04:
        quality = "区间偏窄"
    reason = f"近60分钟趋势 {trend_pct:+.2f}%，ATR约 {volatility_pct:.2f}%，参考近120根K线高低点。"
    return {
        "direction": direction,
        "lower": lower,
        "upper": upper,
        "quality": quality,
        "reason": reason,
        "trend_pct": trend_pct,
        "volatility_pct": volatility_pct,
        "recent_low": recent_low,
        "recent_high": recent_high,
    }


def _live_create_price_html(symbol: str, initial_price: float) -> str:
    import json

    symbol_json = json.dumps(str(symbol or "").upper().strip())
    initial_price_json = json.dumps(float(initial_price or 0))
    client_js = frontend_api_client_js("fetchCreateGridJson")
    return f"""
    <style>
      body {{ margin:0; background:transparent; font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
      .price-box {{
        height:72px;
        border:1px solid rgba(51,65,85,.72);
        border-radius:8px;
        background:rgba(15,23,42,.72);
        display:flex;
        flex-direction:column;
        justify-content:center;
        padding:0 10px;
        box-sizing:border-box;
      }}
      .label {{ color:#9CA3AF; font-size:12px; line-height:1.2; }}
      .value {{ color:#F0B90B; font-size:18px; line-height:1.3; font-weight:900; margin-top:4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
      .meta {{ color:#9CA3AF; font-size:10px; line-height:1.2; margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    </style>
    <div class="price-box">
      <div class="label">当前价格</div>
      <div class="value" id="createGridPrice">等待价格</div>
      <div class="meta" id="createGridMeta">1秒刷新</div>
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
      function render(source, updatedAt) {{
        document.getElementById("createGridPrice").textContent = price(lastPrice);
        document.getElementById("createGridMeta").textContent = `${{symbol}}｜${{source || "local"}}｜${{updatedAt || new Date().toLocaleTimeString()}}`;
      }}
      async function refreshPrice() {{
        try {{
          const ticker = await fetchCreateGridJson(`/api/ticker?symbol=${{encodeURIComponent(symbol)}}`);
          const tickerPrice = num(ticker.last_price || ticker.price);
          if (tickerPrice > 0) {{
            lastPrice = tickerPrice;
            render(ticker.source || "ticker", ticker.updated_at || ticker.close_time);
            return;
          }}
        }} catch (_) {{}}
        try {{
          const depth = await fetchCreateGridJson(`/api/orderbook?symbol=${{encodeURIComponent(symbol)}}`);
          const bids = Array.isArray(depth.bids) ? depth.bids : [];
          const asks = Array.isArray(depth.asks) ? depth.asks : [];
          const bid = bids.length ? num(bids[0].price ?? bids[0][0]) : 0;
          const ask = asks.length ? num(asks[0].price ?? asks[0][0]) : 0;
          if (bid > 0 && ask > 0) lastPrice = (bid + ask) / 2;
          else if (bid > 0) lastPrice = bid;
          else if (ask > 0) lastPrice = ask;
          render(depth.source || "orderbook", depth.updated_at);
        }} catch (err) {{
          render("等待行情", err && err.message ? err.message : "");
        }}
      }}
      render("初始化", "");
      refreshPrice();
      setInterval(refreshPrice, 1000);
    </script>
    """


def _live_grid_html(initial_summary: dict[str, Any]) -> str:
    import json

    initial_json = json.dumps(initial_summary, ensure_ascii=False, default=str)
    client_js = frontend_api_client_js("fetchGridJson")
    return f"""
    <style>
      :root {{ --bg:#050B14; --panel:#0F172A; --border:#1F2937; --border2:#334155; --text:#E5E7EB; --muted:#9CA3AF; --green:#00C087; --red:#F6465D; --yellow:#F0B90B; --blue:#3B82F6; }}
      body {{ margin:0; background:transparent; color:var(--text); font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
      .wrap {{ max-width:1180px; margin:0 auto; }}
      .grid-card {{ border:1px solid var(--border); background:linear-gradient(180deg, rgba(15,23,42,.96), rgba(5,11,20,.92)); border-radius:10px; padding:8px; }}
      .head {{ display:flex; justify-content:space-between; align-items:flex-start; gap:8px; margin-bottom:5px; }}
      .title {{ font-size:14px; font-weight:900; color:#fff; }}
      .status {{ color:var(--muted); font-size:11px; line-height:1.45; text-align:right; }}
      .metrics {{ display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:4px; margin:6px 0; }}
      .metric {{ border:1px solid rgba(51,65,85,.72); background:rgba(5,11,20,.42); border-radius:7px; padding:4px 5px; min-height:33px; }}
      .label {{ color:var(--muted); font-size:9px; line-height:1.1; }}
      .value {{ color:#fff; font-size:11px; font-weight:900; margin-top:2px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
      .green {{ color:var(--green)!important; }} .red {{ color:var(--red)!important; }} .yellow {{ color:var(--yellow)!important; }} .blue {{ color:var(--blue)!important; }}
      .selector {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:6px; margin:6px 0; }}
      .selector-card {{ border:1px solid rgba(51,65,85,.72); background:rgba(15,23,42,.64); border-radius:8px; padding:7px; cursor:pointer; text-align:left; color:var(--text); font:inherit; min-width:0; box-sizing:border-box; }}
      .selector-card.active {{ border-color:rgba(240,185,11,.82); background:rgba(240,185,11,.10); }}
      .selector-name {{ color:#fff; font-size:13px; font-weight:900; display:flex; align-items:center; justify-content:space-between; gap:6px; }}
      .selector-link {{ color:#fff; text-decoration:none; border-bottom:1px solid rgba(240,185,11,.72); }}
      .selector-meta {{ color:var(--muted); font-size:9.5px; margin-top:3px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
      .bot {{ border:1px solid rgba(51,65,85,.72); background:rgba(15,23,42,.62); border-radius:8px; padding:6px; margin-top:6px; }}
      .bot-head {{ display:grid; grid-template-columns:1.25fr .8fr .65fr .65fr .65fr; gap:4px; align-items:center; min-height:25px; border-bottom:1px solid rgba(51,65,85,.28); font-size:10px; }}
      .bot-title {{ font-weight:900; color:#fff; }}
      .mini-row {{ display:grid; grid-template-columns:.85fr .7fr 1fr 1fr; gap:3px; min-height:20px; align-items:center; border-bottom:1px solid rgba(51,65,85,.22); font-size:9.5px; }}
      .trade-row {{ display:grid; grid-template-columns:1.15fr .62fr .86fr .78fr .7fr; gap:3px; min-height:20px; align-items:center; border-bottom:1px solid rgba(51,65,85,.22); font-size:9.5px; }}
      .event-row {{ display:grid; grid-template-columns:1.05fr .72fr .72fr 2.2fr; gap:4px; min-height:20px; align-items:center; border-bottom:1px solid rgba(51,65,85,.22); font-size:9.5px; }}
      .history-panel {{ max-height:245px; overflow:auto; border:1px solid rgba(51,65,85,.42); border-radius:8px; background:rgba(5,11,20,.24); }}
      .orders-panel {{ max-height:190px; overflow:auto; border:1px solid rgba(51,65,85,.42); border-radius:8px; background:rgba(5,11,20,.24); }}
      .history-panel .trade-row,.history-panel .event-row {{ padding:0 4px; }}
      .orders-panel .mini-row {{ padding:0 4px; }}
      .cell {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; }}
      .content-cell {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; }}
      .row-head {{ color:var(--muted); font-weight:800; background:rgba(15,23,42,.8); border-radius:7px; }}
      .split {{ display:grid; grid-template-columns:1fr 1fr; gap:6px; margin-top:6px; align-items:start; }}
      .section-title {{ color:#fff; font-size:11px; font-weight:900; margin:6px 0 3px; }}
      .orderbook {{ display:grid; grid-template-columns:1fr 1fr; gap:6px; margin-top:6px; }}
      .ob-row {{ position:relative; display:grid; grid-template-columns:.86fr .72fr .9fr; gap:3px; align-items:center; min-height:20px; padding:1px 3px; border-bottom:1px solid rgba(51,65,85,.22); font-size:9.5px; overflow:hidden; }}
      .ob-row.header {{ color:var(--muted); font-weight:800; background:rgba(15,23,42,.8); border-radius:6px; }}
      .marker {{ display:inline-flex; align-items:center; justify-content:center; min-height:15px; border-radius:999px; padding:1px 5px; font-size:8px; font-weight:900; margin-left:4px; white-space:nowrap; }}
      .marker.buy {{ color:var(--green); border:1px solid rgba(0,192,135,.45); background:rgba(0,192,135,.10); }}
      .marker.sell {{ color:var(--red); border:1px solid rgba(246,70,93,.45); background:rgba(246,70,93,.10); }}
      .current-line {{ border:1px solid rgba(240,185,11,.42); background:rgba(240,185,11,.10); border-radius:7px; padding:4px 6px; margin:5px 0; display:flex; justify-content:space-between; align-items:center; font-size:10px; font-weight:900; }}
      .feed-line {{ color:var(--muted); font-size:9px; display:flex; justify-content:space-between; gap:8px; margin:2px 0 4px; }}
      .empty {{ border:1px solid var(--border); background:rgba(15,23,42,.62); border-radius:8px; padding:6px; color:var(--muted); font-size:10px; margin-top:5px; }}
      @media (max-width:720px) {{ .metrics {{ grid-template-columns:repeat(3,minmax(0,1fr)); }} .split,.orderbook {{ grid-template-columns:1fr; }} .bot-head {{ grid-template-columns:1.1fr .8fr .55fr .55fr; }} .bot-head .hide-sm {{ display:none; }} .trade-row {{ grid-template-columns:1fr .55fr .82fr .7fr .62fr; }} .event-row {{ grid-template-columns:1fr .72fr .72fr 1.5fr; }} }}
    </style>
    <div class="wrap">
      <div class="grid-card">
        <div class="head">
          <div><div class="title">独立网格实时监控</div><div class="label">组件内每秒刷新，不重载页面</div></div>
          <div class="status" id="gridStatus">初始化</div>
        </div>
        <div class="metrics" id="gridMetrics"></div>
        <div class="section-title">查看运行网格</div>
        <div id="gridSelector" class="selector"></div>
        <div id="gridBots"></div>
        <div class="split">
          <div><div class="section-title">最近成交</div><div id="gridTrades"></div></div>
          <div><div class="section-title">最近事件</div><div id="gridEvents"></div></div>
        </div>
      </div>
    </div>
    <script>
      {client_js}
      let gridData = {initial_json};
      let orderbooks = {{}};
      let livePrices = {{}};
      let selectedBotId = "";
      function num(v, d=0) {{ const n = Number(v); return Number.isFinite(n) ? n : d; }}
      function esc(v) {{
        return String(v ?? "").replace(/[&<>"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[ch]));
      }}
      function money(v) {{ return `${{num(v).toFixed(2)}} USDT`; }}
      function signed(v) {{ const n = num(v); return `${{n >= 0 ? "+" : ""}}${{n.toFixed(4)}} USDT`; }}
      function pct(v) {{ const n = num(v); return `${{n >= 0 ? "+" : ""}}${{n.toFixed(2)}}%`; }}
      function price(v) {{
        const n = num(v, NaN);
        if (!Number.isFinite(n) || n <= 0) return "等待价格";
        if (n >= 1000) return n.toLocaleString(undefined, {{maximumFractionDigits:2}});
        if (n >= 1) return n.toLocaleString(undefined, {{maximumFractionDigits:4}});
        return n.toFixed(8).replace(/0+$/,"").replace(/\\.$/,"");
      }}
      function metric(label, value, cls="") {{
        return `<div class="metric"><div class="label">${{esc(label)}}</div><div class="value ${{esc(cls)}}">${{esc(value)}}</div></div>`;
      }}
      function sideText(side) {{ return side === "buy" ? "买入" : "卖出"; }}
      function fmtTime(value) {{
        const raw = String(value || "");
        return raw.includes(" ") ? raw.split(" ").slice(-1)[0] : raw;
      }}
      function cleanEventContent(value) {{
        return String(value || "")
          .replace(/^buy\\s+/i, "买入 ")
          .replace(/^sell\\s+/i, "卖出 ");
      }}
      function livePriceFor(symbol, bot) {{
        const botId = String((bot || {{}}).bot_id || "");
        if (botId && num(livePrices[botId]) > 0) return num(livePrices[botId]);
        if (symbol && num(livePrices[symbol]) > 0) return num(livePrices[symbol]);
        const depth = orderbooks[symbol] || {{}};
        const bids = Array.isArray(depth.bids) ? depth.bids : [];
        const asks = Array.isArray(depth.asks) ? depth.asks : [];
        const bid = bids.length ? num(bids[0].price ?? bids[0][0]) : 0;
        const ask = asks.length ? num(asks[0].price ?? asks[0][0]) : 0;
        if (bid > 0 && ask > 0) return (bid + ask) / 2;
        if (bid > 0) return bid;
        if (ask > 0) return ask;
        return num((bot || {{}}).mark_price || (bot || {{}}).last_price);
      }}
      function priceFromOrderbook(depth) {{
        const bids = Array.isArray((depth || {{}}).bids) ? depth.bids : [];
        const asks = Array.isArray((depth || {{}}).asks) ? depth.asks : [];
        const bid = bids.length ? num(bids[0].price ?? bids[0][0]) : 0;
        const ask = asks.length ? num(asks[0].price ?? asks[0][0]) : 0;
        if (bid > 0 && ask > 0) return (bid + ask) / 2;
        if (bid > 0) return bid;
        if (ask > 0) return ask;
        return 0;
      }}
      function setLivePrice(bot, value) {{
        const p = num(value);
        if (p <= 0 || !bot) return;
        const botId = String(bot.bot_id || "");
        const symbol = String(bot.symbol || "");
        if (botId) livePrices[botId] = p;
        if (symbol) livePrices[symbol] = p;
      }}
      function orderMarker(levelPrice, orders, side, step) {{
        const tolerance = Math.max(num(step) * .45, Math.abs(num(levelPrice)) * .00001);
        const hits = orders.filter(o => o.side === side && Math.abs(num(o.price) - num(levelPrice)) <= tolerance);
        if (!hits.length) return "";
        return `<span class="marker ${{side}}">${{side === "buy" ? "买挂" : "卖挂"}}${{hits.length > 1 ? hits.length : ""}}</span>`;
      }}
      function nearestOrders(bot, currentPrice) {{
        const current = num(currentPrice || bot.mark_price || bot.last_price);
        const orders = Array.isArray(bot.open_orders) ? bot.open_orders : [];
        const buys = orders.filter(o => o.side === "buy").sort((a,b) => num(b.price)-num(a.price));
        const sells = orders.filter(o => o.side === "sell").sort((a,b) => num(a.price)-num(b.price));
        const nextBuy = buys.find(o => num(o.price) <= current) || buys[0];
        const nextSell = sells.find(o => num(o.price) >= current) || sells[0];
        return `下方买挂：<span class="green">${{nextBuy ? price(nextBuy.price) : "-"}}</span>｜上方卖挂：<span class="red">${{nextSell ? price(nextSell.price) : "-"}}</span>`;
      }}
      function renderOrderbook(bot) {{
        const symbol = bot.symbol || "";
        const depth = orderbooks[symbol] || {{}};
        const bids = Array.isArray(depth.bids) ? depth.bids.slice(0, 8) : [];
        const asks = Array.isArray(depth.asks) ? depth.asks.slice(0, 8).reverse() : [];
        const orders = Array.isArray(bot.open_orders) ? bot.open_orders : [];
        const step = num(bot.grid_step);
        const current = livePriceFor(symbol, bot);
        const sideRows = (rows, side) => rows.map(level => {{
          const p = num(level.price ?? level[0]);
          const q = num(level.quantity ?? level.qty ?? level[1]);
          return `<div class="ob-row"><div class="${{side === "buy" ? "green" : "red"}}">${{esc(price(p))}}${{orderMarker(p, orders, side, step)}}</div><div>${{esc(q.toFixed(5))}}</div><div>${{esc((p*q).toFixed(0))}}</div></div>`;
        }}).join("");
        const feedText = depth.ok === false ? (depth.message || "盘口暂不可用") : `盘口运行中｜${{depth.updated_at || "等待时间"}}｜${{depth.source || "local"}}`;
        return `<div class="section-title">${{esc(symbol)}} 实时盘口｜挂单标注</div>
          <div class="feed-line"><span>${{esc(feedText)}}</span><span>${{esc((bids.length + asks.length) ? `${{bids.length}}买/${{asks.length}}卖` : "等待深度")}}</span></div>
          <div class="current-line"><span>当前价</span><span class="yellow">${{esc(price(current))}}</span></div>
          <div class="orderbook">
            <div><div class="ob-row header"><div>卖盘/挂单</div><div>数量</div><div>金额</div></div>${{sideRows(asks, "sell") || '<div class="empty">等待卖盘</div>'}}</div>
            <div><div class="ob-row header"><div>买盘/挂单</div><div>数量</div><div>金额</div></div>${{sideRows(bids, "buy") || '<div class="empty">等待买盘</div>'}}</div>
          </div>`;
      }}
      function renderSelector(bots) {{
        const selector = document.getElementById("gridSelector");
        if (!selector) return;
        if (!bots.length) {{
          selector.innerHTML = '<div class="empty">当前没有运行网格。</div>';
          selectedBotId = "";
          return;
        }}
        if (!selectedBotId || !bots.some(bot => String(bot.bot_id || "") === selectedBotId)) {{
          selectedBotId = String(bots[0].bot_id || "");
        }}
        selector.innerHTML = bots.map(bot => {{
          const botId = String(bot.bot_id || "");
          const symbol = String(bot.symbol || "-").toUpperCase();
          const pnl = num(bot.floating_pnl);
          const isActive = botId === selectedBotId;
          return `<div role="button" tabindex="0" class="selector-card${{isActive ? " active" : ""}}" data-bot-id="${{esc(botId)}}">
            <div class="selector-name"><a class="selector-link" href="?page=signals&symbol=${{encodeURIComponent(symbol)}}&grid_view=1#kline-area" target="_self">${{esc(symbol)}}</a><span class="${{pnl >= 0 ? "green" : "red"}}">${{esc(signed(pnl))}}</span></div>
            <div class="selector-meta">${{esc(bot.grid_direction || "long_spot")}}｜${{esc(bot.grid_count || 0)}}格｜成交 ${{esc(bot.filled_trades || 0)}}｜买 ${{esc(bot.open_buy_orders || 0)}} / 卖 ${{esc(bot.open_sell_orders || 0)}}</div>
            <div class="selector-meta">${{esc(bot.created_time || bot.bot_id || "")}}</div>
          </div>`;
        }}).join("");
        selector.querySelectorAll(".selector-card").forEach(button => {{
          const selectCard = () => {{
            selectedBotId = String(button.dataset.botId || "");
            render(gridData);
            refreshSelectedMarket(gridData).then(() => render(gridData));
          }};
          button.addEventListener("click", selectCard);
          button.addEventListener("keydown", event => {{
            if (event.key === "Enter" || event.key === " ") {{
              event.preventDefault();
              selectCard();
            }}
          }});
        }});
        selector.querySelectorAll(".selector-link").forEach(link => {{
          link.addEventListener("click", event => event.stopPropagation());
        }});
      }}
      function render(data) {{
        data = data || {{}};
        if (data.orderbooks && typeof data.orderbooks === "object") {{
          orderbooks = {{...orderbooks, ...data.orderbooks}};
        }}
        const allBots = Array.isArray(data.bots) ? data.bots : [];
        const bots = allBots.filter(bot => bot.status === "running" || bot.status === "paused");
        bots.forEach(bot => setLivePrice(bot, priceFromOrderbook(orderbooks[bot.symbol]) || bot.mark_price || bot.last_price));
        renderSelector(bots);
        const selectedBots = selectedBotId ? bots.filter(bot => String(bot.bot_id || "") === selectedBotId) : bots.slice(0, 1);
        const runningSymbols = new Set(selectedBots.map(bot => String(bot.symbol || "").toUpperCase()).filter(Boolean));
        const runningIds = new Set(selectedBots.map(bot => String(bot.bot_id || "")).filter(Boolean));
        const allTrades = Array.isArray(data.trades) ? data.trades : [];
        const allEvents = Array.isArray(data.events) ? data.events : [];
        const trades = allTrades.filter(row => runningIds.has(String(row.bot_id || "")) || runningSymbols.has(String(row.symbol || "").toUpperCase()));
        const events = allEvents.filter(row => runningIds.has(String(row.bot_id || "")) || runningSymbols.has(String(row.symbol || "").toUpperCase()));
        const pnl = num(data.total_pnl);
        document.getElementById("gridStatus").innerHTML = `状态：${{data.ok === false ? "异常" : "运行"}}<br>更新时间：${{new Date().toLocaleTimeString()}}<br>来源：${{data.source || "local"}}`;
        document.getElementById("gridMetrics").innerHTML = [
          metric("运行网格", String(data.total_running_bots || 0), data.total_running_bots ? "green" : "yellow"),
          metric("投入资金", money(data.total_investment), "blue"),
          metric("当前权益", money(data.total_equity), pnl >= 0 ? "green" : "red"),
          metric("总盈亏", signed(pnl), pnl >= 0 ? "green" : "red"),
          metric("收益率", pct(data.total_pnl_pct), num(data.total_pnl_pct) >= 0 ? "green" : "red"),
          metric("成交", String(trades.length), "blue"),
        ].join("");
        document.getElementById("gridBots").innerHTML = selectedBots.length ? selectedBots.map(bot => {{
          const livePrice = livePriceFor(bot.symbol || "", bot);
          const botPnl = num(bot.floating_pnl);
          const orders = Array.isArray(bot.open_orders) ? bot.open_orders : [];
          const buys = orders.filter(o => o.side === "buy").sort((a,b) => num(b.price)-num(a.price));
          const sells = orders.filter(o => o.side === "sell").sort((a,b) => num(a.price)-num(b.price));
          const orderRows = buys.concat(sells).map(order => `<div class="mini-row"><div class="${{order.side === "buy" ? "green" : "red"}}">${{esc(sideText(order.side))}}</div><div>${{esc(order.level_index ?? "")}}</div><div>${{esc(price(order.price))}}</div><div>${{esc(order.side === "buy" ? num(order.quote_amount).toFixed(3) : num(order.quantity).toFixed(8))}}</div></div>`).join("");
          return `<div class="bot">
            <div class="bot-head">
              <div><span class="bot-title">${{esc(bot.symbol)}}</span>｜${{esc(bot.status)}}｜${{esc(bot.grid_direction || "long_spot")}}｜${{esc(bot.grid_count)}}格</div>
              <div class="${{botPnl >= 0 ? "green" : "red"}}">${{esc(signed(botPnl))}}</div>
              <div class="blue">成交 ${{esc(bot.filled_trades || 0)}}</div>
              <div class="green">买 ${{esc(bot.open_buy_orders || 0)}}</div>
              <div class="red hide-sm">卖 ${{esc(bot.open_sell_orders || 0)}}</div>
            </div>
            <div class="metrics">
              ${{metric("当前价", price(livePrice), "")}}
              ${{metric("区间", `${{price(bot.lower_price)}}-${{price(bot.upper_price)}}`, "")}}
              ${{metric("余额", money(bot.quote_balance), "")}}
              ${{metric("持币", num(bot.base_inventory).toFixed(8), "")}}
              ${{metric("空头", num(bot.short_inventory).toFixed(8), "")}}
              ${{metric("已实现利润", signed(bot.realized_profit), num(bot.realized_profit) >= 0 ? "green" : "red")}}
              ${{metric("手续费", money(bot.total_fee), "yellow")}}
            </div>
            <div class="current-line"><span>${{nearestOrders(bot, livePrice)}}</span><span>自动跟随挂单</span></div>
            <div class="section-title">全部挂单</div>
            <div class="orders-panel">
              <div class="mini-row row-head"><div>方向</div><div>格子</div><div>价格</div><div>数量/金额</div></div>
              ${{orderRows || '<div class="empty">暂无挂单</div>'}}
            </div>
            ${{renderOrderbook(bot)}}
          </div>`;
        }}).join("") : '<div class="empty">当前暂无运行网格。</div>';
        document.getElementById("gridTrades").innerHTML = trades.length ? `<div class="history-panel"><div class="trade-row row-head"><div>时间</div><div>方向</div><div>价格</div><div>数量</div><div>利润</div></div>` + trades.slice(0, 50).map(row => `<div class="trade-row"><div class="cell" title="${{esc(row.time || "")}}">${{esc(fmtTime(row.time))}}</div><div class="${{row.side === "buy" ? "green" : "red"}}">${{esc(sideText(row.side))}}</div><div class="cell">${{esc(price(row.price))}}</div><div class="cell">${{esc(num(row.quantity).toFixed(8))}}</div><div class="${{num(row.profit) >= 0 ? "green" : "red"}}">${{esc(num(row.profit).toFixed(4))}}</div></div>`).join("") + `</div>` : '<div class="empty">暂无成交。</div>';
        document.getElementById("gridEvents").innerHTML = events.length ? `<div class="history-panel"><div class="event-row row-head"><div>时间</div><div>类型</div><div>币种</div><div>内容</div></div>` + events.slice(0, 50).map(row => `<div class="event-row"><div class="cell" title="${{esc(row.time || "")}}">${{esc(fmtTime(row.time))}}</div><div class="cell">${{esc(row.event_type || "")}}</div><div class="cell">${{esc(row.symbol || "")}}</div><div class="content-cell" title="${{esc(cleanEventContent(row.content))}}">${{esc(cleanEventContent(row.content))}}</div></div>`).join("") + `</div>` : '<div class="empty">暂无事件。</div>';
      }}
      async function refreshOrderbooks(data) {{
        const bots = Array.isArray(data.bots) ? data.bots.filter(b => b.status === "running" || b.status === "paused") : [];
        const selected = bots.find(bot => String(bot.bot_id || "") === selectedBotId);
        const prioritized = [];
        if (selected) prioritized.push(selected);
        bots.forEach(bot => {{
          if (!prioritized.some(item => String(item.bot_id || "") === String(bot.bot_id || ""))) prioritized.push(bot);
        }});
        await Promise.all(prioritized.slice(0, 4).map(async bot => {{
          try {{
            const ticker = await fetchGridJson(`/api/ticker?symbol=${{encodeURIComponent(bot.symbol)}}`);
            setLivePrice(bot, ticker.last_price || ticker.price);
          }} catch (_) {{}}
          try {{
            const depth = await fetchGridJson(`/api/orderbook?symbol=${{encodeURIComponent(bot.symbol)}}`);
            orderbooks[bot.symbol] = depth;
            setLivePrice(bot, priceFromOrderbook(depth));
          }} catch (_) {{}}
        }}));
      }}
      async function refreshSelectedMarket(data) {{
        const bots = Array.isArray((data || {{}}).bots) ? data.bots.filter(b => b.status === "running" || b.status === "paused") : [];
        const bot = bots.find(item => String(item.bot_id || "") === selectedBotId) || bots[0];
        if (!bot || !bot.symbol) return;
        try {{
          const ticker = await fetchGridJson(`/api/ticker?symbol=${{encodeURIComponent(bot.symbol)}}`);
          setLivePrice(bot, ticker.last_price || ticker.price);
        }} catch (_) {{}}
        try {{
          const depth = await fetchGridJson(`/api/orderbook?symbol=${{encodeURIComponent(bot.symbol)}}`);
          orderbooks[bot.symbol] = depth;
          setLivePrice(bot, priceFromOrderbook(depth));
        }} catch (_) {{}}
      }}
      async function refreshGrid() {{
        try {{
          const data = await fetchGridJson("/api/grid_summary");
          gridData = data;
          await refreshOrderbooks(gridData);
          render(gridData);
        }} catch (err) {{
          document.getElementById("gridStatus").innerHTML = `状态：接口不可用<br>${{err && err.message ? err.message : "正在重试"}}`;
          render(gridData);
        }}
      }}
      render(gridData);
      refreshGrid();
      setInterval(refreshGrid, 1000);
    </script>
    """


def render_grid_trading_page(page_titles: dict[str, tuple[str, str]], version: str, current_symbol: str) -> None:
    render_page_head("grid_trading", page_titles, version)
    query_grid_symbol = str(st.query_params.get("grid_symbol", "") or "").upper().strip()
    query_grid_direction = str(st.query_params.get("grid_direction", "") or "").strip()
    symbol = query_grid_symbol or str(current_symbol or "BTCUSDT").upper().strip()
    if query_grid_symbol:
        selected_price = _price(symbol)
    else:
        selected_price = 0.0
    price_map = _grid_price_map(symbol)
    if selected_price > 0:
        price_map[symbol] = selected_price
    if price_map:
        update_grid_bots(price_map)
    summary = get_grid_summary(price_map)
    bots = summary.get("bots") or []
    summary["orderbooks"] = _grid_orderbook_map(bots)
    trades = summary.get("trades") or []
    events = summary.get("events") or []
    current_price = price_map.get(symbol, 0) or selected_price or _fallback_price_from_bots(symbol, bots)
    running_count = int(summary.get("total_running_bots", 0) or 0)
    price_status = "实时行情" if price_map.get(symbol, 0) > 0 else "缓存价" if current_price > 0 else "等待行情"

    render_metric_grid(
        [
            ("运行网格", str(running_count), "green" if running_count else "yellow"),
            ("投入资金", money_text(summary.get("total_investment")), "blue"),
            ("当前权益", money_text(summary.get("total_equity")), "green" if _to_float(summary.get("total_pnl")) >= 0 else "red"),
            ("总盈亏", f"{_to_float(summary.get('total_pnl')):+.4f} USDT", "green" if _to_float(summary.get("total_pnl")) >= 0 else "red"),
            ("收益率", f"{_to_float(summary.get('total_pnl_pct')):+.2f}%", "green" if _to_float(summary.get("total_pnl_pct")) >= 0 else "red"),
            ("当前币价", format_price(current_price), ""),
            ("价格状态", price_status, "green" if price_status == "实时行情" else "yellow"),
            ("刷新", "组件内1秒", "blue"),
        ]
    )
    if running_count > 0 and price_status != "实时行情":
        st.warning("当前网格页显示的是缓存价。后台未拿到该币种最新行情前，不会产生新的网格成交。")

    tabs = st.tabs(["创建网格", "运行网格", "成交记录", "事件日志"])
    with tabs[0]:
        st.markdown(
            '<div class="app-shell"><div class="module-card"><div class="module-title">创建独立模拟网格</div><div class="module-desc">仅使用本地模拟资金和公共行情，不影响当前模拟交易开仓规则。</div></div></div>',
            unsafe_allow_html=True,
        )
        recommendations = build_grid_recommendations(8)
        if recommendations:
            cards = []
            for item in recommendations:
                direction = str(item.get("suggested_direction") or "long_spot")
                href = f'?page=grid_trading&grid_symbol={item.get("symbol")}&grid_direction={direction}'
                cards.append(
                    f"""<a class="status-card" href="{href}" target="_self" style="display:block;text-decoration:none;color:inherit;margin-top:6px;">
                    <b>{item.get("symbol")}</b>｜{GRID_DIRECTION_LABELS.get(direction, direction)}｜评分 {item.get("grid_score")}｜{item.get("quality")}<br>
                    区间 {format_price(item.get("lower_price"))} - {format_price(item.get("upper_price"))}｜
                    ATR {float(item.get("atr_pct", 0) or 0):.2f}%｜趋势 {float(item.get("trend_pct", 0) or 0):+.2f}%<br>
                    {"；".join(str(x) for x in item.get("reasons", [])[:4])}
                    </a>"""
                )
            st.markdown(
                f'<div class="app-shell"><div class="module-title">网格推荐对象</div>{"".join(cards)}</div>',
                unsafe_allow_html=True,
            )
            with st.container():
                st.markdown("**自动模拟网格**")
                a1, a2, a3, a4 = st.columns(4)
                auto_investment = a1.number_input("单网格资金", min_value=10.0, max_value=10000.0, value=100.0, step=10.0, key="auto_grid_investment")
                auto_grid_count = a2.number_input("自动网格数", min_value=2, max_value=200, value=20, step=1, key="auto_grid_count")
                auto_min_score = a3.number_input("最低评分", min_value=50, max_value=95, value=70, step=1, key="auto_grid_min_score")
                auto_fee_rate = a4.number_input("手续费率", min_value=0.0, max_value=0.01, value=0.0004, step=0.0001, format="%.4f", key="auto_grid_fee")
                b1, b2 = st.columns(2)
                if b1.button("自动开一个推荐网格", width="stretch"):
                    result = auto_open_recommended_grids(1, int(auto_min_score), float(auto_investment), int(auto_grid_count), float(auto_fee_rate))
                    if result.get("opened_count"):
                        st.success(f"已自动创建 {result.get('opened_count')} 个模拟网格。")
                    else:
                        st.warning("没有符合条件的推荐对象：" + "；".join(str(row.get("symbol")) + " " + str(row.get("reason")) for row in result.get("skipped", [])[:3]))
                    st.rerun()
                if b2.button("自动开前三个推荐网格", width="stretch"):
                    result = auto_open_recommended_grids(3, int(auto_min_score), float(auto_investment), int(auto_grid_count), float(auto_fee_rate))
                    if result.get("opened_count"):
                        st.success(f"已自动创建 {result.get('opened_count')} 个模拟网格。")
                    else:
                        st.warning("没有符合条件的推荐对象：" + "；".join(str(row.get("symbol")) + " " + str(row.get("reason")) for row in result.get("skipped", [])[:3]))
                    st.rerun()
        suggestion = _grid_range_suggestion(symbol, current_price)
        default_lower = _to_float(suggestion.get("lower"), current_price * 0.94 if current_price else 0.0)
        default_upper = _to_float(suggestion.get("upper"), current_price * 1.06 if current_price else 0.0)
        suggested_direction = query_grid_direction if query_grid_direction in GRID_DIRECTION_LABELS else str(suggestion.get("direction") or "long_spot")
        st.markdown(
            f"""<div class="app-shell"><div class="status-card">
            <b>区间建议</b>｜{GRID_DIRECTION_LABELS.get(suggested_direction, suggested_direction)}｜{suggestion.get("quality", "-")}<br>
            建议区间：{format_price(default_lower)} - {format_price(default_upper)}｜
            近高低：{format_price(suggestion.get("recent_low"))} - {format_price(suggestion.get("recent_high"))}<br>
            {suggestion.get("reason", "")}
            </div></div>""",
            unsafe_allow_html=True,
        )
        with st.form("create_grid_bot_form"):
            c1, c2 = st.columns(2)
            new_symbol = c1.text_input("交易对象", value=symbol)
            investment = c2.number_input("投入 USDT", min_value=10.0, max_value=100000.0, value=100.0, step=10.0)
            direction_keys = list(GRID_DIRECTION_LABELS.keys())
            direction_index = direction_keys.index(suggested_direction) if suggested_direction in direction_keys else 0
            grid_direction = st.radio("网格方向", direction_keys, index=direction_index, format_func=lambda item: GRID_DIRECTION_LABELS.get(item, item), horizontal=True)
            c3, c_live, c4, c5 = st.columns([1, 1, 1, 0.9])
            lower = c3.number_input("区间下限", min_value=0.0, value=float(default_lower), step=0.0001, format="%.8f")
            c_live.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            with c_live:
                components.html(_live_create_price_html(str(new_symbol).upper().strip(), current_price), height=78, scrolling=False)
            upper = c4.number_input("区间上限", min_value=0.0, value=float(default_upper), step=0.0001, format="%.8f")
            grid_count = c5.number_input("网格数量", min_value=2, max_value=200, value=20, step=1)
            fee_rate = st.number_input("模拟手续费率", min_value=0.0, max_value=0.01, value=0.0004, step=0.0001, format="%.4f")
            preview_price = _price(str(new_symbol).upper().strip())
            ok, reasons = validate_grid_config(str(new_symbol).upper().strip(), float(lower), float(upper), int(grid_count), float(investment), preview_price, str(grid_direction))
            if reasons:
                st.warning("；".join(reasons))
            if st.form_submit_button("启动模拟网格", width="stretch"):
                try:
                    bot = create_grid_bot(str(new_symbol).upper().strip(), float(lower), float(upper), int(grid_count), float(investment), preview_price, float(fee_rate), str(grid_direction))
                    st.success(f"已启动网格：{bot.get('bot_id')}")
                    st.rerun()
                except Exception as exc:
                    st.error(f"启动失败：{exc}")

    with tabs[1]:
        components.html(_live_grid_html(summary), height=1180, scrolling=True)
        active_bots = [bot for bot in bots if bot.get("status") in {"running", "paused"}]
        if active_bots:
            st.markdown("**网格控制**")
        for bot in active_bots:
            bot_id = str(bot.get("bot_id") or "")
            bot_symbol = str(bot.get("symbol") or "-")
            status = str(bot.get("status") or "")
            direction_text = GRID_DIRECTION_LABELS.get(str(bot.get("grid_direction") or "long_spot"), str(bot.get("grid_direction") or "long_spot"))
            st.caption(f"{bot_symbol}｜{direction_text}｜{status}｜{bot_id}")
            c1, c2, c3, c4, c5 = st.columns(5)
            if status == "running":
                if c1.button("暂停补单", key=f"pause_{bot_id}", width="stretch"):
                    pause_grid_bot(bot_id, "页面暂停补单")
                    st.rerun()
            else:
                if c1.button("恢复运行", key=f"resume_{bot_id}", width="stretch"):
                    resume_grid_bot(bot_id, "页面恢复运行")
                    st.rerun()
            if c2.button("停止保留", key=f"stop_keep_{bot_id}", width="stretch"):
                stop_grid_bot(bot_id, "停止策略并保留持仓和挂单记录")
                st.rerun()
            if c3.button("撤销挂单", key=f"cancel_{bot_id}", width="stretch"):
                cancel_grid_orders(bot_id, "停止策略并撤销全部模拟挂单")
                st.rerun()
            if c4.button("市价平仓", key=f"close_{bot_id}", width="stretch"):
                close_price = _price(bot_symbol) or _to_float(bot.get("mark_price"), _to_float(bot.get("last_price")))
                close_grid_position(bot_id, close_price, "停止策略、撤销挂单并按当前价市价平仓")
                st.rerun()
            if c5.button("紧急强停", key=f"emergency_{bot_id}", width="stretch"):
                close_price = _price(bot_symbol) or _to_float(bot.get("mark_price"), _to_float(bot.get("last_price")))
                close_grid_position(bot_id, close_price, "紧急强停：撤销挂单并按当前价强制平仓", emergency=True)
                st.rerun()

    with tabs[2]:
        if not trades:
            st.info("暂无网格成交。")
        else:
            st.dataframe(
                [
                    {
                        "时间": row.get("time"),
                        "币种": row.get("symbol"),
                        "方向": "买入" if row.get("side") == "buy" else "卖出",
                        "价格": format_price(row.get("price")),
                        "数量": f"{float(row.get('quantity', 0) or 0):.8f}",
                        "利润": f"{float(row.get('profit', 0) or 0):+.4f}",
                        "手续费": f"{float(row.get('fee', 0) or 0):.4f}",
                    }
                    for row in trades[:200]
                ],
                width="stretch",
                hide_index=True,
            )

    with tabs[3]:
        if not events:
            st.info("暂无网格事件。")
        for event in events[:100]:
            st.caption(f"{event.get('time')}｜{event.get('event_type')}｜{event.get('symbol')}｜{event.get('content')}")

"""Orderbook and whale-monitor panels for the signals page."""

from __future__ import annotations

import json
from html import escape
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from app_pages.kline_page import live_refresh_due
from components.local_api import frontend_api_client_js
from services import market_cache
from services.background_refresher import refresh_orderbook_now, refresh_symbol_now, refresh_whales_now
from services.orderbook_analyzer import analyze_orderbook
from utils.formatters import format_compact, format_percent, format_price


def _signal_color(value: str) -> str:
    if value in {"资金流入", "健康上涨", "健康下跌", "支持交易", "轻仓支持"}:
        return "green"
    if value in {"资金恐慌", "高风险", "禁止开仓", "反对交易"}:
        return "red"
    if value in {"中等风险", "横盘震荡", "观望", "中性", "谨慎交易"}:
        return "yellow"
    return "blue"

def _orderbook_level_html(level: dict[str, Any], side: str, max_quantity: float, large_order: dict[str, Any] | None) -> str:
    """生成单行盘口 HTML。"""
    quantity = float(level.get("quantity", 0) or 0)
    width = 0 if max_quantity <= 0 else min(100, quantity / max_quantity * 100)
    side_class = "ask" if side == "ask" else "bid"
    color_class = "red" if side == "ask" else "green"
    is_large = bool(large_order and large_order.get("price_text") == level.get("price_text"))
    large_class = " large" if is_large else ""
    return (
        f'<div class="orderbook-row{large_class}">'
        f'<div class="depth-bar {side_class}" style="width:{width:.2f}%;"></div>'
        f'<div class="ob-cell {color_class}">{level.get("price_text", "-")}</div>'
        f'<div class="ob-cell ob-right">{level.get("quantity_text", "-")}</div>'
        f'<div class="ob-cell ob-right">{level.get("cumulative_text", "-")}</div>'
        "</div>"
    )


def _large_order_text(title: str, large_order: dict[str, Any] | None) -> str:
    """格式化大单监控文本。"""
    if not large_order:
        return f"{title}：暂无异常"
    distance = large_order.get("distance_percent")
    distance_text = f"{distance:.2f}%" if distance is not None else "待确认"
    return f'{title}：{large_order.get("price_text")} / {large_order.get("quantity_text")} / 距离 {distance_text}'


def _whale_order_text(order: dict[str, Any] | None, empty_text: str) -> str:
    """格式化大单订单文本，兼容后端不同字段。"""
    if not order:
        return empty_text
    amount = order.get("amount")
    if amount in (None, "") and not order.get("amount_text"):
        return empty_text
    price_text = order.get("price_text") or format_price(order.get("price"))
    quantity_text = order.get("quantity_text") or format_compact(order.get("quantity"))
    amount_text = order.get("amount_text") or format_compact(amount)
    return f"{price_text} / {quantity_text} / {amount_text}"


def _whale_trade_rows_html(whale: dict[str, Any] | None) -> str:
    """渲染最新大单或最新成交列表。"""
    if not whale:
        return '<div class="status-card">大单数据正在初始化。</div>'
    rows = list(whale.get("latest") or [])
    showing_recent = False
    if not rows:
        rows = list(whale.get("recent_trades") or [])
        showing_recent = bool(rows)
    rows = rows[:6]
    if not rows:
        return '<div class="status-card">当前暂无达到阈值的大单，等待下一轮成交同步。</div>'
    note = '<div class="status-card">当前未触发大单阈值，以下显示最新成交。</div>' if showing_recent else ""
    body = "".join(
        f"""<div class="orderbook-row">
          <div class="{ "green" if str(row.get("direction")) == "主动买入" else "red" }">{escape(str(row.get("time", "-")))}</div>
          <div>{escape(str(row.get("direction", "-")))}</div>
          <div class="ob-right">{escape(str(row.get("price_text") or format_price(row.get("price"))))}</div>
          <div class="ob-right">{escape(str(row.get("amount_text") or format_compact(row.get("amount"))))}</div>
        </div>"""
        for row in rows
    )
    return (
        note
        + '<div class="orderbook-row header"><div>时间</div><div>方向</div><div class="ob-right">价格</div><div class="ob-right">金额</div></div>'
        + body
    )


def frontend_orderbook_html(
    symbol: str,
    initial_orderbook: dict[str, Any] | None = None,
    initial_ticker: dict[str, Any] | None = None,
    initial_whale: dict[str, Any] | None = None,
) -> str:
    """生成前端自刷新的盘口订单簿组件。"""
    initial_orderbook_json = json.dumps(initial_orderbook or {}, ensure_ascii=False, default=str)
    initial_ticker_json = json.dumps(initial_ticker or {}, ensure_ascii=False, default=str)
    initial_whale_json = json.dumps(initial_whale or {}, ensure_ascii=False, default=str)
    return f"""
    <style>
      body {{ margin:0; background:transparent; font-family:Arial,'Microsoft YaHei',sans-serif; color:#E5E7EB; }}
      .orderbook-card {{ border:1px solid #1F2937; background:linear-gradient(180deg, rgba(15,23,42,.96), rgba(5,11,20,.92)); border-radius:14px; padding:10px; }}
      .orderbook-head {{ display:flex; justify-content:space-between; gap:8px; margin-bottom:6px; }}
      .orderbook-title {{ font-size:16px; font-weight:900; color:#fff; }}
      .desc,.status {{ color:#9CA3AF; font-size:11px; line-height:1.45; }}
      .status {{ text-align:right; }}
      .grid {{ display:grid; grid-template-columns:1fr; gap:8px; align-items:start; }}
      .book-panel {{ min-width:0; }}
      .summary-rail {{ min-width:0; border:1px solid rgba(31,41,55,.9); background:rgba(3,7,18,.34); border-radius:12px; padding:7px; }}
      @media (min-width:600px) {{
        .grid {{ grid-template-columns:minmax(0,1.62fr) minmax(170px,.9fr); }}
        .summary-rail {{ position:sticky; top:0; }}
      }}
      .row {{ position:relative; display:grid; grid-template-columns:1fr 1fr 1fr; align-items:center; min-height:22px; padding:1px 4px; border-bottom:1px solid rgba(51,65,85,.26); font-size:10px; overflow:hidden; }}
      .header {{ color:#9CA3AF; font-weight:800; background:rgba(15,23,42,.8); border-radius:7px; }}
      .bar {{ position:absolute; right:0; top:0; bottom:0; opacity:.22; z-index:0; }}
      .askbar {{ background:#F6465D; }} .bidbar {{ background:#00C087; }}
      .cell {{ position:relative; z-index:1; }} .right {{ text-align:right; }}
      .red {{ color:#F6465D; }} .green {{ color:#00C087; }} .yellow {{ color:#F0B90B; }} .blue {{ color:#3B82F6; }}
      .last {{ text-align:center; padding:8px; border-top:1px solid #1F2937; border-bottom:1px solid #1F2937; margin:4px 0; }}
      .last-price {{ color:#fff; font-size:20px; font-weight:900; }}
      .summary {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px; margin-top:8px; }}
      .side-summary {{ display:grid; grid-template-columns:1fr; gap:6px; margin-top:7px; }}
      .box {{ min-height:42px; border:1px solid rgba(31,41,55,.95); background:linear-gradient(180deg, rgba(15,23,42,.82), rgba(8,13,24,.74)); border-radius:10px; padding:6px 8px; box-sizing:border-box; }}
      .side-summary .box {{ min-height:50px; display:flex; flex-direction:column; justify-content:center; }}
      .label {{ color:#9CA3AF; font-size:10px; font-weight:800; letter-spacing:0; }} .value {{ font-size:16px; font-weight:900; margin-top:3px; line-height:1.12; word-break:break-word; }}
      .side-summary .value {{ font-size:17px; }}
      .ratio {{ height:8px; display:flex; overflow:hidden; border-radius:999px; background:#111827; border:1px solid #1F2937; }}
      .buy {{ background:#00C087; }} .sell {{ background:#F6465D; }}
      .section-title {{ margin-top:10px; padding-top:8px; border-top:1px solid #1F2937; color:#fff; font-size:14px; font-weight:900; }}
      .whale-panel {{ margin-top:8px; border-top:1px solid #1F2937; padding-top:8px; }}
      .whale-row {{ display:grid; grid-template-columns:.72fr 1fr 1fr 1fr; gap:4px; min-height:22px; align-items:center; border-bottom:1px solid rgba(51,65,85,.26); font-size:10px; }}
      .whale-head {{ color:#9CA3AF; font-weight:800; background:rgba(15,23,42,.8); border-radius:7px; }}
      .whale-note {{ color:#9CA3AF; font-size:11px; line-height:1.45; margin-top:5px; }}
    </style>
    <div class="orderbook-card">
      <div class="orderbook-head">
        <div><div class="orderbook-title">{symbol} 盘口订单簿</div><div class="desc">Binance Public Depth｜买盘10档 / 卖盘10档</div></div>
        <div class="status" id="status">连接中...</div>
      </div>
      <div class="grid">
        <div class="book-panel">
          <div class="row header"><div>卖盘价格</div><div class="right">数量</div><div class="right">累计</div></div>
          <div id="asks"></div>
          <div class="last"><div class="last-price" id="lastPrice">正在获取</div><div id="change" class="yellow">--</div></div>
          <div class="row header"><div>买盘价格</div><div class="right">数量</div><div class="right">累计</div></div>
          <div id="bids"></div>
        </div>
        <div class="summary-rail">
          <div class="ratio"><div id="buyBar" class="buy" style="width:50%"></div><div id="sellBar" class="sell" style="width:50%"></div></div>
          <div class="side-summary">
            <div class="box"><div class="label">买盘</div><div class="value green" id="buyRatio">--</div></div>
            <div class="box"><div class="label">卖盘</div><div class="value red" id="sellRatio">--</div></div>
            <div class="box"><div class="label">盘口状态</div><div class="value yellow" id="obState">等待数据</div></div>
            <div class="box"><div class="label">多空倾向</div><div class="value blue" id="bias">等待数据</div></div>
            <div class="box"><div class="label">大买单</div><div class="value green" id="largeBid">--</div></div>
            <div class="box"><div class="label">大卖单</div><div class="value red" id="largeAsk">--</div></div>
          </div>
        </div>
      </div>
      <div class="whale-panel">
        <div class="section-title" style="margin-top:0;">大单监控 / 大资金行为面板</div>
        <div class="summary">
          <div class="box"><div class="label">大单强度</div><div class="value yellow" id="whaleScore">等待数据</div></div>
          <div class="box"><div class="label">大单方向</div><div class="value blue" id="whaleStatus">等待数据</div></div>
          <div class="box"><div class="label">庄家行为</div><div class="value blue" id="whaleDealer">等待数据</div></div>
          <div class="box"><div class="label">风险提示</div><div class="value yellow" id="whaleRisk">等待确认</div></div>
          <div class="box"><div class="label">5分钟净流入</div><div class="value" id="whaleNet">等待数据</div></div>
          <div class="box"><div class="label">15分钟净流入</div><div class="value" id="whaleNet15">等待数据</div></div>
          <div class="box"><div class="label">主动买入金额</div><div class="value green" id="whaleBuy">等待数据</div></div>
          <div class="box"><div class="label">主动卖出金额</div><div class="value red" id="whaleSell">等待数据</div></div>
          <div class="box"><div class="label">最大买单</div><div class="value green" id="whaleMaxBuy">--</div></div>
          <div class="box"><div class="label">最大卖单</div><div class="value red" id="whaleMaxSell">--</div></div>
          <div class="box"><div class="label">买入/卖出笔数</div><div class="value" id="whaleCounts">--</div></div>
          <div class="box"><div class="label">更新时间</div><div class="value" id="whaleTime">--</div></div>
        </div>
        <div class="whale-note" id="whaleConclusion">大单综合结论正在同步。</div>
        <div class="whale-note" id="whaleExplanation">大单数据正在同步。</div>
        <details class="whale-note" style="margin-top:8px;">
          <summary style="cursor:pointer;color:#E5E7EB;font-weight:800;">大单监控调试信息</summary>
          <div id="whaleDebug" style="margin-top:6px;line-height:1.55;">等待大单调试数据。</div>
        </details>
        <div style="margin-top:8px;">
          <div class="section-title" style="margin-top:0;">最新大单</div>
          <div class="whale-row whale-head"><div>时间</div><div>方向</div><div>价格</div><div>金额</div></div>
          <div id="whaleRows"><div class="whale-note">当前交易对象大单数据不足。</div></div>
        </div>
        <div class="whale-note" id="whaleStats">1分钟 / 5分钟 / 15分钟统计等待数据。</div>
      </div>
    </div>
    <script>
      const symbol = "{symbol}";
      const initialDepth = {initial_orderbook_json};
      const initialTicker = {initial_ticker_json};
      const initialWhale = {initial_whale_json};
      const frontCache = {{
        depth: initialDepth || {{}},
        ticker: initialTicker || {{}},
        whale: initialWhale || {{}},
        trades: []
      }};
      {frontend_api_client_js("fetchOrderbookJson")}
      async function fetchJsonDirect(url) {{
        const joiner = url.includes("?") ? "&" : "?";
        const res = await fetch(`${{url}}${{joiner}}_=${{Date.now()}}`, {{cache:"no-store"}});
        if (!res.ok) throw new Error(`HTTP ${{res.status}}`);
        return await res.json();
      }}
      async function fetchAnyJson(urls) {{
        let lastErr = null;
        for (const url of urls) {{
          try {{ return await fetchJsonDirect(url); }} catch (err) {{ lastErr = err; }}
        }}
        throw lastErr || new Error("frontend fetch failed");
      }}
      function normalizeDirectDepth(data) {{
        const normalizeLevel = (level) => {{
          const priceText = String(level[0]);
          const qtyText = String(level[1]);
          return {{
            price: Number(priceText),
            quantity: Number(qtyText),
            price_text: clean(priceText),
            quantity_text: clean(qtyText)
          }};
        }};
        return {{
          ok: true,
          source: "frontend_binance_depth",
          symbol,
          lastUpdateId: data.lastUpdateId,
          last_update_id: data.lastUpdateId,
          bids: (data.bids || []).slice(0, 10).map(normalizeLevel),
          asks: (data.asks || []).slice(0, 10).map(normalizeLevel),
          updated_at: new Date().toLocaleTimeString()
        }};
      }}
      function normalizeDirectTicker(data) {{
        return {{
          ok: true,
          source: "frontend_binance_ticker",
          symbol,
          last_price: Number(data.lastPrice || data.price),
          price: Number(data.lastPrice || data.price),
          price_change_percent: Number(data.priceChangePercent || 0),
          updated_at: new Date().toLocaleTimeString()
        }};
      }}
      async function fetchDepthLive() {{
        try {{
          return await fetchOrderbookJson(`/api/orderbook?symbol=${{encodeURIComponent(symbol)}}`);
        }} catch (localErr) {{
          try {{
          const data = await fetchAnyJson([
            `https://api.binance.com/api/v3/depth?symbol=${{encodeURIComponent(symbol)}}&limit=20`,
            `https://fapi.binance.com/fapi/v1/depth?symbol=${{encodeURIComponent(symbol)}}&limit=20`
          ]);
          return normalizeDirectDepth(data);
          }} catch (directErr) {{
            throw localErr;
          }}
        }}
      }}
      async function fetchTickerLive() {{
        try {{
          return await fetchOrderbookJson(`/api/ticker?symbol=${{encodeURIComponent(symbol)}}`);
        }} catch (localErr) {{
          try {{
          const data = await fetchAnyJson([
            `https://api.binance.com/api/v3/ticker/24hr?symbol=${{encodeURIComponent(symbol)}}`,
            `https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=${{encodeURIComponent(symbol)}}`
          ]);
          return normalizeDirectTicker(data);
          }} catch (directErr) {{
            throw localErr;
          }}
        }}
      }}
      function buildFrontendWhale(trades) {{
        const now = Date.now();
        const rows = (trades || []).map(t => {{
          const price = Number(t.p || t.price || 0);
          const qty = Number(t.q || t.qty || 0);
          const amount = price * qty;
          const timeMs = Number(t.T || t.time || now);
          const direction = t.m === true ? "主动卖出" : "主动买入";
          return {{price, qty, amount, timeMs, direction}};
        }}).filter(t => Number.isFinite(t.amount) && t.amount > 0);
        const windowRows = (minutes) => rows.filter(t => now - t.timeMs <= minutes * 60 * 1000);
        const stat = (minutes) => {{
          const list = windowRows(minutes);
          const buy = list.filter(t => t.direction === "主动买入");
          const sell = list.filter(t => t.direction !== "主动买入");
          const buyAmount = buy.reduce((a, t) => a + t.amount, 0);
          const sellAmount = sell.reduce((a, t) => a + t.amount, 0);
          return {{count:list.length, buy_count:buy.length, sell_count:sell.length, buy_amount:buyAmount, sell_amount:sellAmount, net_amount:buyAmount - sellAmount, trade_count:list.length}};
        }};
        const s1 = stat(1), s5 = stat(5), s15 = stat(15);
        const threshold = Math.max(50000, Number(frontCache.ticker.last_price || 0) * 1.2);
        const whaleRows = rows.filter(t => t.amount >= threshold).slice(-12).reverse();
        const latest = whaleRows.map(t => ({{
          time: new Date(t.timeMs).toLocaleTimeString(),
          direction: t.direction,
          price_text: fmtPrice(t.price),
          amount_text: fmtAmount(t.amount),
          amount: t.amount,
          price: t.price,
          quantity: t.qty,
          quantity_text: clean(t.qty.toFixed(8))
        }}));
        const score = Math.min(100, Math.round(whaleRows.length * 8 + Math.min(50, Math.abs(s15.net_amount) / 80000)));
        return {{
          ok: true,
          source: "frontend_binance_trades",
          symbol,
          updated_time: new Date().toLocaleTimeString(),
          updated_at: new Date().toLocaleTimeString(),
          whale_score: score,
          whale_score_text: score >= 80 ? "大单异常活跃" : score >= 50 ? "大单活跃" : "普通活跃",
          whale_direction: s5.net_amount > 0 ? "主动买入占优" : s5.net_amount < 0 ? "主动卖出占优" : "多空均衡",
          dealer_behavior: Math.abs(s15.net_amount) > 0 ? "前端成交缓存分析" : "等待更多成交",
          risk_tip: score >= 80 ? "活跃，防剧烈波动" : "注意盘口同步变化",
          explanation: "前端成交缓存每3秒更新，不依赖Streamlit缓存刷新。",
          net_inflow_5m: s5.net_amount,
          net_inflow_15m: s15.net_amount,
          active_buy_amount: s5.buy_amount,
          active_sell_amount: s5.sell_amount,
          buy_whale_count: s5.buy_count,
          sell_whale_count: s5.sell_count,
          buy_sell_count_text: `买入 ${{s5.buy_count}} 笔 / 卖出 ${{s5.sell_count}} 笔`,
          latest,
          recent_trades: latest,
          stats: {{"1m": s1, "5m": s5, "15m": s15}},
          debug: {{symbol, data_source:"frontend Binance aggTrades", raw_trade_count: rows.length, threshold, data_quality:"frontend_live"}}
        }};
      }}
      async function fetchWhaleLive() {{
        try {{
          return await fetchOrderbookJson(`/api/whales?symbol=${{encodeURIComponent(symbol)}}`);
        }} catch (localErr) {{
          try {{
          const data = await fetchAnyJson([
            `https://fapi.binance.com/fapi/v1/aggTrades?symbol=${{encodeURIComponent(symbol)}}&limit=1000`,
            `https://api.binance.com/api/v3/aggTrades?symbol=${{encodeURIComponent(symbol)}}&limit=1000`
          ]);
          frontCache.trades = Array.isArray(data) ? data : [];
          return buildFrontendWhale(frontCache.trades);
          }} catch (directErr) {{
            throw localErr;
          }}
        }}
      }}
      function clean(v) {{ return String(v).replace(/0+$/,'').replace(/\\.$/,''); }}
      function fmtPrice(v) {{
        const n = Number(v);
        if (!Number.isFinite(n)) return "-";
        if (n >= 1000) return n.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
        if (n >= 1) return n.toLocaleString(undefined, {{minimumFractionDigits:4, maximumFractionDigits:4}});
        return n.toFixed(8).replace(/0+$/,'').replace(/\\.$/,'');
      }}
      function sum(levels) {{ return levels.reduce((a,l)=>a+Number(l[1]),0); }}
      function cumulative(levels) {{
        let total = 0;
        return levels.map(l => {{ total += Number(l[1]); return {{price:l[0], qty:l[1], cumulative:total}}; }});
      }}
      function renderRows(el, rows, side, maxQty) {{
        el.innerHTML = rows.map(r => {{
          const width = maxQty > 0 ? Math.min(100, Number(r.qty) / maxQty * 100) : 0;
          return `<div class="row"><div class="bar ${{side}}bar" style="width:${{width.toFixed(2)}}%"></div><div class="cell ${{side==='ask'?'red':'green'}}">${{clean(r.price)}}</div><div class="cell right">${{clean(r.qty)}}</div><div class="cell right">${{clean(r.cumulative.toFixed(8))}}</div></div>`;
        }}).join("");
      }}
      function fmtAmount(v) {{
        const n = Number(v || 0);
        const sign = n < 0 ? "-" : "";
        const a = Math.abs(n);
        if (a >= 1e9) return `${{sign}}${{(a/1e9).toFixed(2)}}B`;
        if (a >= 1e6) return `${{sign}}${{(a/1e6).toFixed(2)}}M`;
        if (a >= 1e3) return `${{sign}}${{(a/1e3).toFixed(2)}}K`;
        return `${{sign}}${{a.toFixed(2)}}`;
      }}
      function hasValue(v) {{ return v !== undefined && v !== null && v !== ""; }}
      function valueOr(v, fallback) {{ return hasValue(v) ? v : fallback; }}
      function num(v) {{ return Number(hasValue(v) ? v : 0); }}
      function orderText(order, emptyText) {{
        order = order || {{}};
        if (!hasValue(order.amount) && !hasValue(order.amount_text)) return emptyText;
        const priceText = valueOr(order.price_text, fmtPrice(order.price));
        const qtyText = valueOr(order.quantity_text, clean(num(order.quantity).toFixed(8)));
        const amountText = valueOr(order.amount_text, fmtAmount(order.amount));
        return `${{priceText}} / ${{qtyText}} / ${{amountText}}`;
      }}
      function updateWhales(whale) {{
        whale = whale || {{}};
        const stats = whale.stats || {{}};
        const s1 = stats["1m"] || {{}};
        const s5 = stats["5m"] || {{}};
        const s15 = stats["15m"] || {{}};
        const score = num(valueOr(whale.whale_score, whale.score));
        const level = valueOr(whale.whale_score_text, valueOr(whale.level, score === 0 ? "暂无大单" : "等待数据"));
        const direction = valueOr(whale.whale_direction, valueOr(whale.status, "等待数据"));
        const updated = valueOr(whale.updated_time, valueOr(whale.updated_at, "--"));
        const net = num(valueOr(whale.net_inflow_5m, s5.net_amount));
        const net15 = num(valueOr(whale.net_inflow_15m, s15.net_amount));
        const buyAmount = num(valueOr(whale.active_buy_amount, s5.buy_amount));
        const sellAmount = num(valueOr(whale.active_sell_amount, s5.sell_amount));
        const buyCount = num(valueOr(whale.buy_whale_count, s5.buy_count));
        const sellCount = num(valueOr(whale.sell_whale_count, s5.sell_count));
        document.getElementById("whaleScore").textContent = `${{score}} / 100 · ${{level}}`;
        document.getElementById("whaleStatus").textContent = direction;
        document.getElementById("whaleTime").textContent = updated;
        document.getElementById("whaleExplanation").textContent = valueOr(whale.explanation, "大单数据正在同步。");
        if (whale.error) {{
          document.getElementById("whaleStatus").textContent = "大单数据获取失败";
          document.getElementById("whaleRisk").textContent = "请稍后重试";
        }}
        if (!Object.keys(whale).length) {{
          document.getElementById("whaleStatus").textContent = "正在获取大单数据";
          document.getElementById("whaleRisk").textContent = "等待首次缓存";
        }}
        const netEl = document.getElementById("whaleNet");
        netEl.textContent = fmtAmount(net);
        netEl.className = net >= 0 ? "value green" : "value red";
        const net15El = document.getElementById("whaleNet15");
        net15El.textContent = fmtAmount(net15);
        net15El.className = net15 >= 0 ? "value green" : "value red";
        document.getElementById("whaleBuy").textContent = fmtAmount(buyAmount);
        document.getElementById("whaleSell").textContent = fmtAmount(sellAmount);
        document.getElementById("whaleCounts").textContent = valueOr(whale.buy_sell_count_text, `买入 ${{buyCount}} 笔 / 卖出 ${{sellCount}} 笔`);
        document.getElementById("whaleStats").textContent =
          `1分钟：大单${{num(s1.count)}}笔｜净流入 ${{fmtAmount(s1.net_amount)}}　5分钟：买入 ${{fmtAmount(buyAmount)}}｜卖出 ${{fmtAmount(sellAmount)}}｜净流入 ${{fmtAmount(net)}}　15分钟：大单${{num(s15.count)}}笔｜净流入 ${{fmtAmount(net15)}}`;
        const rows = ((whale.latest || []).length ? whale.latest : (whale.recent_trades || [])).slice(0,5);
        const showingRecentTrades = !(whale.latest || []).length && rows.length;
        const buyRows = (whale.latest || []).filter(t => t.direction === "主动买入");
        const sellRows = (whale.latest || []).filter(t => t.direction !== "主动买入");
        const maxBuy = buyRows.reduce((m,t)=>Number(t.amount || 0)>Number(m.amount||0)?t:m, {{}});
        const maxSell = sellRows.reduce((m,t)=>Number(t.amount || 0)>Number(m.amount||0)?t:m, {{}});
        document.getElementById("whaleMaxBuy").textContent = orderText(whale.largest_buy_order || maxBuy, "暂无大买单");
        document.getElementById("whaleMaxSell").textContent = orderText(whale.largest_sell_order || maxSell, "暂无大卖单");
        const dealerText = valueOr(whale.dealer_behavior, net15 > 0 && score >= 60 ? "疑似吸筹/拉升" : net15 < 0 && score >= 60 ? "疑似派发/压制" : "无明显行为");
        document.getElementById("whaleDealer").textContent = dealerText;
        const riskText = valueOr(whale.risk_tip, score >= 80 ? "活跃，防剧烈波动" : Math.abs(net15) > 0 ? "注意方向延续" : "当前暂无明显大单，但成交数据正常。");
        document.getElementById("whaleRisk").textContent = riskText;
        document.getElementById("whaleConclusion").textContent =
          net15 > 0 ? "大单综合结论：最近15分钟大单净流入偏正，短线资金更偏主动买入；若盘口买盘同步增强，偏向多头观察，但接近压力位时不建议追高。"
          : net15 < 0 ? "大单综合结论：最近15分钟大单净流出偏负，主动卖出压力更强；若盘口卖盘同步增强，需警惕下行延续。"
          : (Object.keys(whale).length ? "大单综合结论：当前暂无明显大单，但成交数据正常，资金行为暂未形成稳定方向。" : "大单综合结论：大单数据正在初始化。");
        document.getElementById("whaleRows").innerHTML = rows.length ? rows.map(t => {{
          const cls = t.direction === "主动买入" ? "green" : "red";
          return `<div class="whale-row"><div>${{t.time || "-"}}</div><div class="${{cls}}">${{t.direction || "-"}}</div><div>${{t.price_text || "-"}}</div><div>${{t.amount_text || "-"}}</div></div>`;
        }}).join("") : '<div class="whale-note">当前暂无达到阈值的大单，但成交数据正常。</div>';
        if (showingRecentTrades) {{
          document.getElementById("whaleRows").innerHTML =
            '<div class="whale-note">当前未触发大单阈值，以下显示最新成交。</div>' + document.getElementById("whaleRows").innerHTML;
        }}
        const debug = whale.debug || {{}};
        document.getElementById("whaleDebug").innerHTML =
          `当前交易对象：${{valueOr(debug.symbol, whale.symbol || symbol)}}<br>` +
          `数据源：${{valueOr(debug.data_source, "Binance Futures aggTrades")}}<br>` +
          `最近获取交易条数：${{num(valueOr(debug.raw_trade_count, whale.raw_trade_count))}}<br>` +
          `大单阈值：${{fmtAmount(valueOr(debug.threshold, whale.threshold))}} USDT<br>` +
          `5分钟统计交易数：${{num(debug.stats_5m_trade_count)}}<br>` +
          `15分钟统计交易数：${{num(debug.stats_15m_trade_count)}}<br>` +
          `主动买入金额：${{fmtAmount(valueOr(debug.active_buy_amount, buyAmount))}}<br>` +
          `主动卖出金额：${{fmtAmount(valueOr(debug.active_sell_amount, sellAmount))}}<br>` +
          `大单买入笔数：${{num(valueOr(debug.buy_whale_count, buyCount))}}<br>` +
          `大单卖出笔数：${{num(valueOr(debug.sell_whale_count, sellCount))}}<br>` +
          `数据质量：${{valueOr(debug.data_quality, whale.data_quality || "等待数据")}}<br>` +
          `错误信息：${{valueOr(debug.error, whale.error || "无")}}`;
      }}
      function renderOrderbook(depth, ticker) {{
        depth = depth || {{}};
        ticker = ticker || {{}};
        const bids = cumulative((depth.bids || []).map(l => [l.price_text ?? l.price, l.quantity_text ?? l.quantity]).slice(0,10));
        const asks = cumulative((depth.asks || []).map(l => [l.price_text ?? l.price, l.quantity_text ?? l.quantity]).slice(0,10));
        const maxQty = Math.max(...bids.concat(asks).map(r => Number(r.qty)), 0);
        renderRows(document.getElementById("asks"), asks.slice().reverse(), "ask", maxQty);
        renderRows(document.getElementById("bids"), bids, "bid", maxQty);
        const bidTotal = sum((depth.bids || []).map(l => [l.price_text ?? l.price, l.quantity_text ?? l.quantity]));
        const askTotal = sum((depth.asks || []).map(l => [l.price_text ?? l.price, l.quantity_text ?? l.quantity]));
        const total = bidTotal + askTotal;
        const buyRatio = total ? bidTotal / total * 100 : 50;
        const sellRatio = 100 - buyRatio;
        document.getElementById("buyBar").style.width = `${{buyRatio}}%`;
        document.getElementById("sellBar").style.width = `${{sellRatio}}%`;
        document.getElementById("buyRatio").textContent = `${{buyRatio.toFixed(1)}}%`;
        document.getElementById("sellRatio").textContent = `${{sellRatio.toFixed(1)}}%`;
        const hasDepth = bids.length > 0 && asks.length > 0;
        document.getElementById("obState").textContent = hasDepth ? (buyRatio > 58 ? "买盘强势" : sellRatio > 58 ? "卖盘强势" : "多空均衡") : "后台刷新中";
        document.getElementById("bias").textContent = hasDepth ? (buyRatio > sellRatio ? "多头占优" : sellRatio > buyRatio ? "空头占优" : "均衡") : "等待盘口";
        const largeBid = bids.reduce((m,r)=>Number(r.qty)>Number(m.qty||0)?r:m, {{}});
        const largeAsk = asks.reduce((m,r)=>Number(r.qty)>Number(m.qty||0)?r:m, {{}});
        document.getElementById("largeBid").textContent = largeBid.price ? `${{clean(largeBid.price)}} / ${{clean(largeBid.qty)}}` : "--";
        document.getElementById("largeAsk").textContent = largeAsk.price ? `${{clean(largeAsk.price)}} / ${{clean(largeAsk.qty)}}` : "--";
        const chg = Number(ticker.price_change_percent);
        document.getElementById("lastPrice").textContent = fmtPrice(ticker.last_price);
        const chgEl = document.getElementById("change");
        chgEl.textContent = Number.isFinite(chg) ? `${{chg > 0 ? "+" : ""}}${{chg.toFixed(2)}}%` : "正在获取";
        chgEl.className = Number.isFinite(chg) ? (chg >= 0 ? "green" : "red") : "yellow";
        document.getElementById("status").innerHTML = hasDepth ? `状态：实时<br>更新时间：${{new Date().toLocaleTimeString()}}<br>ID：${{depth.lastUpdateId || depth.last_update_id || "-"}}` : `状态：后台刷新中<br>${{depth.message || "等待盘口缓存"}}`;
      }}
      async function updateOrderbook() {{
        try {{
          const [depth, ticker] = await Promise.all([
            fetchDepthLive(),
            fetchTickerLive()
          ]);
          frontCache.depth = depth || frontCache.depth;
          frontCache.ticker = ticker || frontCache.ticker;
          renderOrderbook(frontCache.depth, frontCache.ticker);
        }} catch (err) {{
          if ((frontCache.depth.bids || []).length || (frontCache.depth.asks || []).length) {{
            renderOrderbook(frontCache.depth, frontCache.ticker);
            document.getElementById("status").innerHTML = `状态：前端缓存<br>更新时间：${{new Date().toLocaleTimeString()}}`;
            return;
          }}
          document.getElementById("status").innerHTML = "盘口数据获取失败<br>正在重试";
        }}
      }}
      async function updateWhalePanel() {{
        try {{
          const whale = await fetchWhaleLive();
          frontCache.whale = whale || frontCache.whale;
          updateWhales(frontCache.whale);
        }} catch (err) {{
          if (Object.keys(frontCache.whale || {{}}).length) {{
            updateWhales(frontCache.whale);
            document.getElementById("whaleTime").textContent = `前端缓存 ${{new Date().toLocaleTimeString()}}`;
            return;
          }}
          document.getElementById("whaleStatus").textContent = "大单数据获取失败";
          document.getElementById("whaleRisk").textContent = "正在重试";
        }}
      }}
      if ((initialDepth.bids || []).length || (initialDepth.asks || []).length) renderOrderbook(initialDepth, initialTicker);
      if (Object.keys(initialWhale || {{}}).length) updateWhales(initialWhale);
      updateOrderbook();
      updateWhalePanel();
      setInterval(updateOrderbook, 1000);
      setInterval(updateWhalePanel, 3000);
    </script>
    """


def render_orderbook_system(symbol: str, ticker: dict[str, Any] | None) -> None:
    """渲染盘口订单簿系统。"""
    live_symbol = st.session_state.get("current_symbol", symbol)
    if st.session_state.get("signal_frontend_live_panels", True):
        live_ticker = market_cache.get_ticker(live_symbol) or ticker
        orderbook = market_cache.get_orderbook(live_symbol)
        whale = market_cache.get_whales(live_symbol)
        if not live_ticker:
            try:
                refresh_symbol_now(live_symbol)
                live_ticker = market_cache.get_ticker(live_symbol) or ticker
            except Exception as exc:
                market_cache.set_ticker_error(f"盘口价格刷新失败：{exc!r}")
        if not orderbook:
            try:
                refresh_orderbook_now(live_symbol)
                orderbook = market_cache.get_orderbook(live_symbol)
            except Exception as exc:
                market_cache.set_orderbook_error(f"盘口服务端刷新失败：{exc!r}")
        if not whale:
            try:
                refresh_whales_now(live_symbol)
                whale = market_cache.get_whales(live_symbol)
            except Exception:
                whale = market_cache.get_whales(live_symbol)
        components.html(frontend_orderbook_html(str(live_symbol), orderbook, live_ticker, whale), height=820, scrolling=False)
        return
    live_ticker = market_cache.get_ticker(live_symbol) or ticker
    if not live_ticker or live_refresh_due(f"ticker:{live_symbol}", 2.0):
        try:
            refresh_symbol_now(live_symbol)
            live_ticker = market_cache.get_ticker(live_symbol) or ticker
        except Exception as exc:
            market_cache.set_ticker_error(f"盘口价格刷新失败：{exc!r}")
    orderbook = market_cache.get_orderbook(live_symbol)
    if not orderbook or live_refresh_due(f"orderbook:{live_symbol}", 2.0):
        try:
            refresh_orderbook_now(live_symbol)
            orderbook = market_cache.get_orderbook(live_symbol)
        except Exception as exc:
            market_cache.set_orderbook_error(f"盘口服务端刷新失败：{exc!r}")
    whale = market_cache.get_whales(live_symbol)
    if not whale or live_refresh_due(f"whale:{live_symbol}", 5.0):
        try:
            refresh_whales_now(live_symbol)
            whale = market_cache.get_whales(live_symbol)
        except Exception as exc:
            market_cache.set_whale_error(f"大单服务端刷新失败：{exc!r}")
    snapshot = market_cache.snapshot()
    current_price = live_ticker.get("last_price") if live_ticker else None
    change = live_ticker.get("price_change_percent") if live_ticker else None
    analysis = analyze_orderbook(orderbook, current_price)
    bids = analysis.get("bids", [])
    asks = analysis.get("asks", [])
    limit = 10
    visible_asks = list(reversed(asks[:limit]))
    visible_bids = bids[:limit]
    orderbook_update_time = orderbook.get("updated_at") if orderbook else snapshot.get("orderbook_last_update_time", "初始化中")
    orderbook_refresh_count = snapshot.get("refresh_counts", {}).get("orderbook", 0)
    orderbook_id = orderbook.get("last_update_id", "-") if orderbook else "-"
    max_quantity = max([float(level.get("quantity", 0) or 0) for level in asks[:10] + bids[:10]] or [0])
    st.markdown(
        f"""<div class="app-shell"><div class="orderbook-card">
        <div class="orderbook-head">
          <div><div class="orderbook-title">{live_symbol} 盘口订单簿</div><div class="module-desc">Binance Public Depth｜买盘10档 / 卖盘10档</div></div>
          <div class="orderbook-status">状态：{snapshot.get("orderbook_status", "初始化中")}<br>更新时间：{orderbook_update_time}<br>刷新：{orderbook_refresh_count}｜ID：{orderbook_id}</div>
        </div>""",
        unsafe_allow_html=True,
    )
    if not orderbook:
        error = escape(str(snapshot.get("orderbook_last_error") or "正在获取盘口数据"))
        st.markdown(f'<div class="pending">{error}</div></div></div>', unsafe_allow_html=True)
    else:
        ask_rows = "".join(_orderbook_level_html(level, "ask", max_quantity, analysis.get("large_ask")) for level in visible_asks)
        bid_rows = "".join(_orderbook_level_html(level, "bid", max_quantity, analysis.get("large_bid")) for level in visible_bids)
        change_class = "green" if change is not None and change >= 0 else "red" if change is not None else "yellow"
        buy_ratio = float(analysis.get("buy_ratio", 0) or 0)
        sell_ratio = float(analysis.get("sell_ratio", 0) or 0)
        st.markdown(
            f"""<div class="orderbook-grid">
              <div class="orderbook-table">
                <div class="orderbook-row header"><div>卖盘价格</div><div class="ob-right">数量</div><div class="ob-right">累计</div></div>
                {ask_rows}
                <div class="last-price-box"><div class="last-price">{format_price(current_price)}</div><div class="{change_class}">{format_percent(change)}</div></div>
                <div class="orderbook-row header"><div>买盘价格</div><div class="ob-right">数量</div><div class="ob-right">累计</div></div>
                {bid_rows}
              </div>
              <div>
                <div class="ratio-bar"><div class="ratio-buy" style="width:{buy_ratio:.2f}%;"></div><div class="ratio-sell" style="width:{sell_ratio:.2f}%;"></div></div>
                <div class="orderbook-summary">
                  <div class="metric-box"><div class="metric-label">买盘</div><div class="metric-value green">{buy_ratio:.1f}%</div></div>
                  <div class="metric-box"><div class="metric-label">卖盘</div><div class="metric-value red">{sell_ratio:.1f}%</div></div>
                  <div class="metric-box"><div class="metric-label">盘口状态</div><div class="metric-value yellow">{analysis.get("status", "等待数据")}</div></div>
                  <div class="metric-box"><div class="metric-label">多空倾向</div><div class="metric-value blue">{analysis.get("bias", "等待数据")}</div></div>
                  <div class="metric-box"><div class="metric-label">支撑位</div><div class="metric-value green">{analysis.get("support_level") or "待确认"}</div></div>
                  <div class="metric-box"><div class="metric-label">压力位</div><div class="metric-value red">{analysis.get("resistance_level") or "待确认"}</div></div>
                </div>
                <div class="status-card" style="margin-top:8px;">
                  {_large_order_text("大买单", analysis.get("large_bid"))}<br>
                  {_large_order_text("大卖单", analysis.get("large_ask"))}
                </div>
              </div>
            </div></div></div>""",
            unsafe_allow_html=True,
        )
    whale = whale or {}
    whale_stats = whale.get("stats") or {}
    stats_5m = whale_stats.get("5m") or {}
    stats_15m = whale_stats.get("15m") or {}
    whale_score = whale.get("whale_score", whale.get("score", 0))
    whale_level = whale.get("whale_score_text") or whale.get("level") or ("暂无大单" if not whale else "等待数据")
    whale_direction = whale.get("whale_direction") or whale.get("status") or "等待数据"
    net_5m = whale.get("net_inflow_5m", stats_5m.get("net_amount", 0))
    net_15m = whale.get("net_inflow_15m", stats_15m.get("net_amount", 0))
    buy_amount = whale.get("active_buy_amount", stats_5m.get("buy_amount", 0))
    sell_amount = whale.get("active_sell_amount", stats_5m.get("sell_amount", 0))
    raw_trade_count = whale.get("raw_trade_count") or (whale.get("debug") or {}).get("raw_trade_count") or 0
    whale_update_time = whale.get("updated_time") or whale.get("updated_at") or snapshot.get("whale_last_update_time", "初始化中")
    whale_error = snapshot.get("whale_last_error") if not whale else whale.get("error")
    st.markdown(
        f"""<div class="app-shell"><div class="module-card">
        <div class="module-title">大单监控 / 大资金行为面板</div>
        <div class="module-desc">{escape(str(live_symbol))}｜服务端直读缓存｜成交源：{escape(str(whale.get("source", "Binance Futures aggTrades") if whale else "初始化中"))}</div>
        <div class="metric-grid">
          <div class="metric-box"><div class="metric-label">大单强度</div><div class="metric-value yellow">{escape(str(whale_score))} / 100 · {escape(str(whale_level))}</div></div>
          <div class="metric-box"><div class="metric-label">大单方向</div><div class="metric-value blue">{escape(str(whale_direction))}</div></div>
          <div class="metric-box"><div class="metric-label">庄家行为</div><div class="metric-value blue">{escape(str(whale.get("dealer_behavior", "无明显行为") if whale else "等待数据"))}</div></div>
          <div class="metric-box"><div class="metric-label">风险提示</div><div class="metric-value yellow">{escape(str(whale.get("risk_tip", "等待确认") if whale else "等待首次缓存"))}</div></div>
          <div class="metric-box"><div class="metric-label">5分钟净流入</div><div class="metric-value {_signal_color("资金流入" if float(net_5m or 0) >= 0 else "资金恐慌")}">{format_compact(net_5m)}</div></div>
          <div class="metric-box"><div class="metric-label">15分钟净流入</div><div class="metric-value {_signal_color("资金流入" if float(net_15m or 0) >= 0 else "资金恐慌")}">{format_compact(net_15m)}</div></div>
          <div class="metric-box"><div class="metric-label">主动买入金额</div><div class="metric-value green">{format_compact(buy_amount)}</div></div>
          <div class="metric-box"><div class="metric-label">主动卖出金额</div><div class="metric-value red">{format_compact(sell_amount)}</div></div>
          <div class="metric-box"><div class="metric-label">最大买单</div><div class="metric-value green">{escape(_whale_order_text(whale.get("largest_buy_order"), "暂无大买单"))}</div></div>
          <div class="metric-box"><div class="metric-label">最大卖单</div><div class="metric-value red">{escape(_whale_order_text(whale.get("largest_sell_order"), "暂无大卖单"))}</div></div>
          <div class="metric-box"><div class="metric-label">买入/卖出笔数</div><div class="metric-value">{escape(str(whale.get("buy_sell_count_text", "等待数据")))}</div></div>
          <div class="metric-box"><div class="metric-label">更新时间</div><div class="metric-value">{escape(str(whale_update_time))}</div></div>
        </div>
        <div class="status-card" style="margin-top:8px;">
          <b>大单综合结论</b><br>
          {escape(str(whale.get("explanation", "大单数据正在同步。" if not whale_error else whale_error)))}
        </div>
        <details class="status-card" style="margin-top:8px;" open>
          <summary><b>最新大单 / 最新成交</b></summary>
          <div style="margin-top:8px;">{_whale_trade_rows_html(whale)}</div>
        </details>
        <details class="status-card" style="margin-top:8px;">
          <summary><b>大单调试信息</b></summary>
          当前交易对象：{escape(str(live_symbol))}<br>
          原始成交条数：{escape(str(raw_trade_count))}<br>
          5分钟统计：买入 {format_compact(stats_5m.get("buy_amount", 0))} / 卖出 {format_compact(stats_5m.get("sell_amount", 0))} / 净流入 {format_compact(stats_5m.get("net_amount", 0))}<br>
          15分钟统计：净流入 {format_compact(stats_15m.get("net_amount", 0))}<br>
          错误信息：{escape(str(whale_error or "无"))}
        </details>
        </div></div>""",
        unsafe_allow_html=True,
    )

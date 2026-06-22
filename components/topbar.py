"""Fixed market topbar and ticker helpers."""

from __future__ import annotations

import time
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st

from app_pages.kline_page import local_scores
from components.local_api import frontend_api_client_js
from components.opportunity_board import _combined_trade_opportunities
from services import market_cache
from services.binance_public import get_24hr_ticker
from utils.formatters import format_percent, format_price, format_score, safe_number


def _append_debug_log(path: Path | None, message: str) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass


def _risk_class(risk_score: Any) -> str:
    score = safe_number(risk_score)
    if score is None:
        return "yellow"
    if score >= 70:
        return "red"
    if score >= 45:
        return "yellow"
    return "green"


def _opportunity_class(opportunity_score: Any) -> str:
    score = safe_number(opportunity_score)
    if score is None:
        return "yellow"
    if score >= 70:
        return "green"
    if score >= 45:
        return "yellow"
    return "red"


def _find_opportunity_row(symbol: str, rankings: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any] | None:
    """Find a symbol row from the live opportunity board."""
    live_symbol = str(symbol or "").upper().strip()
    if not live_symbol:
        return None
    for row in _combined_trade_opportunities(rankings or market_cache.get_rankings(), 10):
        if str(row.get("symbol", "")).upper() == live_symbol:
            return row
    for row in st.session_state.get("opportunity_board", []) or []:
        if str(row.get("symbol", "")).upper() == live_symbol:
            return row
    return None


def ticker_from_rankings(symbol: str, rankings: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any] | None:
    """Build a lightweight ticker from ranking rows when live ticker cache is cold."""
    row = _find_opportunity_row(symbol, rankings)
    if not row:
        return None
    price = safe_number(row.get("last_price"), safe_number(row.get("current_price"), safe_number(row.get("price"))))
    if price is None or price <= 0:
        return None
    return {
        "symbol": str(row.get("symbol") or symbol).upper().strip(),
        "last_price": price,
        "price_change_percent": safe_number(row.get("price_change_percent"), 0) or 0,
        "quote_volume": safe_number(row.get("quote_volume"), 0) or 0,
        "volume": safe_number(row.get("volume"), 0) or 0,
        "updated_at": row.get("updated_at") or "来自机会榜缓存",
        "price_status": "ranking",
    }


def get_effective_ticker(
    symbol: str,
    rankings: dict[str, list[dict[str, Any]]] | None = None,
    *,
    debug_log_path: Path | None = None,
) -> dict[str, Any] | None:
    """Read live ticker first, then bridge from rankings if needed."""
    normalized = str(symbol or "").upper().strip()
    ticker = market_cache.get_ticker(normalized)
    if ticker:
        ticker.setdefault("price_status", "live")
        return ticker
    fallback = ticker_from_rankings(normalized, rankings)
    if fallback:
        market_cache.set_ticker(normalized, fallback)
        _append_debug_log(debug_log_path, f"ticker_bridge_from_rankings symbol={normalized}")
        return fallback
    return None


def render_fixed_market_bar(symbol: str, debug_log_path: Path | None = None) -> None:
    """Render the front-end refreshed fixed market bar."""
    try:
        live_symbol = str(st.session_state.get("current_symbol") or symbol or "BTCUSDT").upper().strip()
        interval = market_cache.get_kline_interval()
        rankings = market_cache.get_rankings()
        ticker = get_effective_ticker(live_symbol, rankings, debug_log_path=debug_log_path)
        if not ticker:
            try:
                ticker = get_24hr_ticker(live_symbol)
                market_cache.set_ticker(live_symbol, ticker)
            except Exception:
                ticker = None
        kline_rows = market_cache.get_klines(live_symbol, interval)
        orderbook = market_cache.get_orderbook(live_symbol)
        whale = market_cache.get_whales(live_symbol)
        scores = local_scores(ticker, kline_rows) or {}
        opportunity_row = _find_opportunity_row(live_symbol, rankings)
        committee_summary = (opportunity_row or {}).get("committee_summary") or {}
        final_opp = (opportunity_row or {}).get("final_opportunity_score", scores.get("opportunity_score"))
        risk_value = (opportunity_row or {}).get("risk_score", scores.get("risk_score"))
        status_bits = []
        if not ticker:
            status_bits.append("等待实时价格")
        if len(kline_rows) < 80:
            status_bits.append(f"K线{len(kline_rows)}/80")
        if not orderbook:
            status_bits.append("盘口等待刷新")
        if not whale:
            status_bits.append("暂无明显大单")
        data_status = "｜".join(status_bits) or "数据实时"
        ai_advice = committee_summary.get("final_action") or scores.get("trend_label") or data_status
        update_time = escape(str((ticker or {}).get("updated_at") or market_cache.snapshot().get("last_update_time") or "等待更新"))
        price = format_price((ticker or {}).get("last_price"))
        change_number = safe_number((ticker or {}).get("price_change_percent"))
        change = format_percent(change_number)
        change_class = "green" if (change_number is not None and change_number >= 0) else "red" if change_number is not None else "yellow"
        risk_class = _risk_class(risk_value)
        opp_class = _opportunity_class(final_opp)
    except Exception as exc:
        live_symbol = str(symbol or "BTCUSDT")
        price = "顶部行情栏渲染失败"
        change = "重试中"
        change_class = risk_class = opp_class = "yellow"
        ai_advice = f"获取失败：{exc!r}"
        risk_value = None
        final_opp = None
        data_status = "顶部行情栏异常"
        update_time = "等待更新"
    html = f"""
    <style>
      body {{ margin:0; background:transparent; font-family:Arial,'Microsoft YaHei',sans-serif; }}
      .market-ticker {{ position:fixed; top:0; left:0; right:0; z-index:9999; background:rgba(5,11,20,.98); border-bottom:1px solid #1F2937; padding:4px 6px; }}
      .market-ticker-inner {{ display:grid; grid-template-columns:1.02fr .9fr .66fr .78fr .62fr .62fr; gap:4px; max-width:1180px; margin:0 auto; }}
      .ticker-cell {{ min-height:29px; border:1px solid #1F2937; background:rgba(15,23,42,.92); border-radius:8px; padding:3px 5px; overflow:hidden; }}
      .ticker-label {{ color:#9CA3AF; font-size:8px; line-height:1.1; white-space:nowrap; }}
      .ticker-value {{ color:#E5E7EB; font-weight:900; font-size:10px; line-height:1.3; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
      .green {{ color:#00C087 !important; }} .red {{ color:#F6465D !important; }} .yellow {{ color:#F0B90B !important; }} .blue {{ color:#3B82F6 !important; }}
    </style>
    <div class="market-ticker"><div class="market-ticker-inner">
      <div class="ticker-cell"><div class="ticker-label">当前交易对象</div><div class="ticker-value" id="symbol">{live_symbol}</div></div>
      <div class="ticker-cell"><div class="ticker-label">当前价格</div><div class="ticker-value" id="price">{price}</div></div>
      <div class="ticker-cell"><div class="ticker-label">涨跌幅</div><div class="ticker-value {change_class}" id="change">{change}</div></div>
      <div class="ticker-cell"><div class="ticker-label">AI建议 / 状态</div><div class="ticker-value yellow" title="{escape(data_status)}｜更新：{update_time}">{escape(str(ai_advice))}</div></div>
      <div class="ticker-cell"><div class="ticker-label">风险评分</div><div class="ticker-value {risk_class}">{format_score(risk_value)}</div></div>
      <div class="ticker-cell"><div class="ticker-label">机会评分</div><div class="ticker-value {opp_class}">{format_score(final_opp)}</div></div>
    </div></div>
    <script>
      const symbol = "{live_symbol}";
      const priceEl = document.getElementById("price");
      const changeEl = document.getElementById("change");
      {frontend_api_client_js("fetchTopbarJson")}
      function fmtPrice(v) {{
        const n = Number(v);
        if (!Number.isFinite(n)) return "正在获取";
        if (n >= 1000) return n.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
        if (n >= 1) return n.toLocaleString(undefined, {{minimumFractionDigits:4, maximumFractionDigits:4}});
        return n.toFixed(8).replace(/0+$/,'').replace(/\\.$/,'');
      }}
      async function tick() {{
        try {{
          const data = await fetchTopbarJson(`/api/ticker?symbol=${{encodeURIComponent(symbol)}}`);
          const change = Number(data.price_change_percent);
          priceEl.textContent = fmtPrice(data.last_price);
          changeEl.textContent = Number.isFinite(change) ? `${{change > 0 ? "+" : ""}}${{change.toFixed(2)}}%` : "重试中";
          changeEl.className = Number.isFinite(change) ? `ticker-value ${{change >= 0 ? "green" : "red"}}` : "ticker-value yellow";
        }} catch (err) {{
          priceEl.textContent = err && err.message ? err.message : "获取失败";
          changeEl.textContent = "等待刷新";
          changeEl.className = "ticker-value yellow";
        }}
      }}
      tick();
      setInterval(tick, 1000);
    </script>
    """
    st.html(html, width="stretch", unsafe_allow_javascript=True)

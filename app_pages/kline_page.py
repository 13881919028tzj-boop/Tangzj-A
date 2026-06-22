"""Kline chart page and chart-building helpers."""

from __future__ import annotations

import json
from html import escape
from typing import Any

import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from plotly.offline import get_plotlyjs
from plotly.subplots import make_subplots

from components.local_api import frontend_api_client_js
from services import market_cache
from services.background_refresher import refresh_klines_now, refresh_symbol_now
from utils.formatters import format_compact, format_price, safe_score

KLINE_INTERVALS = ["1m", "5m", "15m", "30m", "1h", "4h"]
MA_OPTIONS = ["MA5", "MA10", "MA20", "MA60", "MA120"]


def live_refresh_due(key: str, seconds: float) -> bool:
    """Session-local throttle for live server-side refreshes."""
    import time

    now = time.monotonic()
    refresh_times = st.session_state.setdefault("_live_refresh_times", {})
    last = float(refresh_times.get(key, 0) or 0)
    if now - last < seconds:
        return False
    refresh_times[key] = now
    return True


def on_kline_interval_change() -> None:
    """K线周期切换回调。"""
    market_cache.set_kline_interval(st.session_state.kline_interval)
    market_cache.request_kline_refresh()


def reset_follow_latest() -> None:
    """恢复K线跟随最新价格。"""
    st.session_state.follow_latest = True

def calculate_ma(values: list[float], window: int) -> list[float | None]:
    """计算简单移动平均线。"""
    result: list[float | None] = []
    rolling_sum = 0.0
    for index, value in enumerate(values):
        rolling_sum += value
        if index >= window:
            rolling_sum -= values[index - window]
        result.append(rolling_sum / window if index + 1 >= window else None)
    return result


def local_scores(ticker: dict[str, Any] | None, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """根据本地行情与K线计算趋势、风险、机会评分。"""
    default = {"trend_label": "计算中", "trend_score": None, "risk_score": None, "opportunity_score": None}
    if not ticker or len(rows) < 60:
        return default
    closes = [row["close"] for row in rows]
    close = closes[-1]
    ma20 = calculate_ma(closes, 20)[-1]
    ma60 = calculate_ma(closes, 60)[-1]
    change = ticker["price_change_percent"]
    recent_high = max(row["high"] for row in rows[-40:])
    recent_low = min(row["low"] for row in rows[-40:])
    volatility = ((recent_high - recent_low) / close) * 100 if close else 0

    trend_score = 50
    if ma20 and ma60:
        if close > ma20 > ma60:
            trend_score += 28
        elif close < ma20 < ma60:
            trend_score -= 28
        elif close > ma20:
            trend_score += 10
        elif close < ma20:
            trend_score -= 10
    trend_score += min(12, max(-12, change * 2))
    trend_score = int(max(0, min(100, trend_score)))

    risk_score = int(max(0, min(100, volatility * 6 + max(0, abs(change) - 3) * 5)))
    opportunity_score = int(max(0, min(100, trend_score * 0.72 + (100 - risk_score) * 0.28)))

    if trend_score >= 82 and change >= 0:
        trend_label = "强势上涨"
    elif trend_score >= 62:
        trend_label = "上涨"
    elif trend_score <= 18 and change <= 0:
        trend_label = "强势下跌"
    elif trend_score <= 38:
        trend_label = "下跌"
    else:
        trend_label = "震荡"
    return {"trend_label": trend_label, "trend_score": trend_score, "risk_score": risk_score, "opportunity_score": opportunity_score}


def score_text(value: Any, loading: str = "计算中") -> str:
    """区分未计算和真实0分。"""
    if value is None:
        return loading
    return str(value)


def score_color(value: Any, good_at: int, warn_at: int | None = None) -> str:
    """评分卡片颜色，未计算时使用黄色加载态。"""
    if value is None:
        return "yellow"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "yellow"
    if warn_at is not None and number >= warn_at:
        return "red"
    return "green" if number >= good_at else "yellow"


def build_ai_status(scores: dict[str, Any]) -> tuple[str, str]:
    """首页AI建议状态，避免把已开发模块显示成待接入。"""
    opportunity = safe_score(scores.get("opportunity_score"))
    risk = safe_score(scores.get("risk_score"))
    if opportunity is None or risk is None:
        return "计算中", "yellow"
    if risk >= 85:
        return "风控禁止", "red"
    if opportunity >= 80 and risk < 65:
        return "候选复核", "green"
    if opportunity >= 70:
        return "重点观察", "yellow"
    return "继续观察", "blue"


def detect_cross(rows: list[dict[str, Any]], ma_short: list[float | None], ma_long: list[float | None]) -> dict[str, Any]:
    """识别最近一次均线金叉或死叉。"""
    for index in range(len(rows) - 1, 0, -1):
        prev_short, prev_long = ma_short[index - 1], ma_long[index - 1]
        curr_short, curr_long = ma_short[index], ma_long[index]
        if None in (prev_short, prev_long, curr_short, curr_long):
            continue
        if prev_short <= prev_long and curr_short > curr_long:
            return {"type": "golden", "label": "金叉", "time": rows[index]["open_datetime"], "price": rows[index]["close"]}
        if prev_short >= prev_long and curr_short < curr_long:
            return {"type": "death", "label": "死叉", "time": rows[index]["open_datetime"], "price": rows[index]["close"]}
    return {"type": "none", "label": "无明显交叉", "time": None, "price": None}


def detect_support_resistance(rows: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """基于最近 K线高低点估算关键支撑与压力。"""
    if len(rows) < 20:
        return None, None
    recent = rows[-120:]
    current = recent[-1]["close"]
    lows = [row["low"] for row in recent]
    highs = [row["high"] for row in recent]
    support_candidates = sorted({price for price in lows if price <= current}, reverse=True)
    resistance_candidates = sorted({price for price in highs if price >= current})
    support = support_candidates[0] if support_candidates else min(lows)
    resistance = resistance_candidates[0] if resistance_candidates else max(highs)
    return support, resistance


def analyze_kline_state(rows: list[dict[str, Any]], ma20: list[float | None], ma60: list[float | None]) -> str:
    """输出简洁 K线状态。"""
    if len(rows) < 80 or ma20[-1] is None or ma60[-1] is None:
        return "数据积累中"
    close = rows[-1]["close"]
    recent_high = max(row["high"] for row in rows[-31:-1])
    recent_low = min(row["low"] for row in rows[-31:-1])
    previous_close = rows[-2]["close"]
    if close > recent_high:
        return "突破"
    if previous_close > recent_high and close < recent_high:
        return "假突破"
    if close < recent_low:
        return "下跌趋势"
    if close > ma20[-1] > ma60[-1]:
        return "上涨趋势"
    if close < ma20[-1] < ma60[-1]:
        return "下跌趋势"
    if abs(close - ma20[-1]) / close < 0.01:
        return "回踩"
    return "震荡整理"


def add_ma_trace(fig: go.Figure, x_values: list[Any], ma_values: list[float | None], name: str, color: str) -> None:
    """向图表添加均线。"""
    fig.add_trace(
        go.Scatter(x=x_values, y=ma_values, mode="lines", name=name, line={"color": color, "width": 1.25}, connectgaps=False),
        row=1,
        col=1,
    )


def build_kline_figure(symbol: str, interval: str, rows: list[dict[str, Any]], visible_mas: list[str], follow_latest: bool, chart_interactive: bool) -> tuple[go.Figure, dict[str, Any]]:
    """构建专业 K线图。"""
    x_values = [row["open_datetime"] for row in rows]
    closes = [row["close"] for row in rows]
    ma_map = {
        "MA5": calculate_ma(closes, 5),
        "MA10": calculate_ma(closes, 10),
        "MA20": calculate_ma(closes, 20),
        "MA60": calculate_ma(closes, 60),
        "MA120": calculate_ma(closes, 120),
    }
    ma_colors = {"MA5": "#F0B90B", "MA10": "#3B82F6", "MA20": "#A78BFA", "MA60": "#22C55E", "MA120": "#F97316"}
    support, resistance = detect_support_resistance(rows)
    cross = detect_cross(rows, ma_map["MA20"], ma_map["MA60"])
    state = analyze_kline_state(rows, ma_map["MA20"], ma_map["MA60"])
    last_close = closes[-1]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.74, 0.26])
    hover_text = [
        (
            f"时间：{row['open_datetime'].strftime('%Y-%m-%d %H:%M:%S')}<br>"
            f"开盘：{format_price(row['open'])}<br>最高：{format_price(row['high'])}<br>"
            f"最低：{format_price(row['low'])}<br>收盘：{format_price(row['close'])}<br>成交量：{format_compact(row['volume'])}"
        )
        for row in rows
    ]
    fig.add_trace(
        go.Candlestick(
            x=x_values,
            open=[row["open"] for row in rows],
            high=[row["high"] for row in rows],
            low=[row["low"] for row in rows],
            close=closes,
            name="K线",
            increasing={"line": {"color": "#00C087"}, "fillcolor": "#00C087"},
            decreasing={"line": {"color": "#F6465D"}, "fillcolor": "#F6465D"},
            text=hover_text,
            hoverinfo="text",
        ),
        row=1,
        col=1,
    )
    for name in visible_mas:
        if name in ma_map:
            add_ma_trace(fig, x_values, ma_map[name], name, ma_colors[name])

    volume_colors = ["#00C087" if row["close"] >= row["open"] else "#F6465D" for row in rows]
    fig.add_trace(go.Bar(x=x_values, y=[row["volume"] for row in rows], name="Volume", marker={"color": volume_colors, "opacity": 0.58}), row=2, col=1)

    def add_price_guide(price: float | None, label: str, color: str, dash: str, yshift: int) -> None:
        if price is None:
            return
        fig.add_hline(y=price, line_dash=dash, line_color=color, line_width=1, row=1, col=1)
        fig.add_annotation(
            x=0.985,
            xref="paper",
            y=price,
            yref="y",
            text=f"{label} {format_price(price)}",
            showarrow=True,
            arrowhead=2,
            arrowsize=1,
            arrowwidth=1,
            arrowcolor=color,
            ax=-72,
            ay=yshift,
            bgcolor="rgba(15,23,42,.94)",
            bordercolor=color,
            borderwidth=1,
            font={"color": "#FFFFFF", "size": 10},
        )

    add_price_guide(resistance, "压力", "#F6465D", "dash", -24)
    add_price_guide(last_close, "现价", "#F0B90B", "dot", 0)
    add_price_guide(support, "支撑", "#22C55E", "dash", 24)

    if cross["type"] != "none":
        marker_color = "#00C087" if cross["type"] == "golden" else "#F6465D"
        marker_symbol = "triangle-up" if cross["type"] == "golden" else "triangle-down"
        fig.add_trace(
            go.Scatter(x=[cross["time"]], y=[cross["price"]], mode="markers+text", name=cross["label"], text=[cross["label"]], textposition="top center", marker={"color": marker_color, "size": 12, "symbol": marker_symbol}),
            row=1,
            col=1,
        )

    layout_range = None
    if follow_latest and not chart_interactive and len(x_values) > 80:
        layout_range = [x_values[-80], x_values[-1]]
    fig.update_layout(
        title=f"{symbol} K线图（{interval}）",
        autosize=True,
        height=570,
        paper_bgcolor="#050B14",
        plot_bgcolor="#050B14",
        font={"color": "#E5E7EB", "size": 11},
        margin={"l": 42, "r": 16, "t": 38, "b": 24},
        hovermode="x unified",
        hoverlabel={"bgcolor": "#0F172A", "bordercolor": "#334155", "font": {"color": "#FFFFFF"}},
        dragmode="zoom" if chart_interactive else False,
        uirevision=f"{symbol}-{interval}",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.01, "xanchor": "left", "x": 0},
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor="rgba(148,163,184,.12)",
        rangeslider_visible=False,
        range=layout_range,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="rgba(229,231,235,.55)",
        spikethickness=1,
    )
    fig.update_yaxes(showgrid=True, gridcolor="rgba(148,163,184,.12)", autorange=True, fixedrange=False, showspikes=True, spikecolor="rgba(229,231,235,.55)", row=1, col=1)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(148,163,184,.10)", autorange=True, fixedrange=False, row=2, col=1)
    meta = {"support": support, "resistance": resistance, "cross": cross, "state": state, "last_close": last_close, "ma20": ma_map["MA20"][-1], "ma60": ma_map["MA60"][-1]}
    return fig, meta


def build_kline_meta(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """只计算 K线摘要信息，不触发 Python Plotly 图形构建。"""
    closes = [row["close"] for row in rows]
    ma20 = calculate_ma(closes, 20)
    ma60 = calculate_ma(closes, 60)
    support, resistance = detect_support_resistance(rows)
    cross = detect_cross(rows, ma20, ma60)
    state = analyze_kline_state(rows, ma20, ma60)
    return {
        "support": support,
        "resistance": resistance,
        "cross": cross,
        "state": state,
        "last_close": closes[-1] if closes else None,
        "ma20": ma20[-1] if ma20 else None,
        "ma60": ma60[-1] if ma60 else None,
    }


def frontend_kline_html(symbol: str, interval: str, visible_mas: list[str], chart_interactive: bool, follow_latest: bool, initial_rows: list[dict[str, Any]] | None = None) -> str:
    """生成前端自刷新的 Plotly K线组件。"""
    visible_mas_json = json.dumps(visible_mas, ensure_ascii=False)
    initial_rows_json = json.dumps(initial_rows or [], ensure_ascii=False, default=str)
    plotly_js = get_plotlyjs()
    touch_action = "none" if chart_interactive else "pan-y"
    return f"""
    <style>
      html, body {{ margin:0; width:100%; height:100%; background:#050B14; font-family:Arial,'Microsoft YaHei',sans-serif; overscroll-behavior:contain; }}
      body {{ touch-action:{touch_action}; user-select:none; -webkit-user-select:none; -webkit-tap-highlight-color:transparent; }}
      #chart-wrap {{ height:660px; border:1px solid #263241; border-radius:10px; background:#050B14; overflow:hidden; touch-action:{touch_action}; overscroll-behavior:contain; position:relative; }}
      #chart {{ width:100%; height:100%; touch-action:{touch_action}; background:#050B14; }}
      .js-plotly-plot, .plot-container, .svg-container, .main-svg, .draglayer, .nsewdrag, .plotly {{ touch-action:{touch_action} !important; }}
      #status {{ position:absolute; top:8px; right:10px; z-index:5; color:#9CA3AF; font-size:11px; background:rgba(15,23,42,.78); border:1px solid #334155; border-radius:8px; padding:4px 7px; }}
    </style>
    <div id="chart-wrap">
      <div id="status">K线前端连接中...</div>
      <div id="chart"></div>
    </div>
    <script>{plotly_js}</script>
    <script>
      const symbol = "{symbol}";
      const interval = "{interval}";
      const visibleMas = {visible_mas_json};
      const initialRows = {initial_rows_json};
      const chartInteractive = {str(bool(chart_interactive)).lower()};
      const followLatest = {str(bool(follow_latest)).lower()};
      const statusEl = document.getElementById("status");
      let rows = (initialRows || []).map(r => ({{openTime:r.openTime, open:Number(r.open), high:Number(r.high), low:Number(r.low), close:Number(r.close), volume:Number(r.volume), closeTime:r.closeTime}}));
      let klineMessage = "";
      let userInteracted = false;
      let listenerBound = false;
      let gestureBound = false;
      let touchState = null;
      let renderPending = false;
      let viewRange = null;
      const chart = document.getElementById("chart");
      const chartWrap = document.getElementById("chart-wrap");
      const viewStorageKey = `ai-model:kline:view:${{symbol}}:${{interval}}`;
      {frontend_api_client_js("fetchKlineJson")}
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
      function normalizeDirectKlines(data) {{
        return (Array.isArray(data) ? data : []).map(r => ({{
          openTime: Number(r[0]),
          open: Number(r[1]),
          high: Number(r[2]),
          low: Number(r[3]),
          close: Number(r[4]),
          volume: Number(r[5]),
          closeTime: Number(r[6])
        }}));
      }}
      async function fetchKlinesLive() {{
        try {{
          const data = await fetchAnyJson([
            `https://api.binance.com/api/v3/klines?symbol=${{encodeURIComponent(symbol)}}&interval=${{encodeURIComponent(interval)}}&limit=300`,
            `https://fapi.binance.com/fapi/v1/klines?symbol=${{encodeURIComponent(symbol)}}&interval=${{encodeURIComponent(interval)}}&limit=300`
          ]);
          return {{source:"frontend_binance_klines", rows: normalizeDirectKlines(data)}};
        }} catch (err) {{
          return await fetchKlineJson(`/api/klines?symbol=${{encodeURIComponent(symbol)}}&interval=${{encodeURIComponent(interval)}}`);
        }}
      }}
      async function fetchTickerLive() {{
        try {{
          const data = await fetchAnyJson([
            `https://api.binance.com/api/v3/ticker/24hr?symbol=${{encodeURIComponent(symbol)}}`,
            `https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=${{encodeURIComponent(symbol)}}`
          ]);
          return {{last_price: Number(data.lastPrice || data.price), source:"frontend_binance_ticker"}};
        }} catch (err) {{
          return await fetchKlineJson(`/api/ticker?symbol=${{encodeURIComponent(symbol)}}`);
        }}
      }}

      function fmtPrice(v) {{
        const n = Number(v);
        if (!Number.isFinite(n)) return "-";
        if (n >= 1000) return n.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
        if (n >= 1) return n.toLocaleString(undefined, {{minimumFractionDigits:4, maximumFractionDigits:4}});
        return n.toFixed(8).replace(/0+$/,'').replace(/\\.$/,'');
      }}
      function ma(values, period) {{
        return values.map((_, i) => {{
          if (i + 1 < period) return null;
          let sum = 0;
          for (let j = i - period + 1; j <= i; j++) sum += values[j];
          return sum / period;
        }});
      }}
      function supportResistance(lows, highs) {{
        const recentLows = lows.slice(-80);
        const recentHighs = highs.slice(-80);
        return {{
          support: Math.min(...recentLows),
          resistance: Math.max(...recentHighs)
        }};
      }}
      function intervalMs() {{
        const map = {{"1m":60000,"3m":180000,"5m":300000,"15m":900000,"30m":1800000,"1h":3600000,"2h":7200000,"4h":14400000,"6h":21600000,"8h":28800000,"12h":43200000,"1d":86400000}};
        return map[interval] || 60000;
      }}
      function activeXRange(x) {{
        if (viewRange) return viewRange;
        if (followLatest && !userInteracted && x.length > 90) return [x[x.length - 90], new Date(x[x.length - 1].getTime() + intervalMs() * 1.2)];
        return undefined;
      }}
      function saveViewRange() {{
        if (!viewRange) return;
        try {{
          localStorage.setItem(viewStorageKey, JSON.stringify([new Date(viewRange[0]).getTime(), new Date(viewRange[1]).getTime()]));
        }} catch (_) {{}}
      }}
      function restoreViewRange() {{
        try {{
          const raw = localStorage.getItem(viewStorageKey);
          if (!raw) return;
          const parsed = JSON.parse(raw);
          if (!Array.isArray(parsed) || parsed.length < 2) return;
          const start = Number(parsed[0]);
          const end = Number(parsed[1]);
          if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return;
          viewRange = [new Date(start), new Date(end)];
          userInteracted = true;
        }} catch (_) {{}}
      }}
      function currentRangeMs() {{
        if (viewRange) return [new Date(viewRange[0]).getTime(), new Date(viewRange[1]).getTime()];
        const x = rows.map(r => new Date(r.openTime));
        const range = activeXRange(x);
        if (range) return [new Date(range[0]).getTime(), new Date(range[1]).getTime()];
        if (rows.length) return [Number(rows[Math.max(0, rows.length - 90)].openTime), Number(rows[rows.length - 1].openTime) + intervalMs() * 1.2];
        const now = Date.now();
        return [now - intervalMs() * 90, now + intervalMs()];
      }}
      function clampRange(start, end) {{
        const minSpan = intervalMs() * 8;
        const maxSpan = Math.max(intervalMs() * 300, rows.length ? (Number(rows[rows.length - 1].openTime) - Number(rows[0].openTime) + intervalMs() * 20) : intervalMs() * 300);
        let span = Math.max(minSpan, Math.min(maxSpan, end - start));
        let center = (start + end) / 2;
        if (rows.length) {{
          const minTime = Number(rows[0].openTime) - intervalMs() * 10;
          const maxTime = Number(rows[rows.length - 1].openTime) + intervalMs() * 12;
          center = Math.max(minTime + span / 2, Math.min(maxTime - span / 2, center));
        }}
        return [new Date(center - span / 2), new Date(center + span / 2)];
      }}
      function setViewRangeMs(start, end) {{
        viewRange = clampRange(start, end);
        userInteracted = true;
        saveViewRange();
      }}
      function scheduleDraw() {{
        if (renderPending) return;
        renderPending = true;
        requestAnimationFrame(async () => {{
          renderPending = false;
          try {{ await draw(); }} catch (_) {{}}
        }});
      }}
      function rowsInRange(range) {{
        if (!range) return rows.slice(-120);
        const start = new Date(range[0]).getTime();
        const end = new Date(range[1]).getTime();
        const visible = rows.filter(r => Number(r.openTime) >= start - intervalMs() && Number(r.openTime) <= end + intervalMs());
        return visible.length ? visible : rows.slice(-120);
      }}
      function paddedRange(values, fallback) {{
        const clean = values.filter(v => Number.isFinite(Number(v))).map(Number);
        if (!clean.length) return fallback;
        const min = Math.min(...clean);
        const max = Math.max(...clean);
        const span = Math.max(max - min, Math.abs(max) * 0.002, 1e-8);
        return [min - span * 0.14, max + span * 0.14];
      }}
      function buildTraces() {{
        const x = rows.map(r => new Date(r.openTime));
        const open = rows.map(r => r.open);
        const high = rows.map(r => r.high);
        const low = rows.map(r => r.low);
        const close = rows.map(r => r.close);
        const volume = rows.map(r => r.volume);
        const traces = [{{
          type:"candlestick",
          x, open, high, low, close,
          name:"K线",
          increasing:{{line:{{color:"#00C087", width:1.35}}, fillcolor:"#00C087"}},
          decreasing:{{line:{{color:"#F6465D", width:1.35}}, fillcolor:"#F6465D"}},
          whiskerwidth:.45,
          hovertext: rows.map(r => `时间：${{new Date(r.openTime).toLocaleString()}}<br>开盘：${{fmtPrice(r.open)}}<br>最高：${{fmtPrice(r.high)}}<br>最低：${{fmtPrice(r.low)}}<br>收盘：${{fmtPrice(r.close)}}<br>成交量：${{r.volume.toLocaleString()}}`),
          hoverinfo:"text",
          xaxis:"x",
          yaxis:"y"
        }}];
        const maConfig = {{
          MA5:[5,"#F0B90B"], MA10:[10,"#3B82F6"], MA20:[20,"#A78BFA"], MA60:[60,"#22C55E"], MA120:[120,"#F97316"]
        }};
        for (const key of visibleMas) {{
          if (!maConfig[key]) continue;
          traces.push({{type:"scatter", mode:"lines", x, y:ma(close, maConfig[key][0]), name:key, line:{{color:maConfig[key][1], width:1.25}}, connectgaps:false, xaxis:"x", yaxis:"y"}});
        }}
        traces.push({{
          type:"bar", x, y:volume, name:"Volume",
          marker:{{color: rows.map(r => r.close >= r.open ? "#00C087" : "#F6465D"), opacity:.58}},
          width: intervalMs() * .72,
          xaxis:"x", yaxis:"y2"
        }});
        return traces;
      }}
      function buildLayout() {{
        const x = rows.map(r => new Date(r.openTime));
        const high = rows.map(r => r.high);
        const low = rows.map(r => r.low);
        const close = rows.map(r => r.close);
        const sr = supportResistance(low, high);
        const last = close[close.length - 1];
        const range = activeXRange(x);
        const visible = rowsInRange(range);
        const priceRange = paddedRange(visible.flatMap(r => [r.low, r.high]), undefined);
        const volumeRange = [0, Math.max(...visible.map(r => Number(r.volume) || 0), 1) * 1.25];
        return {{
          title:{{text:`${{symbol}} K线图（${{interval}}）`, font:{{color:"#F8FAFC", size:15}}}},
          paper_bgcolor:"#050B14", plot_bgcolor:"#050B14", font:{{color:"#DDE6F3", size:11}},
          margin:{{l:50,r:78,t:38,b:30}},
          dragmode: chartInteractive ? "pan" : false,
          hovermode:"x unified",
          hoverlabel:{{bgcolor:"#0F172A", bordercolor:"#334155", font:{{color:"#FFFFFF"}}}},
          showlegend:true,
          legend:{{orientation:"h", y:1.035, x:0, font:{{size:10, color:"#CBD5E1"}}}},
          uirevision:`${{symbol}}-${{interval}}`,
          grid:{{rows:2, columns:1, subplots:[["xy"],["xy2"]], roworder:"top to bottom"}},
          xaxis:{{type:"date", domain:[0,1], anchor:"y", rangeslider:{{visible:false}}, showgrid:true, gridcolor:"rgba(148,163,184,.14)", zeroline:false, range, fixedrange:!chartInteractive, showspikes:true, spikemode:"across", spikesnap:"cursor", spikecolor:"#64748B", spikethickness:1, tickfont:{{color:"#CBD5E1", size:10}}}},
          yaxis:{{domain:[.28,1], anchor:"x", showgrid:true, gridcolor:"rgba(148,163,184,.14)", zeroline:false, autorange:!priceRange, range:priceRange, fixedrange:false, side:"right", tickfont:{{color:"#CBD5E1", size:10}}}},
          yaxis2:{{domain:[0,.22], anchor:"x", showgrid:true, gridcolor:"rgba(148,163,184,.10)", zeroline:false, range:volumeRange, fixedrange:false, tickfont:{{color:"#94A3B8", size:10}}}},
          shapes:[
            {{type:"line", xref:"paper", x0:0, x1:1, yref:"y", y0:sr.resistance, y1:sr.resistance, line:{{color:"#F6465D", dash:"dash", width:1}}}},
            {{type:"line", xref:"paper", x0:0, x1:1, yref:"y", y0:last, y1:last, line:{{color:"#F0B90B", dash:"dot", width:1}}}},
            {{type:"line", xref:"paper", x0:0, x1:1, yref:"y", y0:sr.support, y1:sr.support, line:{{color:"#22C55E", dash:"dash", width:1}}}}
          ],
          annotations:[
            {{xref:"paper", x:.985, yref:"y", y:sr.resistance, text:`压力 ${{fmtPrice(sr.resistance)}}`, showarrow:true, ax:-76, ay:-22, bgcolor:"rgba(15,23,42,.94)", bordercolor:"#F6465D", font:{{color:"#fff", size:10}}}},
            {{xref:"paper", x:.985, yref:"y", y:last, text:`现价 ${{fmtPrice(last)}}`, showarrow:true, ax:-76, ay:0, bgcolor:"rgba(15,23,42,.94)", bordercolor:"#F0B90B", font:{{color:"#fff", size:10}}}},
            {{xref:"paper", x:.985, yref:"y", y:sr.support, text:`支撑 ${{fmtPrice(sr.support)}}`, showarrow:true, ax:-76, ay:22, bgcolor:"rgba(15,23,42,.94)", bordercolor:"#22C55E", font:{{color:"#fff", size:10}}}}
          ]
        }};
      }}
      function config() {{
        return {{
          scrollZoom: chartInteractive,
          displayModeBar:false,
          responsive:true,
          doubleClick:"reset",
          staticPlot: !chartInteractive
        }};
      }}
      function bindTouchGestures() {{
        if (gestureBound || !chartInteractive) return;
        gestureBound = true;
        const point = t => ({{x:t.clientX, y:t.clientY}});
        const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
        const midpoint = (a, b) => ({{x:(a.x + b.x) / 2, y:(a.y + b.y) / 2}});
        const ratioAt = x => {{
          const rect = chartWrap.getBoundingClientRect();
          return Math.max(0, Math.min(1, (x - rect.left) / Math.max(rect.width, 1)));
        }};
        chartWrap.addEventListener("touchstart", ev => {{
          if (!chartInteractive) return;
          ev.preventDefault();
          ev.stopPropagation();
          const [start, end] = currentRangeMs();
          if (ev.touches.length >= 2) {{
            const p1 = point(ev.touches[0]);
            const p2 = point(ev.touches[1]);
            const mid = midpoint(p1, p2);
            touchState = {{mode:"pinch", start, end, span:end - start, dist:Math.max(1, distance(p1, p2)), ratio:ratioAt(mid.x)}};
          }} else if (ev.touches.length === 1) {{
            const p = point(ev.touches[0]);
            touchState = {{mode:"pan", start, end, x:p.x}};
          }}
        }}, {{passive:false}});
        chartWrap.addEventListener("touchmove", ev => {{
          if (!chartInteractive || !touchState) return;
          ev.preventDefault();
          ev.stopPropagation();
          const rect = chartWrap.getBoundingClientRect();
          if (touchState.mode === "pinch" && ev.touches.length >= 2) {{
            const p1 = point(ev.touches[0]);
            const p2 = point(ev.touches[1]);
            const mid = midpoint(p1, p2);
            const ratio = ratioAt(mid.x);
            const nextDist = Math.max(1, distance(p1, p2));
            const scale = Math.max(.16, Math.min(6, touchState.dist / nextDist));
            const span = touchState.span * scale;
            const anchor = touchState.start + touchState.span * touchState.ratio;
            setViewRangeMs(anchor - span * ratio, anchor + span * (1 - ratio));
            scheduleDraw();
          }} else if (touchState.mode === "pan" && ev.touches.length === 1) {{
            const p = point(ev.touches[0]);
            const span = touchState.end - touchState.start;
            const offset = -(p.x - touchState.x) * span / Math.max(rect.width, 1);
            setViewRangeMs(touchState.start + offset, touchState.end + offset);
            scheduleDraw();
          }}
        }}, {{passive:false}});
        chartWrap.addEventListener("touchend", ev => {{
          if (!chartInteractive) return;
          ev.preventDefault();
          ev.stopPropagation();
          if (!ev.touches.length) touchState = null;
        }}, {{passive:false}});
        chartWrap.addEventListener("wheel", ev => {{
          if (!chartInteractive) return;
          ev.preventDefault();
          ev.stopPropagation();
          const [start, end] = currentRangeMs();
          const span = end - start;
          const ratio = ratioAt(ev.clientX);
          const scale = ev.deltaY > 0 ? 1.16 : .86;
          const anchor = start + span * ratio;
          const nextSpan = span * scale;
          setViewRangeMs(anchor - nextSpan * ratio, anchor + nextSpan * (1 - ratio));
          scheduleDraw();
        }}, {{passive:false}});
      }}
      async function fetchKlines() {{
        const data = await fetchKlinesLive();
        klineMessage = data.message || "";
        const payloadRows = Array.isArray(data) ? data : (data.rows || []);
        rows = payloadRows.map(r => ({{openTime:r.openTime, open:Number(r.open), high:Number(r.high), low:Number(r.low), close:Number(r.close), volume:Number(r.volume), closeTime:r.closeTime}}));
      }}
      async function fetchTickerPatch() {{
        const data = await fetchTickerLive();
        const price = Number(data.last_price);
        if (!rows.length || !Number.isFinite(price)) return;
        const last = rows[rows.length - 1];
        last.close = price;
        last.high = Math.max(last.high, price);
        last.low = Math.min(last.low, price);
      }}
      async function draw() {{
        if (!rows.length) return;
        await Plotly.react(chart, buildTraces(), buildLayout(), config());
        if (!listenerBound && chart.on) {{
          chart.on("plotly_relayout", ev => {{
            userInteracted = true;
            if (ev && ev["xaxis.range[0]"] && ev["xaxis.range[1]"]) {{
              viewRange = [new Date(ev["xaxis.range[0]"]), new Date(ev["xaxis.range[1]"])];
              saveViewRange();
            }}
            if (ev && Array.isArray(ev["xaxis.range"]) && ev["xaxis.range"].length >= 2) {{
              viewRange = [new Date(ev["xaxis.range"][0]), new Date(ev["xaxis.range"][1])];
              saveViewRange();
            }}
            if (ev && ev["xaxis.autorange"]) {{
              viewRange = null;
              userInteracted = false;
              try {{ localStorage.removeItem(viewStorageKey); }} catch (_) {{}}
            }}
          }});
          listenerBound = true;
        }}
        bindTouchGestures();
      }}
      async function init() {{
        try {{
          restoreViewRange();
          if (rows.length) {{
            await draw();
            statusEl.textContent = "K线缓存已显示，实时更新中";
          }}
          await fetchKlines();
          restoreViewRange();
          if (rows.length) {{
            await draw();
            statusEl.textContent = "K线实时更新中";
          }} else {{
            statusEl.textContent = klineMessage || "等待本地K线缓存...";
          }}
        }} catch (err) {{
          statusEl.textContent = err && err.message ? err.message : "K线获取失败，正在重试";
        }}
      }}
      async function fastLoop() {{
        try {{
          if (!rows.length) await fetchKlines();
          await fetchTickerPatch();
          await draw();
          statusEl.textContent = rows.length ? `实时：${{new Date().toLocaleTimeString()}}` : (klineMessage || "K线正在后台刷新");
        }} catch (err) {{
          statusEl.textContent = err && err.message ? err.message : "实时更新失败，重试中";
        }}
      }}
      async function slowLoop() {{
        try {{
          await fetchKlines();
          await draw();
        }} catch (err) {{}}
      }}
      init();
      setInterval(fastLoop, 1000);
      setInterval(slowLoop, 15000);
    </script>
    """


def render_kline_live_status(symbol: str) -> None:
    """只刷新 K线实时状态，不重建图表。"""
    interval = market_cache.get_kline_interval()
    ticker = market_cache.get_ticker(symbol)
    snapshot = market_cache.snapshot()
    price = format_price(ticker["last_price"]) if ticker else "正在获取"
    st.markdown(
        f"""<div class="kline-meta-grid">
        <div class="kline-meta-box"><div class="metric-label">实时价格</div><div class="metric-value yellow">{price}</div></div>
        <div class="kline-meta-box"><div class="metric-label">K线缓存</div><div class="metric-value blue">{snapshot.get("kline_last_update_time", "初始化中")}</div></div>
        <div class="kline-meta-box"><div class="metric-label">当前周期</div><div class="metric-value">{interval}</div></div>
        <div class="kline-meta-box"><div class="metric-label">图表刷新策略</div><div class="metric-value green">操作优先</div></div>
        </div>""",
        unsafe_allow_html=True,
    )


def render_kline_system(symbol: str) -> None:
    """渲染专业实时K线系统。"""
    snapshot = market_cache.snapshot()
    interval = market_cache.get_kline_interval()
    if live_refresh_due(f"ticker:{symbol}", 2.0):
        try:
            refresh_symbol_now(symbol)
            snapshot = market_cache.snapshot()
        except Exception as exc:
            market_cache.set_ticker_error(f"K线现价刷新失败：{exc!r}")
            snapshot = market_cache.snapshot()
    rows = market_cache.get_klines(symbol, interval)
    if not rows or live_refresh_due(f"kline:{symbol}:{interval}", 8.0):
        try:
            refresh_klines_now(symbol, interval)
            rows = market_cache.get_klines(symbol, interval)
            snapshot = market_cache.snapshot()
        except Exception as exc:
            market_cache.set_kline_error(f"K线服务端刷新失败：{exc!r}")
            snapshot = market_cache.snapshot()
    st.markdown('<div id="kline-area"></div>', unsafe_allow_html=True)
    st.markdown(
        f"""<div class="app-shell"><div class="kline-card">
        <div class="kline-head">
          <div><div class="kline-title">专业实时K线系统</div><div class="module-desc">{symbol}｜后台更新｜图表操作优先，避免缩放闪屏</div></div>
          <div class="kline-status">状态：{snapshot.get("kline_status", "初始化中")}<br>更新：{snapshot.get("kline_last_update_time", "初始化中")}</div>
        </div>""",
        unsafe_allow_html=True,
    )
    st.radio("K线周期", KLINE_INTERVALS, key="kline_interval", horizontal=True, on_change=on_kline_interval_change, label_visibility="collapsed")
    c1, c2 = st.columns([1.7, 1])
    with c1:
        st.multiselect("均线显示", MA_OPTIONS, key="ma_visibility", placeholder="选择需要显示的均线")
    with c2:
        st.toggle("跟随最新", key="follow_latest")
        st.button("回到最新", on_click=reset_follow_latest, width="stretch")
    st.toggle("进入K线操作模式", key="chart_interactive", help="开启后启用缩放、拖动和十字悬浮；关闭时优先保证手机页面滚动顺畅。")
    if not rows:
        error = snapshot.get("kline_last_error") or "正在获取K线数据"
        left, right = st.columns([2.15, .85])
        with left:
            st.markdown(f'<div class="pending">{escape(str(error))}</div>', unsafe_allow_html=True)
        with right:
            st.markdown(f'<div class="side-stack"><div class="summary-card"><div class="summary-label">K线缓存</div><div class="summary-value yellow">{escape(str(error))}</div></div><div class="summary-card"><div class="summary-label">当前周期</div><div class="summary-value">{interval}</div></div><div class="summary-card"><div class="summary-label">历史查看模式</div><div class="summary-value blue">{"跟随最新" if st.session_state.follow_latest else "手动浏览"}</div></div></div>', unsafe_allow_html=True)
        st.markdown("</div></div>", unsafe_allow_html=True)
        return
    meta = build_kline_meta(rows)
    cross = meta["cross"]
    cross_text = cross["label"] if cross["type"] == "none" else f"{cross['label']}｜{cross['time'].strftime('%Y-%m-%d %H:%M:%S')}"
    components.html(
        frontend_kline_html(
            symbol,
            interval,
            st.session_state.ma_visibility,
            st.session_state.chart_interactive,
            st.session_state.follow_latest,
            rows,
        ),
        height=680,
        scrolling=False,
    )
    st.markdown(
        f"""<div class="side-stack">
        <div class="summary-card"><div class="summary-label">当前价格</div><div class="summary-value">{format_price(meta["last_close"])}</div></div>
        <div class="summary-card"><div class="summary-label">K线状态</div><div class="summary-value yellow">{meta["state"]}</div></div>
        <div class="summary-card"><div class="summary-label">MA20</div><div class="summary-value">{format_price(meta["ma20"])}</div></div>
        <div class="summary-card"><div class="summary-label">MA60</div><div class="summary-value">{format_price(meta["ma60"])}</div></div>
        <div class="summary-card"><div class="summary-label">支撑位</div><div class="summary-value green">{format_price(meta["support"]) if meta["support"] else "待确认"}</div></div>
        <div class="summary-card"><div class="summary-label">压力位</div><div class="summary-value red">{format_price(meta["resistance"]) if meta["resistance"] else "待确认"}</div></div>
        <div class="summary-card"><div class="summary-label">均线交叉</div><div class="summary-value">{cross_text}</div></div>
        <div class="summary-card"><div class="summary-label">刷新方式</div><div class="summary-value green">前端实时K线</div></div>
        </div>""",
        unsafe_allow_html=True,
    )
    st.markdown("</div></div>", unsafe_allow_html=True)

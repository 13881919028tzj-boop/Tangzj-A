"""Reusable Streamlit UI helpers."""

from __future__ import annotations

from html import escape
from textwrap import dedent
from typing import Any

import streamlit as st


def signal_color(value: str) -> str:
    text = str(value)
    if any(keyword in text for keyword in ["支持", "做多", "多头", "BUY", "long", "允许", "approved", "盈利"]):
        return "green"
    if any(keyword in text for keyword in ["反对", "禁止", "做空", "空头", "SELL", "short", "blocked", "亏损"]):
        return "red"
    if any(keyword in text for keyword in ["谨慎", "等待", "观察", "中性", "WAIT", "yellow"]):
        return "yellow"
    return "blue"


def safe_committee_text(value: Any, limit: int = 260) -> str:
    text = str(value or "暂无")
    text = " ".join(text.split())
    return text[:limit] + "..." if len(text) > limit else text


def html_no_code_block(html: str) -> str:
    return "\n".join(line.lstrip() for line in dedent(str(html)).splitlines()).strip()


def render_html(html: str) -> None:
    st.markdown(html_no_code_block(html), unsafe_allow_html=True)


def render_page_head(page_key: str, page_titles: dict[str, tuple[str, str]], version: str) -> None:
    title, desc = page_titles[page_key]
    st.markdown(
        f'<div class="app-shell"><div class="page-head"><div><div class="page-title">{escape(title)}</div><div class="page-desc">{escape(desc)}</div></div><div class="version-pill">{escape(version)}</div></div></div>',
        unsafe_allow_html=True,
    )


def render_metric_grid(items: list[tuple[str, str, str]]) -> None:
    html = ['<div class="app-shell"><div class="metric-grid">']
    for label, value, klass in items:
        html.append(f'<div class="metric-box"><div class="metric-label">{escape(str(label))}</div><div class="metric-value {klass}">{value}</div></div>')
    html.append("</div></div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def normalize_symbol(symbol: Any, fallback: str = "BTCUSDT") -> str:
    normalized = str(symbol or "").upper().strip()
    return normalized if normalized.endswith("USDT") else fallback


def kline_href(symbol: Any, fallback: str = "BTCUSDT") -> str:
    normalized = normalize_symbol(symbol, fallback)
    return f"?page=signals&symbol={escape(normalized)}#kline-area"


def kline_symbol_link(symbol: Any, label: str | None = None, css_class: str = "rank-link", fallback: str = "BTCUSDT") -> str:
    normalized = normalize_symbol(symbol, fallback)
    text = escape(str(label or normalized))
    return f'<a class="{css_class}" href="{kline_href(normalized, fallback)}" target="_self">{text}</a>'


def render_kline_jump_links(symbols: list[Any], title: str = "相关币种K线", fallback: str = "BTCUSDT") -> None:
    unique: list[str] = []
    seen: set[str] = set()
    for item in symbols:
        symbol = normalize_symbol(item, "")
        if symbol and symbol not in seen:
            seen.add(symbol)
            unique.append(symbol)
    if not unique:
        return
    links = " ".join(kline_symbol_link(symbol, symbol, "watch-pill", fallback=fallback) for symbol in unique[:24])
    st.markdown(
        f'<div class="app-shell"><div class="status-card"><b>{escape(title)}</b><br>{links}</div></div>',
        unsafe_allow_html=True,
    )

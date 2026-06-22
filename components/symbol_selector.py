"""Global symbol selector widgets."""

from __future__ import annotations

from html import escape
from typing import Callable

import streamlit as st

from services import market_cache


def filter_symbols(query: str, symbols: list[str]) -> list[str]:
    """Case-insensitive USDT symbol search with exact and prefix priority."""
    clean_symbols = [str(symbol).upper().strip() for symbol in symbols if str(symbol).upper().strip().endswith("USDT")]
    seen: set[str] = set()
    unique_symbols = []
    for symbol in clean_symbols:
        if symbol not in seen:
            seen.add(symbol)
            unique_symbols.append(symbol)
    q = str(query or "").upper().strip()
    if not q:
        return unique_symbols[:50]
    exact = [symbol for symbol in unique_symbols if symbol == q or symbol.replace("USDT", "") == q]
    prefix = [symbol for symbol in unique_symbols if symbol.startswith(q) and symbol not in exact]
    contains = [symbol for symbol in unique_symbols if q in symbol and symbol not in exact and symbol not in prefix]
    return (exact + prefix + contains)[:50]


def render_global_symbol_selector(
    location: str = "overview",
    symbols: list[str] | None = None,
    *,
    fallback_symbols: list[str] | None = None,
    set_symbol: Callable[[str, str], None],
) -> None:
    """Render the global current-symbol selector and synchronize via callback."""
    available = symbols or market_cache.get_symbols(fallback_symbols or ["BTCUSDT"])
    current = str(st.session_state.get("current_symbol") or "BTCUSDT").upper().strip()
    query_key = f"{location}_symbol_search_query"
    select_key = f"{location}_symbol_selector"
    st.markdown(
        f"""<div class="app-shell"><div class="symbol-panel">
        <div class="symbol-row"><div><div class="symbol-panel-title">当前交易对象</div><div class="symbol-current">{escape(current)}</div><div class="symbol-hint">可输入 BTC、ETH、SOL、PEPE 等关键词搜索，切换后全站同步。</div></div></div>
        </div></div>""",
        unsafe_allow_html=True,
    )
    popover = getattr(st, "popover", None)
    container = popover(f"{current} ▼", width="stretch") if popover else st.expander(f"{current} ▼", expanded=False)
    with container:
        search = st.text_input("搜索交易对象", key=query_key, placeholder="输入 BTC / ETH / SOL / 1000 / PEPE")
        filtered = filter_symbols(search, available)
        if current and current not in filtered:
            filtered = [current] + filtered
        if not filtered:
            st.caption("没有匹配的交易对象")
            return
        st.caption(f"显示前 {min(len(filtered), 50)} 个匹配交易对")
        selected = st.selectbox(
            "选择交易对象",
            filtered,
            index=filtered.index(current) if current in filtered else 0,
            key=select_key,
        )
        if selected and selected != current:
            set_symbol(selected, f"{location}_search")
            st.rerun()


def render_symbol_search_panel(
    symbols: list[str],
    key_prefix: str,
    *,
    fallback_symbols: list[str] | None = None,
    set_symbol: Callable[[str, str], None],
) -> None:
    """Compatibility wrapper for the global symbol selector."""
    render_global_symbol_selector(
        key_prefix,
        symbols,
        fallback_symbols=fallback_symbols,
        set_symbol=set_symbol,
    )

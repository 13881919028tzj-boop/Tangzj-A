"""Global Streamlit CSS for the mobile-first UI."""

from __future__ import annotations

import streamlit as st


def inject_styles() -> None:
    st.markdown(
        """
        <style>
          :root { --bg:#050B14; --panel:#0F172A; --panel2:#111827; --border:#1F2937; --border2:#334155; --text:#E5E7EB; --muted:#9CA3AF; --green:#00C087; --red:#F6465D; --yellow:#F0B90B; --blue:#3B82F6; }
          .stApp { background:radial-gradient(circle at 20% 0%, rgba(59,130,246,.12), transparent 28%), var(--bg); color:var(--text); }
          [data-testid="stHeader"] { background:transparent; height:0; } [data-testid="stToolbar"] { display:none; }
          .block-container { padding:48px 8px 82px; max-width:1180px; }
          h1,h2,h3,p,span,div,label { color:var(--text); }
          .app-shell { max-width:1180px; margin:0 auto; }
          .market-ticker { position:fixed; top:0; left:0; right:0; z-index:998; background:rgba(5,11,20,.97); border-bottom:1px solid var(--border2); backdrop-filter:blur(14px); padding:4px 6px; }
          .market-ticker-inner { max-width:1180px; margin:0 auto; display:grid; grid-template-columns:1.12fr 1fr .76fr .78fr .72fr .72fr; gap:4px; }
          .ticker-cell { min-height:29px; border:1px solid var(--border); background:rgba(15,23,42,.92); border-radius:8px; padding:3px 5px; overflow:hidden; }
          .ticker-label { color:var(--muted); font-size:9px; line-height:1; }
          .ticker-value { color:#fff; font-size:11px; font-weight:900; margin-top:2px; white-space:nowrap; }
          .green { color:var(--green)!important; } .red { color:var(--red)!important; } .yellow { color:var(--yellow)!important; } .blue { color:var(--blue)!important; }
          .page-head { display:flex; justify-content:space-between; align-items:flex-start; gap:10px; margin:10px 0 8px; }
          .page-title { font-size:20px; font-weight:900; }
          .page-desc,.module-desc,.small-muted { color:var(--muted); font-size:11px; line-height:1.45; }
          .version-pill,.pending { border:1px solid rgba(240,185,11,.35); color:var(--yellow); background:rgba(240,185,11,.08); border-radius:999px; padding:5px 8px; font-size:11px; font-weight:800; width:max-content; }
          .module-grid { display:grid; grid-template-columns:1fr; gap:8px; }
          .module-card,.kline-card,.list-card { border:1px solid var(--border); background:linear-gradient(180deg, rgba(15,23,42,.96), rgba(17,24,39,.92)); border-radius:14px; padding:10px; }
          .module-card { min-height:78px; }
          .rank-layout { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; align-items:start; }
          .rank-layout .list-card { min-width:0; padding:8px; }
          .rank-layout .module-title { font-size:13px; }
          .module-title { font-size:15px; font-weight:900; color:#fff; }
          .metric-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin:8px 0; }
          .metric-box { border:1px solid var(--border2); background:rgba(5,11,20,.48); border-radius:10px; padding:7px; min-height:50px; }
          .metric-label { color:var(--muted); font-size:11px; }
          .metric-value { color:#fff; font-size:15px; font-weight:900; margin-top:3px; }
          .terminal-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:7px; margin:8px 0; }
          .terminal-card { border:1px solid rgba(51,65,85,.72); background:rgba(5,11,20,.50); border-radius:10px; padding:7px; min-height:48px; }
          .terminal-label { color:var(--muted); font-size:10px; line-height:1.1; }
          .terminal-value { color:#fff; font-size:14px; font-weight:900; margin-top:4px; line-height:1.18; overflow-wrap:anywhere; }
          .side-layout { display:grid; grid-template-columns:minmax(0,2.15fr) minmax(220px,.85fr); gap:8px; align-items:stretch; margin-top:8px; }
          .side-stack { display:grid; grid-template-columns:1fr; gap:6px; align-content:start; }
          .summary-card { border:1px solid rgba(51,65,85,.72); background:rgba(15,23,42,.72); border-radius:10px; padding:7px; min-height:44px; }
          .summary-label { color:var(--muted); font-size:10px; }
          .summary-value { color:#fff; font-size:14px; font-weight:900; margin-top:3px; overflow-wrap:anywhere; }
          .committee-summary-panel { border:1px solid var(--border); background:rgba(15,23,42,.74); border-radius:12px; padding:9px; margin-top:8px; }
          .committee-summary-title { color:#fff; font-size:13px; font-weight:900; margin-bottom:6px; }
          .committee-summary-strip { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; }
          .committee-summary-item { border:1px solid rgba(51,65,85,.72); background:rgba(5,11,20,.38); border-radius:9px; padding:6px; min-height:40px; overflow:hidden; }
          .committee-summary-item .label { color:var(--muted); font-size:9px; line-height:1.1; white-space:nowrap; }
          .committee-summary-item .value { color:#fff; font-size:12px; line-height:1.18; font-weight:900; margin-top:4px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
          .committee-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; margin-top:8px; }
          .committee-vote-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px; margin-top:8px; }
          .quick-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin-top:8px; }
          .quick-button { border:1px solid var(--border2); background:rgba(5,11,20,.5); border-radius:12px; padding:9px; min-height:46px; display:flex; align-items:center; justify-content:center; text-align:center; font-weight:900; }
          .symbol-panel { border:1px solid var(--border); background:rgba(15,23,42,.82); border-radius:14px; padding:8px; margin:8px 0; }
          .symbol-panel-title { font-size:13px; font-weight:900; color:#fff; margin-bottom:5px; }
          .symbol-row { display:grid; grid-template-columns:1fr auto; gap:8px; align-items:center; }
          .symbol-current { color:#fff; font-size:18px; font-weight:900; }
          .symbol-hint { color:var(--muted); font-size:11px; }
          .rank-list { display:flex; flex-direction:column; gap:0; margin-top:6px; }
          .rank-row { display:grid; grid-template-columns:25px 1.22fr .92fr .7fr .82fr; gap:3px; align-items:center; min-height:22px; border-bottom:1px solid rgba(51,65,85,.28); font-size:9.8px; text-decoration:none; }
          .rank-row:last-child { border-bottom:none; }
          .rank-head { position:sticky; top:38px; z-index:5; color:var(--muted); font-size:9.4px; font-weight:800; background:rgba(15,23,42,.98); border-radius:8px; }
          .rank-index { color:var(--yellow); font-weight:900; }
          .rank-index.gold { color:#F0B90B; } .rank-index.silver { color:#CBD5E1; } .rank-index.bronze { color:#CD7F32; }
          .rank-symbol { font-weight:900; color:#fff; }
          .rank-volume { color:var(--muted); text-align:right; }
          .rank-link { color:inherit; text-decoration:none; }
          .rank-link:hover { background:rgba(240,185,11,.07); border-radius:7px; }
          div[data-testid="stTabs"] [role="tablist"] { overflow-x:auto; flex-wrap:nowrap; gap:4px; border-bottom:1px solid rgba(51,65,85,.55); }
          div[data-testid="stTabs"] [role="tab"] { flex:0 0 auto; white-space:nowrap; color:var(--muted); font-size:12px; font-weight:900; padding:6px 8px; }
          div[data-testid="stTabs"] [aria-selected="true"] { color:var(--yellow)!important; border-bottom-color:var(--yellow)!important; }
          .opp-row { display:grid; grid-template-columns:1.28fr .86fr .66fr .66fr; gap:5px; align-items:center; min-height:34px; border-bottom:1px solid rgba(51,65,85,.28); padding:4px 0; font-size:10.2px; }
          .opp-row.compact-five { grid-template-columns:1.22fr .66fr .68fr .62fr .72fr; gap:4px; min-height:32px; }
          .opp-row:last-child { border-bottom:none; }
          .rank-layout .opp-row { grid-template-columns:1.28fr .78fr .62fr .72fr; gap:3px; min-height:30px; font-size:9.2px; }
          .rank-layout .opp-meta { font-size:8px; }
          .rank-layout .opp-symbol { font-size:9.4px; }
          .opp-symbol { color:#fff; font-weight:900; }
          .opp-meta { color:var(--muted); font-size:9px; line-height:1.35; }
          .score-pill { border:1px solid rgba(59,130,246,.38); background:rgba(59,130,246,.10); color:#93C5FD; border-radius:999px; padding:2px 6px; font-weight:900; width:max-content; }
          .advice-pill { border:1px solid rgba(240,185,11,.34); background:rgba(240,185,11,.08); color:var(--yellow); border-radius:999px; padding:2px 6px; font-weight:900; width:max-content; }
          .watch-pill { display:inline-flex; align-items:center; justify-content:center; min-height:21px; border:1px solid rgba(240,185,11,.38); background:rgba(240,185,11,.08); color:var(--yellow); border-radius:999px; padding:2px 7px; font-size:9px; font-weight:900; text-decoration:none; white-space:nowrap; }
          .watch-pill.done { border-color:rgba(0,192,135,.38); background:rgba(0,192,135,.09); color:var(--green); }
          .watch-info-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:1px; border:1px solid rgba(51,65,85,.75); border-radius:12px; overflow:hidden; margin-top:8px; background:rgba(5,11,20,.34); }
          .watch-info-cell { min-height:42px; padding:6px; border-right:1px solid rgba(51,65,85,.45); border-bottom:1px solid rgba(51,65,85,.45); }
          .watch-info-cell:nth-child(4n) { border-right:none; }
          .watch-info-label { color:var(--muted); font-size:9px; line-height:1; }
          .watch-info-value { color:#fff; font-size:12px; font-weight:900; margin-top:4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
          .watch-action-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:4px; margin:5px 0 8px; }
          .watch-action-grid div[data-testid="stButton"] > button { width:100%; min-height:25px; font-size:10px; padding:1px 3px; }
          div[data-testid="stForm"],
          div[data-testid="stForm"] > div,
          div[data-testid="stTextInput"],
          div[data-testid="stNumberInput"],
          div[data-testid="stTextArea"],
          div[data-testid="stDateInput"],
          div[data-testid="stTimeInput"],
          div[data-baseweb="input"],
          div[data-baseweb="textarea"],
          div[data-baseweb="input"] > div,
          div[data-baseweb="textarea"] > div,
          div[data-testid="stTextInput"] input,
          div[data-testid="stNumberInput"] input,
          div[data-testid="stTextArea"] textarea,
          div[data-testid="stDateInput"] input,
          div[data-testid="stTimeInput"] input {
            background:rgba(15,23,42,.96)!important;
            color:#E5E7EB!important;
            border:1px solid rgba(51,65,85,.88)!important;
            border-radius:8px!important;
            box-shadow:none!important;
          }
          div[data-testid="stTextInput"] input:focus,
          div[data-testid="stNumberInput"] input:focus,
          div[data-testid="stTextArea"] textarea:focus,
          div[data-testid="stDateInput"] input:focus,
          div[data-testid="stTimeInput"] input:focus {
            border-color:rgba(240,185,11,.72)!important;
            box-shadow:0 0 0 1px rgba(240,185,11,.22)!important;
          }
          div[data-testid="stTextInput"] input::placeholder,
          div[data-testid="stNumberInput"] input::placeholder,
          div[data-testid="stTextArea"] textarea::placeholder { color:#64748B!important; opacity:1!important; }
          div[data-testid="stNumberInput"] button,
          div[data-testid="stNumberInput"] button:hover {
            background:rgba(5,11,20,.74)!important;
            color:#E5E7EB!important;
            border-color:rgba(51,65,85,.88)!important;
          }
          div[data-baseweb="select"],
          div[data-baseweb="select"] > div,
          div[data-baseweb="select"] > div > div,
          div[data-baseweb="select"] [role="combobox"],
          div[data-baseweb="popover"] ul,
          div[data-baseweb="menu"],
          div[data-baseweb="menu"] ul,
          div[data-baseweb="menu"] li,
          div[role="listbox"],
          div[role="option"] {
            background:rgba(15,23,42,.98)!important;
            color:#E5E7EB!important;
            border-color:rgba(51,65,85,.88)!important;
            box-shadow:0 12px 28px rgba(0,0,0,.38)!important;
          }
          div[data-baseweb="select"] span,
          div[data-baseweb="select"] svg,
          div[data-baseweb="menu"] li,
          div[data-baseweb="popover"] li { color:#E5E7EB!important; fill:#E5E7EB!important; }
          div[data-baseweb="menu"] li:hover,
          div[data-baseweb="popover"] li:hover,
          div[role="option"][aria-selected="true"] {
            background:rgba(240,185,11,.12)!important;
            color:#F0B90B!important;
          }
          div[data-testid="stRadio"] label,
          div[data-testid="stCheckbox"] label { color:#E5E7EB!important; }
          div[data-testid="stRadio"] [role="radiogroup"] label,
          div[data-testid="stCheckbox"] label {
            background:rgba(15,23,42,.62);
            border:1px solid rgba(51,65,85,.55);
            border-radius:9px;
            padding:4px 7px;
          }
          div[data-testid="stSlider"] [data-baseweb="slider"] div { color:#E5E7EB!important; }
          div[data-testid="stButton"] > button,
          div[data-testid="stFormSubmitButton"] > button,
          button[kind],
          button[data-testid] {
            min-height:24px!important;
            padding:1px 5px!important;
            border-radius:7px!important;
            border:1px solid rgba(51,65,85,.78)!important;
            background:rgba(15,23,42,.9)!important;
            color:#E5E7EB!important;
            font-size:10.5px;
            font-weight:900;
            line-height:1.1;
            box-shadow:none!important;
          }
          div[data-testid="stFormSubmitButton"] > button:hover,
          div[data-testid="stButton"] > button:hover,
          button[kind]:hover {
            border-color:rgba(240,185,11,.6)!important;
            color:#F0B90B!important;
            background:rgba(5,11,20,.96)!important;
          }
          div[data-testid="stExpander"] { background:#0f172a!important; border:1px solid #24324a!important; border-radius:14px!important; color:#e5e7eb!important; overflow:hidden; }
          div[data-testid="stExpander"] details { background:#0f172a!important; color:#e5e7eb!important; }
          div[data-testid="stExpander"] summary { background:#0f172a!important; color:#e5e7eb!important; border-radius:12px!important; }
          div[data-testid="stExpander"] * { color:#e5e7eb; }
          pre, code { background:#111827!important; color:#e5e7eb!important; border-color:#24324a!important; }
          .status-card { border:1px solid var(--border); background:rgba(15,23,42,.74); border-radius:12px; padding:9px; font-size:12px; line-height:1.6; }
          .error-box { border:1px solid rgba(246,70,93,.42); background:rgba(246,70,93,.12); color:#FCA5A5; border-radius:12px; padding:10px; margin:8px 0; font-size:12px; }
          .kline-head { display:flex; justify-content:space-between; align-items:flex-start; gap:8px; margin-bottom:8px; }
          .kline-title { font-size:16px; font-weight:900; color:#fff; }
          .kline-status { color:var(--muted); font-size:11px; line-height:1.5; text-align:right; }
          .kline-meta-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin-top:8px; }
          .kline-meta-box { border:1px solid var(--border2); background:rgba(15,23,42,.68); border-radius:12px; padding:8px; min-height:56px; }
          .js-plotly-plot .plotly, .js-plotly-plot .main-svg { touch-action:none; }
          .orderbook-card { border:1px solid var(--border); background:linear-gradient(180deg, rgba(15,23,42,.96), rgba(5,11,20,.92)); border-radius:14px; padding:10px; margin:8px 0; }
          .orderbook-head { display:flex; justify-content:space-between; align-items:flex-start; gap:8px; margin-bottom:6px; }
          .orderbook-title { font-size:16px; font-weight:900; color:#fff; }
          .orderbook-status { color:var(--muted); font-size:11px; line-height:1.45; text-align:right; }
          .orderbook-grid { display:grid; grid-template-columns:1fr; gap:6px; }
          .orderbook-table { display:flex; flex-direction:column; gap:0; }
          .orderbook-row { position:relative; display:grid; grid-template-columns:1fr 1fr 1fr; align-items:center; min-height:22px; padding:1px 4px; border-bottom:1px solid rgba(51,65,85,.26); font-size:10px; overflow:hidden; }
          .orderbook-row.header { color:var(--muted); font-weight:800; background:rgba(15,23,42,.8); border-radius:7px; }
          .orderbook-row.large { background:rgba(240,185,11,.08); }
          .depth-bar { position:absolute; top:1px; bottom:1px; right:0; opacity:.18; border-radius:5px; pointer-events:none; }
          .depth-bar.ask { background:var(--red); }
          .depth-bar.bid { background:var(--green); }
          .ob-cell { position:relative; z-index:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
          .ob-right { text-align:right; }
          .last-price-box { border:1px solid var(--border2); border-radius:12px; padding:8px; margin:6px 0; text-align:center; background:rgba(5,11,20,.52); }
          .last-price { font-size:22px; font-weight:900; color:#fff; }
          .ratio-bar { display:grid; grid-template-columns:1fr 1fr; height:8px; overflow:hidden; border-radius:999px; background:rgba(148,163,184,.18); margin:7px 0; }
          .ratio-buy { background:var(--green); }
          .ratio-sell { background:var(--red); }
          .orderbook-summary { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px; margin-top:8px; }
          .orderbook-summary .metric-box { min-height:48px; padding:6px; }
          .bottom-nav { position:fixed; left:0; right:0; bottom:0; z-index:999; background:rgba(5,11,20,.96); border-top:1px solid var(--border2); backdrop-filter:blur(14px); padding:5px 6px 6px; overflow-x:auto; scrollbar-width:none; }
          .bottom-nav::-webkit-scrollbar { display:none; }
          .bottom-nav-inner { max-width:1160px; min-width:max-content; margin:0 auto; display:flex; flex-wrap:nowrap; gap:3px; }
          .nav-item { flex:0 0 54px; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:2px; min-height:46px; border-radius:10px; color:var(--muted); font-size:9.5px; line-height:1; border:1px solid transparent; text-decoration:none; white-space:nowrap; }
          .nav-item.active { color:var(--yellow); background:rgba(240,185,11,.09); border-color:rgba(240,185,11,.28); } .nav-icon { font-size:15px; line-height:1; }
          @media (min-width:720px) { .block-container { padding:52px 18px 96px; } .module-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } .metric-grid,.kline-meta-grid { grid-template-columns:repeat(4,minmax(0,1fr)); } }
          @media (min-width:900px) { .orderbook-grid { grid-template-columns:1fr 1fr; } }
          @media (min-width:1100px) { .module-grid { grid-template-columns:repeat(3,minmax(0,1fr)); } }
          @media (max-width:900px) { .side-layout { grid-template-columns:1fr; } .side-stack { grid-template-columns:repeat(2,minmax(0,1fr)); } .terminal-grid,.committee-grid,.committee-summary-strip { grid-template-columns:repeat(2,minmax(0,1fr)); } .committee-vote-grid { grid-template-columns:1fr; } }
          @media (max-width:430px) { .market-ticker-inner { grid-template-columns:1.08fr .98fr .72fr .72fr .66fr .66fr; gap:3px; } .ticker-cell { min-height:27px; padding:3px 4px; } .ticker-label { font-size:8px; } .ticker-value { font-size:10px; } .rank-row { grid-template-columns:22px 1.15fr .83fr .66fr .76fr; min-height:21px; font-size:9px; } .opp-row.compact-five { grid-template-columns:1.05fr .6fr .64fr .58fr .62fr; gap:2px; font-size:9px; } .watch-pill { font-size:8px; padding:2px 5px; } .watch-info-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } .watch-info-cell:nth-child(2n) { border-right:none; } .watch-action-grid { grid-template-columns:repeat(4,minmax(0,1fr)); gap:3px; } .watch-action-grid div[data-testid="stButton"] > button { font-size:9px; } .terminal-grid,.side-stack,.committee-grid,.committee-summary-strip { grid-template-columns:repeat(2,minmax(0,1fr)); gap:5px; } .terminal-card,.summary-card,.committee-summary-item { padding:6px; min-height:40px; } .terminal-value,.summary-value,.committee-summary-item .value { font-size:12px; } .bottom-nav { padding:4px 4px 5px; } .bottom-nav-inner { gap:2px; } .nav-item { flex-basis:42px; min-height:40px; border-radius:8px; font-size:7.5px; } .nav-icon { font-size:12px; } }
        </style>
        """,
        unsafe_allow_html=True,
    )

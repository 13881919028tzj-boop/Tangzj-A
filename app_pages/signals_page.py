"""Signal page and trading committee UI."""

from __future__ import annotations

import re
from html import escape
from textwrap import dedent
from typing import Any, Callable

import streamlit as st

from app_pages.kline_page import render_kline_system
from app_pages.orderbook_page import render_orderbook_system
from app_pages.signal_analysis_page import render_signal_analysis as render_signal_analysis_page
from app_pages.simulation_page import committee_decision_to_sim_signal
from app_state.session import set_current_symbol
from components.market_widgets import render_watchlist_quick_controls
from components.topbar import get_effective_ticker
from components.ui import render_page_head
from services import market_cache
from services.ai_committee_engine import get_committee_candidates
from services.manual_position_override import evaluate_manual_position_override, save_manual_position_override
from services.sim_trade_engine import create_pending_sim_order, get_sim_account_summary, validate_signal_for_simulation
from utils.formatters import format_price


def render_committee_overview_window(decision: dict[str, Any]) -> None:
    """总览页委员会精简窗口。"""
    if not decision:
        return
    v91 = decision.get("trading_committee_v91") or {}
    v91_risk = v91.get("risk_judge") or {}
    v91_position = v91.get("position_plan") or {}
    v91_execution = v91.get("execution_plan") or {}
    permission = _committee_permission_text(str(decision.get("trade_permission", "rejected")))
    action = str(decision.get("final_action", "继续观察"))
    direction = str(decision.get("final_direction_text", "中性"))
    simulation_text = "是" if decision.get("approved_for_simulation") else "否"
    supporting = list(decision.get("supporting_members") or [])
    opposing = list(decision.get("opposing_members") or [])
    veto_members = list(decision.get("veto_members") or [])
    weight_summary = _committee_weight_summary(decision)
    vote_weight_text = (
        f"支持{_fmt_weight(weight_summary['support_weight'])} / "
        f"观望{_fmt_weight(weight_summary['neutral_weight'])} / "
        f"反对{_fmt_weight(weight_summary['oppose_weight'] + weight_summary['veto_weight'])}"
    )
    vote_cards = []
    for member in list(decision.get("member_votes") or [])[:8]:
        reasons = list(member.get("reasons") or [])
        risks = list(member.get("risks") or [])
        reason = reasons[0] if reasons else (risks[0] if risks else "等待更多数据确认。")
        member_name = str(member.get("member_name", "委员"))
        member_weight = _committee_member_weight(member_name, dict(decision.get("committee_weights") or {}))
        member_bucket = _committee_vote_bucket(member)
        member_type = "影子" if member.get("shadow") else "正式"
        vote_cards.append(
            f"""<div class="summary-card">
              <div class="summary-label">{escape(member_name)} · {member_type} · 权重{_fmt_weight(member_weight)}</div>
              <div class="summary-value {_signal_color(str(member.get("vote", "")))}">{escape(str(member.get("vote", "建议观望")))}</div>
              <div class="module-desc">计入：{escape(member_bucket)}｜方向：{escape(str(member.get("direction_text", "中性")))}｜信心：{member.get("confidence", 0)}｜加权：{escape(str(member.get("weighted_score", 0)))}｜否决：{"是" if member.get("veto") else "否"}</div>
              <div class="module-desc">理由：{escape(_safe_committee_text(reason))}</div>
            </div>"""
        )
    veto_status = "已触发" if veto_members else "未触发"
    render_html(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">交易委员会</div>
            <div class="metric-value {_signal_color(action)}">{escape(action)} · {escape(direction)}</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">当前交易对象</div><div class="summary-value yellow">{escape(str(decision.get("symbol", "-")))}</div></div>
              <div class="summary-card"><div class="summary-label">交易许可</div><div class="summary-value {_signal_color(action)}">{escape(permission)}</div></div>
              <div class="summary-card"><div class="summary-label">共振等级</div><div class="summary-value yellow">{escape(str(decision.get("resonance_text", "无共振")))}</div></div>
              <div class="summary-card"><div class="summary-label">委员会置信度</div><div class="summary-value blue">{decision.get("committee_confidence", 0)} / 100</div></div>
              <div class="summary-card"><div class="summary-label">委员会风险</div><div class="summary-value {_signal_color(str(decision.get("committee_risk_score", 0)))}">{decision.get("committee_risk_score", 0)} / 100</div></div>
              <div class="summary-card"><div class="summary-label">建议仓位</div><div class="summary-value yellow">{escape(str(decision.get("position_suggestion", "0%")))}</div></div>
              <div class="summary-card"><div class="summary-label">模拟候选</div><div class="summary-value {_signal_color("支持交易" if decision.get("approved_for_simulation") else "反对交易")}">{simulation_text}</div></div>
              <div class="summary-card"><div class="summary-label">风险否决</div><div class="summary-value {_signal_color("禁止开仓" if veto_members else "支持交易")}">{veto_status}</div></div>
              <div class="summary-card"><div class="summary-label">投票统计</div><div class="summary-value">支持{len(supporting)} / 反对{len(opposing)} / 否决{len(veto_members)}</div></div>
              <div class="summary-card"><div class="summary-label">权重投票</div><div class="summary-value yellow">{vote_weight_text}</div></div>
              <div class="summary-card"><div class="summary-label">影子参考</div><div class="summary-value blue">{_fmt_weight(weight_summary['shadow_weight'])}</div></div>
              <div class="summary-card"><div class="summary-label">9.1交易结论</div><div class="summary-value {_signal_color(str(v91.get("final_action", "WAIT")))}">{escape(str(v91.get("final_action", "WAIT")))} / {escape(str(v91.get("final_direction", "WAIT")))}</div></div>
              <div class="summary-card"><div class="summary-label">风险裁判</div><div class="summary-value {_signal_color("禁止开仓" if v91_risk.get("blocked") else "支持交易")}">{escape(str(v91_risk.get("risk_verdict", "WAIT")))}</div></div>
              <div class="summary-card"><div class="summary-label">仓位委员会</div><div class="summary-value yellow">{float(v91_position.get("position_size_pct", 0) or 0):.2f}% / {int(v91_position.get("leverage", 1) or 1)}x</div></div>
              <div class="summary-card"><div class="summary-label">执行委员会</div><div class="summary-value {_signal_color("支持交易" if v91_execution.get("execution_allowed") else "反对交易")}">{escape(str(v91_execution.get("execution_type", "WAIT")))}</div></div>
            </div>
            {_render_committee_summary_panel(decision)}
            <details class="status-card" style="margin-top:8px;">
              <summary><b>查看委员意见</b></summary>
              <div class="committee-vote-grid">{"".join(vote_cards)}</div>
            </details>
            <a class="watch-pill" href="?page=signals&symbol={escape(str(decision.get("symbol", "")))}" target="_self" style="margin-top:8px;">查看完整委员会详情</a>
          </div>
        </div>
        """
    )
    render_committee_full_summary_expander(decision, "查看完整总结")

def _signal_color(value: str) -> str:
    """根据中文信号选择颜色类。"""
    if value in {"强多", "偏多", "顺势做多", "轻仓试多", "上升趋势", "突破", "回踩确认", "加速上涨", "金叉", "多头延续", "极强", "偏强", "资金流入", "健康上涨", "健康下跌", "空头回补", "安全", "较安全", "低", "低风险", "偏低风险", "可交易", "轻仓可试", "支持交易", "轻仓支持"}:
        return "green"
    if value in {"强空", "偏空", "顺势做空", "轻仓试空", "下降趋势", "跌破", "加速下跌", "极高风险", "高风险", "死叉", "空头延续", "极弱", "偏弱", "资金恐慌", "危险上涨", "多头拥挤", "空头拥挤", "恐慌下跌", "空头挤压风险", "多头踩踏风险", "高风险双向震荡", "高风险上涨", "高风险下跌", "多空双杀风险", "疑似诱多", "疑似诱空", "极端风险", "高", "极高", "不建议开仓", "禁止开仓", "反对交易"}:
        return "red"
    if value in {"中等风险", "横盘震荡", "假突破", "观望", "不建议追多", "不建议追空", "资金观望", "资金过热", "高风险震荡", "中性", "正常", "中", "震荡观望", "谨慎交易"}:
        return "yellow"
    return "blue"


def _safe_committee_text(value: Any, limit: int = 260) -> str:
    """Redact keys/URLs/code-like blobs before rendering committee text."""
    text = str(value or "")
    leak_markers = [
        "HTTPSConnectionPool",
        "NameResolutionError",
        "Max retries exceeded",
        "generateContent",
        "chat/completions",
        "api.deepseek.com",
        "generativelanguage.googleapis.com",
        "api_key",
        "x-goog-api-key",
        "Authorization",
        "Traceback",
    ]
    if any(marker.lower() in text.lower() for marker in leak_markers):
        return "外部AI暂不可用，已按观望处理；本地委员会继续运行。"
    text = re.sub(r"([?&]key=)[^&\s)\"']+", r"\1[已隐藏]", text, flags=re.IGNORECASE)
    text = re.sub(r"(Authorization:\s*Bearer\s+)[^\s,;]+", r"\1[已隐藏]", text, flags=re.IGNORECASE)
    text = re.sub(r"(x-goog-api-key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,;]+", r"\1[已隐藏]", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://[^\s)\"']+", "[外部接口地址已隐藏]", text)
    text = re.sub(r"\b[A-Za-z0-9_\-]{24,}\b", "[敏感片段已隐藏]", text)
    text = re.sub(r"```.*?```", "[代码块已隐藏]", text, flags=re.DOTALL)
    text = " ".join(text.replace("\n", " ").split())
    if len(text) > limit:
        text = text[:limit].rstrip() + "..."
    return text


def _render_numbered(items: list[str]) -> str:
    """渲染紧凑编号解释。"""
    return "".join(f"<li>{escape(_safe_committee_text(item))}</li>" for item in items)


def _html_no_code_block(html: str) -> str:
    """Remove leading indentation so Markdown never treats HTML as a code block."""
    return "\n".join(line.lstrip() for line in dedent(str(html)).splitlines()).strip()


def render_html(html: str) -> None:
    st.markdown(_html_no_code_block(html), unsafe_allow_html=True)


def _to_weight_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _committee_member_weight(member_name: str, weights: dict[str, Any]) -> float:
    """按委员名称读取权重，兼容“大单/庄家委员”空格差异。"""
    if member_name in weights:
        return _to_weight_float(weights.get(member_name), 0)
    compact_name = member_name.replace(" ", "")
    for name, weight in weights.items():
        if str(name).replace(" ", "") == compact_name:
            return _to_weight_float(weight, 0)
    return 0.0


def _committee_vote_bucket(member: dict[str, Any]) -> str:
    name = str(member.get("member_name", ""))
    vote_code = str(member.get("vote_code") or "")
    if name in {"观察池委员", "策略验证委员"} or member.get("shadow") or member.get("member_type") == "shadow":
        return "影子复核"
    if member.get("veto") or vote_code == "veto":
        return "硬否决"
    if vote_code in {"strong_support", "support", "weak_support", "neutral_support"}:
        return "支持"
    if vote_code in {"weak_oppose", "oppose"}:
        return "反对"
    return "观望"


def _committee_weight_summary(decision: dict[str, Any]) -> dict[str, Any]:
    weights = dict(decision.get("committee_weights") or {})
    vote_detail = dict(decision.get("vote_detail") or {})
    summary: dict[str, Any] = {
        "support_weight": float(vote_detail.get("support_weight", 0) or 0),
        "oppose_weight": float(vote_detail.get("oppose_weight", 0) or 0),
        "neutral_weight": float(vote_detail.get("observe_weight", 0) or 0),
        "veto_weight": float(vote_detail.get("veto_weight", 0) or 0),
        "shadow_weight": float(vote_detail.get("shadow_weight", 0) or 0),
        "total_config_weight": sum(_to_weight_float(v, 0) for v in weights.values()),
        "direct_weight": float(vote_detail.get("formal_weight", 0) or 0),
        "rows": [],
    }
    for member in list(decision.get("member_votes") or []):
        name = str(member.get("member_name", "委员"))
        weight = _to_weight_float(member.get("weight"), _committee_member_weight(name, weights))
        bucket = _committee_vote_bucket(member)
        summary["rows"].append(
            {
                "name": name,
                "vote": str(member.get("vote", "建议观望")),
                "weight": weight,
                "bucket": bucket,
                "veto": bool(member.get("veto")),
                "confidence": member.get("confidence", 0),
                "member_type": str(member.get("member_type", "official")),
                "direction": str(member.get("direction_text", "中性")),
                "vote_strength": member.get("vote_strength", 0),
                "weighted_score": member.get("weighted_score", 0),
            }
        )
    return summary


def _fmt_weight(value: float) -> str:
    if abs(value - round(value)) < 0.01:
        return f"{int(round(value))}%"
    return f"{value:.1f}%"


def _render_score_breakdown(items: list[dict[str, Any]]) -> str:
    """渲染本地策略评分拆解。"""
    if not items:
        return '<div class="status-card">评分拆解暂不可用。</div>'
    rows = []
    for item in items:
        name = escape(str(item.get("name", "评分")))
        score = int(float(item.get("score", 0) or 0))
        level = escape(str(item.get("level", "等待数据")))
        explanation = escape(str(item.get("explanation", "等待数据同步。")))
        rows.append(
            f"""<div class="status-card" style="margin-top:6px;">
              <b>{name}</b>：<span class="{_signal_color(str(item.get("level", "")))}">{score} / 100 · {level}</span><br>
              {explanation}
            </div>"""
        )
    return "".join(rows)



def _direction_text(direction: str) -> str:
    if direction == "long":
        return "偏多"
    if direction == "short":
        return "偏空"
    return "中性 / 观望"


def _permission_text(permission: str) -> str:
    if permission == "allowed":
        return "允许观察开仓"
    if permission == "cautious":
        return "谨慎轻仓"
    return "禁止开仓"


def render_local_strategy_decision(strategy: dict[str, Any]) -> None:
    """渲染统一本地策略决策模块。"""
    entry = strategy.get("entry_zone") or {}
    stop_loss = strategy.get("stop_loss") or {}
    tp1 = strategy.get("take_profit_1") or {}
    tp2 = strategy.get("take_profit_2") or {}
    data_quality = strategy.get("data_quality") or {}
    reasons_html = _render_numbered(list(strategy.get("reasons") or []))
    risks_html = _render_numbered(list(strategy.get("risks") or []))
    warnings = list(strategy.get("warnings") or [])
    warnings_html = _render_numbered(warnings) if warnings else "<li>暂无额外警告，但仍需严格控制仓位。</li>"
    quality_text = {"good": "良好", "partial": "部分缺失", "poor": "不足"}.get(str(data_quality.get("level")), "未知")
    missing = "、".join(data_quality.get("missing_fields") or []) or "无"
    direction = _direction_text(str(strategy.get("direction", "neutral")))
    permission = _permission_text(str(strategy.get("trade_permission", "blocked")))
    action = str(strategy.get("action", "观望"))
    vote_score = strategy.get("local_vote_score", 0)
    vote_grade = str(strategy.get("local_vote_grade", "D"))
    vote_decision = str(strategy.get("local_vote_decision", "只观察"))
    vote_reason = str(strategy.get("local_vote_reason", "等待策略数据同步。"))
    sections = strategy.get("analysis_sections") or {}
    long_reasons_html = _render_numbered(list(sections.get("long_reasons") or []))
    short_reasons_html = _render_numbered(list(sections.get("short_reasons") or []))
    current_risks_html = _render_numbered(list(sections.get("current_risks") or []))
    conflicts_html = _render_numbered(list(sections.get("signal_conflicts") or []))
    blocked_html = _render_numbered(list(sections.get("blocked_reasons") or []))
    score_breakdown_html = _render_score_breakdown(list(strategy.get("score_breakdown") or []))
    data_handling = str(strategy.get("data_quality_handling", "当前数据质量暂未确认，策略采用保守判断。"))
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">本地策略决策</div>
            <div class="metric-value {_signal_color(action)}">{escape(action)} · {escape(direction)}</div>
            <div class="module-desc">统一读取K线、盘口、衍生品、清算、大单、庄家行为和风险雷达，不依赖外部AI。</div>
            <div class="status-card" style="margin-top:8px;">
              <b>第一层：本地策略核心结论</b><br>
              当前交易对象：{escape(str(strategy.get("symbol", "-")))}<br>
              本地策略方向：{escape(direction)}｜策略类型：{escape(str(strategy.get("strategy_name", "无有效策略")))}<br>
              操作建议：{escape(action)}｜交易权限：{escape(permission)}｜建议仓位：{escape(str(strategy.get("position_suggestion", "0%")))}<br>
              置信度：{strategy.get("confidence", 0)} / 100｜风险评分：{strategy.get("risk_score", 0)} / 100｜机会评分：{strategy.get("opportunity_score", 0)} / 100<br>
              本地投票：{vote_score} / 100 · {escape(vote_grade)}级｜{escape(vote_decision)}
            </div>
            <div class="metric-grid">
              <div class="metric-box"><div class="metric-label">本地投票分</div><div class="metric-value {_signal_color(vote_decision)}">{vote_score} / 100</div></div>
              <div class="metric-box"><div class="metric-label">投票评级</div><div class="metric-value {_signal_color(vote_decision)}">{escape(vote_grade)}级</div></div>
              <div class="metric-box"><div class="metric-label">投票决议</div><div class="metric-value {_signal_color(vote_decision)}">{escape(vote_decision)}</div></div>
              <div class="metric-box"><div class="metric-label">策略类型</div><div class="metric-value yellow">{escape(str(strategy.get("strategy_name", "无有效策略")))}</div></div>
              <div class="metric-box"><div class="metric-label">交易权限</div><div class="metric-value {_signal_color(permission)}">{escape(permission)}</div></div>
              <div class="metric-box"><div class="metric-label">置信度</div><div class="metric-value blue">{strategy.get("confidence", 0)} / 100</div></div>
              <div class="metric-box"><div class="metric-label">风险评分</div><div class="metric-value {_signal_color(str(strategy.get("risk_score", 0)))}">{strategy.get("risk_score", 0)} / 100</div></div>
              <div class="metric-box"><div class="metric-label">机会评分</div><div class="metric-value green">{strategy.get("opportunity_score", 0)} / 100</div></div>
              <div class="metric-box"><div class="metric-label">建议仓位</div><div class="metric-value yellow">{escape(str(strategy.get("position_suggestion", "0%")))}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">
              <b>第二层：入场 / 止损 / 止盈计划</b><br>
              主分析周期：{escape(str(strategy.get("primary_timeframe", "-")))}｜大方向：{escape(str(strategy.get("higher_timeframe_bias", "中性")))}<br>
              参考入场：{escape(str(entry.get("text", "当前不适合开仓")))}<br>
              止损：{escape(_fmt_strategy_price(stop_loss.get("price")))}｜{escape(str(stop_loss.get("reason", "无有效入场，不设置止损")))}<br>
              止盈1：{escape(_fmt_strategy_price(tp1.get("price")))}｜{escape(str(tp1.get("reason", "无有效入场，不设置止盈")))}<br>
              止盈2：{escape(_fmt_strategy_price(tp2.get("price")))}｜{escape(str(tp2.get("reason", "无有效入场，不设置止盈")))}<br>
              风险收益比：{escape(str(strategy.get("risk_reward_ratio") or "待确认"))}<br>
              信号失效：{escape(str(strategy.get("invalid_condition", "等待结构确认")))}<br>
              本地委员投票理由：{escape(vote_reason)}
            </div>
            <details class="status-card" style="margin-top:8px;" open>
              <summary><b>第三层：原因与风险解释</b></summary>
              <div class="module-grid" style="margin-top:8px;">
                <div class="status-card"><b>看多原因</b><ol style="padding-left:18px;margin:6px 0 0 0;">{long_reasons_html}</ol></div>
                <div class="status-card"><b>看空原因</b><ol style="padding-left:18px;margin:6px 0 0 0;">{short_reasons_html}</ol></div>
                <div class="status-card"><b>当前风险</b><ol style="padding-left:18px;margin:6px 0 0 0;">{current_risks_html}</ol></div>
                <div class="status-card"><b>信号冲突</b><ol style="padding-left:18px;margin:6px 0 0 0;">{conflicts_html}</ol></div>
                <div class="status-card"><b>禁止开仓原因</b><ol style="padding-left:18px;margin:6px 0 0 0;">{blocked_html}</ol></div>
              </div>
            </details>
            <details class="status-card" style="margin-top:8px;">
              <summary><b>第四层：评分拆解</b></summary>
              {score_breakdown_html}
            </details>
            <details class="status-card" style="margin-top:8px;">
              <summary><b>第五层：数据质量与调试信息</b></summary>
              数据质量：{escape(str(data_quality.get("level", "poor")))}（{escape(quality_text)}）<br>
              缺失字段：{escape(missing)}<br>
              保守处理：{escape(data_handling)}<br>
              原始看多/看空理由：<ol style="padding-left:18px;margin:6px 0 0 0;">{reasons_html}</ol>
              原始风险：<ol style="padding-left:18px;margin:6px 0 0 0;">{risks_html}</ol>
              风控警告：<ol style="padding-left:18px;margin:6px 0 0 0;">{warnings_html}</ol>
            </details>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _committee_permission_text(permission: str) -> str:
    mapping = {
        "approved": "通过",
        "cautious": "谨慎通过",
        "rejected": "未通过",
        "blocked": "禁止开仓",
        "candidate": "顺势候选",
        "simulation_or_approval": "模拟/自动候选",
        "watch_candidate": "观察候选",
        "observe_only": "只观察",
        "no_auto_trade": "禁止自动交易",
    }
    return mapping.get(permission, permission or "等待决议")


def _committee_brief_summary(decision: dict[str, Any]) -> str:
    """Compact summary for signal/overview bars."""
    v91 = decision.get("trading_committee_v91") or {}
    risk = v91.get("risk_judge") or {}
    action = str(decision.get("final_action") or v91.get("final_action") or "等待决议")
    direction = str(decision.get("final_direction_text") or v91.get("final_direction") or "中性")
    confidence = decision.get("committee_confidence", v91.get("final_confidence", 0))
    risk_score = decision.get("committee_risk_score", risk.get("risk_score", 0))
    risk_verdict = str(risk.get("risk_verdict") or ("BLOCK" if decision.get("veto_members") else "PASS"))
    permission = _committee_permission_text(str(decision.get("trade_permission", "")))
    veto_members = list(decision.get("veto_members") or [])
    veto_text = f"｜否决：{'、'.join(str(x) for x in veto_members[:3])}" if veto_members else ""
    return (
        f"{action}｜方向：{direction}｜许可：{permission}｜"
        f"置信度：{confidence}/100｜风险：{risk_score}/100｜风险裁判：{risk_verdict}{veto_text}"
    )


def _committee_summary_fields(decision: dict[str, Any]) -> list[tuple[str, str, str]]:
    v91 = decision.get("trading_committee_v91") or {}
    risk = v91.get("risk_judge") or {}
    action = str(decision.get("final_action") or v91.get("final_action") or "等待决议")
    direction = str(decision.get("final_direction_text") or v91.get("final_direction") or "中性")
    permission = _committee_permission_text(str(decision.get("trade_permission", "")))
    confidence = str(decision.get("committee_confidence", v91.get("final_confidence", 0)))
    risk_score = str(decision.get("committee_risk_score", risk.get("risk_score", 0)))
    risk_verdict = str(risk.get("risk_verdict") or ("BLOCK" if decision.get("veto_members") else "PASS"))
    veto_members = list(decision.get("veto_members") or [])
    veto_text = "、".join(str(x) for x in veto_members[:2]) if veto_members else "未触发"
    return [
        ("结论", action, _signal_color(action)),
        ("方向", direction, _signal_color(direction)),
        ("许可", permission, _signal_color(action)),
        ("置信度", f"{confidence}/100", "blue"),
        ("风险", f"{risk_score}/100", _signal_color("禁止开仓" if risk_verdict == "BLOCK" else str(risk_score))),
        ("风险裁判", risk_verdict, _signal_color("禁止开仓" if risk_verdict == "BLOCK" else "支持交易")),
        ("否决", veto_text, _signal_color("禁止开仓" if veto_members else "支持交易")),
    ]


def _committee_full_summary_rows(decision: dict[str, Any]) -> list[tuple[str, str]]:
    weight_summary = _committee_weight_summary(decision)
    v91 = decision.get("trading_committee_v91") or {}
    risk = v91.get("risk_judge") or {}
    position = v91.get("position_plan") or {}
    execution = v91.get("execution_plan") or {}
    external_ai = decision.get("external_ai") or {}
    deepseek = external_ai.get("deepseek") or {}
    gemini = external_ai.get("gemini") or {}
    return [
        ("正式权重", f"支持{_fmt_weight(weight_summary['support_weight'])} / 观望{_fmt_weight(weight_summary['neutral_weight'])} / 反对{_fmt_weight(weight_summary['oppose_weight'] + weight_summary['veto_weight'])}"),
        ("共振等级", str(decision.get("resonance_text", "无共振"))),
        ("最终动作", str(decision.get("final_action", "等待决议"))),
        ("交易许可", _committee_permission_text(str(decision.get("trade_permission", "")))),
        ("风险裁判", f"{risk.get('risk_verdict', 'WAIT')} / 风险{risk.get('risk_score', decision.get('committee_risk_score', 0))}/100"),
        ("否决来源", "、".join(str(x) for x in list(decision.get("veto_members") or [])) or "未触发"),
        ("仓位委员会", f"{position.get('position_size_pct', 0)}% / {position.get('leverage', 1)}x"),
        ("执行委员会", str(execution.get("execution_type", "WAIT"))),
        ("DeepSeek", f"{deepseek.get('status', '等待')} / {deepseek.get('vote', '观望')} / 风险{deepseek.get('risk_level', '中')}"),
        ("Gemini", f"{gemini.get('status', '等待')} / {gemini.get('vote', '观望')} / 风险{gemini.get('risk_level', '中')}"),
        ("下一步", str((decision.get("explanation") or {}).get("next_condition") or decision.get("invalid_condition") or "等待下一轮数据确认")),
    ]


def _render_committee_summary_panel(decision: dict[str, Any]) -> str:
    cards = "".join(
        f"""<div class="committee-summary-item">
          <div class="label">{escape(label)}</div>
          <div class="value {color}" title="{escape(str(value))}">{escape(str(value))}</div>
        </div>"""
        for label, value, color in _committee_summary_fields(decision)
    )
    return dedent(f"""
      <div class="committee-summary-panel">
        <div class="committee-summary-title">交易委员会总结</div>
        <div class="committee-summary-strip">{cards}</div>
      </div>
    """)


def render_committee_full_summary_expander(decision: dict[str, Any], label: str = "查看完整委员会总结") -> None:
    with st.expander(label, expanded=False):
        for row_label, value in _committee_full_summary_rows(decision):
            st.markdown(f"**{row_label}**：{_safe_committee_text(value, 220)}")


def _render_trading_committee_v91(decision: dict[str, Any]) -> str:
    """Render the AI_MODEL 9.1 committee structure while keeping legacy fields."""
    v91 = decision.get("trading_committee_v91") or {}
    if not v91:
        return '<div class="status-card">9.1交易委员会结构等待下一轮决议生成。</div>'
    risk = v91.get("risk_judge") or {}
    position = v91.get("position_plan") or {}
    execution = v91.get("execution_plan") or {}
    members_html = "".join(
        f"""<div class="summary-card">
          <div class="summary-label">{escape(str(member.get("name", "委员")))} · {escape(str(member.get("role", "-")))}</div>
          <div class="summary-value {_signal_color(str(member.get("vote", "")))}">{escape(str(member.get("vote", "ABSTAIN")))} / {escape(str(member.get("direction", "WAIT")))}</div>
          <div class="module-desc">评分：{float(member.get("score", 0) or 0):.1f}｜Confidence：{float(member.get("confidence", 0) or 0):.1f}｜DataIntegrity：{float(member.get("data_integrity_score", 0) or 0):.1f}</div>
          <div class="module-desc">{escape(_safe_committee_text(member.get("reason", "")))}</div>
        </div>"""
        for member in list(v91.get("members") or [])
    ) or '<div class="status-card">暂无9.1委员结果。</div>'
    shadow_count = len(list(v91.get("shadow_members") or []))
    return dedent(f"""
    <details class="status-card" style="margin-top:8px;" open>
      <summary><b>AI模型 9.1 交易委员会结构</b></summary>
      <div class="committee-grid" style="margin-top:8px;">
        <div class="summary-card"><div class="summary-label">最终结论</div><div class="summary-value {_signal_color(str(v91.get("final_action", "WAIT")))}">{escape(str(v91.get("final_action", "WAIT")))} / {escape(str(v91.get("final_direction", "WAIT")))}</div></div>
        <div class="summary-card"><div class="summary-label">交易价值评分</div><div class="summary-value blue">{float(v91.get("trade_value_score", 0) or 0):.1f} / 100</div></div>
        <div class="summary-card"><div class="summary-label">最终置信度</div><div class="summary-value blue">{float(v91.get("final_confidence", 0) or 0):.1f} / 100</div></div>
        <div class="summary-card"><div class="summary-label">数据完整度</div><div class="summary-value yellow">{float(v91.get("final_data_integrity_score", 0) or 0):.1f} / 100</div></div>
        <div class="summary-card"><div class="summary-label">风险裁判</div><div class="summary-value {_signal_color("禁止开仓" if risk.get("blocked") else "支持交易")}">{escape(str(risk.get("risk_verdict", "WAIT")))}</div></div>
        <div class="summary-card"><div class="summary-label">风险评分</div><div class="summary-value yellow">{float(risk.get("risk_score", 0) or 0):.1f} / 100</div></div>
        <div class="summary-card"><div class="summary-label">仓位委员会</div><div class="summary-value {_signal_color("支持交易" if position.get("allow_position") else "反对交易")}">{float(position.get("position_size_pct", 0) or 0):.2f}% / {int(position.get("leverage", 1) or 1)}x</div></div>
        <div class="summary-card"><div class="summary-label">执行委员会</div><div class="summary-value {_signal_color("支持交易" if execution.get("execution_allowed") else "反对交易")}">{escape(str(execution.get("execution_type", "WAIT")))}</div></div>
        <div class="summary-card"><div class="summary-label">影子委员</div><div class="summary-value blue">{shadow_count} 个，仅参考</div></div>
      </div>
      <div class="status-card" style="margin-top:8px;"><b>风险裁判理由</b><br>{escape(_safe_committee_text(risk.get("block_reason") or "未触发阻断。"))}<br>{escape(_safe_committee_text("；".join(str(x) for x in list(risk.get("warnings") or [])[:4])))}</div>
      <div class="status-card" style="margin-top:8px;"><b>仓位委员会</b><br>{escape(_safe_committee_text(position.get("reason", "等待仓位建议。")))}</div>
      <div class="status-card" style="margin-top:8px;"><b>执行委员会</b><br>{escape(_safe_committee_text(execution.get("reason", "等待执行计划。")))}</div>
      <div class="committee-vote-grid" style="margin-top:8px;">{members_html}</div>
    </details>
    """)


def render_ai_committee_decision(decision: dict[str, Any]) -> None:
    """渲染交易委员会最终决议与委员投票。"""
    if not decision:
        st.warning("交易委员会暂时无法完成完整分析，已等待下一轮数据。")
        return
    permission = str(decision.get("trade_permission", "rejected"))
    action = str(decision.get("final_action", "继续观察"))
    direction = str(decision.get("final_direction_text", "中性"))
    simulation_text = "是" if decision.get("approved_for_simulation") else "否"
    supporting = list(decision.get("supporting_members") or [])
    opposing = list(decision.get("opposing_members") or [])
    veto_members = list(decision.get("veto_members") or [])
    reasons_html = _render_numbered(list(decision.get("main_reasons") or []))
    risks_html = _render_numbered(list(decision.get("main_risks") or []))
    warnings_html = _render_numbered(list(decision.get("final_warnings") or []))
    explanation = decision.get("explanation") or {}
    hard = decision.get("hard_veto_status") or {}
    soft = decision.get("soft_veto_status") or {}
    external_ai = decision.get("external_ai") or {}
    weights = decision.get("committee_weights") or {}
    weight_summary = _committee_weight_summary(decision)
    weight_vote_text = (
        f"支持{_fmt_weight(weight_summary['support_weight'])} / "
        f"观望{_fmt_weight(weight_summary['neutral_weight'])} / "
        f"反对{_fmt_weight(weight_summary['oppose_weight'] + weight_summary['veto_weight'])} / "
        f"影子{_fmt_weight(weight_summary['shadow_weight'])}"
    )
    weight_rows_html = "".join(
        f"""<div class="summary-card">
          <div class="summary-label">{escape(str(row["name"]))} · {escape("影子" if row.get("member_type") == "shadow" else "正式")}</div>
          <div class="summary-value {_signal_color(str(row["vote"]))}">{escape(str(row["vote"]))}</div>
          <div class="module-desc">权重：{_fmt_weight(float(row["weight"]))}｜计入：{escape(str(row["bucket"]))}｜方向：{escape(str(row.get("direction", "中性")))}｜信心：{escape(str(row["confidence"]))}</div>
          <div class="module-desc">vote_strength：{escape(str(row.get("vote_strength", 0)))}｜weighted_score：{escape(str(row.get("weighted_score", 0)))}</div>
        </div>"""
        for row in list(weight_summary.get("rows") or [])
    ) or '<div class="status-card">暂无委员权重明细。</div>'
    weight_text = " / ".join(f"{k}:{v}%" for k, v in weights.items()) if weights else "等待权重配置"
    deepseek = external_ai.get("deepseek") or {}
    gemini = external_ai.get("gemini") or {}
    ai_consensus = external_ai.get("external_ai_consensus") or {}
    deepseek_summary = deepseek.get("summary") or deepseek.get("main_opinion") or "DeepSeek影子委员未返回意见。"
    gemini_summary = gemini.get("summary") or gemini.get("chart_observation") or "Gemini影子委员未返回意见。"
    deepseek_reasons = _render_numbered(list(deepseek.get("reasons") or []))
    deepseek_risks = _render_numbered(list(deepseek.get("risks") or []))
    gemini_reasons = _render_numbered(list(gemini.get("reasons") or []))
    gemini_risks = _render_numbered(list(gemini.get("risks") or []))
    soft_text = "已触发：" + "、".join(soft.get("members") or []) if soft.get("triggered") else "未触发"
    veto_html = "当前无委员触发强制否决。"
    veto_class = "green"
    veto_status = "未触发"
    if veto_members:
        veto_status = "已触发"
        veto_html = "风险否决已触发，委员会禁止开仓。否决委员：" + "、".join(escape(str(name)) for name in veto_members)
        veto_class = "red"
    render_html(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">交易委员会最终决议</div>
            <div class="metric-value {_signal_color(action)}">{escape(action)} · {escape(direction)}</div>
            <div class="module-desc">委员会读取本地策略、趋势、资金、盘口、清算、大单、风险雷达和观察池状态，只生成决议和模拟候选，不执行交易。</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">当前交易对象</div><div class="summary-value yellow">{escape(str(decision.get("symbol", "-")))}</div></div>
              <div class="summary-card"><div class="summary-label">最终动作 / 方向</div><div class="summary-value {_signal_color(action)}">{escape(action)} / {escape(direction)}</div></div>
              <div class="summary-card"><div class="summary-label">交易许可</div><div class="summary-value {_signal_color(action)}">{escape(_committee_permission_text(permission))}</div></div>
              <div class="summary-card"><div class="summary-label">共振等级</div><div class="summary-value yellow">{escape(str(decision.get("resonance_text", "无共振")))}</div></div>
              <div class="summary-card"><div class="summary-label">委员会置信度</div><div class="summary-value blue">{decision.get("committee_confidence", 0)} / 100</div></div>
              <div class="summary-card"><div class="summary-label">委员会风险</div><div class="summary-value {_signal_color(str(decision.get("committee_risk_score", 0)))}">{decision.get("committee_risk_score", 0)} / 100</div></div>
              <div class="summary-card"><div class="summary-label">建议仓位</div><div class="summary-value yellow">{escape(str(decision.get("position_suggestion", "0%")))}</div></div>
              <div class="summary-card"><div class="summary-label">风控最大仓位</div><div class="summary-value yellow">{escape(str(decision.get("risk_max_position", "0%")))}</div></div>
              <div class="summary-card"><div class="summary-label">模拟候选</div><div class="summary-value {_signal_color("支持交易" if decision.get("approved_for_simulation") else "反对交易")}">{simulation_text}</div></div>
              <div class="summary-card"><div class="summary-label">风险否决</div><div class="summary-value {veto_class}">{veto_status}</div></div>
              <div class="summary-card"><div class="summary-label">投票统计</div><div class="summary-value">支持{len(supporting)} / 反对{len(opposing)} / 否决{len(veto_members)}</div></div>
              <div class="summary-card"><div class="summary-label">正式权重统计</div><div class="summary-value yellow">{weight_vote_text}</div></div>
            </div>
            {_render_trading_committee_v91(decision)}
            {_render_committee_summary_panel(decision)}
            <div class="status-card {veto_class}" style="margin-top:8px;">
              <b>风险否决状态</b><br>{veto_html}
            </div>
            <div class="status-card" style="margin-top:8px;">
              <b>决策权治理</b><br>
              本地策略委员定位：基础提案层。风险委员和实盘安全委员拥有硬否决权。DeepSeek/Gemini 当前为正式投票成员；观察池/策略验证暂为影子复核。<br>
              硬否决：{escape("已触发" if hard.get("blocked") else "未触发")}｜软否决：{escape(soft_text)}<br>
              权重结果：{escape(weight_vote_text)}｜直接决策权重：{escape(_fmt_weight(weight_summary["direct_weight"]))}｜配置总权重：{escape(_fmt_weight(weight_summary["total_config_weight"]))}
            </div>
            <div class="status-card" style="margin-top:8px;">支持：{escape("、".join(supporting) if supporting else "暂无明确支持委员")}<br>反对/观望：{escape("、".join(opposing) if opposing else "暂无明确反对委员")}<br>否决：{escape("、".join(veto_members) if veto_members else "无")}</div>
          </div>
        </div>
        """
    )

    render_committee_full_summary_expander(decision, "查看完整委员会总结")

    with st.expander("委员会权重", expanded=False):
        st.markdown(_safe_committee_text(weight_text, 500))

    with st.expander("按委员权重投票明细", expanded=True):
        for row in list(weight_summary.get("rows") or []):
            st.markdown(
                f"**{row.get('name', '委员')}**｜{row.get('vote', '观望')}｜"
                f"权重 {_fmt_weight(float(row.get('weight', 0) or 0))}｜计入 {row.get('bucket', '-')}｜"
                f"方向 {row.get('direction', '中性')}｜信心 {row.get('confidence', 0)}｜"
                f"weighted_score {row.get('weighted_score', 0)}"
            )
        if not list(weight_summary.get("rows") or []):
            st.markdown("暂无委员权重明细。")

    with st.expander("外部 AI 正式投票复核", expanded=True):
        st.markdown(f"**DeepSeek**：{deepseek.get('status', '等待')}｜{deepseek.get('vote', '观望')}｜方向 {deepseek.get('direction_text', '中性')}｜耗时 {deepseek.get('duration_ms', 0)} ms")
        st.markdown(f"**Gemini**：{gemini.get('status', '等待')}｜{gemini.get('vote', '观望')}｜方向 {gemini.get('direction_text', '中性')}｜耗时 {gemini.get('duration_ms', 0)} ms")
        st.markdown(f"**一致性**：{ai_consensus.get('agreement', '数据不足')}｜综合风险 {ai_consensus.get('combined_risk_level', '中')}｜建议 {ai_consensus.get('suggested_adjustment', '不调整')}")
        st.markdown(f"**DeepSeek摘要**：{_safe_committee_text(deepseek_summary, 260)}")
        st.markdown(f"**Gemini摘要**：{_safe_committee_text(gemini_summary, 260)}")
        st.markdown(f"**外部AI综合**：{_safe_committee_text(ai_consensus.get('summary', '外部AI参与正式投票，但不直接执行交易。'), 260)}")
        st.markdown("外部 AI 当前参与正式权重投票，但不能直接执行交易，不能绕过风险委员和实盘安全委员。")

    with st.expander("委员会解释", expanded=True):
        st.markdown(f"**为什么通过或不通过**：{_safe_committee_text(explanation.get('why_pass_or_not', ''), 260)}")
        st.markdown(f"**当前最大风险**：{_safe_committee_text(explanation.get('max_risk', ''), 260)}")
        st.markdown(f"**下一步观察条件**：{_safe_committee_text(explanation.get('next_condition', ''), 260)}")
        st.markdown(f"**信号失效条件**：{_safe_committee_text(explanation.get('invalid_condition', decision.get('invalid_condition', '')), 260)}")

    with st.expander("主要理由 / 主要风险 / 最终警告", expanded=False):
        st.markdown("**主要理由**")
        for item in list(decision.get("main_reasons") or []):
            st.markdown(f"- {_safe_committee_text(item, 220)}")
        st.markdown("**主要风险**")
        for item in list(decision.get("main_risks") or []):
            st.markdown(f"- {_safe_committee_text(item, 220)}")
        st.markdown("**最终警告**")
        for item in list(decision.get("final_warnings") or []):
            st.markdown(f"- {_safe_committee_text(item, 220)}")

    render_html('<div class="app-shell"><div class="module-card"><div class="module-title">委员投票明细</div><div class="module-desc">每个委员独立判断，风险委员拥有最高否决权。</div>')
    for member in list(decision.get("member_votes") or []):
        member_name = str(member.get("member_name", "委员"))
        vote = str(member.get("vote", "建议观望"))
        veto = "是" if member.get("veto") else "否"
        member_weight = _committee_member_weight(member_name, weights)
        member_bucket = _committee_vote_bucket(member)
        member_type = "影子委员" if member.get("shadow") else "正式委员"
        member_reasons = _render_numbered(list(member.get("reasons") or []))
        member_risks = _render_numbered(list(member.get("risks") or []))
        with st.expander(f"{member_name}｜{member_type}｜{vote}｜权重 {_fmt_weight(member_weight)}｜计入 {member_bucket}｜否决 {veto}", expanded=False):
            render_html(
                f"""
                <div class="status-card">
                  身份：{escape(member_type)}｜权重：{_fmt_weight(member_weight)}｜计入：{escape(member_bucket)}｜否决：{escape(veto)}<br>
                  方向：{escape(str(member.get("direction_text", "中性")))}<br>
                  vote_strength：{escape(str(member.get("vote_strength", 0)))}｜weighted_score：{escape(str(member.get("weighted_score", 0)))}｜软警告：{escape("是" if member.get("soft_warning") else "否")}<br>
                  风险：{escape(str(member.get("risk_level", "中")))}<br>
                  总结：{escape(_safe_committee_text(member.get("summary", "")))}<br>
                  <b>理由</b><ol style="padding-left:18px;margin:6px 0 0 0;">{member_reasons}</ol>
                  <b>风险</b><ol style="padding-left:18px;margin:6px 0 0 0;">{member_risks}</ol>
                </div>
                """
            )
    render_html("</div></div>")


def render_sim_signal_linkage(decision: dict[str, Any]) -> None:
    """信号页委员会结果与模拟交易执行器联动。"""
    if not decision:
        return
    symbol = str(decision.get("symbol") or st.session_state.get("current_symbol", "BTCUSDT"))
    ticker = market_cache.get_ticker(symbol)
    price = float((ticker or {}).get("last_price") or 0)
    signal = committee_decision_to_sim_signal(decision)
    summary = get_sim_account_summary()
    positions = [p for p in summary.get("positions", []) if p.get("status") in {"open", "partially_closed"} and p.get("symbol") == symbol]
    orders = [o for o in summary.get("orders", []) if o.get("status") == "pending" and o.get("symbol") == symbol]
    ok, reasons = validate_signal_for_simulation(signal, {symbol: price})
    status = "可进入模拟交易候选" if ok else "暂不可进入模拟交易"
    color = "green" if ok else "yellow"
    reason_text = "已满足委员会与模拟风控条件。" if ok else "；".join(reasons)
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">模拟交易联动</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">模拟状态</div><div class="summary-value {color}">{escape(status)}</div></div>
              <div class="summary-card"><div class="summary-label">当前价格</div><div class="summary-value">{format_price(price)}</div></div>
              <div class="summary-card"><div class="summary-label">同币种持仓</div><div class="summary-value">{len(positions)}</div></div>
              <div class="summary-card"><div class="summary-label">待触发订单</div><div class="summary-value">{len(orders)}</div></div>
            </div>
            <div class="status-card" style="margin-top:8px;">{escape(reason_text)}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if ok and st.button("加入模拟候选 / 创建本地模拟订单", key=f"sim_link_{symbol}", width="stretch"):
        order = create_pending_sim_order(signal, price)
        if order:
            st.success("已创建本地模拟订单。")
        else:
            st.warning("未创建模拟订单，请查看模拟事件日志。")
        st.rerun()


def render_manual_position_override_panel(decision: dict[str, Any]) -> None:
    """人工仓位干预层：只能在风控允许范围内记录用户选择。"""
    if not decision:
        return
    symbol = str(decision.get("symbol") or st.session_state.get("current_symbol", "BTCUSDT"))
    ticker = market_cache.get_ticker(symbol) or {}
    price = ticker.get("last_price")
    base_eval = evaluate_manual_position_override(decision, 0)
    risk_max = float(base_eval.get("risk_max_position_pct", 0) or 0)
    system_pct = float(base_eval.get("system_position_pct", 0) or 0)
    allowed_hint = bool(decision.get("manual_override_allowed") and risk_max > 0)
    st.markdown(
        f"""
        <div class="app-shell">
          <div class="module-card">
            <div class="module-title">人工仓位干预层</div>
            <div class="module-desc">用户只能在风控允许范围内调整仓位；硬否决、数据质量差、安全锁和风控限制不能被绕过。</div>
            <div class="committee-grid">
              <div class="summary-card"><div class="summary-label">系统建议仓位</div><div class="summary-value yellow">{escape(str(decision.get("system_position_suggestion", decision.get("position_suggestion", "0%"))))}</div></div>
              <div class="summary-card"><div class="summary-label">系统建议中值</div><div class="summary-value">{system_pct:.2f}%</div></div>
              <div class="summary-card"><div class="summary-label">风控最大仓位</div><div class="summary-value {'green' if risk_max > 0 else 'red'}">{risk_max:.2f}%</div></div>
              <div class="summary-card"><div class="summary-label">人工调整</div><div class="summary-value {'green' if allowed_hint else 'red'}">{'允许' if allowed_hint else '不允许'}</div></div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    user_pct = st.slider("用户选择仓位（%）", min_value=0.0, max_value=max(risk_max, 1.0), value=min(system_pct, risk_max) if risk_max else 0.0, step=0.25, key=f"manual_pos_{symbol}", disabled=not allowed_hint)
    preview = evaluate_manual_position_override(decision, user_pct, confirmed=False)
    if preview.get("requires_confirmation"):
        st.warning("你选择的仓位高于系统建议仓位，继续前必须二次确认，本次操作会被标记为人工仓位干预。")
        external_ai = decision.get("external_ai") or {}
        soft_members = [name for name, row in [("DeepSeek", external_ai.get("deepseek") or {}), ("Gemini", external_ai.get("gemini") or {})] if row.get("soft_veto")]
        if soft_members:
            st.warning(f"外部AI存在风险提醒：{'、'.join(soft_members)} 提出软否决或降仓建议。你仍选择提高仓位，本次操作将被记录为人工干预。")
    confirmed = st.checkbox("我确认理解风险，并愿意承担该仓位调整带来的后果", key=f"manual_confirm_{symbol}", disabled=not allowed_hint)
    confirm_text = st.text_input("实盘模式确认短句预留：我确认承担本次仓位调整风险", key=f"manual_confirm_text_{symbol}", disabled=not allowed_hint)
    final_eval = evaluate_manual_position_override(decision, user_pct, confirmed=confirmed, confirm_text=confirm_text)
    if final_eval.get("allowed"):
        st.success(final_eval.get("message"))
    else:
        st.warning(final_eval.get("message"))
    if st.button("记录人工仓位选择", key=f"save_manual_override_{symbol}", width="stretch", disabled=not allowed_hint):
        row = save_manual_position_override(decision, final_eval, mode="模拟", current_price=price, confirm_text=confirm_text)
        if final_eval.get("allowed"):
            st.success(f"人工仓位干预已记录：{row.get('user_selected_position')}%。")
        else:
            st.error(f"人工仓位干预被拒绝并已记录：{row.get('reason')}")


def render_committee_candidates() -> None:
    """渲染观察池进入委员会的候选对象。"""
    candidates = get_committee_candidates()[:10]
    st.markdown('<div class="app-shell"><div class="module-card"><div class="module-title">委员会候选榜</div><div class="module-desc">来源于观察池重点跟踪对象，点击后切换为当前交易对象。</div>', unsafe_allow_html=True)
    if not candidates:
        st.markdown('<div class="status-card">暂无符合条件的委员会候选对象。观察池进入重点跟踪后会自动出现在这里。</div>', unsafe_allow_html=True)
    for index, row in enumerate(candidates, start=1):
        symbol = str(row.get("symbol", "-"))
        cols = st.columns([1.1, .8, .8, .8])
        with cols[0]:
            st.markdown(f"**#{index} {symbol}**  \n{row.get('strategy_name', '-')}")
        with cols[1]:
            st.markdown(f"观察 {row.get('watch_score', 0)}  \n{row.get('status', '-')}")
        with cols[2]:
            st.markdown(f"置信 {row.get('confidence', 0)}  \n风险 {row.get('risk_score', 0)}")
        with cols[3]:
            if st.button("提交委员会", key=f"committee_candidate_{symbol}_{index}", width="stretch"):
                set_current_symbol(symbol)
                st.query_params["page"] = st.session_state.active_page
                st.query_params["symbol"] = symbol
    st.markdown("</div></div>", unsafe_allow_html=True)


def _fmt_strategy_price(value: Any) -> str:
    """策略模块价格格式。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "待确认"
    if number <= 0:
        return "待确认"
    return format_price(number)


def render_signal_analysis(symbol: str, ticker: dict[str, Any] | None, append_signal_debug: Callable[[str], None]) -> None:
    render_signal_analysis_page(
        symbol,
        ticker,
        get_effective_ticker=get_effective_ticker,
        append_signal_debug=append_signal_debug,
        render_local_strategy_decision=render_local_strategy_decision,
        render_ai_committee_decision=render_ai_committee_decision,
        render_sim_signal_linkage=render_sim_signal_linkage,
        render_manual_position_override_panel=render_manual_position_override_panel,
        render_committee_candidates=render_committee_candidates,
    )



def render_signals(symbol: str, ticker: dict[str, Any] | None, scores: dict[str, Any], page_titles: dict[str, str], version: str, append_signal_debug: Callable[[str], None]) -> None:
    """信号页。"""
    render_page_head("signals", page_titles, version)
    render_watchlist_quick_controls(st.session_state.get("current_symbol", symbol), "signals", source="manual")
    fragment = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
    if fragment:
        render_kline_system(st.session_state.get("current_symbol", symbol))
        live_symbol = st.session_state.get("current_symbol", symbol)
        live_ticker = market_cache.get_ticker(live_symbol) or ticker
        render_orderbook_system(live_symbol, live_ticker)

        @fragment(run_every="15s")
        def _live_signal_analysis() -> None:
            live_symbol = st.session_state.get("current_symbol", symbol)
            live_ticker = market_cache.get_ticker(live_symbol) or ticker
            render_signal_analysis(live_symbol, live_ticker, append_signal_debug)

        _live_signal_analysis()
    else:
        render_kline_system(symbol)
        render_orderbook_system(symbol, ticker)
        render_signal_analysis(symbol, ticker, append_signal_debug)

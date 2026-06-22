"""Server health, operations, and Binance diagnostics page rendering."""

from __future__ import annotations

import streamlit as st

from components.ui import render_metric_grid, render_page_head
from services import market_cache
from services.background_refresher import refresh_klines_now, refresh_symbol_now
from services.external_ai_center import get_external_ai_status
from services.server_runtime import (
    apply_safe_startup,
    create_backup,
    get_git_status,
    get_server_health,
    load_backup_settings,
    load_server_settings,
    rotate_logs,
    save_backup_settings,
    save_server_settings,
)
from services.system_diagnostics import load_last_diagnostics, load_recent_binance_logs, run_binance_diagnostics
from services.system_operations import get_system_operations_status, load_recent_log_lines, run_aimodel_control


def render_operations_center() -> None:
    """系统运维中心，兼容本地Windows与Ubuntu/systemd部署。"""
    status = get_system_operations_status()
    external_status = get_external_ai_status()
    gemini_status = external_status.get("gemini") or {}
    gemini_text = "API Key缺失"
    if gemini_status.get("configured"):
        gemini_text = "正常" if not gemini_status.get("last_error") else str(gemini_status.get("last_error"))[:18]
    render_metric_grid(
        [
            ("服务器状态", str(status.get("server_status", "-")), "green"),
            ("AI模型状态", str(status.get("ai_model_status", "-")), "green" if str(status.get("ai_model_status")) == "active" else "yellow"),
            ("GitHub版本", str(status.get("github_version", "-")), "blue"),
            ("运行时间", str(status.get("uptime", "-")), "green"),
            ("CPU占用", str(status.get("cpu", "-")), "yellow"),
            ("内存占用", str(status.get("memory", "-")), "yellow"),
            ("磁盘状态", str(status.get("disk", "-")), "blue"),
            ("运行环境", str(status.get("platform", "-"))[:32], ""),
            ("Gemini", gemini_text, "green" if gemini_text == "正常" else "yellow"),
        ]
    )
    st.caption("Vultr 东京 / Ubuntu 24.04 / systemd 部署时，下方按钮会调用 aimodel 服务。本地 Windows 只显示状态，不执行 systemctl。")
    c1, c2, c3, c4, c5 = st.columns(5)
    actions = [
        (c1, "刷新状态", "status"),
        (c2, "重启AI模型", "restart"),
        (c3, "停止AI模型", "stop"),
        (c4, "启动AI模型", "start"),
        (c5, "GitHub同步更新", "update"),
    ]
    for col, label, action in actions:
        if col.button(label, width="stretch"):
            st.session_state["ops_command_result"] = run_aimodel_control(action)
    result = st.session_state.get("ops_command_result")
    if result:
        if result.get("ok"):
            st.success(result.get("stdout") or "操作已执行。")
        else:
            st.warning(result.get("stderr") or result.get("stdout") or "操作未执行。")
    st.markdown("**日志中心**")
    f1, f2 = st.columns([2, 1])
    keyword = f1.text_input("搜索日志", placeholder="输入关键词")
    level = f2.selectbox("日志级别", ["全部", "INFO", "WARNING", "ERROR"])
    logs = load_recent_log_lines(100, keyword=keyword, level=level)
    if not logs:
        st.info("暂无符合条件的日志。")
    else:
        st.dataframe(logs, width="stretch", hide_index=True)


def render_binance_data_status_center(symbol: str | None = None) -> None:
    """Binance实时数据诊断中心。"""
    current_symbol = str(symbol or st.session_state.get("current_symbol") or "BTCUSDT").upper().strip()
    snapshot = market_cache.snapshot()
    st.markdown("**Binance 实时数据状态中心**")
    render_metric_grid(
        [
            ("Binance连接", str(snapshot.get("binance_status", "初始化")), "green" if snapshot.get("binance_status") == "在线" else "yellow"),
            ("REST状态", "正常" if snapshot.get("binance_status") == "在线" else "重试中", "green" if snapshot.get("binance_status") == "在线" else "yellow"),
            ("WebSocket状态", "REST回退模式", "blue"),
            ("Ticker更新", str(snapshot.get("last_update_time", "初始化中")), "green"),
            ("K线状态", str(snapshot.get("kline_status", "初始化")), "green" if snapshot.get("kline_status") in {"实时", "在线"} else "yellow"),
            ("K线更新", str(snapshot.get("kline_last_update_time", "初始化中")), "green"),
            ("盘口状态", str(snapshot.get("orderbook_status", "初始化")), "green" if snapshot.get("orderbook_status") in {"实时", "在线"} else "yellow"),
            ("最近错误", str(snapshot.get("last_error") or snapshot.get("kline_last_error") or snapshot.get("orderbook_last_error") or "无")[:32], "red" if snapshot.get("last_error") or snapshot.get("kline_last_error") or snapshot.get("orderbook_last_error") else "green"),
        ]
    )
    c1, c2 = st.columns(2)
    if c1.button("运行 Binance 诊断", width="stretch"):
        st.session_state["binance_diagnostics_result"] = run_binance_diagnostics(current_symbol)
    if c2.button("刷新当前交易对象数据", width="stretch"):
        refresh_symbol_now(current_symbol)
        refresh_klines_now(current_symbol, market_cache.get_kline_interval())
        market_cache.request_orderbook_refresh()
        st.success(f"已请求刷新 {current_symbol} 的Ticker、K线和盘口。")
    diag = st.session_state.get("binance_diagnostics_result") or load_last_diagnostics()
    if diag:
        st.caption(f"诊断时间：{diag.get('time')}｜交易对象：{diag.get('symbol')}｜总体状态：{diag.get('status')}")
        checks = [
            {
                "项目": item.get("name"),
                "状态": item.get("status"),
                "耗时ms": item.get("elapsed_ms"),
                "错误原因": item.get("error") or "",
                "样例": item.get("sample") or "",
            }
            for item in diag.get("checks", [])
        ]
        st.dataframe(checks, width="stretch", hide_index=True)
        if diag.get("recent_error"):
            st.warning(f"最近错误：{diag.get('recent_error')}")
    logs = load_recent_binance_logs(100)
    recent_errors = [row for row in logs if str(row.get("level", "")).upper() in {"WARNING", "ERROR"}][:10]
    with st.expander("最近10条实时数据错误", expanded=bool(recent_errors)):
        if recent_errors:
            st.dataframe(
                [
                    {
                        "时间": row.get("time"),
                        "级别": row.get("level"),
                        "模块/接口": row.get("path"),
                        "交易对象": row.get("symbol"),
                        "状态": row.get("result"),
                        "错误原因": row.get("reason"),
                        "耗时ms": row.get("elapsed_ms"),
                    }
                    for row in recent_errors
                ],
                width="stretch",
                hide_index=True,
            )
        else:
            st.success("最近没有实时数据错误。")
    with st.expander("最近100条 Binance 请求日志", expanded=False):
        if logs:
            st.dataframe(logs, width="stretch", hide_index=True)
        else:
            st.info("暂无 Binance 请求日志。")


def render_server_health_page(page_titles: dict[str, tuple[str, str]], version: str) -> None:
    """服务器部署与长期运行优化中心。"""
    render_page_head("server", page_titles, version)
    health = get_server_health()
    metrics = health.get("metrics") or {}
    backup = health.get("backup") or {}
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>服务器长期运行提示</b><br>
            服务器启动默认进入 READ_ONLY。Live Manual 与 LIVE_AUTO_PILOT 不会在重启后自动恢复，真实交易必须重新人工确认。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("运行状态", str(health.get("status", "运行中")), "green" if health.get("status") == "运行中" else "red" if health.get("status") == "异常" else "yellow"),
            ("当前模式", str(health.get("mode", "READ_ONLY")), "yellow"),
            ("启动时间", str(health.get("start_time", "-")), ""),
            ("运行时长", str(health.get("uptime", "-")), "blue"),
            ("心跳时间", str(health.get("last_heartbeat_time", "-")), "green"),
            ("日志目录", "正常" if health.get("logs_writable") else "不可写", "green" if health.get("logs_writable") else "red"),
            ("数据目录", "正常" if health.get("data_writable") else "不可写", "green" if health.get("data_writable") else "red"),
            ("磁盘剩余", f"{float(metrics.get('disk_free_gb', 0) or 0):.2f} GB / {float(metrics.get('disk_free_pct', 0) or 0):.1f}%", "yellow"),
        ]
    )
    tabs = st.tabs(["健康总览", "实时数据诊断", "安全启动", "备份与日志", "部署说明", "访问安全", "系统运维中心"])

    with tabs[0]:
        render_metric_grid(
            [
                ("Python", str(health.get("python_version", "-")), ""),
                ("系统", str(health.get("platform", "-"))[:26], ""),
                ("CPU", f"{metrics.get('cpu_percent')}%" if metrics.get("psutil_available") else "当前环境暂不支持", "yellow"),
                ("内存", f"{metrics.get('memory_percent')}%" if metrics.get("psutil_available") else "当前环境暂不支持", "yellow"),
                ("最近备份", str(backup.get("latest_backup_time") or "未备份"), "green" if backup.get("latest_ok") else "yellow"),
                ("systemd", str(health.get("systemd_status", "-"))[:26], "yellow"),
            ]
        )
        st.markdown("**配置检查**")
        for item in (health.get("config") or {}).get("checks", []):
            if item.get("ok"):
                st.success(f"{item.get('name')}：{item.get('message')}")
            else:
                st.warning(f"{item.get('name')}：{item.get('message')}")
        st.markdown("**最近安全降级事件**")
        events = health.get("recent_safety_events") or []
        if not events:
            st.info("暂无运行时安全降级事件。")
        for event in events[:10]:
            st.caption(f"{event.get('time')}｜{event.get('event')}｜{event.get('risk_level')}｜{event.get('reason')}")

    with tabs[1]:
        render_binance_data_status_center(str(st.session_state.get("current_symbol") or "BTCUSDT"))

    with tabs[2]:
        st.markdown("**服务器安全启动规则**")
        st.info("点击下方按钮会执行安全启动降级：关闭 Live Manual、关闭 LIVE_AUTO_PILOT、保持 READ_ONLY。")
        if st.button("执行服务器安全启动检查", width="stretch"):
            st.session_state["safe_startup_result"] = apply_safe_startup(force=True)
            st.rerun()
        result = st.session_state.get("safe_startup_result")
        if result:
            st.success(result.get("message", "服务器启动完成，当前模式：READ_ONLY。真实交易未自动开启。"))
            for action in result.get("actions", []):
                st.caption(action)
        st.markdown("**最近重启记录**")
        restarts = health.get("recent_restart") or []
        if not restarts:
            st.info("暂无重启记录。")
        for event in restarts:
            st.caption(f"{event.get('time')}｜{event.get('event')}｜恢复模式：{event.get('restored_mode')}｜{event.get('message')}")

    with tabs[3]:
        st.markdown("**备份计划**")
        backup_settings = load_backup_settings()
        with st.form("backup_settings_form"):
            backup_settings["auto_daily_backup"] = st.checkbox("每日自动备份（预留）", value=bool(backup_settings.get("auto_daily_backup")))
            backup_settings["auto_weekly_backup"] = st.checkbox("每周完整备份（预留）", value=bool(backup_settings.get("auto_weekly_backup")))
            backup_settings["include_config"] = st.checkbox("备份 config", value=bool(backup_settings.get("include_config", True)))
            backup_settings["include_data"] = st.checkbox("备份 data", value=bool(backup_settings.get("include_data", True)))
            backup_settings["include_logs"] = st.checkbox("备份 logs", value=bool(backup_settings.get("include_logs", True)))
            if st.form_submit_button("保存备份设置", width="stretch"):
                save_backup_settings(backup_settings)
                st.success("备份设置已保存。")
        b1, b2 = st.columns(2)
        if b1.button("立即手动备份", width="stretch"):
            st.session_state["server_backup_result"] = create_backup("manual")
        if b2.button("执行日志轮转", width="stretch"):
            st.session_state["server_rotation_result"] = rotate_logs()
        backup_result = st.session_state.get("server_backup_result")
        if backup_result:
            (st.success if backup_result.get("ok") else st.error)(f"备份文件：{backup_result.get('zip_file')}｜文件数：{backup_result.get('file_count')}｜大小：{backup_result.get('size_bytes')} bytes")
        rotation_result = st.session_state.get("server_rotation_result")
        if rotation_result:
            st.success(f"日志轮转完成：归档 {rotation_result.get('archived_count')} 个文件。")
        st.markdown("**最近备份状态**")
        st.caption(f"manifest：{backup.get('latest_manifest') or '暂无'}")
        st.caption(f"时间：{backup.get('latest_backup_time') or '暂无'}｜状态：{'成功' if backup.get('latest_ok') else '暂无或失败'}｜大小：{backup.get('latest_size_bytes', 0)} bytes")

    with tabs[4]:
        git_status = get_git_status()
        st.markdown("**部署目录规范**")
        st.code(
            """/opt/ai_model/
app.py
requirements.txt
.env
.env.example
config/
data/
logs/
backups/
scripts/
docs/
runtime/
venv/""",
            language="text",
        )
        st.markdown("**文档与脚本**")
        st.caption("部署文档：docs/DEPLOY_SERVER.md")
        st.caption("远程访问：docs/REMOTE_ACCESS.md")
        st.caption("systemd 示例：docs/ai_model.service.example")
        st.caption("启动脚本：scripts/start_server.sh / scripts/start_local.bat")
        st.caption("更新脚本：scripts/update_from_git.sh")
        st.markdown(f"Git状态：{git_status.get('message')}")
        st.info("远程更新默认不自动执行，必须用户手动运行脚本。")

    with tabs[5]:
        server_settings = load_server_settings()
        env = (health.get("config") or {}).get("env") or {}
        configured = env.get("configured") or {}
        render_metric_grid(
            [
                (".env", "存在" if env.get("env_exists") else "缺少", "green" if env.get("env_exists") else "yellow"),
                ("Binance Key", "已配置" if configured.get("BINANCE_API_KEY") else "未配置", "green" if configured.get("BINANCE_API_KEY") else "yellow"),
                ("Binance Secret", "已配置" if configured.get("BINANCE_API_SECRET") else "未配置", "green" if configured.get("BINANCE_API_SECRET") else "yellow"),
                ("DeepSeek", "已配置" if configured.get("DEEPSEEK_API_KEY") else "未配置", "green" if configured.get("DEEPSEEK_API_KEY") else "yellow"),
                ("Gemini", "已配置" if configured.get("GEMINI_API_KEY") else "未配置", "green" if configured.get("GEMINI_API_KEY") else "yellow"),
                ("访问密码", "已设置" if configured.get("APP_ACCESS_PASSWORD") else "未设置", "green" if configured.get("APP_ACCESS_PASSWORD") else "yellow"),
            ]
        )
        with st.form("server_auth_settings_form"):
            server_settings["enable_simple_auth"] = st.checkbox("开启简单访问密码（读取 ENABLE_SIMPLE_AUTH / APP_ACCESS_PASSWORD）", value=bool(server_settings.get("enable_simple_auth")))
            server_settings["restore_sim_auto_on_restart"] = st.checkbox("重启后允许恢复自动模拟（不影响实盘）", value=bool(server_settings.get("restore_sim_auto_on_restart")))
            if st.form_submit_button("保存访问安全设置", width="stretch"):
                save_server_settings(server_settings)
                st.success("访问安全设置已保存。正式公网部署建议开启访问密码。")
        st.warning("页面不会显示 Secret，也不会把 Secret 写入日志。公网部署前请配置访问密码或反向代理认证。")

    with tabs[6]:
        render_operations_center()

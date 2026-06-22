"""Profile, API interface, and account sync page rendering."""

from __future__ import annotations

from html import escape
from typing import Any, Callable

import streamlit as st

from components.symbol_selector import render_symbol_search_panel
from components.ui import render_metric_grid, render_page_head
from services.cloud_sync_adapter import (
    get_cloud_sync_status,
    list_remote_backups,
    load_sync_audit,
    pull_data,
    save_sync_status,
    sync_approvals,
    sync_backups,
    sync_config_to_cloud,
    sync_notifications,
    sync_reports,
)
from services.external_ai_center import (
    get_external_ai_secret_status,
    get_external_ai_status,
    load_external_ai_audit_log,
    load_external_ai_settings,
    save_external_ai_settings,
    test_external_ai_connection,
    test_external_ai_ssl_environment,
)
from services.live_trading_center import get_live_safety_status, load_live_settings
from services.manual_position_override import load_manual_position_override_log
from services.remote_control_center import load_registered_devices
from services.secure_api_vault import clear_secure_api_values, get_secure_api_status, write_secure_api_values
from services.user_account import (
    bind_device_to_user,
    change_password,
    create_admin_user,
    expire_session,
    get_account_audit,
    get_account_status,
    get_login_audit,
    get_user_permissions,
    get_user_profile,
    save_user_profile,
)


def render_api_external_interface_center() -> None:
    """我的页统一接口中心：交易所、外部AI、通知和审计。"""
    live_status = get_live_safety_status()
    live_settings = live_status.get("settings") or load_live_settings()
    live_credentials = live_status.get("credentials") or {}
    secure_status = get_secure_api_status()
    ai_settings = load_external_ai_settings()
    ai_status = get_external_ai_status()
    deepseek_secret = get_external_ai_secret_status("deepseek")
    gemini_secret = get_external_ai_secret_status("gemini")
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>API 与外部接口中心</b><br>
            所有密钥必须脱敏管理。手机端可直接填写并保存；保存后会自动保留到本机 .env，直到你下次重新填写或手动清除。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    tabs = st.tabs(["交易所接口", "外部 AI 接口", "账户与登录接口", "通知接口", "安全与审计"])
    with tabs[0]:
        render_metric_grid(
            [
                ("Binance API", secure_status.get("BINANCE_API_KEY", {}).get("masked", "未配置"), "yellow"),
                ("Binance Secret", secure_status.get("BINANCE_API_SECRET", {}).get("secret_status", "未配置"), "green" if secure_status.get("BINANCE_API_SECRET", {}).get("configured") else "yellow"),
                ("Testnet API", secure_status.get("BINANCE_TESTNET_API_KEY", {}).get("masked", "未配置"), "yellow"),
                ("Testnet Secret", secure_status.get("BINANCE_TESTNET_API_SECRET", {}).get("secret_status", "未配置"), "green" if secure_status.get("BINANCE_TESTNET_API_SECRET", {}).get("configured") else "yellow"),
                ("当前模式", str(live_settings.get("mode", "read_only")), "yellow"),
                ("Live Manual", "默认禁用", "red"),
            ]
        )
        st.info("手机端填写后会自动保存到本机 .env，并立即在当前运行进程中生效。页面只显示脱敏状态；空输入表示保留原值。")
        with st.form("binance_secure_api_form"):
            b1, b2 = st.columns(2)
            binance_key = b1.text_input("Binance API Key", value="", type="password", placeholder="留空则不修改")
            binance_secret = b2.text_input("Binance API Secret", value="", type="password", placeholder="留空则不修改")
            t1, t2 = st.columns(2)
            testnet_key = t1.text_input("Binance Testnet API Key", value="", type="password", placeholder="留空则不修改")
            testnet_secret = t2.text_input("Binance Testnet API Secret", value="", type="password", placeholder="留空则不修改")
            st.caption("建议真实 Binance API 关闭提现权限，并开启 IP 白名单。")
            if st.form_submit_button("保存并启用交易所 API", width="stretch"):
                result = write_secure_api_values(
                    {
                        "BINANCE_API_KEY": binance_key,
                        "BINANCE_API_SECRET": binance_secret,
                        "BINANCE_TESTNET_API_KEY": testnet_key,
                        "BINANCE_TESTNET_API_SECRET": testnet_secret,
                    }
                )
                (st.success if result.get("ok") else st.warning)(result.get("message"))
                st.rerun()
        with st.expander("清除交易所 API", expanded=False):
            clear_main = st.checkbox("清除 Binance 正式 API Key / Secret")
            clear_test = st.checkbox("清除 Binance Testnet API Key / Secret")
            confirm_clear = st.checkbox("我确认清除选中的交易所 API 密钥")
            if st.button("清除选中的交易所 API", disabled=not confirm_clear, width="stretch"):
                keys: list[str] = []
                if clear_main:
                    keys.extend(["BINANCE_API_KEY", "BINANCE_API_SECRET"])
                if clear_test:
                    keys.extend(["BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"])
                result = clear_secure_api_values(keys)
                (st.success if result.get("ok") else st.warning)(result.get("message"))
                st.rerun()
        if st.button("跳转查看实盘安全中心", width="stretch"):
            st.query_params["page"] = "live"
            st.rerun()
    with tabs[1]:
        ssl_env = test_external_ai_ssl_environment()
        certifi_info = ssl_env.get("certifi") or {}
        render_metric_grid(
            [
                ("DeepSeek Key", deepseek_secret.get("masked_api_key", "未配置"), "yellow"),
                ("DeepSeek模式", str(ai_settings.get("deepseek", {}).get("mode", "shadow")), "blue"),
                ("DeepSeek成功率", f"{float((ai_status.get('deepseek') or {}).get('success_rate', 0) or 0):.1f}%", "green"),
                ("DeepSeek耗时", f"{float((ai_status.get('deepseek') or {}).get('avg_duration_ms', 0) or 0):.0f} ms", ""),
                ("Gemini Key", gemini_secret.get("masked_api_key", "未配置"), "yellow"),
                ("Gemini模式", str(ai_settings.get("gemini", {}).get("mode", "shadow")), "blue"),
                ("Gemini成功率", f"{float((ai_status.get('gemini') or {}).get('success_rate', 0) or 0):.1f}%", "green"),
                ("Gemini耗时", f"{float((ai_status.get('gemini') or {}).get('avg_duration_ms', 0) or 0):.0f} ms", ""),
                ("CA证书", "正常" if ssl_env.get("ok") else "异常", "green" if ssl_env.get("ok") else "red"),
                ("certifi", str(certifi_info.get("certifi_version", "未安装")), "green" if certifi_info.get("installed") else "red"),
            ]
        )
        st.caption(f"DeepSeek 最近调用：{(ai_status.get('deepseek') or {}).get('last_call_time', '暂无')}｜最近错误：{(ai_status.get('deepseek') or {}).get('last_error', '') or '无'}")
        st.caption(f"Gemini 最近调用：{(ai_status.get('gemini') or {}).get('last_call_time', '暂无')}｜最近错误：{(ai_status.get('gemini') or {}).get('last_error', '') or '无'}")
        with st.expander("SSL 证书环境", expanded=not bool(ssl_env.get("ok"))):
            st.markdown(f"CA证书状态：{'正常' if ssl_env.get('ok') else '异常'}")
            st.markdown(f"certifi路径：`{certifi_info.get('certifi_path', '未找到')}`")
            st.markdown(f"Python版本：`{ssl_env.get('python_version')}`｜requests：`{ssl_env.get('requests_version')}`｜urllib3：`{ssl_env.get('urllib3_version')}`")
            st.markdown(f"OpenSSL：`{ssl_env.get('openssl_version')}`｜系统时间：`{ssl_env.get('system_time')}`")
            if ssl_env.get("proxy_env"):
                st.warning("检测到代理环境变量，可能影响 SSL 证书验证。")
                st.json(ssl_env.get("proxy_env"))
            if ssl_env.get("warning"):
                st.warning(str(ssl_env.get("warning")))
            if ssl_env.get("suggestion"):
                st.info(str(ssl_env.get("suggestion")))
            if st.button("测试 SSL 证书环境", width="stretch"):
                st.session_state["external_ai_ssl_env_test"] = test_external_ai_ssl_environment()
            if st.session_state.get("external_ai_ssl_env_test"):
                st.json(st.session_state["external_ai_ssl_env_test"])
        with st.form("external_ai_secure_key_form"):
            a1, a2 = st.columns(2)
            deepseek_key = a1.text_input("DeepSeek API Key", value="", type="password", placeholder="留空则不修改")
            gemini_key = a2.text_input("Gemini API Key", value="", type="password", placeholder="留空则不修改")
            st.caption("手机端填写后会自动保存并保留到下次修改；外部 AI 只读取脱敏交易摘要，API Key 不会发送给任何模型。空输入表示保留原值。")
            if st.form_submit_button("保存并启用外部 AI API Key", width="stretch"):
                result = write_secure_api_values({"DEEPSEEK_API_KEY": deepseek_key, "GEMINI_API_KEY": gemini_key})
                (st.success if result.get("ok") else st.warning)(result.get("message"))
                st.rerun()
        with st.expander("清除外部 AI API Key", expanded=False):
            clear_deepseek = st.checkbox("清除 DeepSeek API Key")
            clear_gemini = st.checkbox("清除 Gemini API Key")
            confirm_ai_clear = st.checkbox("我确认清除选中的外部 AI API Key")
            if st.button("清除选中的外部 AI API Key", disabled=not confirm_ai_clear, width="stretch"):
                keys = []
                if clear_deepseek:
                    keys.append("DEEPSEEK_API_KEY")
                if clear_gemini:
                    keys.append("GEMINI_API_KEY")
                result = clear_secure_api_values(keys)
                (st.success if result.get("ok") else st.warning)(result.get("message"))
                st.rerun()
        with st.form("external_ai_settings_form"):
            updated = dict(ai_settings)
            deep = dict(updated.get("deepseek", {}))
            gem = dict(updated.get("gemini", {}))
            deep["mode"] = st.selectbox("DeepSeek 当前模式", ["off", "shadow", "advisory"], index=["off", "shadow", "advisory"].index(str(deep.get("mode", "shadow"))) if str(deep.get("mode", "shadow")) in ["off", "shadow", "advisory"] else 1, format_func=lambda x: {"off": "关闭", "shadow": "正式投票模式", "advisory": "咨询模式预留"}[x])
            deep["base_url"] = st.text_input("DeepSeek Base URL", value=str(deep.get("base_url", "https://api.deepseek.com")))
            deep["model"] = st.text_input("DeepSeek 模型名称", value=str(deep.get("model", "deepseek-chat")))
            d1, d2, d3 = st.columns(3)
            deep["rate_limit_seconds"] = d1.number_input("DeepSeek限频秒数", min_value=10, max_value=600, value=int(deep.get("rate_limit_seconds", 60)), step=10)
            deep["timeout_seconds"] = d2.number_input("DeepSeek超时秒数", min_value=5, max_value=60, value=int(deep.get("timeout_seconds", 20)), step=5)
            deep["max_input_chars"] = d3.number_input("DeepSeek最大摘要长度", min_value=1000, max_value=20000, value=int(deep.get("max_input_chars", 6000)), step=500)
            deep["cache_enabled"] = st.checkbox("DeepSeek启用缓存", value=bool(deep.get("cache_enabled", True)))
            deep["show_in_committee"] = st.checkbox("DeepSeek参与委员会展示", value=bool(deep.get("show_in_committee", True)))
            deep["include_in_replay_stats"] = st.checkbox("DeepSeek参与复盘统计", value=bool(deep.get("include_in_replay_stats", True)))
            gem["mode"] = st.selectbox("Gemini 当前模式", ["off", "shadow", "advisory"], index=["off", "shadow", "advisory"].index(str(gem.get("mode", "shadow"))) if str(gem.get("mode", "shadow")) in ["off", "shadow", "advisory"] else 1, format_func=lambda x: {"off": "关闭", "shadow": "正式投票模式", "advisory": "咨询模式预留"}[x])
            gem["base_url"] = st.text_input("Gemini Base URL", value=str(gem.get("base_url", "https://generativelanguage.googleapis.com")))
            gem["model"] = st.text_input("Gemini 模型名称", value=str(gem.get("model", "gemini-1.5-flash")))
            g1, g2, g3 = st.columns(3)
            gem["rate_limit_seconds"] = g1.number_input("Gemini限频秒数", min_value=10, max_value=600, value=int(gem.get("rate_limit_seconds", 60)), step=10)
            gem["timeout_seconds"] = g2.number_input("Gemini超时秒数", min_value=5, max_value=60, value=int(gem.get("timeout_seconds", 20)), step=5)
            gem["max_input_chars"] = g3.number_input("Gemini最大摘要长度", min_value=1000, max_value=20000, value=int(gem.get("max_input_chars", 6000)), step=500)
            gem["cache_enabled"] = st.checkbox("Gemini启用缓存", value=bool(gem.get("cache_enabled", True)))
            gem["show_in_committee"] = st.checkbox("Gemini参与委员会展示", value=bool(gem.get("show_in_committee", True)))
            gem["include_in_replay_stats"] = st.checkbox("Gemini参与复盘统计", value=bool(gem.get("include_in_replay_stats", True)))
            st.markdown("**数据授权边界**")
            deep_perms = dict(deep.get("permissions", {}))
            gem_perms = dict(gem.get("permissions", {}))
            deep_perms["market_summary"] = st.checkbox("DeepSeek 允许读取行情摘要", value=bool(deep_perms.get("market_summary", True)))
            deep_perms["local_strategy_summary"] = st.checkbox("DeepSeek 允许读取本地策略摘要", value=bool(deep_perms.get("local_strategy_summary", True)))
            deep_perms["committee_votes_summary"] = st.checkbox("DeepSeek 允许读取委员会投票摘要", value=bool(deep_perms.get("committee_votes_summary", True)))
            deep_perms["simulation_summary"] = st.checkbox("DeepSeek 允许读取模拟交易摘要", value=bool(deep_perms.get("simulation_summary", True)))
            gem_perms["market_summary"] = st.checkbox("Gemini 允许读取行情摘要", value=bool(gem_perms.get("market_summary", True)))
            gem_perms["chart_summary"] = st.checkbox("Gemini 允许读取图表摘要", value=bool(gem_perms.get("chart_summary", True)))
            gem_perms["chart_screenshot"] = st.checkbox("Gemini 允许读取可选图表截图", value=bool(gem_perms.get("chart_screenshot", False)))
            deep_perms["account_sensitive_info"] = False
            deep_perms["api_keys"] = False
            deep_perms["trade_execution"] = False
            gem_perms["account_sensitive_info"] = False
            gem_perms["api_keys"] = False
            gem_perms["trade_execution"] = False
            deep["permissions"] = deep_perms
            gem["permissions"] = gem_perms
            updated["deepseek"] = deep
            updated["gemini"] = gem
            if st.form_submit_button("保存外部 AI 脱敏配置", width="stretch"):
                save_external_ai_settings(updated)
                st.success("外部 AI 脱敏配置已保存。API Key 可在上方安全输入板保存或清除。")
                st.rerun()
        c1, c2 = st.columns(2)
        if c1.button("测试 DeepSeek 接入口", width="stretch"):
            result = test_external_ai_connection("deepseek")
            (st.success if result.get("ok") else st.warning)(result.get("message"))
        if c2.button("测试 Gemini 接入口", width="stretch"):
            result = test_external_ai_connection("gemini")
            (st.success if result.get("ok") else st.warning)(result.get("message"))
    with tabs[2]:
        st.info("账户登录 API、云端账户同步 API、多设备同步和用户配置备份接口已预留。本版本不接入外部身份系统。")
    with tabs[3]:
        st.info("Telegram、邮件、Webhook、服务器告警等通知接口已预留。本版本不发送外部通知。")
    with tabs[4]:
        audit = load_external_ai_audit_log(50)
        overrides = load_manual_position_override_log(50)
        st.markdown("**外部 AI 审计日志**")
        if not audit:
            st.caption("暂无外部 AI 调用日志。")
        for row in audit[:20]:
            st.caption(f"{row.get('time')}｜{row.get('ai_name')}｜{row.get('event')}｜{row.get('symbol')}｜敏感数据：{row.get('contains_sensitive_data')}｜{row.get('result')}")
        st.markdown("**人工仓位干预日志**")
        if not overrides:
            st.caption("暂无人工仓位干预记录。")
        for row in overrides[:20]:
            st.caption(f"{row.get('time')}｜{row.get('symbol')}｜系统{row.get('system_position_suggestion')}｜用户{row.get('user_selected_position')}%｜{row.get('result')}｜{row.get('reason')}")


def render_account_sync_center(page_titles: dict[str, tuple[str, str]]) -> None:
    """用户账户系统 + 云端同步基础中心。"""
    status = get_account_status()
    sync_status = get_cloud_sync_status()
    current_user = st.session_state.get("current_user") or {}
    current_device = st.session_state.get("current_device") or {}
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>账户与云同步基础中心</b><br>
            当前为单用户优先、管理员优先的基础账户系统。云同步使用本地 mock 目录，不上传 API Secret、完整 API Key 或明文密码，也不能触发交易。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("账户登录", "已开启" if status.get("enabled") else "默认关闭", "green" if status.get("enabled") else "yellow"),
            ("用户数量", str(status.get("user_count", 0)), "blue"),
            ("管理员", "已创建" if status.get("has_admin") else "未创建", "green" if status.get("has_admin") else "red"),
            ("活跃会话", str(status.get("active_sessions", 0)), "yellow"),
            ("当前用户", str(current_user.get("username", "未登录/未启用")), "yellow"),
            ("当前设备", str(current_device.get("device_name", "当前设备")), "blue"),
            ("同步状态", "开启" if sync_status.get("enabled") else "关闭", "yellow"),
            ("最近同步", str(sync_status.get("last_sync_time") or "暂无"), ""),
        ]
    )
    tabs = st.tabs(["账户", "设备绑定", "云同步", "同步审计", "账户审计"])

    with tabs[0]:
        if not status.get("has_admin"):
            st.markdown("**创建管理员账户**")
            with st.form("profile_admin_create_form"):
                username = st.text_input("用户名", value="admin")
                display = st.text_input("显示名称", value="管理员")
                password = st.text_input("密码", type="password")
                confirm = st.text_input("确认密码", type="password")
                if st.form_submit_button("创建 admin 管理员", width="stretch"):
                    if password != confirm:
                        st.error("两次密码不一致。")
                    else:
                        result = create_admin_user(username, password, display)
                        (st.success if result.get("ok") else st.error)(result.get("message"))
                        st.rerun()
        else:
            st.markdown("**用户列表**")
            for user in status.get("users", []):
                st.markdown(f"{user.get('username')}｜{user.get('display_name')}｜{user.get('role')}｜{user.get('status')}｜最近登录：{user.get('last_login_time') or '暂无'}")
        if current_user:
            st.markdown("**当前用户配置**")
            profile = get_user_profile(str(current_user.get("user_id", "")))
            with st.form("user_profile_form"):
                profile["display_name"] = st.text_input("显示名称", value=str(profile.get("display_name") or current_user.get("display_name") or "管理员"))
                profile["default_page"] = st.selectbox("默认页面", list(page_titles.keys()), index=list(page_titles.keys()).index(str(profile.get("default_page", "home"))) if str(profile.get("default_page", "home")) in page_titles else 0)
                profile["mobile_layout"] = st.selectbox("手机端布局", ["compact", "comfortable"], index=0 if profile.get("mobile_layout", "compact") == "compact" else 1, format_func=lambda x: "紧凑" if x == "compact" else "舒适")
                profile["show_advanced"] = st.checkbox("显示高级功能", value=bool(profile.get("show_advanced", True)))
                if st.form_submit_button("保存用户配置", width="stretch"):
                    result = save_user_profile(str(current_user.get("user_id")), profile)
                    st.success(result.get("message"))
            with st.expander("修改密码", expanded=False):
                old_pwd = st.text_input("旧密码", type="password")
                new_pwd = st.text_input("新密码", type="password")
                new_pwd2 = st.text_input("确认新密码", type="password")
                if st.button("修改密码", width="stretch"):
                    if new_pwd != new_pwd2:
                        st.error("两次新密码不一致。")
                    else:
                        result = change_password(str(current_user.get("username")), old_pwd, new_pwd)
                        (st.success if result.get("ok") else st.error)(result.get("message"))
            if st.button("退出当前账户会话", disabled=not bool(st.session_state.get("account_session_id")), width="stretch"):
                result = expire_session(str(st.session_state.get("account_session_id")))
                st.session_state.pop("account_session_id", None)
                st.session_state.pop("current_user", None)
                st.success(result.get("message"))
                st.rerun()
        st.info("如需启用账户登录，请在 .env 设置 ENABLE_ACCOUNT_LOGIN=true。默认关闭以避免影响本地开发。")

    with tabs[1]:
        st.markdown("**设备绑定**")
        if current_user:
            permissions = get_user_permissions(str(current_user.get("user_id")))
            st.caption(f"当前用户权限：{', '.join(permissions[:12])}{' ...' if len(permissions) > 12 else ''}")
            if st.button("绑定当前设备到当前用户", width="stretch"):
                result = bind_device_to_user(str(current_user.get("user_id")), str(current_device.get("device_id", "")))
                (st.success if result.get("ok") else st.error)(result.get("message"))
                st.rerun()
        else:
            st.warning("当前未登录账户。账户系统关闭时，设备仍按 8.2 session 级识别。")
        for device in load_registered_devices():
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(device.get("device_name", "-")))}</b>｜{escape(str(device.get("device_id", "-")))}<br>
                  权限：{escape(str(device.get("permission_level", "admin")))}｜可信：{"是" if device.get("trusted") else "否"}｜远控：{"允许" if device.get("allow_remote_control") else "禁止"}<br>
                  最近访问：{escape(str(device.get("last_seen_time", "-")))}｜页面：{escape(str(device.get("last_page", "-")))}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with tabs[2]:
        st.markdown("**本地 mock 云同步**")
        st.caption(f"同步目录：`{sync_status.get('cloud_dir')}`")
        enabled = st.checkbox("启用同步状态标记（不自动执行）", value=bool(sync_status.get("enabled")))
        if st.button("保存同步状态", width="stretch"):
            save_sync_status({"enabled": enabled})
            st.success("同步状态已保存。")
            st.rerun()
        c1, c2, c3 = st.columns(3)
        if c1.button("同步配置", width="stretch"):
            result = sync_config_to_cloud()
            (st.success if result.get("ok") else st.error)(result.get("message"))
        if c2.button("同步通知已读状态", width="stretch"):
            result = sync_notifications()
            (st.success if result.get("ok") else st.error)(result.get("message"))
        if c3.button("同步审批状态", width="stretch"):
            result = sync_approvals()
            (st.success if result.get("ok") else st.error)(result.get("message"))
        r1, r2 = st.columns(2)
        if r1.button("同步报告清单", width="stretch"):
            result = sync_reports()
            (st.success if result.get("ok") else st.error)(result.get("message"))
        if r2.button("同步备份清单", width="stretch"):
            result = sync_backups()
            (st.success if result.get("ok") else st.error)(result.get("message"))
        pull_type = st.selectbox("读取云端资源", ["config", "notifications", "approvals", "reports", "backups", "user_profiles"])
        if st.button("读取 mock 云端数据", width="stretch"):
            st.session_state["cloud_pull_result"] = pull_data(pull_type)
        if st.session_state.get("cloud_pull_result"):
            st.json(st.session_state["cloud_pull_result"])
        st.markdown("**远程备份列表**")
        for item in list_remote_backups()[:20]:
            st.caption(f"{item.get('backup_id')}｜{item.get('size')} bytes｜{item.get('updated_time')}")

    with tabs[3]:
        st.markdown("**同步审计日志**")
        audit = load_sync_audit(100)
        if not audit:
            st.info("暂无同步审计日志。")
        for row in audit:
            st.caption(f"{row.get('time')}｜{row.get('event')}｜{row.get('resource_type')}｜{row.get('result')}｜{row.get('reason', row.get('path', ''))}")

    with tabs[4]:
        st.markdown("**登录审计日志**")
        login_audit = get_login_audit(50)
        if not login_audit:
            st.info("暂无登录审计。")
        for row in login_audit:
            st.caption(f"{row.get('time')}｜{row.get('event')}｜{row.get('username')}｜{row.get('device_id')}｜{row.get('reason', '')}")
        st.markdown("**账户安全日志**")
        for row in get_account_audit(50):
            st.caption(f"{row.get('time')}｜{row.get('event')}｜{row.get('username', row.get('user_id', ''))}｜{row.get('result', '')}｜{row.get('reason', '')}")




def render_profile(symbols: list[str], snapshot: dict[str, Any], *, page_titles: dict[str, tuple[str, str]], version: str, fallback_symbols: list[str], set_symbol: Callable[[str], None], refresh_callback: Callable[[], None]) -> None:
    """我的页：集中放置调试、搜索和刷新控制。"""
    render_page_head("profile", page_titles, version)
    render_symbol_search_panel(symbols, "profile", fallback_symbols=fallback_symbols, set_symbol=set_symbol)
    render_api_external_interface_center()
    render_account_sync_center(page_titles)
    st.button("刷新行情", on_click=refresh_callback, width="stretch")
    st.markdown(
        f"""<div class="app-shell"><div class="status-card">
        <b>系统状态中心</b><br>
        Binance连接状态：{snapshot.get("binance_status", "初始化")}<br>
        当前交易对象：{snapshot.get("current_symbol", "-")}<br>
        数据源：Binance Public REST API<br>
        K线数据源：Binance Public REST Kline<br>
        当前版本：{version}<br>
        行情刷新频率：1秒<br>
        K线刷新频率：1秒<br>
        榜单完整扫描频率：3秒<br>
        衍生品刷新频率：30秒<br>
        大单刷新频率：5秒<br>
        最后行情更新时间：{snapshot.get("last_update_time", "初始化中")}<br>
        最后K线更新时间：{snapshot.get("kline_last_update_time", "初始化中")}<br>
        最后盘口更新时间：{snapshot.get("orderbook_last_update_time", "初始化中")}<br>
        最后衍生品更新时间：{snapshot.get("derivatives_last_update_time", "初始化中")}<br>
        最后大单更新时间：{snapshot.get("whale_last_update_time", "初始化中")}<br>
        大单状态：{snapshot.get("whale_status", "初始化")}<br>
        当前K线周期：{snapshot.get("kline_interval", "1m")}<br>
        交易对象数量：{len(symbols)}<br>
        最近错误信息：{snapshot.get("last_error") or "无"}<br>
        K线错误信息：{snapshot.get("kline_last_error") or "无"}<br>
        盘口错误信息：{snapshot.get("orderbook_last_error") or "无"}<br>
        衍生品错误信息：{snapshot.get("derivatives_last_error") or "无"}<br>
        大单错误信息：{snapshot.get("whale_last_error") or "无"}
        </div></div>""",
        unsafe_allow_html=True,
    )
    current = escape(str(st.session_state.get("current_symbol", "BTCUSDT")))
    st.markdown(
        f"""<div class="app-shell"><div class="module-grid">
          <div class="module-card"><div class="module-title">API状态</div><div class="status-card">Binance：{escape(str(snapshot.get("binance_status", "初始化")))}｜DeepSeek/Gemini 状态见上方外部AI接口中心。</div></div>
          <div class="module-card"><div class="module-title">数据源状态</div><div class="status-card">Ticker：{escape(str(snapshot.get("last_update_time", "初始化中")))}｜K线：{escape(str(snapshot.get("kline_last_update_time", "初始化中")))}｜盘口：{escape(str(snapshot.get("orderbook_last_update_time", "初始化中")))}</div></div>
          <div class="module-card"><div class="module-title">版本信息</div><div class="status-card">{escape(version)}｜交易对象：{current}</div></div>
          <div class="module-card"><div class="module-title">系统日志</div><div class="status-card"><a class="watch-pill" href="?page=server&symbol={current}" target="_self">查看系统运维中心</a></div></div>
          <div class="module-card"><div class="module-title">策略工厂</div><div class="status-card"><a class="watch-pill" href="?page=learning&symbol={current}" target="_self">查看复盘与策略数据</a></div></div>
          <div class="module-card"><div class="module-title">安全锁</div><div class="status-card"><a class="watch-pill" href="?page=live&symbol={current}" target="_self">查看实盘安全中心</a></div></div>
        </div></div>""",
        unsafe_allow_html=True,
    )

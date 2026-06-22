"""Remote control and notification page rendering."""

from __future__ import annotations

from html import escape
from typing import Any, Callable

import streamlit as st

from components.ui import render_metric_grid, render_page_head
from services.live_auto_pilot import (
    disable_live_auto_pilot,
    get_live_auto_status,
    pause_live_auto_pilot,
    release_live_auto_circuit_breaker,
    trigger_live_auto_circuit_breaker,
)
from services.live_trading_center import get_live_safety_status, trigger_live_kill_switch
from services.notification_center import (
    archive_notification,
    create_notification,
    get_notification_rules,
    get_notification_summary,
    load_notifications,
    mark_all_notifications_read,
    mark_notification_read,
    save_notification_rules,
)
from services.remote_control_center import (
    check_permission,
    get_remote_control_status,
    load_registered_devices,
    record_remote_action,
    register_device,
    require_confirmation,
)
from services.server_runtime import get_server_health


def render_remote_control_page(page_titles: dict[str, tuple[str, str]], version: str, ensure_device: Callable[[], dict[str, Any]]) -> None:
    """多设备远程控制 + 通知提醒系统。"""
    render_page_head("remote", page_titles, version)
    device = ensure_device()
    device_id = str(device.get("device_id", ""))
    remote_status = get_remote_control_status(device_id)
    live_status = get_live_safety_status()
    live_settings = live_status.get("settings") or {}
    auto_status = get_live_auto_status()
    server_health = get_server_health()
    notif_summary = get_notification_summary()
    st.markdown(
        """
        <div class="app-shell">
          <div class="module-card warning-box">
            <b>远程控制安全提示</b><br>
            远程控制不能绕过风控、审批、确认短句、安全锁或熔断。通知只提醒，不会直接执行交易。
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_metric_grid(
        [
            ("当前设备", str(device.get("device_name", "当前设备")), "blue"),
            ("权限", str(remote_status.get("permission_level", "admin")), "yellow"),
            ("服务器", str(server_health.get("status", "运行中")), "green" if server_health.get("status") == "运行中" else "red"),
            ("当前模式", str(live_settings.get("mode", "read_only")), "yellow"),
            ("安全锁", "已开启" if live_settings.get("kill_switch_enabled") else "未开启", "red" if live_settings.get("kill_switch_enabled") else "green"),
            ("自动实盘", "开启" if auto_status.get("enabled") else "关闭", "green" if auto_status.get("enabled") else "yellow"),
            ("自动熔断", "已触发" if auto_status.get("circuit_breaker_enabled") else "未触发", "red" if auto_status.get("circuit_breaker_enabled") else "green"),
            ("未读通知", str(notif_summary.get("unread_count", 0)), "red" if notif_summary.get("unread_count") else "green"),
        ]
    )
    tabs = st.tabs(["远程控制", "通知中心", "设备管理", "通知规则", "远程审计"])

    with tabs[0]:
        st.markdown("**远程控制面板**")
        st.caption("真实订单仍必须进入实盘页面和确认流程；这里不能直接下单。")
        phrase = st.text_input("敏感操作确认短句", type="password", placeholder="按操作提示输入对应确认短句")
        c1, c2, c3 = st.columns(3)
        if c1.button("暂停自动实盘试运行", width="stretch"):
            perm = check_permission("pause_auto_live", device_id)
            if perm.get("ok"):
                result = pause_live_auto_pilot("远程控制中心暂停自动实盘试运行。")
                create_notification({"type": "auto_live", "priority": "高", "title": "自动实盘已远程暂停", "message": result.get("message", ""), "source": "remote"})
            else:
                result = perm
            record_remote_action({"device_id": device_id, "device_name": device.get("device_name"), "permission_level": perm.get("permission_level"), "action_type": "pause_auto_live", "page": "remote", "success": result.get("ok"), "reason": result.get("message"), "current_mode": live_settings.get("mode"), "safety_lock_status": live_settings.get("kill_switch_enabled")})
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()
        if c2.button("关闭自动实盘试运行", width="stretch"):
            perm = check_permission("pause_auto_live", device_id)
            if perm.get("ok"):
                result = disable_live_auto_pilot("远程控制中心关闭自动实盘试运行。")
                create_notification({"type": "auto_live", "priority": "高", "title": "自动实盘已远程关闭", "message": result.get("message", ""), "source": "remote"})
            else:
                result = perm
            record_remote_action({"device_id": device_id, "device_name": device.get("device_name"), "permission_level": perm.get("permission_level"), "action_type": "disable_auto_live", "page": "remote", "success": result.get("ok"), "reason": result.get("message"), "current_mode": live_settings.get("mode"), "safety_lock_status": live_settings.get("kill_switch_enabled")})
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()
        if c3.button("进入只读安全模式", width="stretch"):
            perm = check_permission("sim_control", device_id)
            if perm.get("ok"):
                pause_live_auto_pilot("远程进入只读安全模式。")
                result = trigger_live_kill_switch("远程控制中心请求进入只读安全模式。")
                create_notification({"type": "server", "priority": "高", "title": "系统已进入远程只读安全模式", "message": "已暂停自动实盘并触发实盘安全锁。", "source": "remote"})
            else:
                result = perm
            record_remote_action({"device_id": device_id, "device_name": device.get("device_name"), "permission_level": perm.get("permission_level"), "action_type": "read_only_mode", "page": "remote", "success": result.get("ok"), "reason": result.get("message"), "current_mode": live_settings.get("mode"), "safety_lock_status": live_settings.get("kill_switch_enabled")})
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()

        st.markdown("**紧急远程操作**")
        e1, e2 = st.columns(2)
        if e1.button("触发紧急停止 / 安全锁", width="stretch"):
            perm = check_permission("emergency_stop", device_id)
            confirm = require_confirmation("emergency_stop", phrase)
            if perm.get("ok") and confirm.get("ok"):
                pause_live_auto_pilot("远程紧急停止。")
                trigger_live_auto_circuit_breaker("远程紧急停止触发自动实盘熔断。")
                result = trigger_live_kill_switch("远程控制中心触发紧急停止。")
                create_notification({"type": "server", "priority": "紧急", "title": "远程紧急停止已触发", "message": "系统已阻止新的实盘和自动实盘操作。", "source": "remote"})
            else:
                result = {"ok": False, "message": perm.get("message") if not perm.get("ok") else confirm.get("message")}
            record_remote_action({"device_id": device_id, "device_name": device.get("device_name"), "permission_level": perm.get("permission_level"), "action_type": "emergency_stop", "page": "remote", "success": result.get("ok"), "reason": result.get("message"), "second_confirmed": True, "confirm_phrase_ok": confirm.get("ok"), "current_mode": live_settings.get("mode"), "safety_lock_status": live_settings.get("kill_switch_enabled")})
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()
        if e2.button("解除自动实盘熔断", width="stretch"):
            perm = check_permission("release_lock", device_id)
            confirm = require_confirmation("release_auto_circuit", phrase)
            if perm.get("ok") and confirm.get("ok"):
                result = release_live_auto_circuit_breaker("我确认解除自动实盘熔断")
                create_notification({"type": "auto_live", "priority": "高", "title": "自动实盘熔断已远程解除", "message": result.get("message", ""), "source": "remote"})
            else:
                result = {"ok": False, "message": perm.get("message") if not perm.get("ok") else confirm.get("message")}
            record_remote_action({"device_id": device_id, "device_name": device.get("device_name"), "permission_level": perm.get("permission_level"), "action_type": "release_auto_circuit", "page": "remote", "success": result.get("ok"), "reason": result.get("message"), "second_confirmed": True, "confirm_phrase_ok": confirm.get("ok"), "current_mode": live_settings.get("mode"), "safety_lock_status": live_settings.get("kill_switch_enabled")})
            (st.success if result.get("ok") else st.error)(result.get("message"))
            st.rerun()

    with tabs[1]:
        st.markdown("**通知中心**")
        f1, f2 = st.columns(2)
        status_filter = f1.selectbox("状态筛选", ["unread", "read", "archived", "resolved", "全部"], format_func=lambda x: {"unread": "未读", "read": "已读", "archived": "已归档", "resolved": "已解决", "全部": "全部"}[x])
        type_filter = f2.selectbox("类型筛选", ["全部", "approval", "risk", "server", "live", "auto_live", "sim", "external_ai"])
        if st.button("全部标记已读", width="stretch"):
            result = mark_all_notifications_read()
            st.success(result.get("message"))
            st.rerun()
        rows = load_notifications(None if status_filter == "全部" else status_filter, 300)
        if type_filter != "全部":
            rows = [row for row in rows if row.get("type") == type_filter]
        if not rows:
            st.info("当前暂无通知。")
        for row in rows:
            color = "red" if row.get("priority") in {"高", "紧急"} else "yellow" if row.get("priority") == "中" else "blue"
            with st.expander(f"{row.get('priority')}｜{row.get('title')}｜{row.get('status')}｜{row.get('created_time')}", expanded=row.get("status") == "unread" and row.get("priority") in {"高", "紧急"}):
                st.markdown(f"**类型**：{row.get('type')}｜**来源**：{row.get('source')}｜**交易对象**：{row.get('symbol') or '-'}")
                st.markdown(str(row.get("message", "")).replace("\n", "  \n"))
                render_metric_grid([
                    ("优先级", str(row.get("priority")), color),
                    ("合并次数", str(row.get("merge_count", 1)), "yellow"),
                    ("需要关注", "是" if row.get("requires_attention") else "否", "red" if row.get("requires_attention") else "green"),
                    ("关联ID", str(row.get("related_id") or "-"), ""),
                ])
                n1, n2, n3 = st.columns(3)
                if n1.button("标记已读", key=f"read_{row.get('notification_id')}", width="stretch"):
                    mark_notification_read(str(row.get("notification_id")))
                    st.rerun()
                if n2.button("归档", key=f"archive_{row.get('notification_id')}", width="stretch"):
                    archive_notification(str(row.get("notification_id")))
                    st.rerun()
                target_page = ((row.get("actions") or [{}])[0] or {}).get("page")
                if n3.button("进入相关页面", key=f"go_{row.get('notification_id')}", disabled=not bool(target_page), width="stretch"):
                    st.query_params["page"] = target_page
                    st.rerun()

    with tabs[2]:
        st.markdown("**设备管理**")
        name = st.text_input("当前设备名称", value=str(device.get("device_name", "当前设备")))
        if st.button("保存当前设备名称", width="stretch"):
            st.session_state["device_name"] = name
            register_device({**device, "device_name": name})
            st.success("设备名称已保存。")
            st.rerun()
        devices = load_registered_devices()
        if not devices:
            st.info("暂无设备记录。")
        for item in devices:
            st.markdown(
                f"""
                <div class="status-card">
                  <b>{escape(str(item.get("device_name", "-")))}</b>｜{escape(str(item.get("device_id", "-")))}<br>
                  权限：{escape(str(item.get("permission_level", "admin")))}｜可信：{"是" if item.get("trusted") else "否"}｜允许远控：{"是" if item.get("allow_remote_control") else "否"}<br>
                  首次：{escape(str(item.get("first_seen_time", "-")))}｜最近：{escape(str(item.get("last_seen_time", "-")))}｜页面：{escape(str(item.get("last_page", "-")))}
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown("**活跃会话**")
        for sess in remote_status.get("active_sessions") or []:
            st.caption(f"{sess.get('session_id')}｜设备 {sess.get('device_id')}｜权限 {sess.get('permission_level')}｜最近 {sess.get('last_seen_time')}｜页面 {sess.get('last_page')}")

    with tabs[3]:
        st.markdown("**通知规则配置**")
        rules = get_notification_rules()
        with st.form("notification_rules_form"):
            rules["approval_notifications"] = st.checkbox("通知审批单", value=bool(rules.get("approval_notifications", True)))
            rules["risk_notifications"] = st.checkbox("通知高风险", value=bool(rules.get("risk_notifications", True)))
            rules["live_notifications"] = st.checkbox("通知实盘事件", value=bool(rules.get("live_notifications", True)))
            rules["sim_notifications"] = st.checkbox("通知普通模拟事件", value=bool(rules.get("sim_notifications", False)))
            rules["server_notifications"] = st.checkbox("通知服务器异常", value=bool(rules.get("server_notifications", True)))
            rules["auto_live_notifications"] = st.checkbox("通知自动实盘事件", value=bool(rules.get("auto_live_notifications", True)))
            rules["external_ai_notifications"] = st.checkbox("通知外部AI异常", value=bool(rules.get("external_ai_notifications", True)))
            rules["only_high_priority"] = st.checkbox("仅通知高优先级", value=bool(rules.get("only_high_priority", False)))
            rules["mute_low_priority"] = st.checkbox("静音低优先级", value=bool(rules.get("mute_low_priority", True)))
            rules["dedupe_minutes"] = st.number_input("同类通知合并窗口分钟", min_value=1, max_value=120, value=int(rules.get("dedupe_minutes", 5) or 5), step=1)
            rules["retention_days"] = st.number_input("通知保留天数", min_value=7, max_value=365, value=int(rules.get("retention_days", 90) or 90), step=1)
            st.markdown("**外部通知渠道预留**")
            channels = dict(rules.get("channels") or {})
            channels["system"] = st.checkbox("系统内通知", value=bool(channels.get("system", True)))
            channels["telegram"] = st.checkbox("Telegram Bot 预留", value=bool(channels.get("telegram", False)))
            channels["email"] = st.checkbox("邮件通知预留", value=bool(channels.get("email", False)))
            channels["webhook"] = st.checkbox("Webhook 预留", value=bool(channels.get("webhook", False)))
            channels["push"] = st.checkbox("手机 Push 预留", value=bool(channels.get("push", False)))
            channels["wechat"] = st.checkbox("微信通知预留", value=bool(channels.get("wechat", False)))
            rules["channels"] = channels
            if st.form_submit_button("保存通知规则", width="stretch"):
                save_notification_rules(rules)
                st.success("通知规则已保存。外部渠道为预留，不会影响主系统。")
                st.rerun()
        if st.button("生成测试通知", width="stretch"):
            create_notification({"type": "server", "priority": "中", "title": "测试通知", "message": "系统内通知中心工作正常。", "source": "remote"})
            st.success("测试通知已生成。")
            st.rerun()

    with tabs[4]:
        st.markdown("**远程操作审计日志**")
        actions = remote_status.get("recent_actions") or []
        if not actions:
            st.info("暂无远程操作记录。")
        for row in actions:
            st.caption(f"{row.get('time')}｜{row.get('device_name')}｜{row.get('permission_level')}｜{row.get('action_type')}｜成功：{row.get('success')}｜确认：{row.get('confirm_phrase_ok')}｜{row.get('reason')}")

"""外部 AI 安全 HTTPS 客户端。

使用 certifi 提供 CA 证书路径进行 HTTPS 验证；不关闭证书验证，不使用 verify=False。
"""

from __future__ import annotations

import os
import platform
import ssl
import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import requests


CERTIFI_INSTALL_HINT = "请安装或升级证书依赖：python -m pip install --upgrade certifi requests urllib3"
SSL_SUGGESTION = (
    "可能原因：1. Python证书包过旧；2. certifi未安装或过旧；"
    "3. 电脑时间不正确；4. VPN/代理拦截HTTPS证书。建议执行："
    "python -m pip install --upgrade certifi requests urllib3"
)


def _package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except Exception:
        return "未安装"


def get_certifi_status() -> dict[str, Any]:
    try:
        import certifi  # type: ignore

        path = certifi.where()
        return {
            "ok": bool(path and Path(path).exists()),
            "installed": True,
            "certifi_path": path,
            "certifi_version": _package_version("certifi"),
            "message": "certifi 可用。" if path and Path(path).exists() else "certifi 路径不存在。",
            "suggestion": "" if path and Path(path).exists() else CERTIFI_INSTALL_HINT,
        }
    except Exception as exc:
        return {
            "ok": False,
            "installed": False,
            "certifi_path": "",
            "certifi_version": "未安装",
            "message": f"certifi 不可用：{exc}",
            "suggestion": "请安装 certifi：python -m pip install certifi",
        }


def test_ssl_environment() -> dict[str, Any]:
    certifi_status = get_certifi_status()
    proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]
    proxies = {key: os.environ.get(key, "") for key in proxy_keys if os.environ.get(key)}
    warning = "检测到代理环境变量，可能影响 SSL 证书验证。" if proxies else ""
    return {
        "ok": bool(certifi_status.get("ok")),
        "certifi": certifi_status,
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "requests_version": _package_version("requests"),
        "urllib3_version": _package_version("urllib3"),
        "openssl_version": ssl.OPENSSL_VERSION,
        "system_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "proxy_env": proxies,
        "warning": warning,
        "suggestion": certifi_status.get("suggestion") or (warning if warning else "SSL 基础环境正常。"),
    }


def safe_post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    certifi_status = get_certifi_status()
    if not certifi_status.get("ok"):
        return {
            "ok": False,
            "data": {},
            "error_type": "certifi_missing",
            "error_message": certifi_status.get("message", "certifi 不可用。"),
            "suggestion": certifi_status.get("suggestion") or "请安装 certifi：python -m pip install certifi",
        }
    try:
        import certifi  # type: ignore

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=timeout,
            verify=certifi.where(),
        )
        response.raise_for_status()
        return {"ok": True, "data": response.json(), "error_type": "", "error_message": "", "suggestion": ""}
    except requests.exceptions.SSLError as exc:
        return {
            "ok": False,
            "data": {},
            "error_type": "ssl_error",
            "error_message": f"SSL证书验证失败：{exc}",
            "suggestion": SSL_SUGGESTION,
        }
    except requests.exceptions.Timeout as exc:
        return {
            "ok": False,
            "data": {},
            "error_type": "timeout",
            "error_message": f"请求超时：{exc}",
            "suggestion": "请检查网络连接、代理或适当增加外部 AI 超时时间。",
        }
    except requests.exceptions.ConnectionError as exc:
        return {
            "ok": False,
            "data": {},
            "error_type": "connection_error",
            "error_message": f"连接失败：{exc}",
            "suggestion": "请检查网络、DNS、VPN/代理设置，以及 API Base URL 是否正确。",
        }
    except requests.exceptions.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", "")
        text = getattr(exc.response, "text", "")
        return {
            "ok": False,
            "data": {},
            "error_type": "http_error",
            "error_message": f"HTTP请求失败：{status_code} {text[:300]}",
            "suggestion": "请检查 API Key、模型名称、Base URL 和账户额度。",
        }
    except ValueError as exc:
        return {
            "ok": False,
            "data": {},
            "error_type": "json_error",
            "error_message": f"响应不是有效 JSON：{exc}",
            "suggestion": "请检查外部 AI 服务返回内容或 Base URL 是否正确。",
        }
    except Exception as exc:
        return {
            "ok": False,
            "data": {},
            "error_type": "unknown_error",
            "error_message": f"外部 AI 请求异常：{exc}",
            "suggestion": "请稍后重试，或检查网络、证书和 API 配置。",
        }

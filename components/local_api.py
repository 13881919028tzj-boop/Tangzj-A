"""Browser-side helpers for the local market API."""

from __future__ import annotations

from services.local_api_server import get_local_api_port


def frontend_api_client_js(function_name: str = "fetchLocalApiJson") -> str:
    """Generate a browser-side local market API client for remote/server access."""
    port = str(get_local_api_port())
    return (
        """
      const LOCAL_API_PORT = "__PORT__";
      const LOCAL_API_PORTS = Array.from(new Set(["8765", LOCAL_API_PORT, "8766", "8767", "8768", "8769", "8770", "8771", "8772", "8773", "8774"]));
      function localApiCandidateBases() {
        const candidates = [];
        const seen = new Set();
        const addBase = (protocol, hostname) => {
          if (!hostname) return;
          const cleanProtocol = /^https?:$/.test(protocol || "") ? protocol : "http:";
          for (const port of LOCAL_API_PORTS) {
            const base = `${cleanProtocol}//${hostname}:${port}`;
            if (!seen.has(base)) {
              seen.add(base);
              candidates.push(base);
            }
            const httpBase = `http://${hostname}:${port}`;
            if (!seen.has(httpBase)) {
              seen.add(httpBase);
              candidates.push(httpBase);
            }
          }
        };
        const readLocation = (loc) => {
          try {
            addBase(loc.protocol, loc.hostname);
          } catch (_) {}
        };
        readLocation(window.location);
        try {
          if (window.parent && window.parent !== window) readLocation(window.parent.location);
        } catch (_) {}
        if (document.referrer) {
          try {
            const ref = new URL(document.referrer);
            addBase(ref.protocol, ref.hostname);
          } catch (_) {}
        }
        try {
          if (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") {
            addBase("http:", "127.0.0.1");
          }
        } catch (_) {}
        return candidates;
      }
      function buildLocalApiUrl(path, apiBase) {
        const parsed = new URL(String(path || "/"), apiBase);
        const url = new URL(`${parsed.pathname}${parsed.search}`, apiBase);
        url.searchParams.set("_", String(Date.now()));
        url.username = "";
        url.password = "";
        return url.toString();
      }
      async function __FN__(path) {
        const bases = localApiCandidateBases();
        if (!bases.length) throw new Error("无法识别当前公网主机，前端行情API地址生成失败");
        let lastMessage = "本地行情API不可用";
        for (const base of bases) {
          try {
            const res = await fetch(buildLocalApiUrl(path, base), {cache:"no-store"});
            let data = {};
            try { data = await res.json(); } catch (_) { data = {}; }
            if (res.ok && data.ok !== false) return data;
            lastMessage = data.message || data.error || `HTTP ${res.status}`;
          } catch (err) {
            lastMessage = err && err.message ? err.message : "本地行情API不可用";
          }
        }
        if (/URL is not valid|user credentials/i.test(lastMessage)) {
          throw new Error("前端行情API地址无效，请检查公网访问地址和API端口");
        }
        throw new Error(lastMessage);
      }
        """
        .replace("__PORT__", port)
        .replace("__FN__", function_name)
    )

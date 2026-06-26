"""Run market refresh and simulation loops outside a Streamlit page session."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.background_refresher import run_background_refresher_forever
from services.local_api_server import start_local_api_server


if __name__ == "__main__":
    try:
        start_local_api_server()
    except Exception as exc:
        print(f"[后台Worker] 本地前端行情API启动失败，继续运行后台刷新与交易循环。error={exc!r}", flush=True)
    run_background_refresher_forever()

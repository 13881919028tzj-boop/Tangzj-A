#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${AI_MODEL_HOME:-/opt/ai_model}"
"$PROJECT_DIR/scripts/stop_server.sh"
sleep 2
nohup "$PROJECT_DIR/scripts/start_server.sh" >/dev/null 2>&1 &
echo "$(date '+%F %T') streamlit restart requested"

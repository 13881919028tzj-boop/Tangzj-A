#!/usr/bin/env bash
set -euo pipefail

pkill -f "streamlit run app.py" || true
echo "$(date '+%F %T') streamlit stop requested"

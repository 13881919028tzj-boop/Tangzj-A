#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${AI_MODEL_HOME:-/opt/ai_model}"
HOST="${SERVER_HOST:-0.0.0.0}"
PORT="${SERVER_PORT:-8501}"

cd "$PROJECT_DIR"
mkdir -p logs runtime data config backups

if [ -f "venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "venv/bin/activate"
fi

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

echo "$(date '+%F %T') starting AI Model on ${HOST}:${PORT}" >> logs/server.log
echo "$(date '+%F %T') starting background worker" >> logs/background_worker.log
nohup python scripts/background_worker.py >> logs/background_worker.log 2>&1 &
python -m streamlit run app.py --server.address "$HOST" --server.port "$PORT" >> logs/server.log 2>&1

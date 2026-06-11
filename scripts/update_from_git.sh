#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${AI_MODEL_HOME:-/opt/ai_model}"
cd "$PROJECT_DIR"

mkdir -p logs backups
echo "$(date '+%F %T') update requested" >> logs/update.log

if [ ! -d ".git" ]; then
  echo "当前项目未连接 Git，跳过远程更新检查。" | tee -a logs/update.log
  exit 0
fi

python - <<'PY'
from services.server_runtime import create_backup
print(create_backup("manual"))
PY

git status --short | tee -a logs/update.log
git pull --ff-only | tee -a logs/update.log
python -m pip install -r requirements.txt | tee -a logs/update.log

echo "更新完成，请按需运行 scripts/restart_server.sh 重启服务。" | tee -a logs/update.log

# AI模型 8.1 服务器部署说明

## 1. 创建目录

```bash
sudo mkdir -p /opt/ai_model
sudo chown -R $USER:$USER /opt/ai_model
cd /opt/ai_model
```

无 root 权限时可使用：

```bash
mkdir -p ~/AI_MODEL
cd ~/AI_MODEL
```

## 2. 上传代码

将项目文件放入目录，确保包含：

```text
app.py
requirements.txt
services/
scripts/
docs/
.env.example
```

## 3. 创建虚拟环境

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 4. 配置 .env

```bash
cp .env.example .env
nano .env
```

默认保持：

```text
LIVE_TRADING_ENABLED=false
LIVE_AUTO_PILOT_ENABLED=false
DEFAULT_TRADING_MODE=READ_ONLY
```

不要把真实 API Key 写入文档、聊天窗口或 GitHub。

## 5. 测试启动

```bash
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

浏览器打开：

```text
http://服务器IP:8501
```

## 6. 使用启动脚本

```bash
chmod +x scripts/*.sh
AI_MODEL_HOME=/opt/ai_model scripts/start_server.sh
```

## 7. 配置 systemd

```bash
sudo cp docs/ai_model.service.example /etc/systemd/system/ai_model.service
sudo systemctl daemon-reload
sudo systemctl enable ai_model
sudo systemctl start ai_model
sudo systemctl status ai_model
```

查看日志：

```bash
journalctl -u ai_model -f
tail -f /opt/ai_model/logs/server.log
```

## 8. 重启服务

```bash
sudo systemctl restart ai_model
```

或：

```bash
scripts/restart_server.sh
```

## 9. 远程更新

如果项目连接 Git：

```bash
scripts/update_from_git.sh
```

脚本会先备份配置和数据，再执行 `git pull --ff-only` 和依赖安装。自动更新默认不执行，必须用户手动运行。

## 10. 备份数据

在页面进入：

```text
服务器 -> 备份与日志
```

点击“立即备份”。备份文件保存在：

```text
backups/
```

## 11. 安全启动规则

服务器重启后：

* 默认进入 READ_ONLY。
* 不自动恢复 Live Manual。
* 不自动恢复 LIVE_AUTO_PILOT。
* 安全锁、熔断、日志不可写、数据不可写时保持保守状态。
* 真实交易必须重新人工确认。

## 12. 常见问题

### 页面无法访问

检查端口：

```bash
ss -ltnp | grep 8501
```

检查防火墙和云服务器安全组是否开放 `8501/TCP`。

### 缺少依赖

```bash
source venv/bin/activate
python -m pip install -r requirements.txt
```

### SSL 证书错误

```bash
python -m pip install --upgrade certifi requests urllib3
```

### 不想公网暴露

仅使用内网 IP 或配置 Nginx + HTTPS + 访问密码。

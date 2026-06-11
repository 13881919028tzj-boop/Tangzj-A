# 手机远程访问说明

## 本地访问

```bash
http://127.0.0.1:8501
```

## 同局域网手机访问

1. 在电脑上查看局域网 IP。
2. 手机连接同一个 Wi-Fi。
3. 手机浏览器打开：

```bash
http://电脑局域网IP:8501
```

## 服务器访问

```bash
http://服务器IP:8501
```

云服务器需要在安全组中开放 `8501/TCP`。本地防火墙也需要允许该端口。

## 反向代理预留

后续可以使用 Nginx 反向代理：

```nginx
server {
    listen 80;
    server_name ai.example.com;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 安全提醒

不要把管理页面公开给陌生人访问。公网部署建议开启 `ENABLE_SIMPLE_AUTH=true` 并设置 `APP_ACCESS_PASSWORD`。后续 8.2 再做完整账户登录和通知系统。

# 生产服务器部署

生产后台地址为 [https://23.95.43.88/dashboard](https://23.95.43.88/dashboard)。Nginx 终止 HTTPS 并对登录请求限速，Uvicorn 仍只监听 `127.0.0.1:8010`。浏览器访问 HTTP 时重定向到 HTTPS。

SSH 隧道可作为运维备用入口。建立转发后，用 Chrome 或 Edge 打开 `http://localhost:8010/dashboard`。

```shell
ssh -N -L 127.0.0.1:8010:127.0.0.1:8010 root@23.95.43.88
```

| 项目 | 位置 |
| --- | --- |
| 当前代码和虚拟环境 | `/opt/badminton-trend-miner/current` |
| HTTPS 与登录限速 | `/etc/nginx/sites-available/badminton-dashboard` 和 `/etc/nginx/conf.d/badminton-login-limits.conf` |
| 配置及登录密码 | `/etc/badminton-trend-miner/service.env`，权限 0600，管理员账号 `admin` |
| SQLite | `/var/lib/badminton-trend-miner/data/badminton.sqlite3` |
| 模型 | `/var/lib/badminton-trend-miner/models` |
| 自动备份 | `/var/backups/badminton-trend-miner`，每天北京时间 03:40 后执行，保留最近 14 份 |

## 日常运维

在服务器上执行。

```shell
systemctl status badminton-trend-miner
journalctl -u badminton-trend-miner -n 50 --no-pager
curl -fsS http://127.0.0.1:8010/health
systemctl list-timers badminton-trend-miner-backup.timer
systemctl start badminton-trend-miner-backup.service
```

Sportradar key 写入 `/etc/badminton-trend-miner/service.env` 中的 `SPORTRADAR_API_KEYS=`，按订阅类型填写 `SPORTRADAR_ACCESS_LEVEL`，然后重启 `badminton-trend-miner`。先保持自动监控、正式 S 和通知关闭，通过手动导入确认目标赛事的实时与历史逐分覆盖。

备份使用 SQLite 的在线备份接口，包含该数据库快照已注册的模型。备份与原数据库都在同一台主机。恢复前停止应用，先保存现有数据库和模型，再将选定备份中的 SQLite 文件和相应模型一起恢复到状态目录。恢复时须在服务停止后移走旧数据库的 SQLite WAL、SHM 文件，并检查恢复文件归属 `badminton:badminton`、数据库完整性和迁移版本，再启动应用。

## 公网证书

公网入口使用 Let's Encrypt 的短期 IP 地址证书。 `badminton-certificate-renew.timer` 每天检查两次，成功续期后通过部署钩子重载 Nginx。HTTP 的 `/.well-known/acme-challenge/` 专用于证书校验。

```shell
nginx -t
systemctl list-timers badminton-certificate-renew.timer
journalctl -u badminton-certificate-renew.service -n 30 --no-pager
/opt/badminton-certbot/.venv/bin/certbot renew --cert-name badminton-dashboard-ip --dry-run
```

Nginx 配置见 [deploy/nginx](nginx)，续期服务和部署钩子见 [deploy/tls](tls)。

## 安装新版本

将仓库的运行文件（`app`、`alembic`、`scripts`、`deploy`、`alembic.ini` 和 `requirements.txt`）打包为 tar.gz。用 root 执行版本包里的 `deploy/install_release.sh /root/path/to/source.tar.gz`。每个版本创建独立虚拟环境，按需使用 `/etc/badminton-trend-miner/runtime-constraints.txt` 锁定依赖。切换前备份现有数据库和已注册模型。数据库升级后的代码回退需要按迁移兼容性决定是否同时恢复快照。

21 分制候选通过 `OFFICIAL_S_21_POLICY=bwf21-r30-s2-p95-v1` 选择。模型文件置于状态目录的 `models/` 下，由服务用户使用生产数据库配置运行 `python -m scripts.register_bwf21_candidate MODEL --sha256 TRUSTED_HASH` 登记。登记会校验模型文件、特征次序、规则和依赖版本。配置中的 `OFFICIAL_S_ENABLED` 与 `ENABLE_LIVE_MONITOR` 分别控制正式记录与实时监控，通知渠道由各自的开关控制。

当前生产通过 systemd 的 `21-point-policy.conf` 在 `service.env` 之后加载 `/etc/badminton-trend-miner/official-s.env`。需要停用正式 S 时，在后者将 `OFFICIAL_S_ENABLED` 设为 `false` 后重启服务。保留已发布记录及其模型供结算和审计；未发布比赛会按所选规则版本和模型重新确认。

## 生产 Slack 推送

Slack 是正式 S 的主推送通道，备用通道留空。Telegram 和邮件由基础环境配置关闭。生产使用 [上线开关模板](official-s.env.example)、[Slack 配置模板](slack-alert.env.example)及 [systemd 覆盖文件](systemd-overrides/)。

将 Slack 配置模板复制为 `/etc/badminton-trend-miner/slack-alert.env`，设为 root 所有、权限 `0600`，然后在服务器上填写指定频道的 `SLACK_WEBHOOK_URL`。将 `30-slack-alert.conf` 安装到 `/etc/systemd/system/badminton-trend-miner.service.d/`，执行 `systemctl daemon-reload` 后重启应用。停止 Slack 推送时，在该环境文件将 `ENABLE_SLACK_ALERT` 设为 `false` 后重启。正式消息会提醒频道成员，连通性测试使用普通测试消息。

# AGENTS.md — 羽毛球女单信号系统生产环境

本说明适用于 `D:/Downloads/badminton-trend-miner`。生产服务独立于网球系统，使用本项目的配置、SQLite 数据和模型。

## 生产连接

| 项目 | 配置 |
| --- | --- |
| 主机 | `23.95.43.88`，Ubuntu 24.04 |
| 运维登录 | `ssh root@23.95.43.88`，使用已配置的 SSH 密钥 |
| 公网后台 | [https://23.95.43.88/dashboard](https://23.95.43.88/dashboard) |
| 登录账号 | `admin` |
| 登录密码和 Sportradar API key | 从服务器 `/etc/badminton-trend-miner/service.env` 读取，文件权限为 `0600` |
| 21 分制上线开关 | systemd 通过 `21-point-policy.conf` 在 `service.env` 之后加载 `/etc/badminton-trend-miner/official-s.env` |
| Slack 正式推送 | systemd 通过 `30-slack-alert.conf` 加载 `/etc/badminton-trend-miner/slack-alert.env`，文件权限为 `0600` |
| 当前版本目录 | `/opt/badminton-trend-miner/current`，指向 `/opt/badminton-trend-miner/releases/` 下的版本 |
| SQLite 文件 | `/var/lib/badminton-trend-miner/data/badminton.sqlite3` |
| 模型目录 | `/var/lib/badminton-trend-miner/models` |
| 备份目录 | `/var/backups/badminton-trend-miner` |

凭据保存在服务器配置中。仓库、部署包和运维输出只记录配置位置。

## 架构与状态

| 组件 | 生产用途 |
| --- | --- |
| `nginx.service` | 公网 80/443 入口。HTTP 跳转到 HTTPS，ACME 校验路径除外。对登录 POST 按来源 IP 和全局频率限速。 |
| `badminton-trend-miner.service` | 以 `badminton` 用户运行单个 Uvicorn 进程，监听 `127.0.0.1:8010`。同一进程运行 Web、任务队列和调度器。 |
| SQLite | 启用 WAL 和外键约束，应用启动时由 Alembic 检查并应用迁移。 |
| `badminton-trend-miner-backup.timer` | 已停用（生产磁盘仅 16 GB，不在服务器上留存自动备份；需要时手动打包拉取到本机 `E:\badminton-trend-miner-backup\`）。 |
| `badminton-certificate-renew.timer` | 每天检查两次 Let's Encrypt IP 证书。续期成功后验证配置并重载 Nginx。 |

Nginx 站点配置为 `/etc/nginx/sites-available/badminton-dashboard`，限速配置为 `/etc/nginx/conf.d/badminton-login-limits.conf`。证书名称为 `badminton-dashboard-ip`。备份位于生产主机本地。

## 常用运维命令

登录服务器后执行以下命令。

```bash
systemctl status badminton-trend-miner nginx --no-pager
journalctl -u badminton-trend-miner -n 80 --no-pager
curl -fsS http://127.0.0.1:8010/health
curl -fsS https://23.95.43.88/health
systemctl list-timers --all 'badminton-*'
```

重新读取应用配置时重启应用。修改代理配置后先校验，再重载 Nginx。

```bash
systemctl restart badminton-trend-miner
nginx -t && systemctl reload nginx
systemctl start badminton-trend-miner-backup.service
journalctl -u badminton-trend-miner-backup.service -n 30 --no-pager
journalctl -u badminton-certificate-renew.service -n 30 --no-pager
```

公网入口排查时可用 SSH 转发访问应用。

```bash
ssh -N -L 127.0.0.1:8010:127.0.0.1:8010 root@23.95.43.88
```

建立转发后在 Chrome 或 Edge 打开 `http://localhost:8010/dashboard`。

## 生产数据库

日常查询经 SSH 在服务器上执行，使用只读连接。下面查询正式 S 的结算分布。

```bash
python3 - <<'PY'
import sqlite3
from contextlib import closing

uri = "file:/var/lib/badminton-trend-miner/data/badminton.sqlite3?mode=ro"
with closing(sqlite3.connect(uri, uri=True, timeout=15)) as db:
    rows = db.execute("""
        SELECT final_result, COUNT(*)
        FROM trend_signal_logs
        WHERE official_match_id IS NOT NULL
        GROUP BY final_result
        ORDER BY final_result
    """)
    for result, count in rows:
        print(result or "pending", count)
PY
```

复制运行中的数据库时使用自动备份输出。备份任务通过 SQLite 在线备份接口得到一致快照，并校验完整性。恢复或手工修改生产数据前先备份，恢复时停止服务并按运维手册处理旧数据库的 WAL 和 SHM 文件。

## Sportradar 与正式 S

- 数据源为 Sportradar Badminton v2，赛事范围为女单。补充密钥时编辑服务器配置中的 `SPORTRADAR_API_KEYS`，按订阅填写 `SPORTRADAR_ACCESS_LEVEL`，再重启应用。
- 2026-11-25 生产已验证并配置七把 Badminton v2 试用密钥。每把 1,000 次为本地记账上限，实际账户额度以 Sportradar 为准。
- 2025 年历史回填覆盖 2025-01-07 至 2025-12-21 的 BWF World Tour 女单（Super 300、500、750、1000 及年终总决赛），共 30 站、1,058 条赛果和状态记录，其中 969 场逐分通过校验，共 80,654 个回合。
- 2026-01-06 至 2026-11-25 的 BWF World Tour（Super 300、500、750、1000）女单有 919 场完整逐分用于两季 21 分制模型。实时监控、正式 S 和 Slack 推送已启用。自动历史导入、Telegram 和邮件关闭。
- 21 分制正式 S 使用 `bwf21-r30-s2-p95-v1` 和模型 1。整场从第 30 回合开始评估，同一方向连续两个有效 S、胜率至少 0.95、对手低于 A，且推荐方有至少 10 场同赛制逐分完整的历史完赛。每场最多发布一次，冻结触发点、方向与模型版本。`trend_signal_logs.official_match_id IS NOT NULL` 表示正式记录。
- 21 分制和 15 分制的训练、验证和推断分别处理。比赛退赛或取消时按结算规则记录。
- 信号演练使用测试数据库或离线回放，生产信号库仅用于真实比赛。

## 部署边界

- **轻量发版优先**：日常代码小改（无依赖变更）可直接将改动文件同步到 `/opt/badminton-trend-miner/current` 后执行 `systemctl restart badminton-trend-miner` 并做 `/health` 校验，无需重建虚拟环境或做本地备份。
- 需要完整发版或依赖变更时，使用 `deploy/install_release.sh`：默认复用 `/opt/badminton-trend-miner/.venv`（仅 `requirements.txt` 变化时安装依赖），不触发本地备份，自动只保留最近 2 个 release。配置、数据库、模型目录独立于版本目录。
- Uvicorn 端口保持回环绑定，公网流量经 Nginx。网球生产主机、配置和数据独立管理。
- 数据库迁移、备份恢复和版本回退详见 [部署与运维手册](deploy/README.md)。

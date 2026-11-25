# Badminton Trend Miner

从 WTA 系统迁移的羽毛球女单项目。保留历史同步、逐分回放、球员画像、模型训练、实时监控、正式 S 确认、赛后结算和 Dashboard。数据用单个 SQLite 文件，启用 WAL、外键和写锁等待。网页、定时任务和后台任务队列共用一个应用进程。项目使用自己的配置、比赛数据和模型目录。

## 本地启动

需要 Python 3.12。Windows 可用 PowerShell 执行下列命令。

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
Copy-Item .env.example .env
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --workers 1
```

Linux 使用 `.venv/bin/python`，用 `cp .env.example .env` 创建配置。打开 `http://127.0.0.1:8010/dashboard`。启动前可在 `.env` 设置登录密码和随机会话密钥。Docker 使用 `docker compose up -d --build`，默认只绑定回环地址并使用持久卷。

## 生产环境

[部署与运维手册](deploy/README.md)记录了服务器、SSH 访问、密钥配置和备份恢复。

## 接入 Sportradar

使用 Badminton v2 的每日赛程、实时赛程、比赛摘要和单场时间线，通过 `x-api-key` 鉴权。需要羽毛球产品授权，覆盖级别决定能否取到完整逐分。补齐 `.env` 里的 `SPORTRADAR_API_KEYS` 和 `SPORTRADAR_ACCESS_LEVEL` 后，可先导入几天历史，核对女单识别、赛果和逐分。无密钥时 Dashboard 仍可启动，实时调用保持空闲。

- [Badminton v2 概览](https://developer.sportradar.com/badminton/reference/badminton-overview)
- [Sportradar 鉴权](https://developer.sportradar.com/getting-started/docs/authentication)
- [每日赛程与分页](https://developer.sportradar.com/badminton/reference/badminton-daily-summaries)
- [单场逐分时间线](https://developer.sportradar.com/badminton/reference/badminton-sport-event-timeline)
- [Sportradar 赛事覆盖](https://coverage-matrix.sportradar.com/)

以下例子假定已激活项目的虚拟环境。

```text
python scripts/import_sportradar_history.py --from 2026-11-23 --to 2026-11-24
python scripts/rebuild_profiles.py
python scripts/mine_player_patterns.py
python scripts/train_trend_model.py --scoring-target 21
python scripts/run_backtest.py --from 2026-11-25 --to 2026-11-30
python -m pytest
```

## 回放、训练和正式 S

- 逐分按比分增量验证，比分缺失、跳分和来源冲突会暂停正式信号。赛后仅用可重放且与完赛胜者一致的数据生成标签。首个发球人缺失时记为未知，后续根据上一回合胜方推断。
- 默认按赛事日期采用三局两胜的 21 分制或 15 分制，2027 年 1 月 4 日起采用 15 分制。两种规则分别训练和选用模型。特殊赛事可通过 `SCORING_TARGET_OVERRIDE` 配置，建议单独建库处理。赛事分级在来源明确提供时才展示。[BWF 改制公告](https://bwfbadminton.com/news-single/2026/04/25/bwf-members-approve-3x15-scoring-system/)
- 训练按比赛日切分并报告留出集结果，包含比分基线、比分加赛前画像基线以及完整模型。回放只选择其训练样本已在目标比赛日之前结束的同赛制模型，统计单场首次确认、覆盖率以及距离终场的回合数。在线轮询可能晚于回放中的首次确认。
- `OFFICIAL_S_ENABLED`、实时监控和通知默认关闭。校准后可分别启用。默认实验条件是至少 12 个回合，连续三个有效的 S 评估，确认趋势分达到 0.70，且候选选手拥有至少 10 场同赛制先前完赛样本。每场只发布一次并冻结当时的方向、比分和模型版本。可用 `SIGNAL_COMPETITION_IDS` 将发布限定在选中的赛事 ID。
- 生产 21 分制使用 `bwf21-r30-s2-p95-v1`，第 30 回合起算，同一方向连续 2 个有效 S，胜率至少 0.95，对手低于 A。推荐方仍需至少 10 场同赛制逐分完整的历史完赛。正式 S 通过 Slack Incoming Webhook 推送。[生产配置与运维](deploy/README.md)
- 低资源主机默认只跑一个 Uvicorn 进程。建议在本地验证并训练，再在服务器上做推断。从项目根目录运行，模型目录默认是相对路径 `models/`。模型文件与 SQLite 注册记录需一并迁移，且使用同版本 scikit-learn。生产环境使用自己的登录密钥、HTTPS 反向代理和 SQLite 在线备份。

目前的接口验证基于文档和模拟响应。真实订阅配额、赛事覆盖、事件顺序和延迟需要拿到密钥后实测。

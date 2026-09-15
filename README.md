# Sequoia-X: 王者回归 | The King Returns

> A 股量化选股系统 V2 | A-Share Quantitative Stock Selection System V2

---

## 简介 | Introduction

Sequoia-X V2 是面向 A 股市场的量化选股系统，基于现代 Python 工程化标准从零重构。
系统以 OOP 架构、向量化计算和增量数据更新为核心设计原则，每日收盘后自动选股并在终端输出结果。

数据层使用 [baostock](http://baostock.com)（免费、无需注册、无限流）拉取历史及增量日 K 数据（后复权）。
部署默认写入 Postgres；本地开发仍可回退到 SQLite。

---

## 两种运行模式

```bash
python main.py               # 日常模式：8进程增量补数据 + 跑策略 + 终端输出（2~3分钟）
python main.py --backfill     # 回填模式：全市场历史K线一次性灌入（约12分钟）
```

---

## 内置策略 | Strategies

| 策略 | 说明 |
|---|---|
| **TurtleTrade** | 海龟突破：20日新高 + 成交额过亿 + 阳线防诱多，按涨幅排序 |
| **MaVolume** | 均线+放量突破 |
| **HighTightFlag** | 高而窄的旗形整理突破 |
| **LimitUpShakeout** | 涨停洗盘回踩确认 |
| **UptrendLimitDown** | 上升趋势中的跌停反包 |
| **RpsBreakout** | 欧奈尔 RPS 相对强度突破 |

---

## 部署 | Docker Compose

生产部署用 Compose（Postgres 16 + Web），**不需要 `.env`**。一键脚本会安装 Docker/Compose（如缺少）并启动。策略、同步间隔、起始日期等业务配置都在 Postgres / 网页里，换机器只拷库即可。细节见 `deploy/RUNBOOK.md`。

```bash
# Linux
./install.sh

# Windows
powershell -ExecutionPolicy Bypass -File deploy\scripts\bootstrap.ps1
# http://127.0.0.1:8002/
```

换机器：先 `docker compose stop`，再拷贝 **`/workspace/sequoia-x/postgres`**（Postgres 必须先停再拷；该目录由安装脚本自动创建），新机器上同样放到 `/workspace/sequoia-x/postgres` 后 `docker compose up -d --build`。等价做法是 `pg_dump` / `restore-db.sh`。

## 本地开发 | Quick Start

### 环境要求

- Python >= 3.10

### 1. 安装依赖

```bash
# 推荐使用 uv（快速包管理器）
uv sync

# 或者 pip
pip install .
```

### 2. 首次回填历史数据

未设置 `DATABASE_URL` 时写入本地 SQLite；Compose 环境里 `python main.py` 会连同一套 Postgres。业务起始日期从库里的同步配置读取，不读 `.env`。

```bash
python main.py --backfill
```

约 12 分钟完成 ~5200 只 A 股历史后复权日 K 数据回填。

### 3. 日常运行

```bash
python main.py
```

建议配合 crontab 每个交易日收盘后自动执行：

```cron
15 19 * * 1-5 cd /root/Sequoia-X && .venv/bin/python main.py >> log.txt 2>&1
```

---

## 目录结构 | Project Structure

```
Sequoia-X/
├── main.py                      # 入口：argparse 分发日常/回填模式
├── pyproject.toml               # 依赖声明 + ruff/pytest 配置
├── docker-compose.yml           # 生产部署：Postgres + Web
├── Dockerfile                   # 前端构建 + API 镜像
├── data/                        # 本地 SQLite 等（不入 git）；Compose Postgres 在 /workspace/sequoia-x/postgres

├── sequoia_x/
│   ├── core/
│   │   ├── config.py            # 连接 DSN + 从数据库读业务配置
│   │   └── logger.py            # rich 结构化日志
│   ├── data/
│   │   └── engine.py            # 数据引擎（baostock 回填 + 增量同步 + SQLite）
│   ├── strategy/
│   │   ├── base.py              # 策略抽象基类
│   │   ├── turtle_trade.py      # 海龟交易策略
│   │   ├── ma_volume.py         # 均线放量策略
│   │   ├── high_tight_flag.py   # 高窄旗形策略
│   │   ├── limit_up_shakeout.py # 涨停洗盘策略
│   │   ├── uptrend_limit_down.py # 上升跌停策略
│   │   └── rps_breakout.py      # RPS 突破策略
└── tests/                       # 属性测试（hypothesis）
```

---

## 数据说明

- **数据源**：[baostock](http://baostock.com)（免费、无需注册、无限流）
- **复权方式**：后复权（hfq）— 历史价格不变，适合增量存储，避免除权导致数据错乱
- **存储**：Docker Compose 使用 Postgres（宿主机 **`/workspace/sequoia-x/postgres`**，脚本会自动 `mkdir`）；业务配置与行情同库，拷该目录即可迁机。本地 `python main.py` 在未设置 `DATABASE_URL` 时回退 SQLite（`data/sequoia_v2.db`）
- **日常增量**：8 进程并行通过 baostock 拉取，2~3 分钟完成全市场更新

---

## 许可证 | License

MIT

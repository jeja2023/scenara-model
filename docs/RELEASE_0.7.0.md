# Vision Model Lab 0.7.0 发布说明

发布日期：2026-07-27

## 发布主题

`0.7.0` 是面向持续运行和多实例部署的生产化版本。本版本完成优化清单 A-G 与 P1：训练日志和取消检查显著降载，任务具备 worker 归属与心跳，长期运行服务具备周期清理能力，并建立版本、lint、依赖审计、容器冒烟和可复现依赖门禁。

## 运行时性能

- 外部命令日志由逐行独立事务改为内存缓冲后批量落库，默认达到 200 行或 1 秒触发写入；阶段事件前与任务退出时强制清空缓冲，保留事件顺序和最后一批排障日志。
- 高频取消回调仍可每 0.1 秒调用，但数据库查询默认最多每 3 秒一次；取消一旦确认会被缓存，不再继续查库。
- 取消查询同时更新任务心跳，避免为心跳增加独立线程或额外高频数据库路径。
- 模型文件 SHA-256 以路径、大小和纳秒 mtime 缓存，重复扫描大模型包不再反复全文件读取。
- 新打包模型始终绕过摘要缓存，避免 `copy2` 保留源 mtime 时把旧文件摘要写入新模型卡。

## 多实例与元数据

- `pipeline_jobs` 新增 `worker_id` 和 `heartbeat_at`。
- worker 通过 `claim_pipeline_job` 原子认领 queued 任务，通过 `heartbeat_pipeline_job` 续租运行中任务。
- 启动回收仅处理当前 worker 遗留、无归属、无心跳或心跳超过 120 秒的任务；其他实例心跳正常的任务保持运行。
- 新增迁移 `20260726_060_job_heartbeat`，SQLite 与 PostgreSQL 均支持从旧表结构补列。
- SQLite 使用每线程长连接、30 秒 busy timeout 和经过实际生效校验的 `WAL -> TRUNCATE -> DELETE` journal mode 回落。
- `/health` 返回 `metadata_journal_mode`，可直接确认 SQLite 是否实际启用 WAL；PostgreSQL 返回 `postgresql`。

## 生命周期与数据保留

- 新增 `VMLAB_LOG_RETENTION_DAYS`，默认保留 30 天任务日志和审计事件；设为 `0` 时禁用该清理。
- 新增 `VMLAB_MAINTENANCE_INTERVAL_SECONDS`，默认每 3600 秒执行过期会话和历史记录清理，最小允许值为 60 秒。
- 服务关闭时会取消维护任务、停止流水线线程池并关闭元数据连接。

## 认证与命令安全

- 认证中间件解析出的身份缓存到请求状态，路由依赖直接复用；会话认证从每请求两次数据库查询降为一次。
- 首次启动未配置管理员口令时生成随机口令并仅写入启动日志，不再使用固定默认弱口令。
- 登录失败按“用户名 + 客户端 IP”限流；默认连续失败 5 次后锁定 300 秒，可通过 `VMLAB_LOGIN_MAX_FAILURES` 和 `VMLAB_LOGIN_LOCKOUT_SECONDS` 调整。
- 外部命令使用最小环境变量集合，默认剥离 `VMLAB_METADATA_DB`、`VMLAB_ADMIN_PASSWORD`、认证令牌和对象存储凭证。
- `VMLAB_EXTERNAL_COMMAND_ENV_PASSTHROUGH` 可用逗号分隔变量名，显式放行训练脚本确实需要的额外环境变量。

## 版本与工程门禁

- Python package、运行时 `__version__`、前端 package 与 lockfile 统一为 `0.7.0`。
- 新增 `scripts/check_versions.py`，同时校验 Python、前端、README 和 CHANGELOG 的最新版本。
- `dev` extra 包含 Ruff、Alembic 与 SQLAlchemy，干净 clone 可直接执行 lint 和迁移测试。
- Ruff 覆盖 `src`、`scripts`、`tests` 和 `migrations`；存量 import、未使用符号和现代化规则问题已经清零。
- 新增 `constraints.txt`，限制后端依赖的已验证主版本范围。
- CI 新增 Python 漏洞审计：先升级 pip，测试结束后卸载本地项目包，再以 `--strict` 审计所有第三方发行包。
- Pyright 作为观察任务运行，当前不阻断合并；待存量类型问题清零后再移除 `continue-on-error`。

## Docker 与 compose

- Dockerfile 将第三方依赖和源码安装拆层；源码变化不再触发 ONNX/FastAPI 等依赖全量重装。
- 占位包安装结束后删除 `src` 与 `build`，避免 setuptools 复用旧 build 目录使运行时版本残留为 `0.0.0`。
- 容器冒烟会断言 `vision_model_lab.__version__` 与安装包元数据一致。
- `VMLAB_EXTRAS=postgres,s3` 可在镜像中安装 PostgreSQL 与 S3/MinIO 驱动。
- `docker-compose.postgres.yml` 提供 PostgreSQL + MinIO 叠加配置，并等待两个依赖服务健康后启动应用。

生产组合启动命令：

```powershell
docker compose `
  -f docker-compose.yml `
  -f docker-compose.postgres.yml `
  --profile postgres --profile minio up --build
```

## 升级步骤

1. 更新 Python 环境并应用约束：

   ```powershell
   python -m pip install --upgrade pip
   python -m pip install -e ".[dev]" -c constraints.txt
   ```

2. 备份元数据数据库后执行迁移：

   ```powershell
   vmlab storage migrate
   # 或在安装 migrations extra 的环境中执行：
   python -m alembic upgrade head
   ```

3. 生产环境显式设置管理员口令和保留策略：

   ```powershell
   $env:VMLAB_ADMIN_PASSWORD="<strong-password>"
   $env:VMLAB_LOG_RETENTION_DAYS="30"
   $env:VMLAB_MAINTENANCE_INTERVAL_SECONDS="3600"
   ```

4. 重新构建前端和镜像，确认 `/health` 的版本与 journal mode：

   ```powershell
   cd frontend
   npm ci
   npm run build
   cd ..
   docker compose up --build
   ```

## 兼容性

- API 路径和已有请求格式保持兼容。
- `pipeline_jobs` 响应会新增可空的 `worker_id` 与 `heartbeat_at` 字段，旧客户端可忽略。
- 取消检测的最坏可见延迟从亚秒级变为约 3 秒，以换取训练期间显著降低数据库压力。
- 日志仍按行返回，但批量落库后管理台可能最多延迟约 1 秒看到最新输出。
- PostgreSQL 多实例部署必须执行新迁移；未迁移时运行时 DDL 会补列，但仍建议用 Alembic 保持迁移历史一致。
- P2 清单中的 httpOnly cookie、下载签名 URL、全局 401 处理、列表游标分页等契约级改造未包含在本版本。

## 验证结果

- `python -m pytest`：84 passed。
- `python -m ruff check src scripts tests migrations`：通过。
- `python scripts/check_versions.py`：通过，版本 `0.7.0`。
- `python scripts/acceptance_check.py --skip-pytest`：通过。
- `npm run build`：通过，前端包版本 `0.7.0`。
- `npm audit --omit=dev --audit-level=high`：0 vulnerabilities。
- Linux 容器内 `pip-audit --desc --strict`：No known vulnerabilities found。
- 默认镜像导入与 `/health` 冒烟通过；运行时版本 `0.7.0`，SQLite journal mode 为 `WAL`。
- `postgres,s3` extras 镜像成功导入 boto3、psycopg 和 psycopg-pool。

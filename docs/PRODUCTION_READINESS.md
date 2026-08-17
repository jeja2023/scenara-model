# 生产交付验收

## 环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SCENARA_MODEL_WORKSPACE` | 当前工作目录 | 工作区根路径 |
| `SCENARA_MODEL_METADATA_DB` | `artifacts/scenara_model.sqlite3` | SQLite 元数据路径或 PostgreSQL DSN；`:memory:` 仅供测试，重启即丢数据 |
| `SCENARA_MODEL_CORS_ORIGINS` | `*` | API CORS 白名单，生产应显式配置 |
| `SCENARA_MODEL_SERVE_FRONTEND` | `false` | 是否由 FastAPI 托管迁移期独立前端构建产物 |
| `SCENARA_MODEL_FRONTEND_DIST` | `frontend/dist` | 前端构建目录 |
| `SCENARA_MODEL_MAX_PACKAGE_SCAN_FILES` | `500` | 模型包扫描上限 |
| `SCENARA_MODEL_STORAGE_BACKEND` | `local` | 对象存储后端：`local`、`s3`、`minio` |
| `SCENARA_MODEL_STORAGE_URI` | 工作区路径 | local 根路径或 `s3://bucket/prefix` / `minio://bucket/prefix` |
| `SCENARA_MODEL_AUTH_TOKEN` | 空 | 静态服务令牌，供 CI 与脚本通过 `Authorization: Bearer <token>` 认证 |
| `SCENARA_MODEL_ADMIN_PASSWORD` | 空 | 首次启动创建 admin 的口令；未设置时自动生成随机口令并打印到启动日志 |
| `SCENARA_MODEL_SESSION_TTL_HOURS` | `24` | 登录会话有效期 |
| `SCENARA_MODEL_LOGIN_MAX_FAILURES` | `5` | 同一用户名+IP 连续登录失败上限 |
| `SCENARA_MODEL_LOGIN_LOCKOUT_SECONDS` | `300` | 达到失败上限后的锁定时长 |
| `SCENARA_MODEL_LOG_RETENTION_DAYS` | `30` | 任务日志与审计事件保留天数 |
| `SCENARA_MODEL_MAINTENANCE_INTERVAL_SECONDS` | `3600` | 周期维护间隔 |
| `SCENARA_MODEL_EXTERNAL_COMMAND_ENV_PASSTHROUGH` | 空 | 显式放行给外部命令的环境变量名（逗号分隔） |
| `SCENARA_MODEL_MAX_UPLOAD_BYTES` | `524288000` | 上传文件大小上限 |
| `SCENARA_MODEL_PIPELINE_WORKERS` | `2` | 异步流水线线程池 worker 数 |
| `SCENARA_MODEL_EXTERNAL_COMMAND_TIMEOUT_SECONDS` | `3600` | 外部训练/导出/评估命令超时 |
| `SCENARA_MODEL_EXTERNAL_COMMAND_LOG_MAX_CHARS` | `20000` | 外部命令日志保留字符数 |
| `SCENARA_MODEL_ALLOW_SHELL_COMMANDS` | `false` | 是否允许字符串 shell 命令 |
| `SCENARA_MODEL_S3_ENDPOINT_URL` | 空 | MinIO 或兼容 S3 endpoint |
| `SCENARA_MODEL_S3_REGION` | 空 | S3 区域 |
| `SCENARA_MODEL_S3_ACCESS_KEY_ID` / `SCENARA_MODEL_S3_SECRET_ACCESS_KEY` | 空 | 兼容 S3 凭证；也可使用 AWS 标准环境变量 |

## 本地验收

```powershell
$env:PYTHONDONTWRITEBYTECODE="1"
.\.venv\Scripts\python.exe -m pytest
python scripts/prepare_dataset.py data/manifests/example_train_v1.jsonl --json
python scripts/validate_contract.py models-fragment configs/export/models.fragment.template.yml --json
python scripts/validate_contract.py release-decision configs/export/release-decision.template.yml --json
python scripts/hash_artifact.py MODEL_RND_TRAINING_PLAN.md
python scripts/run_pipeline.py --config configs/experiments/detection_yolo_baseline.yml --package
python scripts/validate_model_package.py shared-models --allow-missing-sidecars --allow-missing-examples --json
python scripts/acceptance_check.py
python scripts/runtime_check.py --base-url http://127.0.0.1:8080
```

前端依赖可用时：

```powershell
cd frontend
npm install
npm run build
```

## API 启动

```powershell
$env:SCENARA_MODEL_METADATA_DB="artifacts/scenara_model.sqlite3"
python scripts/serve_api.py --host 127.0.0.1 --port 8080 --metadata-db artifacts/scenara_model.sqlite3
```

## Docker

前端构建完成后：

```powershell
docker compose up --build
```

如生产网络需要使用镜像源，可通过 build args 替换基础镜像：

```powershell
docker build `
  --build-arg NODE_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/node:22-alpine `
  --build-arg PYTHON_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.12-slim `
  -t scenara-model:local .
```

当前 Dockerfile 默认使用 Docker Hub 官方镜像（`node:22-alpine`、`python:3.12-slim`），与 CI 行为一致；受限网络环境按上例通过 build args 覆盖为内部镜像源。镜像以非 root 用户 `scenara-model` 运行并内置 HEALTHCHECK。

可选生产形态（PostgreSQL / MinIO）：

```powershell
docker compose `
  -f docker-compose.yml `
  -f docker-compose.postgres.yml `
  --profile postgres --profile minio up --build
```

## 当前门禁

- Python 测试必须通过。
- 模型包严格模式必须校验 ONNX、sha256、模型卡、labels、样例。
- API 路径不允许逃逸工作区。
- CORS 在生产环境必须收紧到明确域名。
- 生产环境必须设置 `SCENARA_MODEL_ADMIN_PASSWORD`，自动生成口令仅适合首次本地启动；CI 与脚本可改用 `SCENARA_MODEL_AUTH_TOKEN`。
- 多实例生产部署建议使用 PostgreSQL，并把对象存储切换为 MinIO/S3 或内部制品系统。
- 大文件不提交 Git，只提交 manifest、配置、模板和报告。
- 生产流水线默认拒绝合成 ONNX、自报指标和缺少 checkpoint 的训练；仅 `package.profile: smoke` 可显式放行开发基线。
- 生产训练配置必须声明 `training.produced_checkpoint`、`export.produced_onnx` 和 `evaluation.produced_metrics`，平台会校验文件存在且在本次命令后发生变化。
- 生产模型注册会执行严格 ONNX、输入契约、模型卡溯源和实测指标校验；`smoke` stage 不得作为发布审批的生产模型。
- GPU 训练建议通过 `training.preflight_command` 执行 `scripts/examples/check_training_runtime.py --require-module <framework> --require-cuda`；相同 experiment ID 的并发运行会被拒绝。

## 2026-07 安全与任务运行补充

- 前端静态文件回退会校验路径必须停留在 `frontend/dist` 内，禁止通过编码后的 `..` 读取工作区文件。
- 全部 `/api` 接口（登录除外）均要求会话或静态令牌；生产环境应在网关层补充统一限流与访问控制。
- 流水线支持异步 job：`POST /api/pipelines/run` 传入 `{"async": true}` 后，可通过 `/api/pipelines/jobs` 查询状态，并可对 job 执行 cancel/retry。
- 外部训练命令默认只允许 argv list；字符串 shell 命令默认禁用。确需兼容旧命令时设置 `SCENARA_MODEL_ALLOW_SHELL_COMMANDS=true`，并限制 `SCENARA_MODEL_EXTERNAL_COMMAND_TIMEOUT_SECONDS` 和日志长度。
- 本地对象存储使用 `SCENARA_MODEL_STORAGE_URI`，默认 `artifacts/object-store`；对象 key 会校验不能逃逸存储根目录。
- SQLite 使用 WAL journal mode（回落顺序 `WAL -> TRUNCATE -> DELETE`，并校验 PRAGMA 实际生效值）、busy timeout 和每线程长连接；多实例或多人生产部署仍建议迁移 PostgreSQL。
- 上传入口受 `SCENARA_MODEL_MAX_UPLOAD_BYTES` 限制，超限文件会被拒绝并清理部分写入。
## Python 环境隔离建议

当前项目依赖应安装在专用虚拟环境中，避免与全局机器学习工具链互相牵制：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pip check
```

如果需要同时安装 Paddle、Torch、ONNX 优化器等重依赖，建议为训练框架另建环境，平台 API 环境只保留管理、校验和 ONNX Runtime 所需依赖。

## 1.0.0-dev.1 发布门禁

- Python 包、运行时 `__version__`、前端包版本和 lockfile 已统一为 `1.0.0-dev.1` / `1.0.0.dev1`，并由 `scripts/check_versions.py` 自动校验。
- `start.py` 默认构建并启用迁移期本地管理台；`--backend-only` 保留 API-only 启动。
- `npm ci` 和 `npm audit --json` 为 0 vulnerabilities；`npm run build` 通过，构建链为 Vite 8。
- 针对启动提示、前端开关默认值和静态资源路径保护的 Python 测试通过；Ruff 与 Pyright 针对相关文件通过。
- 完整说明见 `docs/RELEASE_1.0.0-dev.1.md`。
## 0.3.0 发布门禁

- Python 包版本、运行时 `__version__`、前端包版本已统一为 `0.3.0`。
- `.venv\Scripts\python.exe -m pip check` 和 `python -m pip check` 均通过。
- `.venv\Scripts\python.exe -m pytest` 和全局 `python -m pytest` 均为 32 passed。
- `npm run build` 通过，`npm audit --omit dev` 为 0 vulnerabilities。
- `python scripts\acceptance_check.py` 通过。
- 默认 `docker build -t scenara-model:0.3.0 .` 通过，镜像 API 导入 smoke test 通过。
- 完整说明见 `docs/RELEASE_0.3.0.md`。

## 0.4.0 发布门禁

- Python 包版本、运行时 `__version__`、前端包版本已统一为 `0.4.0`。
- `.venv\Scripts\python.exe -m pytest` 当前为 37 passed。
- `npm run build` 通过，构建产物版本为 `scenara-model-frontend@0.4.0`。
- S3/MinIO、PostgreSQL、Alembic 均作为可选 extra 接入，默认安装保持轻量。
- 任务日志、产物索引、数据集版本、模型注册、发布审批和灰度/回滚 API 均有回归测试覆盖。
- 完整说明见 `docs/RELEASE_0.4.0.md`。
## 0.4.1 发布门禁

- Python 包版本、运行时 `__version__`、前端包版本和 lockfile 已统一为 `0.4.1`。
- `python -m pytest` 当前为 39 passed，2 warnings。
- `npm run build` 通过，构建产物版本为 `scenara-model-frontend@0.4.1`。
- `python -m compileall -q src tests migrations` 通过。
- 异步流水线取消、Alembic baseline、普通 SQLite 迁移路径、前端取消状态反馈均有回归测试或构建校验覆盖。
- 完整说明见 `docs/RELEASE_0.4.1.md`。

## 0.8.0 发布门禁

- Python 包、运行时 `__version__`、前端包版本和 lockfile 已统一为 `0.8.0`，并由 `scripts/check_versions.py` 自动校验。
- `python -m pytest` 为 89 passed；Ruff、Pyright（0 errors）、离线 acceptance、前端 TypeScript/Vite 构建和 `pip check` 均通过。
- 生产训练拒绝合成 ONNX、自报指标、缺少 checkpoint、缺少真实 manifest、过期制品和未通过 runtime preflight 的环境。
- 生产模型包严格校验 ONNX、输入 shape/dtype、SHA256、labels、examples、模型卡和训练溯源；模型包文件使用原子替换写入。
- `smoke` 模型不能注册为 candidate/staging/production，不能获得 release approval，也不能执行 staging/production rollout。
- 当前验收环境为 CPU-only 且没有真实业务数据，因此仅完成流程拒绝和 smoke 回归验证；真实 GPU 训练仍需部署侧提供数据与 CUDA 环境。
- 完整说明见 `docs/RELEASE_0.8.0.md`。

## 0.7.0 发布门禁

- Python 包、运行时 `__version__`、前端包版本和 lockfile 已统一为 `0.7.0`，并由 `scripts/check_versions.py` 自动校验。
- `python -m pytest` 为 84 passed；Ruff、离线 acceptance、前端 TypeScript/Vite 构建均通过。
- `npm audit --omit=dev --audit-level=high` 为 0 vulnerabilities；Linux 容器内 Python 严格依赖审计为 No known vulnerabilities found。
- SQLite job 心跳迁移、其他 live worker 保护、日志批量 flush、取消节流、认证单次查库、历史记录清理和 PostgreSQL 健康检查均有回归测试。
- 默认 Docker 镜像运行时版本为 `0.7.0`，`/health` 返回 200 且 SQLite journal mode 为 `WAL`。
- `postgres,s3` extras 镜像已验证 boto3、psycopg、psycopg-pool 可导入；PostgreSQL + MinIO compose 叠加配置解析通过。
- 完整说明见 `docs/RELEASE_0.7.0.md`。

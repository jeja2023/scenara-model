# 运行维护手册

## 日常启动

```powershell
python scripts/serve_api.py --host 127.0.0.1 --port 8080 --metadata-db artifacts/vision_model_lab.sqlite3
```

访问：

- 管理台：`http://127.0.0.1:8080/`
- OpenAPI：`http://127.0.0.1:8080/docs`
- 健康检查：`http://127.0.0.1:8080/health`

## 发布前检查

```powershell
$env:PYTHONDONTWRITEBYTECODE="1"
vmlab storage migrate
python scripts/check_versions.py
python -m ruff check src scripts tests migrations
python -m pytest
python scripts/acceptance_check.py --skip-pytest
cd frontend
npm ci
npm run build
npm audit --omit=dev --audit-level=high
```

服务启动后：

```powershell
python scripts/runtime_check.py --base-url http://127.0.0.1:8080
```

## 模型包接收流程

1. 将候选模型包放入 `shared-models/<project>/`。
2. 执行模型包校验：

   ```powershell
   python scripts/validate_model_package.py shared-models/<project> --model-id <artifact>.onnx --strict-hash --strict-onnx
   ```

3. 校验 `models.yml` 建议片段：

   ```powershell
   python scripts/validate_contract.py models-fragment configs/export/models.fragment.template.yml
   ```

4. 校验上线判定：

   ```powershell
   python scripts/validate_contract.py release-decision configs/export/release-decision.template.yml
   ```

## 故障处理

- `/health` 不通：确认进程是否监听 8080，或换端口启动。
- 前端空白：重新执行 `npm run build`，确认 `frontend/dist/assets` 存在，然后重启 API。
- SQLite 写入失败：使用 `--metadata-db :memory:` 验证是否为磁盘权限问题，再切换到可写路径。
- 多实例任务被意外回收：检查 `/health` 后端类型和 `pipeline_jobs.worker_id` / `heartbeat_at`，确认各实例时钟同步且心跳未超过 120 秒。
- 日志增长过快：调整 `VMLAB_LOG_RETENTION_DAYS`，并确认周期维护没有持续报错；设为 `0` 会禁用历史日志清理。
- Docker 构建失败：确认 Docker Desktop 可用；当前默认基础镜像已切到已验证镜像源，仍可通过 build args 覆盖为 Docker Hub 或企业镜像源。
- 国内或内网环境可用 `--build-arg NODE_IMAGE=... --build-arg PYTHON_IMAGE=...` 指向企业镜像源；已验证的示例为华为云镜像源下的 Node 22 和 Python 3.12 slim。

## 2026-07 安全与任务运行补充

- 前端静态文件回退会校验路径必须停留在 `frontend/dist` 内，禁止通过编码后的 `..` 读取工作区文件。
- 全部 `/api` 接口（登录除外）均要求会话或静态令牌；生产环境必须配置强管理员口令，并在网关层补统一限流与访问控制。
- 流水线支持异步 job：`POST /api/pipelines/run` 传入 `{"async": true}` 后，可通过 `/api/pipelines/jobs` 查询状态，并可对 job 执行 cancel/retry。
- 外部训练命令默认只允许 argv list；字符串 shell 命令默认禁用。确需兼容旧命令时设置 `VMLAB_ALLOW_SHELL_COMMANDS=true`，并限制 `VMLAB_EXTERNAL_COMMAND_TIMEOUT_SECONDS` 和日志长度。
- 本地对象存储使用 `VMLAB_STORAGE_URI`，默认 `artifacts/object-store`；对象 key 会校验不能逃逸存储根目录。
- SQLite 使用 WAL journal mode（回落顺序 `WAL -> TRUNCATE -> DELETE`，并校验 PRAGMA 实际生效值）、busy timeout 和每线程长连接；多实例或多人生产部署仍建议迁移 PostgreSQL。
- 上传入口受 `VMLAB_MAX_UPLOAD_BYTES` 限制，超限文件会被拒绝并清理部分写入。
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
## 0.3.0 发布检查

本版本已统一版本号到 `0.3.0`，并完成以下环境修复：

- 项目 `.venv` 已可运行 `pip check` 和全量 pytest。
- 全局 Python 依赖冲突已清理，`python -m pip check` 通过。
- Windows 用户代理残留已备份到 `artifacts/windows-proxy-before-20260703184731.txt` 并清理。
- 默认 Docker 构建已通过：`docker build -t vision-model-lab:0.3.0 .`。
- 镜像 smoke test 已通过：`docker run --rm vision-model-lab:0.3.0 python -c "from vision_model_lab.api import app; print(app.title)"`。

完整发布说明见 `docs/RELEASE_0.3.0.md`。

## 0.4.0 运维补充

- 任务详情：`GET /api/pipelines/jobs/{job_id}` 会返回 `logs` 和 `artifacts`，也可分别访问 `/logs` 与 `/artifacts`。
- 对象存储：local 继续适合单机；MinIO/S3 需安装 `vision-model-lab[s3]` 并配置 `VMLAB_STORAGE_BACKEND`、`VMLAB_STORAGE_URI` 和 S3 凭证。
- 元数据：SQLite 继续适合单机；PostgreSQL 需安装 `vision-model-lab[postgres]` 并设置 `VMLAB_METADATA_DB=postgresql://...`。
- 迁移：轻量环境执行 `vmlab storage migrate`；正式环境安装 `vision-model-lab[migrations]` 后执行 `alembic upgrade head`。
- MLOps：数据集版本、模型注册、发布审批和灰度/回滚记录都写入元数据存储，生产环境应定期备份。
- 生产 adapter：`ultralytics_yolo`、`torchreid`、`torchvision_classifier`、`segmentation_framework` 只提供平台入口，训练框架和 argv 命令由部署环境提供。
## 0.4.1 运维补充

- 取消任务时，job 会先进入 `cancellation_requested`，工作线程确认后进入 `cancelled`；排障时优先查看 job 详情中的 `cancelled_stage` 和 `cancelled_reason`。
- 外部训练/导出/评估命令会在取消请求后终止子进程，部署侧应确保训练脚本能处理终止信号并及时释放 GPU/临时文件。
- Alembic 使用普通 SQLite 文件路径时无需手动拼接 `sqlite:///`；正式环境仍建议显式配置 PostgreSQL DSN。
- 大模型目录扫描受 `VMLAB_MAX_PACKAGE_SCAN_FILES` 保护，触顶时应收窄扫描目录或提高上限。
- 管理台会显示“排队中”“取消中”“已取消”等中文状态，可用 job 详情确认取消时间、阶段、原因和保留产物。

## 0.7.0 运维补充

- 升级前备份元数据数据库并执行 `vmlab storage migrate` 或 `python -m alembic upgrade head`，确认 `pipeline_jobs` 已包含 `worker_id` 与 `heartbeat_at`。
- `/health` 的 `version` 应为 `0.7.0`；SQLite 部署的 `metadata_journal_mode` 应优先显示 `WAL`，PostgreSQL 部署应显示 `postgresql`。
- 外部命令日志批量落库后，管理台最新日志最多可能延迟约 1 秒；任务结束、失败或阶段切换时会强制落完缓冲。
- 取消请求默认最多约 3 秒被 worker 观察到；该延迟用于把训练期间的取消查库压力降低一个数量级以上。
- 生产环境设置 `VMLAB_ADMIN_PASSWORD`、`VMLAB_LOG_RETENTION_DAYS` 和 `VMLAB_MAINTENANCE_INTERVAL_SECONDS`；不要依赖自动生成口令进行常态部署。
- PostgreSQL + MinIO 使用 `docker-compose.postgres.yml` 叠加文件启动；镜像通过 `VMLAB_EXTRAS=postgres,s3` 安装对应驱动。
- 完整发布和升级说明见 `docs/RELEASE_0.7.0.md`。

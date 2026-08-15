# 更新日志

本文件记录 `scenara-model` 的主要功能变更、交付状态和验证结果。格式参考 Keep a Changelog，版本号遵循语义化版本。

## [Unreleased]

## [1.0.0-dev.0] - 2026-08-15

### Changed

- 仓库由 `vision-model-lab` 正式迁移为 `scenara-model`，Python 包、CLI、镜像、服务和环境变量同步使用 Scenara 标识。
- 按《景枢平台总体开发规范》明确模型平台只负责训练、评估、注册和不可变模型制品，不负责数据事实来源或生产部署。
- 锁定 `@scenara/repository-contracts` `1.0.0`，记录 Dataset Version 输入和部署反馈契约依赖。
- 独立前端和本地账号体系降级为迁移期兼容实现，默认不启用，正式接入统一 Console/IAM 前保持 `seed` 状态。

## [0.8.0] - 2026-07-28

本版本将训练、导出、评估和发布链路提升为可审计的生产制品流程；开发基线与真实模型明确分离，缺少真实训练产物时不会生成可发布模型。

### 生产训练信任链

- `package.profile` 默认使用 `production`；生产训练强制要求外部 checkpoint、外部 ONNX、实测 metrics、train/val/test manifest、真实样例和非空指标阈值。
- 外部训练、导出和评估命令增加 checkpoint/ONNX/metrics 的 SHA256、mtime 和本次运行新鲜度校验，拒绝“命令成功但产物未更新”的假成功。
- 模型卡自动记录数据集版本、输入契约、实测指标、阈值、checkpoint/ONNX 来源、实验配置哈希、代码 revision 和训练命令。
- 生产模型包执行严格 ONNX、输入 shape/dtype、SHA256、labels、examples 和模型卡溯源校验；模型文件、侧车文件和示例文件采用原子替换写入。

### 发布与运行可靠性

- `smoke` 仅用于开发基线，不得注册为 candidate/staging/production，不得通过发布审批或生产 rollout。
- 模型注册增加 candidate/staging/production 阶段约束，production rollout 必须有已审批发布和已注册 rollback target。
- 同一 experiment ID 增加跨线程、跨进程互斥，避免重复运行覆盖训练制品。
- 增加训练 runtime preflight，检查训练框架、Torch、CUDA 和 GPU 设备；缺失数据或 GPU 时在训练前失败并给出结构化原因。
- 本地 manifest 可校验图像文件存在性，生产配置补齐 Ultralytics 数据布局和命令入口。

### 工程、文档与验证

- 新增 `src/scenara_model/trust.py`、训练 runtime 检查脚本和生产数据目录说明。
- CLI 增加 `--strict-provenance` 与 `--check-local-files`，验收脚本切换为严格模型包校验。
- 修复 Alembic、启动脚本和测试中的静态类型问题，全仓 Pyright 达到 0 errors。
- 本版本验证：89 项 Python 测试、Ruff、Pyright、前端 TypeScript/Vite 构建、离线 acceptance 和 `pip check` 全部通过。

### 已知边界

- 当前发布不包含真实业务数据或训练权重；CPU-only 环境不会被伪装成 GPU 训练成功。部署真实训练前必须补齐 manifest/图像、训练框架环境和 CUDA 设备。

### 生产训练可信链

- `package.profile` 默认使用 `production`：要求外部训练 checkpoint、外部 ONNX、实测 metrics、三份数据 manifest、真实样例、指标阈值和已填写限制；合成 baseline 仅能显式使用 `smoke`。
- 外部训练、导出和评估产物增加前后 sha256/mtime 指纹，拒绝命令返回成功但复用旧 checkpoint、ONNX 或 metrics 的假成功。
- 模型卡自动写入数据版本、实测指标、阈值、checkpoint 哈希、配置哈希、代码 revision 和阶段来源；生产包校验 ONNX 输入 shape/dtype 与模型卡一致。
- 模型注册对 candidate/staging/production 强制执行 ONNX 与生产溯源校验；smoke 模型不能通过发布审批或进入 staging/production rollout，生产 rollout 必须有审批和已注册回滚目标。
- 增加训练 runtime preflight、同 experiment 跨线程/跨进程互斥、本地 manifest 资产检查，以及 JSON/YAML 原子写入。

## [0.7.0] - 2026-07-27

本次版本集中落地 2026-07 生产化审查中的 A-G 与 P1 优化，降低训练任务对元数据存储的压力，补齐多实例任务归属、周期维护、依赖与 CI 门禁，并修复部署验证中发现的镜像版本残留和严格审计失效问题。

### 性能与运行时

- 新增 `_JobLogBuffer`：外部命令 stdout/stderr 按 200 行或 1 秒批量写入 `pipeline_job_logs`，阶段事件前和任务退出时强制 flush，避免高输出训练任务逐行开启事务并确保尾部日志不丢失。
- 新增 `_CancelCheck`：首次调用立即查询取消状态，后续从每 0.1 秒一次数据库访问节流为默认每 3 秒一次；同一路径续写 worker 心跳，不增加额外轮询线程。
- `sha256_file` 新增以路径、文件大小和纳秒 mtime 为签名的线程安全摘要缓存，模型包扫描不再反复读取大体积 ONNX；新打包文件显式 `use_cache=False`，避免 `shutil.copy2` 保留 mtime 时复用错误摘要。
- SQLite 元数据存储改为每线程长连接，任务日志支持单事务批量写入；journal mode 使用 `WAL -> TRUNCATE -> DELETE` 安全回落并校验 PRAGMA 实际生效值。

### 多实例、存储与维护

- `pipeline_jobs` 新增 `worker_id` 与 `heartbeat_at`，新增 `claim_pipeline_job` / `heartbeat_pipeline_job`；启动回收仅处理本 worker 遗留任务、无归属任务或心跳超时任务，不再误杀其他存活实例的训练。
- 新增 Alembic 迁移 `20260726_060_job_heartbeat`，SQLite 与 PostgreSQL 运行时 DDL 同步补列，schema 版本更新为 `20260726_060_job_heartbeat`。
- 新增周期维护任务，按 `SCENARA_MODEL_MAINTENANCE_INTERVAL_SECONDS` 清理过期会话，并按 `SCENARA_MODEL_LOG_RETENTION_DAYS` 清理任务日志和审计事件；关闭服务时会取消维护任务并回收存储连接。
- `/health` 新增 `metadata_journal_mode`；SQLite 返回实际 journal mode，内存库返回 `memory`，PostgreSQL 返回 `postgresql`，避免在 PostgreSQL 后端执行 SQLite PRAGMA。

### 认证与安全

- 路由依赖复用认证中间件写入的 `request.state.identity`，每个已认证请求不再重复查询 `auth_sessions`。
- 未配置 `SCENARA_MODEL_ADMIN_PASSWORD` 时首次启动改为生成随机管理员口令，不再使用固定弱口令；新增按用户名和客户端 IP 计数的登录失败限流与临时锁定。
- 外部训练命令环境改为最小继承并剥离平台数据库、管理员口令和对象存储凭证；仅通过 `SCENARA_MODEL_EXTERNAL_COMMAND_ENV_PASSTHROUGH` 显式放行额外变量。

### 工程化与依赖

- Python、运行时、前端 package 与 lockfile 版本统一升级到 `0.7.0`；新增 `scripts/check_versions.py` 并接入离线验收，阻止版本声明再次漂移。
- `dev` extra 补齐 Ruff、Pyright、Alembic 与 SQLAlchemy，保证干净环境可以执行 lint、类型检查和迁移测试；新增 Ruff 配置并清零存量 lint 问题。
- 新增 `constraints.txt`，为后端运行、开发、PostgreSQL 和 S3 依赖设置已验证的主版本上界。
- CI 新增 Ruff、Pyright 阻断门禁、Python 严格漏洞审计和 Docker 服务冒烟；类型检查安装 PostgreSQL 与 S3 extras 并清零可空路径、适配器协议、可选存储后端和数据库自增 ID 的类型问题，审计前升级 pip，并在测试后卸载本地项目包，只严格审计可由漏洞数据库识别的第三方依赖。
- GitHub Actions 升级到 Node 24 兼容版本：`actions/checkout@v5`、`actions/setup-python@v6` 和 `actions/setup-node@v5`，消除 Node 20 退役警告。
- 前端继续执行 TypeScript/Vite 生产构建及 `npm audit --omit=dev --audit-level=high`。

### 部署

- Dockerfile 使用占位包缓存第三方依赖层，源码变化只重装项目自身；占位安装后同时清理 `src` 与 `build`，修复旧构建目录让运行时 `__version__` 错误残留为 `0.0.0` 的问题。
- Docker 依赖层先升级 pip；镜像 smoke test 断言运行时版本与安装包元数据一致，不再只验证模块可导入。
- Dockerfile 新增 `SCENARA_MODEL_EXTRAS` build arg；compose 默认构建 `postgres,s3` extras，并允许通过环境变量覆盖元数据与对象存储后端。
- 新增 `docker-compose.postgres.yml`，将应用连接到 compose 内的 PostgreSQL 与 MinIO，并通过健康条件控制启动顺序。

### 文档与验证

- 同步 README、架构、运维、生产验收与优化清单，新增 `docs/RELEASE_0.7.0.md`。
- 后端全量测试：84 passed；Ruff、Pyright、版本门禁、离线验收、前端构建、npm audit、compose 配置解析和 Python 严格依赖审计通过。
- 默认 Docker 镜像 `/health` 返回 200、版本为 `0.7.0`、SQLite journal mode 为 `WAL`；`postgres,s3` extras 镜像成功导入 boto3、psycopg 与 psycopg-pool。

## [0.6.0] - 2026-07-16

### 新增（登录与认证）

- 新增登录页面：用户名 + 密码登录，会话令牌存储于浏览器 `localStorage`，顶栏显示当前用户并支持退出登录。
- 全部 `/api` 接口（除 `/api/auth/login`）现在要求认证；`/health` 与前端静态资源保持公开（容器健康检查依赖）。会话失效时前端自动回到登录页。
- 新增 `users` / `auth_sessions` 元数据表（SQLite/PostgreSQL 双后端 + Alembic 迁移 `20260717_050`）。密码使用 PBKDF2-HMAC-SHA256（210k 迭代）加盐哈希，数据库仅存会话令牌的 SHA-256 摘要。
- 首次启动自动创建 `admin` 账户：密码取 `SCENARA_MODEL_ADMIN_PASSWORD`，未设置时为 `admin123` 并在启动日志中提醒修改。新增 `SCENARA_MODEL_SESSION_TTL_HOURS`（默认 24）配置会话有效期。
- 新增 CLI：`scenara-model user set-password --username <u> --password <p> [--create]`，用于修改密码或创建用户；密码变更自动撤销该用户全部已有会话。
- `SCENARA_MODEL_AUTH_TOKEN` 静态令牌继续有效（面向 CI/脚本自动化），与登录会话并行使用。
- 登录成功/失败、退出、用户创建均记录审计事件。

## [0.5.0] - 2026-07-16

本次为可信性与生产可用性大版本：修复了交付信任链上的全部严重缺陷，并系统性升级任务运行时、元数据存储、前端与部署配置。

### 修复（交付信任链）

- 修复外部导出命令产出的真实 ONNX 被合成桩模型无条件覆盖的严重缺陷；外部导出成功后立即校验并返回，报告新增 `onnx_source` 字段（`external_command` / `synthetic_baseline` / `reused`）。
- 修复外部命令 stdout/stderr 无并发读取导致长日志（>64KB）必然管道死锁的问题；改为 reader 线程逐行消费，并通过 `log_sink` 将外部命令输出逐行写入任务日志。
- 训练/导出/评估任一阶段失败立即短路，报告新增 `failed_stage` / `failed_reason`；打包前强制校验流水线状态为 `completed`，失败的运行绝不产出模型包。
- 新增评估指标回读协议：`evaluation.produced_metrics` 指向外部命令输出的 JSON 指标文件，adapter 回读后写入报告并标注 `metrics_source: measured`；声明了该字段但回读失败时评估失败而非静默回落；自报指标标注 `declared`，前端展示区分「实测/自报/基线」徽章。
- 导出阶段默认不再复用已存在的 ONNX；显式配置 `export.reuse_existing: true` 时才复用可加载产物。

### 修复（任务运行时）

- 取消/超时改为终止整棵进程树（Windows 用 `taskkill /T`，POSIX 用进程组信号），不再泄漏 DataLoader/torchrun 等孙进程。
- 外部命令显式使用 UTF-8 解码（`errors="replace"`），中文 Windows 环境下不再因 GBK 解码失败导致任务崩溃。
- 外部命令环境剥离 `SCENARA_MODEL_AUTH_TOKEN`、S3/AWS 凭证等平台机密。
- 日志截断改为保留头部+尾部（失败排障最需要的 Traceback 在尾部）。
- 阶段级异常兜底：适配器抛出的未捕获异常转为结构化 failed 载荷，保留已完成阶段的结果。
- 产物目录统一锚定 `SCENARA_MODEL_WORKSPACE`，服务进程 CWD 与工作区不一致时产物不再"失联"。

### 修复（元数据存储与 API）

- 任务状态机改为单条带守卫的 UPDATE：终态（completed/failed/cancelled）不可被取消或迟到的完成回退。
- 服务启动时回收孤儿任务（running/cancellation_requested → failed）并重新提交 queued 任务；关闭时优雅停机线程池。
- 任务日志支持 `since_id` 增量拉取与 `tail` 取尾；长任务最新日志不再不可达。
- 时间戳统一为带 Z 后缀的 ISO8601（毫秒级），SQLite/PostgreSQL 两后端输出一致，前端解析不再产生时区偏移。
- `SCENARA_MODEL_METADATA_DB` 默认值从 `:memory:` 改为 `artifacts/scenara_model.sqlite3`；`/health` 新增 `metadata_persistent` 字段。
- 空库清理守卫：WAL 文件非空时拒绝删除，杜绝崩溃恢复窗口的静默丢数据。
- PostgreSQL 改用 psycopg_pool 连接池（未安装时回落按需建连），去掉全局锁串行化；`_prepare_sql` 命名参数改为词边界正则替换。
- 同一配置存在未完结任务时拒绝重复提交（409）；retry 仅允许终态任务。
- 新增 `GET /api/pipelines/artifacts/{id}/download` 产物下载端点。
- Alembic 迁移脚本与运行时 DDL 对齐（PG TIMESTAMPTZ/BIGSERIAL，SQLite TEXT/INTEGER）；`scenara-model storage migrate` 优先调用 alembic upgrade head。

### 修复（前端）

- 修复 Pipeline 页面 69 处中文乱码（非 UTF-8 编辑器保存事故），CI 新增乱码检查，新增 `.editorconfig` 锁定 UTF-8。
- 轮询降载：高频轮询只拉任务列表，全库包扫描仅在任务完结时刷新一次；adapters/审计事件只在挂载时拉取。
- 引入结构化 `ApiError`（含 HTTP 状态码），`Promise.allSettled` 替代 `Promise.all`，单端点失败不再全局瘫痪。
- Packages/DataLabeling/Experiments 页面补齐错误处理；Packages 页渲染完整校验结果（issue 列表）。
- 实验表单 task/status 改为下拉枚举（存英文码、显示中文），不再以中文显示值污染后端词汇表。
- 产物链接改为可用的下载端点，展示人类可读文件大小；任务详情竞态保护（仅接受最后一次请求的响应）。
- 实验列表按 id 去重合并 DB 与 index.yml 记录；新增 `:focus-visible` 焦点环与 `aria-selected`。

### 修复（工程化与部署）

- `.env` 移出版本库（`git rm --cached`）并加入 `.gitignore`；`start_model.py` 首次启动自动从 `.env.example` 复制；env 测试改为防泄漏检查。
- `start_model.py` 的 dotenv 加载不再覆盖 shell 中显式导出的环境变量。
- Dockerfile：非 root 运行、HEALTHCHECK、依赖层与源码层分离、默认基础镜像改回 docker.io 官方镜像。
- docker-compose：restart 策略、healthcheck、`env_file` 支持、可选 postgres/minio profiles。
- CI：乱码检查、`npm audit --omit=dev --audit-level=high`、acceptance_check 支持 `--skip-pytest` 消除重复执行、并发取消、acceptance 子进程 600 秒超时。
- `runtime_check.py` 处理连接拒绝（URLError），服务未启动时输出合法 JSON 报告而非崩溃。
- `scripts/train.py` 移除死状态 `external_command_declared`；`run_pipeline.py` 取消（4）与失败（2）退出码区分。

### 新增

- 适配器契约新增 `log_sink` 逐行日志回调，异步任务运行期间可实时查看外部命令输出。
- 回归测试从 39 条扩充到 60 条：覆盖导出覆盖、管道死锁、失败短路、指标回读、状态机守卫、孤儿回收、日志分页、时区、WAL 守卫、`_prepare_sql` 纯函数等关键路径。

## [0.4.1] - 2026-07-13

### 新增

- 新增流水线取消闭环：训练、导出、评估和打包阶段都会检查取消请求，外部命令会终止子进程并返回 `cancelled` 结果。
- 新增 job 取消详情：异步任务会记录 `cancelled_at`、`cancelled_stage`、`cancelled_reason` 和已生成产物索引。
- 新增管理台 `queued`、`cancelled`、`cancellation_requested` 中文状态，以及 job 详情里的取消提示、取消时间、取消阶段和取消原因。
- 新增 `docs/RELEASE_0.4.1.md`，汇总本次取消链路、迁移、扫描和前端反馈补丁。

### 变更

- 版本号统一升级到 `0.4.1`，同步 Python 包、运行时 `__version__`、前端包版本和 lockfile。
- 异步 job 完成时会按 `completed`、`failed`、`cancelled` 分别记录审计事件；同步运行也会记录取消或失败动作。
- 模型包扫描改为增量收集 ONNX 文件，超过 `SCENARA_MODEL_MAX_PACKAGE_SCAN_FILES` 后提前停止，避免大目录一次性排序扫描。
- 管理台状态徽章新增进行中和中性样式，取消中与已取消不再被误判为失败展示。

### 修复

- 修复 Alembic baseline 空库迁移表结构不完整的问题，补齐实验、模型包校验、流水线运行、job、审计、产物、数据集版本、模型注册、发布审批和灰度/回滚表。
- 修复 `SCENARA_MODEL_METADATA_DB` 使用普通 SQLite 文件路径时 Alembic 无法识别为 SQLAlchemy URL 的问题。
- 修复 reference/local adapter 在取消请求下仍继续执行后续阶段的问题。
- 修复前端对 `cancellation_requested` 仍显示取消按钮、job 详情缺少取消反馈的问题。

### 验证

- `python -m pytest` 通过：39 passed，2 warnings。
- `npm run build` 通过，前端包版本为 `0.4.1`。
- `python -m compileall -q src tests migrations` 通过。

## [0.4.0] - 2026-07-03

### 新增

- 新增流水线 job 阶段日志和产物索引表：`pipeline_job_logs`、`pipeline_artifacts`，job 详情接口会返回日志和产物。
- 新增数据集版本库、模型注册表、发布审批和灰度/回滚记录表及 API：`/api/datasets/versions`、`/api/models/registry`、`/api/releases/approvals`、`/api/deployments/rollouts`。
- 新增 S3/MinIO 对象存储 provider，`SCENARA_MODEL_STORAGE_BACKEND=s3|minio` 时通过可选 `boto3` 上传到 bucket/prefix。
- 新增 PostgreSQL 元数据存储入口，`SCENARA_MODEL_METADATA_DB=postgresql://...` 时通过可选 `psycopg` 使用外部数据库。
- 新增 Alembic 迁移目录和 `scenara-model storage migrate`，支持正式迁移流程和轻量运行时初始化。
- 新增生产框架 adapter 入口：`ultralytics_yolo`、`torchreid`、`torchvision_classifier`、`segmentation_framework`，可通过 argv 外部命令接入真实训练框架。
- 新增前端任务运行详情面板，展示阶段日志、失败原因、产物链接和任务状态。
- 新增 `docs/RELEASE_0.4.0.md`，汇总本次 MLOps、存储、校验和前端增强。

### 变更

- 版本号统一升级到 `0.4.0`，同步 Python 包、运行时 `__version__`、前端包版本和可编辑安装元数据。
- 异步流水线运行会记录 training/export/evaluation/package 阶段事件，并在同步/异步运行后索引 ONNX、导出报告和模型包产物。
- 数据集注册将 `labels` 作为版本元数据，另以 `allowed_labels` 控制 manifest label 白名单，避免把检测标注 URI 误判为类别值。
- `pyproject.toml` 增加 `s3`、`postgres`、`migrations` 可选依赖组，默认安装仍保持轻量。

### 校验增强

- manifest 校验支持最小 split 样本数和可选 label 白名单。
- 模型包 expected output 校验支持 detections/predictions/embedding/mask 结构检查，并校验 expected label 必须存在于 labels.txt。
- 模型卡支持 `metric_thresholds`，可要求指标存在、为数值并达到门槛。
- 新增 S3 key 逃逸、MLOps 元数据、job 日志/产物、manifest 门槛和 expected label 一致性的回归测试。

### 验证

- `.venv\Scripts\python.exe -m pytest` 通过：37 passed。
- `npm run build` 通过，前端包版本为 `0.4.0`。
- `.venv\Scripts\python.exe -m pip check` 和 `python -m pip check` 均通过。
- `npm audit --omit dev` 通过：0 vulnerabilities。
- `.venv\Scripts\python.exe scripts\acceptance_check.py` 通过。
- `docker build -t scenara-model:0.4.0 .` 通过。
- `docker run --rm scenara-model:0.4.0 python -c "from scenara_model import __version__; from scenara_model.api import app; print(__version__, app.title)"` 输出 `0.4.0 Scenara Model`。
- `.venv\Scripts\python.exe -m pip install -e ".[dev]"` 已更新为 `scenara-model==0.4.0`。

### 已知事项

- S3/MinIO、PostgreSQL 和 Alembic 依赖为可选 extra；默认开发环境不会安装这些外部服务依赖。
- 生产框架 adapter 采用命令入口，具体 Ultralytics/TorchReID/TorchVision/MMSegmentation 环境仍需由部署侧安装和配置 argv 命令。

## [0.3.0] - 2026-07-03

### 新增

- 新增流水线异步任务能力：`POST /api/pipelines/run` 支持 `{"async": true}`，并提供 `/api/pipelines/jobs` 的列表、详情、取消和重试接口。
- 新增 `pipeline_jobs` 元数据表，用于记录 queued、running、completed、failed、cancelled 和 cancellation_requested 等任务状态。
- 新增前端流水线任务视图，支持异步启动、轮询任务状态、查看最新结果、取消和重试。
- 新增对象存储抽象与本地实现，上传文件会写入受限本地对象存储根目录，禁止对象 key 逃逸。
- 新增上传大小限制、流水线 worker 数、外部命令超时、外部命令日志长度、对象存储根路径和 shell 命令开关等环境变量。
- 新增项目专用 `.venv` 开发环境流程，`dev` 依赖补充 `pytest-asyncio>=0.25`，用于隔离全局机器学习工具链。
- 新增 `docs/RELEASE_0.3.0.md`，汇总本次安全、任务、存储、验证和环境修复内容。

### 变更

- 版本号统一升级到 `0.3.0`，同步 Python 包、运行时 `__version__` 和前端包版本。
- Dockerfile 默认基础镜像切换为当前网络可访问的华为云 Docker Hub 镜像源；仍保留 `NODE_IMAGE`、`PYTHON_IMAGE` build args，可按部署环境覆盖为官方镜像或企业镜像。
- SQLite 连接策略改为 `MEMORY -> WAL -> DELETE -> OFF` 的 journal mode 逐级回退，并保留 busy timeout、foreign key 和进程内线程锁，避免受限 Windows 环境下产生 WAL 探针残留或初始化失败。
- API 响应模型统一使用 `ApiModel`，规避 Pydantic `model_id` protected namespace warning。
- `/health` 增加存储 URI、pipeline worker、外部 shell 命令开关等运行时信息。
- 前端 API 客户端支持异步流水线返回 `run` 或 `job` 两种结果，并补充任务管理接口类型。
- 测试临时目录统一由 `tests/conftest.py` 的 `workspace_tmp_path` 管理，避免依赖系统 Temp 或 pytest basetemp 权限。
- 验证文档从“直接使用全局 Python”更新为优先使用项目 `.venv`，同时保留全局 Python 验证说明。

### 安全加固

- 前端静态文件 fallback 增加路径边界检查，防止编码后的 `..` 读取工作区或系统文件。
- API 工作区路径解析统一限制在 `SCENARA_MODEL_WORKSPACE` 内，模型包 `model_id` 禁止绝对路径和 `..` 逃逸。
- 本地对象存储对 key 做绝对路径和父目录逃逸校验，写入前确认目标仍在存储根目录内。
- 外部训练/导出/评估命令默认只允许 argv list；字符串 shell 命令默认禁用，需显式设置 `SCENARA_MODEL_ALLOW_SHELL_COMMANDS=true` 才能兼容旧配置。
- 外部命令增加 cwd 边界校验、执行超时和日志截断，导出产物路径也会校验在工作区内。
- 上传接口增加 `SCENARA_MODEL_MAX_UPLOAD_BYTES` 限制，超限时返回 413 并删除部分写入文件。

### 校验增强

- 模型包校验新增 `package.model_outside_package`，拒绝包目录外模型文件。
- 模型包样例校验新增图片签名检查和 expected JSON 根结构检查。
- 模型卡校验新增输入 shape 正整数、layout、dtype、deployment max batch size 和 limitations 列表检查。
- 数据 manifest 校验新增图片扩展名、非空字段、label 类型和 tags 列表检查。
- 交付契约校验新增 input/output/artifact 对象类型、sidecar 引用非空、上线判定列表去重和非空检查。
- 流水线合成样例图片改为写入 JPEG 签名占位内容，避免严格样例校验失败。

### 环境修复

- 创建项目 `.venv` 并安装 `.[dev]`，项目环境 `pip check` 已通过。
- 修复全局 Python 环境冲突：对齐 `protobuf`、`sympy`、`aiosqlite`、`torchaudio`、`torchvision`，并清理损坏的 `~elery` 残留；全局 `python -m pip check` 已通过。
- 清理 Windows 用户代理残留的 `ProxyServer=127.0.0.1:10808` 和 PAC 配置，原值备份到 `artifacts/windows-proxy-before-20260703184731.txt`。
- Docker 默认构建已验证通过，镜像 `scenara-model:0.3.0` 可正常导入 `scenara_model.api`。

### 验证

- `.venv\Scripts\python.exe -m pip check` 通过。
- `python -m pip check` 通过。
- `.venv\Scripts\python.exe -m pytest` 通过：32 passed。
- `python -m pytest` 通过：32 passed。
- `npm run build` 通过。
- `npm audit --omit dev` 通过：0 vulnerabilities。
- `python scripts\acceptance_check.py` 通过。
- `docker build -t scenara-model:0.3.0 .` 通过。
- `docker run --rm scenara-model:0.3.0 python -c "from scenara_model.api import app; print(app.title)"` 输出 `Scenara Model`。

### 已知事项

- 完整 `npm audit` 仍会报告 Vite/esbuild 开发依赖 advisory；生产检查 `npm audit --omit dev` 为 0，最终 Docker 后端镜像不携带 Node 开发依赖。
- 当前目录不是 Git 仓库，无法提供 git tag 或提交记录；版本号已在项目文件中启用。

## [0.2.0] - 2026-06-09

### 新增

- 新增任务适配器注册表，训练、导出和评估脚本不再只支持 `reference_identity`。
- 新增 YOLO 检测、ReID、分类、分割本地基线适配器，均可运行训练记录、ONNX 导出和评估报告流程，并支持执行外部训练、导出、评估命令。
- 新增流水线脚本 `scripts/run_pipeline.py`，支持一键训练、导出、评估和创建标准模型包。
- 新增真实可校验的 `shared-models/cross_camera_tracking/person_detector_yolov8n_v1.0.0_fp32.onnx` 示例模型包。
- 新增 FastAPI 流水线、模型包创建、上传、误差分析、审计事件和适配器接口。
- 新增管理台“流水线”视图，支持启动流水线、创建模型包、上传制品、查看指标、误差分析和审计记录。
- 新增 SQLite 流水线运行表和审计事件表，新增本地对象存储入口与可选 Bearer Token 鉴权。

### 变更

- `configs/experiments/detection_yolo_baseline.yml` 绑定可执行 `detection_yolo_baseline` 适配器。
- `python scripts/validate_model_package.py shared-models --allow-missing-sidecars --allow-missing-examples` 支持递归扫描模型仓库根目录。
- 文档更新为当前能力：内置本地基线适配器，真实生产训练框架仍通过后续适配器或外部命令接入。

### 验证

- `python -m pytest` 通过，当时结果为 23 个测试通过。
- `npm run build` 通过。
- `python scripts/run_pipeline.py --config configs/experiments/detection_yolo_baseline.yml --package` 通过。
- `python scripts/validate_model_package.py shared-models --allow-missing-sidecars --allow-missing-examples` 通过。

### 仍需生产化

- 内置 YOLO/ReID/分类/分割适配器是可运行本地基线；真实训练可先通过外部命令接入，长期仍建议接入 Ultralytics、TorchReID、具体分类/分割框架或内部训练平台的专用适配器。
- 分布式任务队列、PostgreSQL、MinIO/S3 原生后端、统一身份认证和细粒度权限仍需按部署环境接入。

## [0.1.0] - 2026-06-08

### 新增

- 搭建视觉模型研发与交付仓库骨架，覆盖数据清单、标注规范、实验配置、模型评估、ONNX 导出和标准模型包交付。
- 新增 Python 核心包 `scenara_model`，提供模型命名解析、模型卡校验、模型包创建与校验、数据清单校验、交付契约校验和工具函数。
- 新增 FastAPI 管理接口，包含健康检查、模型包扫描、模型包校验、数据清单校验、交付契约校验、实验记录和模板入口。
- 新增 React + TypeScript 管理台，提供概览、模型包、实验和数据标注视图。
- 新增 SQLite 元数据存储，用于记录实验信息和模型包校验历史。
- 新增参考身份模型流程，支持本地训练、导出和评估的最小闭环验证。
- 新增 Dockerfile、docker-compose、运行态检查脚本和离线验收脚本，便于本地和容器化验证。
- 新增生产准备、架构、交付标准和运维文档。

### 变更

- 管理台可见文案统一调整为中文，包括导航、标题、按钮、状态、表头、默认选项和校验结果。
- 默认模型目录、示例数据清单和交付契约模板改为中文选项展示，内部仍使用原路径和契约枚举调用接口。
- OpenAPI 标题和接口描述调整为中文，并和管理台品牌保持一致。
- 运行态检查脚本兼容中文品牌标题，同时保留旧英文标题的兼容判断。
- 管理台网络异常提示收敛为中文，避免直接展示浏览器英文错误。

### 修复

- 修复数据清单和交付契约校验结果中直接透出英文错误消息的问题，改为按错误码展示中文问题和中文处理建议。
- 修复实验页残留英文 `ID` 表头和英文示例值的问题。
- 修复前端默认路径直接暴露在初始界面的问题。

### 验证

- `npm run build` 通过。
- `python -m pytest` 通过，当时结果为 19 个测试通过。
- `python scripts/runtime_check.py --base-url http://127.0.0.1:8080` 通过。
- `python scripts/acceptance_check.py --runtime-base-url http://127.0.0.1:8080` 通过。
- 本地管理台 `http://127.0.0.1:8080/` 已完成浏览器实测，标题和主要可见文案为中文。
- OpenAPI `http://127.0.0.1:8080/docs` 对应的接口标题已更新为 `视觉模型研发平台`。

### 已知事项

- 元数据存储当时使用 SQLite；生产多实例部署前建议迁移到 PostgreSQL 或内部统一元数据服务。
- 大模型文件和数据集仍建议放在 NAS、MinIO、S3 或本地挂载目录，不进入 Git 仓库。
- CLI 文档当时仍以开发者命令使用为主，后续如面向非开发用户开放，可继续补中文帮助文本。






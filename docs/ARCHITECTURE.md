# 架构设计

## 分层

```text
frontend/
  React + TypeScript 管理台
  流水线、任务详情、模型包、实验、数据与契约入口

FastAPI management API
  包扫描、校验、manifest 校验、实验记录、异步流水线、任务日志、产物索引、上传、误差分析、MLOps 记录、审计、模板入口

Python core package
  命名规则、模型卡、模型包、数据 manifest、ONNX 检查、任务适配器、对象存储 provider、元数据存储

Storage
  SQLite 默认，PostgreSQL 可选
  local / S3 / MinIO 对象存储
  shared-models / artifacts / data manifests
```

## 边界

算法侧负责：

- 数据 manifest、数据集版本和质量记录。
- 标注规范和质检报告。
- 训练、评估、导出入口。
- YOLO 检测、ReID、分类、分割本地基线适配器；生产环境可通过 `ultralytics_yolo`、`torchreid`、`torchvision_classifier`、`segmentation_framework` adapter 配置外部 argv 命令接入真实框架。
- ONNX 文件和标准模型包。
- 模型卡、labels、样例、expected 输出。
- 模型注册、发布审批和灰度/回滚记录。

推理服务负责：

- 在线 API。
- 模型缓存、灰度、回滚执行。
- 鉴权、监控、业务适配。
- 基于 `models.yml` 的运行时配置。

## API

- `GET /health`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `GET /api/packages/scan`
- `POST /api/packages/validate`
- `GET /api/package-validations`
- `POST /api/manifests/validate`
- `POST /api/contracts/validate`
- `GET /api/experiments`
- `POST /api/experiments`
- `GET /api/templates`
- `GET /api/adapters`
- `POST /api/pipelines/run`
- `GET /api/pipelines/runs`
- `GET /api/pipelines/jobs`
- `GET /api/pipelines/jobs/{job_id}`
- `GET /api/pipelines/jobs/{job_id}/logs`
- `GET /api/pipelines/jobs/{job_id}/artifacts`
- `POST /api/pipelines/jobs/{job_id}/cancel`
- `POST /api/pipelines/jobs/{job_id}/retry`
- `GET /api/pipelines/artifacts/{artifact_id}/download`
- `POST /api/packages/create`
- `POST /api/uploads`
- `POST /api/error-analysis`
- `GET /api/audit-events`
- `GET|POST /api/datasets/versions`
- `GET|POST /api/models/registry`
- `GET|POST /api/releases/approvals`
- `GET|POST /api/deployments/rollouts`

## 外部命令适配器契约

`training` / `export` / `evaluation` 三个阶段均支持 `command`（argv 列表；字符串 shell 命令默认禁用，需 `SCENARA_MODEL_ALLOW_SHELL_COMMANDS=true`）与 `command_cwd`（限定在工作区内）。关键产物契约：

- `export.produced_onnx`：外部导出命令产出的 ONNX 路径（经工作区边界校验）。命令成功后平台将其复制到目标产物路径并立即执行加载校验；**外部导出结果绝不会被合成基线模型覆盖**，报告中的 `onnx_source` 字段标识来源（`external_command` / `synthetic_baseline` / `reused`）。
- `export.reuse_existing: true`：显式声明才复用已存在的可加载 ONNX；默认每次导出都重新生成，避免改配置重跑后静默交付旧模型。
- `evaluation.produced_metrics`：外部评估命令输出的 JSON 指标文件路径。评估成功后平台回读该文件作为真实指标并标注 `metrics_source: measured`；声明了该字段但回读失败时评估阶段直接失败。未配置外部评估时，`expected_metrics` 会被标注为 `metrics_source: declared`（自报值），发布审批链路应据此区分指标可信度。

## 生产训练门禁

流水线的 `package.profile` 默认为 `production`。生产包必须同时满足：

- training/export/evaluation 三个阶段均由外部命令完成；训练命令必须声明并新生成 `training.produced_checkpoint`，导出命令必须声明并新生成 `export.produced_onnx`，评估命令必须声明并新生成 `evaluation.produced_metrics`。
- `dataset.train_manifest`、`dataset.val_manifest`、`dataset.test_manifest` 存在且通过 manifest 校验；`package.examples_dir` 必须提供真实样例和期望输出。
- `package.metric_thresholds` 非空且所有实测指标达标；指标不足或低于阈值时禁止打包。
- 自动生成的模型卡会记录 checkpoint/ONNX/metrics 的 sha256、来源、实验 ID、配置路径和实测指标；模型卡与 ONNX 的输入 shape/dtype 不一致时禁止打包。

开发和 CI 的合成 baseline 必须显式设置 `package.profile: smoke`。smoke 包可以用于验证编排和 ONNX Runtime 加载，但模型卡会标记为不可发布，模型注册接口也只允许注册到 `smoke` stage。

运行时保障：外部命令 stdout/stderr 由 reader 线程逐行消费（无管道死锁），按条数或时间批量写入任务日志；阶段事件和任务退出前强制 flush。取消检查按 3 秒窗口节流并同时续写 worker 心跳；取消/超时会终止整棵进程树；子进程使用剥离平台数据库、鉴权、管理员口令和对象存储凭证后的最小环境；输出以 UTF-8 解码。

`training.preflight_command` 可在训练命令前验证框架、驱动和 GPU。相同 experiment ID 同时受进程内锁和跨进程文件锁保护，CLI、同步 API 与异步 worker 不能并发覆盖同一产物目录。阶段报告与 YAML/JSON 元数据通过临时文件原子替换，进程中断不会留下半写文件。

## 任务归属与维护

- worker 以“主机名 + 进程 ID”标识，原子认领 queued 任务并把 `worker_id`、`heartbeat_at` 写入元数据。
- 服务启动时只回收本 worker 遗留、无归属、无心跳或心跳超时的任务，不影响其他实例心跳正常的运行中任务。
- 周期维护清理过期认证会话、超期任务日志和审计事件；保留期与维护间隔由环境变量配置。
- SQLite 适合单实例；多实例使用 PostgreSQL 时仍由数据库状态守卫保证只有一个 worker 能从 queued 认领任务。

## 扩展边界

- SQLite/local store 适合单机开发和离线验收；多实例生产部署建议切 PostgreSQL 和 S3/MinIO。
- 当前 worker 是进程内线程池；如果需要跨机器调度、优先级队列或 GPU 资源编排，可继续接 RQ、Celery、Kubernetes Job 或内部任务平台。
- 生产 adapter 只提供平台入口和安全执行约束，具体训练框架、模型代码、GPU 调度和依赖镜像由部署环境提供。

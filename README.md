# Scenara Model

当前版本：`1.0.0-dev.2`。完整变更见 [CHANGELOG.md](CHANGELOG.md)，迁移说明见 [docs/RELEASE_1.0.0-dev.2.md](docs/RELEASE_1.0.0-dev.2.md)。

`scenara-model` 是景枢模型平台的责任仓库，负责实验、训练任务、模型评估、模型版本、模型注册和不可变 Model Package 发布。训练数据只引用 `scenara-data` 发布的不可变 Dataset Version；生产准入、激活、流量切换和回滚由 `scenara` 负责。

当前成熟度为 `seed`。仓库由 `vision-model-lab` 迁移而来，已有训练与制品能力可以继续验证，但共享 IAM、统一 Console 和正式跨仓库契约接入尚未完成，因此不得标记为 `production_ready`。责任团队为 Scenara Model，迁移门禁和临时例外见 [ADR-0001](docs/adr/0001-repository-rename-and-control-plane-transition.md)。

当前未完成项和验收顺序见 [剩余任务计划](docs/REMAINING_TASK_PLAN.md)。

## 当前能力

- 标准模型包创建与校验。
- 模型命名、模型卡、labels、样例、sha256 校验。
- JSONL 数据 manifest 校验。
- FastAPI 管理接口。
- 迁移期 React/TypeScript 管理台源码，部署默认禁用并冻结新增业务；本地 `python start.py` 会为一键体验自动构建并托管，正式入口必须接入 `scenara` 统一 Console。
- YOLO 检测、ReID、分类、分割的本地基线适配器；配置 `training.command`、`export.command`、`evaluation.command` 后会执行真实外部框架命令并记录日志。baseline 仅用于 smoke，生产包默认要求真实 checkpoint、ONNX、实测指标、样例和阈值。
- 实验记录、流水线运行、任务日志、产物索引、模型包校验、Dataset Version 引用、模型注册和模型制品发布的元数据存储。
- local/S3/MinIO 对象存储入口、上传接口和误差样本摘要。
- 迁移期本地鉴权与静态令牌兼容实现；正式部署前必须替换为 Core 信任的短期服务凭据和统一权限 ID。

## 一键启动

任意平台在项目根目录执行：

```powershell
python start.py
```

脚本会读取 `.env`（首次启动自动从 `.env.example` 复制），创建或复用 `.venv`，安装 Python 依赖，安装/构建迁移期前端，初始化元数据存储并启动后端 API。默认根路径会托管本地管理台；仅需后端时使用 `--backend-only`。

常用参数：

```powershell
python start.py --port 8080
python start.py --skip-install
python start.py --backend-only
```

启动后访问：

- 迁移期管理台：`http://127.0.0.1:8080/`
- 接口文档：`http://127.0.0.1:8080/docs`
- 健康检查：`http://127.0.0.1:8080/health`
- 统一 Console：由 `scenara` 提供；迁移期本地管理台仅用于当前过渡阶段。

## 快速命令

```powershell
python -m pytest
python scripts/prepare_dataset.py data/manifests/example_train_v1.jsonl
python scripts/validate_contract.py models-fragment configs/export/models.fragment.template.yml
python scripts/validate_contract.py release-decision configs/export/release-decision.template.yml
python scripts/train.py --config configs/experiments/reference_identity.yml
python scripts/export_onnx.py --config configs/experiments/reference_identity.yml
python scripts/evaluate.py --config configs/experiments/reference_identity.yml
python scripts/run_pipeline.py --config configs/experiments/detection_yolo_baseline.yml --package
python scripts/train.py --config configs/experiments/detection_ultralytics_external.yml
python scripts/validate_model_package.py shared-models --allow-missing-sidecars --allow-missing-examples
python scripts/serve_api.py --host 127.0.0.1 --port 8080
python scripts/runtime_check.py --base-url http://127.0.0.1:8080
```

安装为本地开发包后也可以使用：

```powershell
pip install -e .[dev]
scenara-model --help
```

## 标准模型包

```text
shared-models/
  <project_name>/
    <artifact_name>.onnx
    <artifact_name>.model-card.yml
    <artifact_name>.labels.txt
    <artifact_name>.examples/
      frame_001.jpg
      frame_001.expected.json
```

模型文件命名：

```text
<task>_<architecture>_v<semver>_<precision>.onnx
```

示例：

```text
person_detector_yolov8n_v1.0.0_fp32.onnx
person_reid_osnet_ibn_x1_0_v1.1.0_fp32.onnx
defect_classifier_resnet50_v2.0.0_fp16.onnx
```

## 前后端设计

- 核心语言：Python 3.11+。
- API：FastAPI。
- 元数据：SQLite 默认，PostgreSQL 可选。
- 前端：React + TypeScript + Vite。
- 大文件：NAS、MinIO、S3 或本地挂载目录，不提交 Git；当前内置 local/S3/MinIO 对象存储 provider。

更多设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)、[docs/DELIVERY_STANDARD.md](docs/DELIVERY_STANDARD.md) 和 [docs/OPERATIONS.md](docs/OPERATIONS.md)。

生产部署和验收见 [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md)。

版本变更见 [CHANGELOG.md](CHANGELOG.md)。

启动后访问：

- 迁移期管理台：`http://127.0.0.1:8080/`
- 接口文档：`http://127.0.0.1:8080/docs`
- 健康检查：`http://127.0.0.1:8080/health`
- 统一 Console：由 `scenara` 提供；迁移期本地管理台仅用于当前过渡阶段。

## 近期工程化增强

- API 静态文件回退和模型包 `model_id` 已增加路径边界校验，防止读取工作区或包目录外文件。
- 流水线支持异步 job：`POST /api/pipelines/run` 可传 `{"async": true}`，并通过 `/api/pipelines/jobs` 查询、取消或重试。
- 外部训练命令默认禁用 shell 字符串，只允许 argv list；可通过 `SCENARA_MODEL_ALLOW_SHELL_COMMANDS` 显式兼容旧配置。
- 本地对象存储、上传大小、pipeline worker 数、外部命令超时和日志长度均可通过环境变量配置。
- SQLite 元数据存储使用 journal mode 逐级回退、busy timeout 和线程锁；多实例生产部署仍建议迁移 PostgreSQL。
## 0.3.0 环境与发布说明

- 推荐使用项目虚拟环境：`.\.venv\Scripts\python.exe -m pip check` 和 `.\.venv\Scripts\python.exe -m pytest` 均已验证通过。
- 全局 Python 环境依赖冲突已修复，`python -m pip check` 当前通过。
- Dockerfile 默认基础镜像使用已验证可访问的镜像源；仍可通过 `NODE_IMAGE`、`PYTHON_IMAGE` build args 覆盖。
- Windows 代理残留已备份到 `artifacts/windows-proxy-before-20260703184731.txt` 后清理，Docker 默认构建已通过。

## 0.4.0 MLOps 与存储扩展

- 流水线 job 会记录阶段日志和产物索引，前端“运行详情”可查看日志、错误和产物链接。
- 新增数据集版本、模型注册、发布审批和灰度/回滚 API，支撑长期 MLOps 流程。
- `SCENARA_MODEL_STORAGE_BACKEND` 支持 `local`、`s3`、`minio`；S3/MinIO 需要安装 `scenara-model[s3]`。
- `SCENARA_MODEL_METADATA_DB` 支持 SQLite 路径和 PostgreSQL DSN；PostgreSQL 需要安装 `scenara-model[postgres]`。
- 新增 Alembic 迁移目录；轻量环境可执行 `scenara-model storage migrate`，正式环境可执行 `alembic upgrade head`。
- 新增生产框架 adapter 入口，部署侧配置 argv 命令后可接入 Ultralytics、TorchReID、TorchVision 或分割框架。

## 0.4.1 取消闭环与稳定性补丁

- 流水线 job 支持从 `cancellation_requested` 到 `cancelled` 的完整取消闭环，会记录取消时间、取消阶段、取消原因和已生成产物。
- 管理台已补齐 `queued`、`cancelled`、`cancellation_requested` 中文状态，job 详情可直接查看取消反馈。
- Alembic baseline 迁移已补齐核心表结构，`SCENARA_MODEL_METADATA_DB` 支持普通 SQLite 文件路径。
- 模型包扫描会在超过上限后提前停止，避免大目录一次性排序扫描。

# Vision Model Lab 0.8.0 发布说明

发布日期：2026-07-28

## 发布主题

`0.8.0` 将训练平台的核心交付链路升级为生产制品流程：训练、导出、评估、模型卡、模型包、注册和发布审批现在共享同一条可验证的信任链。开发用 smoke baseline 与真实训练产物明确分离，缺少真实证据时不会生成可发布模型。

## 主要变化

### 真实训练与制品

- 生产 adapter 强制要求真实 train/val/test manifest、外部 checkpoint、外部 ONNX 和实测 metrics。
- 训练 runtime preflight 检查训练框架、Torch、CUDA 和 GPU 设备，并在任务启动前返回结构化失败原因。
- 外部命令的 checkpoint、ONNX、metrics 增加 SHA256、mtime 和本次运行新鲜度校验，阻止复用旧文件伪装为本次结果。
- 本地 manifest 支持检查图像文件存在性；Ultralytics 示例补齐数据布局、训练、导出和评估命令。

### 模型卡与发布门禁

- 自动模型卡记录数据集版本、输入契约、实测指标、阈值、代码 revision、配置哈希、训练命令和产物来源。
- 生产模型包严格校验 ONNX、输入 shape/dtype、SHA256、labels、examples 和模型卡溯源，所有打包写入采用原子替换。
- 模型注册阶段收紧为 `smoke`、`candidate`、`staging`、`production`；smoke 模型不能审批或 rollout。
- staging/production rollout 要求已注册模型、已批准发布和 rollback target；production 额外拒绝 smoke 来源。

### 并发与工程质量

- 同一 experiment ID 增加跨线程、跨进程互斥，避免重复任务互相覆盖产物。
- CLI 增加 `--strict-provenance` 和 `--check-local-files`。
- 修复 Alembic、启动脚本和测试的静态类型问题，统一版本检查和严格验收脚本。
- Python、运行时、前端 package 与 lockfile 统一升级至 `0.8.0`。

## 升级步骤

```powershell
python -m pip install -e ".[dev]"
python -m pip check
python -m alembic upgrade head
python scripts/check_versions.py
```

生产训练前准备真实数据并校验运行环境：

```powershell
python scripts/prepare_dataset.py data/manifests/person_detection_train_v1.jsonl --json
python scripts/examples/check_training_runtime.py --require-module ultralytics --require-cuda
python scripts/run_pipeline.py --config configs/experiments/detection_ultralytics_external.yml --package
```

## 验证结果

- 89 项 Python 测试通过。
- Ruff 通过。
- Pyright：0 errors。
- 前端 TypeScript/Vite 生产构建通过。
- 离线 acceptance check 通过。
- `pip check` 无依赖冲突。

## 已知边界

本次发布没有内置真实业务数据或训练权重。当前开发机使用 CPU-only Torch，且没有业务数据 manifest/图像，因此未执行真实 GPU 训练；生产配置会在 preflight 或 manifest 阶段明确拒绝，而不是生成伪生产模型。

生产部署仍需提供训练框架环境、CUDA/GPU、真实数据、对象存储和 PostgreSQL（多实例场景），并由业务方确认指标阈值、失败模式和发布审批结果。

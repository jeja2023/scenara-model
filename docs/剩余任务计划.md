# scenara-model 剩余任务计划

**适用规范：** `景枢平台总体开发规范.md` 1.3.0

**当前版本：** `1.0.0-dev.3`

**当前成熟度：** `seed`

## 已完成基线

- Training Job、Experiment、异步流水线、任务日志、产物索引和取消/重试状态机。
- `dataset-version-input` 引用校验：授权、版本、UTC RFC3339 时间和 manifest SHA-256；ReID 已适配 `scenara.portrait.surveillance-review.v1` 布控误报复核数据集。
- 训练/导出/评估 Adapter、模型卡、Model Package、Registry、Release Approval 和 Rollout API。
- 本地 SQLite/PostgreSQL 元数据边界、local/S3/MinIO 对象存储入口、认证和审计。

## 剩余交付

| 编号 | 状态 | 任务 | 责任边界 | 验收证据 |
| --- | --- | --- | --- | --- |
| MODEL-P1 | implemented | 将现有领域 API 接入 Core 统一 IAM/服务凭据和公共 Console 代理 | Model + Core | 权限透传、审计关联、公共路径兼容测试 |
| MODEL-P2 | seed | 接入 FastReID 适配器并固定代码、环境、数据集和检查点摘要 | Model | 可复现实验日志、配置、checkpoint SHA-256 |
| MODEL-P3 | implemented | 完成通用 Model Package、评估和发布审批本地闭环 | Model | 100 项本地回归、ModelPackageManifest、模型卡和样例 |
| MODEL-P4 | planned | 消费 `deployment-feedback`，验证签名、event_id 幂等和状态重放 | Model + Core + Contracts | webhook 兼容、重放拒绝、审计和回滚测试 |
| MODEL-P5 | planned | 固定 ReID Rank-1/5/10、mAP 评估集及模型权属/许可证清单 | Model + 业务方 | 固定数据集摘要、评估报告、权属原件 |
| MODEL-P6 | planned | PostgreSQL/S3 目标环境、恢复、GPU 和离线安装资格 | Model + Deploy | 容量、恢复、离线安装和依赖清单报告 |

## 执行顺序

```text
MODEL-P1 -> MODEL-P2 -> MODEL-P5
MODEL-P3 -> MODEL-P4
MODEL-P5 + MODEL-P6 -> qualified
```

本地 Smoke Adapter 只能证明流水线连通性，不能替代 FastReID、真实指标、模型权属或目标硬件证据。未完成上述外部证据前保持 `seed`。

```powershell
.\.venv\Scripts\python.exe -m pytest -rA
.\.venv\Scripts\python.exe scripts\acceptance_check.py --skip-pytest
```

# Scenara Model 0.4.1 发布说明

发布日期：2026-07-13

## 发布主题

`0.4.1` 是 0.4 系列的稳定性和可观测性补丁版本，重点补齐异步流水线取消闭环、迁移基线一致性、大目录扫描保护，以及管理台对取消状态的中文反馈。

## 主要改进

- 流水线训练、导出、评估和打包阶段支持取消检查，外部命令会在收到取消请求后终止子进程并返回 `cancelled` 结果。
- 异步 job 会区分 `cancellation_requested` 和 `cancelled`，最终写入取消审计事件、取消时间、取消阶段、取消原因和已生成产物索引。
- 同步流水线运行也会在失败或取消时记录更准确的审计动作，便于追踪实际结果。
- 管理台补齐 `queued`、`cancelled`、`cancellation_requested` 中文状态，job 详情展示取消提示、取消时间、取消阶段和取消原因。
- 状态徽章新增进行中和中性样式，让“取消中”和“已取消”不再被误显示为普通失败。
- 模型包扫描改为增量收集 ONNX 文件，超过 `SCENARA_MODEL_MAX_PACKAGE_SCAN_FILES` 后提前停止，避免大目录一次性排序扫描。
- Alembic baseline 迁移补齐核心元数据表，并支持 `SCENARA_MODEL_METADATA_DB` 使用普通 SQLite 文件路径。

## 兼容性

- 这是补丁版本，无破坏性 API 变更。
- `PipelineRunRecord.report` 和 job result 现在显式包含 `cancelled_stage`、`cancelled_reason` 和 `artifacts` 字段，旧客户端可继续忽略这些字段。
- 原有 `completed`、`failed`、`running` 等状态语义保持不变。

## 验证结果

- `python -m pytest`：39 passed，2 warnings。
- `npm run build`：通过，前端包版本为 `0.4.1`。
- `python -m compileall -q src tests migrations`：通过。

## 升级建议

- 推荐从 `0.4.0` 直接升级到 `0.4.1`。
- 使用 Alembic 的环境建议重新执行 `alembic upgrade head` 验证 baseline 能在空库上创建完整表结构。
- 使用异步流水线的环境建议在管理台验证一次取消流程，确认 job 从“取消中”进入“已取消”并记录取消详情。

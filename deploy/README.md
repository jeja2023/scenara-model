# 部署说明

当前部署入口仍为根目录 `Dockerfile` 与 Compose 文件。默认只启动模型领域 API，`SCENARA_MODEL_SERVE_FRONTEND=false`；统一 Console 由 `scenara` 提供。

生产部署前必须完成 Core IAM/权限透传、PostgreSQL、Redis、S3-compatible Provider、备份恢复、安全扫描和契约兼容门禁。当前成熟度为 `seed`，不得作为生产就绪声明。

FastReID 使用独立的 Linux GPU 运行时：`deploy/training/Dockerfile.fastreid` 和 `deploy/training/docker-compose.fastreid.yml`。构建、GPU 预检和 manifest 物化步骤见 [FastReID训练运行手册](../docs/FastReID训练运行手册.md)。

PostgreSQL + S3/MinIO 的实机资格脚本为 `scripts/qualify_target_environment.py`。完成不可变备份和恢复演练使用 `scripts/backup_postgres.py` 与 `scripts/restore_postgres.py`；恢复脚本只能指向明确批准的恢复目标，并要求 archive SHA-256 和显式确认开关。

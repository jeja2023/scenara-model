# 部署说明

当前部署入口仍为根目录 `Dockerfile` 与 Compose 文件。默认只启动模型领域 API，`SCENARA_MODEL_SERVE_FRONTEND=false`；统一 Console 由 `scenara` 提供。

生产模式必须设置 `SCENARA_MODEL_DEPLOYMENT_PROFILE=production`、`SCENARA_MODEL_AUTH_MODE=core`、Core 签发的 `SCENARA_MODEL_SERVICE_TOKEN` 和独立的 `SCENARA_MODEL_CONTEXT_SIGNING_KEY`。该模式不创建或接受本地用户名密码登录，Model 只验证 Core 透传的短时身份上下文；`local` 仅用于迁移期开发。

生产基线见 `deploy/compose.production.yml`。Model 还需要独立的 Data 服务凭据和 Data 上下文签名密钥，以读取 `scenara-data` 发布的 Dataset Version；所有凭据使用外部 secret 挂载，不写入 Compose 文件。标准生产使用镜像 digest；当前单机生产可使用 Core 仓库脚本按完整 Git commit SHA 本地构建 `scenara-model:<commit>`。

当前测试数据环境可使用 `deploy/compose.shared-test.yml`。它只启动 Model API 和迁移任务，连接 Core 仓库 `deploy/shared-infra/compose.yml` 提供的共享 PostgreSQL、Redis、MinIO 和平台网络；Model 使用独立的 `scenara_model` 数据库及 `scenara-model-artifacts` bucket。

单机内网生产使用 Core 仓库 `deploy/shared-infra/compose.tls.yml` 提供的内部 CA/TLS，Model 通过 `model.scenara.internal` 和 `data.scenara.internal` 访问内部服务，不需要公网域名。

生产部署前必须完成 Core IAM/权限透传、PostgreSQL、Redis、S3-compatible Provider、备份恢复、安全扫描和契约兼容门禁。当前成熟度为 `seed`，不得作为生产就绪声明。

FastReID 使用独立的 Linux GPU 运行时：`deploy/training/Dockerfile.fastreid` 和 `deploy/training/docker-compose.fastreid.yml`。构建、GPU 预检和 manifest 物化步骤见 [FastReID训练运行手册](../docs/FastReID训练运行手册.md)。

PostgreSQL + S3/MinIO 的实机资格脚本为 `scripts/qualify_target_environment.py`。完成不可变备份和恢复演练使用 `scripts/backup_postgres.py` 与 `scripts/restore_postgres.py`；恢复脚本只能指向明确批准的恢复目标，并要求 archive SHA-256 和显式确认开关。

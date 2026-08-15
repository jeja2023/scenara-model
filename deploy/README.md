# 部署说明

当前部署入口仍为根目录 `Dockerfile` 与 Compose 文件。默认只启动模型领域 API，`SCENARA_MODEL_SERVE_FRONTEND=false`；统一 Console 由 `scenara` 提供。

生产部署前必须完成 Core IAM/权限透传、PostgreSQL、Redis、S3-compatible Provider、备份恢复、安全扫描和契约兼容门禁。当前成熟度为 `seed`，不得作为生产就绪声明。

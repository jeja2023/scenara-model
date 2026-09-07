# API 文档

现有 FastAPI 路由源自迁移前仓库，当前作为 `seed` 兼容接口保留。正式跨仓库 API 必须统一使用 `/api/v1/`，由 `scenara-contracts` 定义请求、响应、错误、认证、权限、幂等和版本，并通过 Core 网关或明确的服务间 API 暴露。

在完成契约迁移前，不得把现有内部路由视为稳定公共接口。统一 Console 由 Core 提供，模型服务只保留领域 API 和后台任务。

`deployment-feedback` 已使用版本化路径 `POST /api/v1/deployment-feedback` 消费 Core 的 `model.deployment.changed` 签名 Webhook。该端点不接受本地登录令牌替代签名；协议、幂等语义和配置见 [部署反馈消费说明](部署反馈消费说明.md)。

生产训练输入可通过 `POST /api/datasets/versions` 提交 `data_version_id`，Model 会使用 Core 委托身份从独立 `scenara-data` 读取已发布 Dataset Version 的引用和不可变清单，并在落盘前校验 `manifest_sha256`；生产模式不接受仅靠本地文件路径伪造的远程数据版本。

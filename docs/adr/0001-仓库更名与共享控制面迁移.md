# ADR-0001：仓库更名与共享控制面迁移

- 状态：Accepted，临时例外待移除
- 日期：2026-08-15
- 负责人：Scenara Model
- 复审日期：2026-09-15

## 决策

原 `vision-model-lab` 仓库迁移为 `scenara-model`。正式包、CLI、镜像、服务、配置和文档统一使用 `scenara-model`；原远程仓库仅作为 `upstream` 保留来源历史。

模型平台只拥有实验、训练任务、评估、模型版本、模型注册和不可变模型制品。Dataset Version 来自 `scenara-data`，生产准入、激活和回滚由 `scenara` 执行，跨仓库数据只通过已发布契约、API、事件和对象引用传递。

## 临时例外

- 适用范围：现有 `frontend/` 和本地用户名密码认证代码。
- 原因：保留上游 `0.8.0` 的本地验证入口，避免仓库迁移同时重写全部控制面。
- 风险：形成第二套 Console、IAM 和审计入口，产生权限与契约漂移。
- 控制：独立前端默认禁用；不得新增业务页面；本地账号仅用于开发验证；发布文档必须标记为 `seed`。
- 移除条件：Core 完成统一路由、短期服务凭据、权限透传、审计回传和 Model 页面接入后，删除本地登录入口及独立前端发布路径。

## 退出门禁

1. 使用 Core 签发或信任的短期服务凭据。
2. 透传 organization/project/principal/request/trace/idempotency 上下文并独立鉴权。
3. 通过 `dataset-version-input` 和 `deployment-feedback` 消费方兼容测试。
4. 在统一 Console 完成模型域页面并移除独立前端构建发布。
5. 记录迁移、回滚、安全和端到端证据后再提升成熟度。

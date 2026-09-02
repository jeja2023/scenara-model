# Scenara Model 1.0.0.dev0 迁移说明

本开发版本完成仓库和正式标识迁移，来源基线为 `vision-model-lab` `0.8.0`（上游提交 `fe3f4b2`）。它用于继续整理模型训练、评估、注册和不可变制品能力，不是生产就绪发布。

## 已完成

- 仓库、Python 包、CLI、镜像、服务和环境变量统一更名。
- 保留上游 Git 历史，并将旧远程改名为 `upstream`，避免误推送。
- 锁定跨仓库契约包 `@scenara/repository-contracts` `1.0.0`。
- 默认关闭迁移期独立前端，并记录统一 Console/IAM 的退出门禁。

## 未完成门禁

- Core 统一身份、权限、审计和 Console 接入。
- Dataset Version 正式消费方契约测试。
- FastReID Adapter、Rank-1/5/10、mAP 和固定评估集资格验证。
- Model Package 到 Core 准入、加载、激活和回滚的跨仓库端到端测试。

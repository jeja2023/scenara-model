# 开发规范

模型平台按 Experiment、Training Job、Evaluation、Model Version、Registry 和 Model Package 划分领域。第三方训练框架必须经 Adapter 接入；业务层不得直接绑定 FastReID、PyTorch、ONNX Runtime、数据库或对象存储实现。

训练只消费已发布 Dataset Version。跨仓库 API、事件和 Manifest 先在 `scenara-contracts` 发布，客户端显式配置认证、超时、重试、幂等、熔断、追踪和错误映射。

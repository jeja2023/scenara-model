# 数据模型文档

模型平台拥有 Experiment、Training Job、Evaluation、Model、Model Version、Model Checkpoint 和 Model Package。Model Version 发布后不可覆盖，并绑定 Dataset Version、训练配置、代码版本、运行环境、评估结果和模型文件。

Model Package 至少包含模型、模型卡、输入/输出定义、元数据、标签和校验值，并以 SHA-256 不可变对象引用交付 Core。生产激活、流量切换和回滚不属于本仓库。

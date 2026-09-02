# 测试文档

```powershell
.\.venv\Scripts\python.exe scripts\check_versions.py
.\.venv\Scripts\python.exe -m ruff check src scripts tests migrations
.\.venv\Scripts\python.exe -m pytest
```

当前测试覆盖本地训练/导出/评估适配器、模型包、元数据存储、鉴权和 API。正式资格验证还需 FastReID 固定评估集、Rank-1/5/10、mAP、真实 PostgreSQL/S3、恢复测试和 Model Package 到 Core 的端到端证据。

`1.0.0-dev.3` 的基线另覆盖 Contracts 摘要锁定、OCR/Behavior/Fashion Dataset Version 领域/标注模式匹配、生产适配器失败关闭，以及多文件 bundle 的路径、大小和 SHA-256 校验。当前工作区已升级到 Contracts `1.2.0`，额外覆盖人像 ReID 对 `scenara.portrait.surveillance-review.v1` 布控误报复核数据集和 bundle 的校验。

# 测试文档

```powershell
.\.venv\Scripts\python.exe scripts\check_versions.py
.\.venv\Scripts\python.exe -m ruff check src scripts tests migrations
.\.venv\Scripts\python.exe -m pytest
```

当前测试覆盖本地训练/导出/评估适配器、模型包、元数据存储、鉴权和 API。正式资格验证还需 FastReID 固定评估集、Rank-1/5/10、mAP、真实 PostgreSQL/S3、恢复测试和 Model Package 到 Core 的端到端证据。

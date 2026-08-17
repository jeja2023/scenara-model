# Scenara Model 1.0.0.dev1 更新说明

本开发版本修正本地启动体验和前端依赖安全告警，仍处于 `seed` 阶段。正式 Console、IAM 和跨仓库生产门禁仍以 `scenara` 为准。

## 已完成

- `start.py` 默认执行本地一键启动：准备 Python 环境、安装 Python 依赖、初始化元数据存储、安装/构建迁移期前端，并由后端托管根路径。
- 保留 `python start.py --backend-only`，用于只启动领域 API、不构建或托管迁移期前端的场景。
- 后端静态前端开关默认值与 `.env.example`、Docker 和 Compose 保持一致：`SCENARA_MODEL_SERVE_FRONTEND=false`。一键启动需要前端时由 `start.py` 显式启用。
- 启动日志会按实际可用状态打印入口，避免在前端未启用或构建产物缺失时提示会返回 404 的根路径。
- 前端构建链升级到 `vite@8.2.1` 与 `@vitejs/plugin-react@6.0.5`，并声明 Node 版本要求 `^20.19.0 || >=22.12.0`。
- 清理 `npm audit` 报告中的 Vite、esbuild、PostCSS、nanoid 相关安全告警。

## 使用方式

```powershell
python start.py
```

启动后默认访问：

- 迁移期管理台：`http://127.0.0.1:8080/`
- 接口文档：`http://127.0.0.1:8080/docs`
- 健康检查：`http://127.0.0.1:8080/health`

仅启动后端：

```powershell
python start.py --backend-only
```

## 验证

- `python scripts/check_versions.py`
- `npm ci`
- `npm audit --json`
- `npm run build`
- `python -m pytest tests/test_start_entry.py tests/test_start_model.py tests/test_settings.py tests/test_api.py::test_frontend_fallback_does_not_serve_workspace_files`
- `python -m ruff check start.py scripts/start_model.py src/scenara_model/settings.py tests/test_start_entry.py tests/test_start_model.py tests/test_settings.py`
- `python -m pyright start.py scripts/start_model.py src/scenara_model/settings.py tests/test_start_entry.py tests/test_start_model.py tests/test_settings.py`

## 仍未完成

- Core 统一身份、权限、审计和 Console 接入。
- Dataset Version 正式消费方契约测试。
- Model Package 到 Core 准入、加载、激活和回滚的跨仓库端到端测试。

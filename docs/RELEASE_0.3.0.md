# Scenara Model 0.3.0 发布说明

发布日期：2026-07-03

## 发布主题

`0.3.0` 是一次工程化和运行安全版本，重点补齐异步流水线任务、安全边界、SQLite 兼容性、模型包/数据契约校验、前端任务管理、测试隔离、Docker 构建和 Python 环境治理。

## 主要能力

- 流水线从同步执行扩展为同步/异步两种模式，异步 job 可查询、取消和重试。
- 管理台增加任务状态列表和操作入口，适合长时间训练/导出/评估流程。
- 上传、对象存储、模型包扫描、外部命令执行均增加显式限制和边界检查。
- 模型包、模型卡、manifest 和交付契约校验更严格，能更早发现交付包缺陷。
- Docker 默认构建不再依赖当前失效的 Docker Hub 直连路径，可通过镜像源完成构建。
- 项目 `.venv` 和全局 Python 环境均已通过 `pip check`。

## API 与任务运行

- `POST /api/pipelines/run` 默认保持同步行为。
- 请求体传入 `{"async": true}` 时返回 `job`，后台线程池执行流水线。
- 新增接口：
  - `GET /api/pipelines/jobs`
  - `GET /api/pipelines/jobs/{job_id}`
  - `POST /api/pipelines/jobs/{job_id}/cancel`
  - `POST /api/pipelines/jobs/{job_id}/retry`
- `/health` 返回 `storage_uri`、`pipeline_workers`、`external_shell_commands_allowed` 等运行时信息。

## 安全与边界

- 工作区路径、前端 fallback、模型包 `model_id`、对象存储 key、外部命令 cwd 和导出产物路径均校验不能逃逸允许范围。
- shell 字符串命令默认禁用，推荐使用 argv list。
- 上传大小由 `SCENARA_MODEL_MAX_UPLOAD_BYTES` 控制，默认 500 MiB。
- 外部命令超时由 `SCENARA_MODEL_EXTERNAL_COMMAND_TIMEOUT_SECONDS` 控制，日志由 `SCENARA_MODEL_EXTERNAL_COMMAND_LOG_MAX_CHARS` 截断。

## 存储与兼容性

- SQLite 连接使用线程锁和 busy timeout。
- journal mode 按 `MEMORY -> WAL -> DELETE -> OFF` 回退，避免受限 Windows 沙箱下 WAL 探针文件残留和 `disk I/O error`。
- 默认损坏的 SQLite 文件已备份到 `artifacts/recovered/`，当前默认库可正常打开。
- 多实例生产部署仍建议迁移 PostgreSQL。

## 环境与 Docker

- 推荐使用：

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest
```

- 全局 Python 环境也已修复依赖冲突：`python -m pip check` 通过。
- Windows 用户代理残留已清理，原值备份在 `artifacts/windows-proxy-before-20260703184731.txt`。
- 默认 Docker 构建命令已验证：

```powershell
docker build -t scenara-model:0.3.0 .
docker run --rm scenara-model:0.3.0 python -c "from scenara_model.api import app; print(app.title)"
```

## 验证结果

- `.venv\Scripts\python.exe -m pip check`：通过。
- `python -m pip check`：通过。
- `.venv\Scripts\python.exe -m pytest`：32 passed。
- `python -m pytest`：32 passed。
- `npm run build`：通过。
- `npm audit --omit dev`：0 vulnerabilities。
- `python scripts\acceptance_check.py`：通过。
- `docker build -t scenara-model:0.3.0 .`：通过。
- Docker 镜像导入 API smoke test：输出 `Scenara Model`。

## 升级注意

- `Dockerfile` 默认基础镜像使用华为云镜像源，外部环境可通过 `--build-arg NODE_IMAGE=... --build-arg PYTHON_IMAGE=...` 覆盖。
- 完整 `npm audit` 中的 Vite/esbuild advisory 仅影响开发服务器；生产门禁使用 `npm audit --omit dev`。
- 当前目录不是 Git 仓库，发布记录以文件版本和文档为准。
# 优化待办

本文件记录 2026-07 代码审查中发现、但尚未落盘的优化项。已完成部分见 commit `53366e7`。

## 落地状态（2026-07-28）

- `0.8.0` 已完成并准备发布：生产训练信任链、真实制品新鲜度、模型卡溯源、严格模型包、发布阶段门禁、runtime preflight 和同实验互斥均已落地。
- 当前验证基线：89 项 Python 测试、Ruff、Pyright、离线验收、前端构建和 `pip check` 全部通过。
- 真实 GPU 训练尚未在本机执行：当前 Torch 为 CPU-only，且业务数据 manifest/图像未提供；生产配置会在训练前明确失败。
- 完整发布与升级说明见 [RELEASE_0.8.0.md](RELEASE_0.8.0.md)。

- A、C、D、E、F、G 与 P1-1/P1-2/P1-3 已完成并由 `0.7.0` 发布；B 已由前序提交完成。
- P2 仍为未排期事项，本轮未修改其 API、认证和前端契约。
- 验证基线：`84 passed`；版本门禁、Ruff、离线验收、前端构建、compose 配置解析、默认 Docker 镜像和 `postgres,s3` extras 镜像均通过。
- 完整发布与升级说明见 [RELEASE_0.7.0.md](RELEASE_0.7.0.md)。

## 已完成（无需重做）

- `storage.py`：journal mode 候选序列去掉排在最前的 `MEMORY` 并校验 PRAGMA 实际生效值；每线程一条长连接替代「每次 connect/close + 进程级锁」；`record_pipeline_job_log` 去掉回读；新增 `record_pipeline_job_logs` 批量写入与 `close()`。
- `local_tasks.py`：`_command_env()` 改为前缀 + 关键字剥离，堵住 `VMLAB_METADATA_DB`（PG DSN 明文口令）与 `VMLAB_ADMIN_PASSWORD` 泄露；新增 `VMLAB_EXTERNAL_COMMAND_ENV_PASSTHROUGH` 放行开关。
- `api.py`：默认管理员口令改为随机生成；新增 `_LoginThrottle` 登录失败限流。
- `settings.py`：新增 `login_max_failures`、`login_lockout_seconds`、`log_retention_days`、`maintenance_interval_seconds` 四个字段（后两个**尚无消费方**，见 P1-3）。

验证基线：`python -m pytest` → 76 passed。

---

## A. 性能：日志缓冲与取消节流

**问题**：`_run_external_command` 每 0.1 秒调一次 `should_cancel()`，每次都是一次完整的 `get_pipeline_job` 查询；`log_sink` 每行日志一次独立事务。一个每秒输出 100 行的训练任务约等于每秒 200+ 次数据库操作，会连带拖住所有 HTTP 请求。

`storage.py` 的批量写入方法已就绪，缺的是调用方。

**改动**：`api.py` 在 `_pipeline_job_detail` 之前插入两个类：

```python
class _JobLogBuffer:
    """外部命令日志缓冲：按条数或时间批量落库。

    训练脚本每秒可输出数百行，逐行独立事务会让元数据库写入成为瓶颈，
    并连带拖住所有 HTTP 请求。
    """

    def __init__(self, job_id: int, *, max_entries: int = 200, max_interval: float = 1.0) -> None:
        self._job_id = job_id
        self._max_entries = max_entries
        self._max_interval = max_interval
        self._lock = threading.Lock()
        self._pending: list[tuple[str, str]] = []
        self._last_flush = time.monotonic()

    def add(self, stream: str, line: str) -> None:
        with self._lock:
            self._pending.append((stream, line))
            due = len(self._pending) >= self._max_entries or (time.monotonic() - self._last_flush) >= self._max_interval
            if not due:
                return
            entries, self._pending = self._pending, []
            self._last_flush = time.monotonic()
        self._write(entries)

    def flush(self) -> None:
        with self._lock:
            entries, self._pending = self._pending, []
            self._last_flush = time.monotonic()
        self._write(entries)

    def _write(self, entries: list[tuple[str, str]]) -> None:
        if not entries:
            return
        try:
            STORE.record_pipeline_job_logs(self._job_id, entries)
        except Exception:  # noqa: BLE001 - 日志落库失败不应中断训练任务
            logger.exception("failed to persist %d job log line(s) for job %s", len(entries), self._job_id)


class _CancelCheck:
    """取消检查节流。

    外部命令以 0.1 秒间隔轮询，直接查库等于训练期间每秒 10 次查询；
    取消是人工操作，秒级延迟完全可接受。
    """

    def __init__(self, job_id: int, *, interval: float = 3.0) -> None:
        self._job_id = job_id
        self._interval = interval
        self._checked_at = 0.0
        self._cancelled = False

    def __call__(self) -> bool:
        if self._cancelled:
            return True
        now = time.monotonic()
        if now - self._checked_at < self._interval:
            return False
        self._checked_at = now
        try:
            self._cancelled = STORE.get_pipeline_job(self._job_id)["status"] == "cancellation_requested"
        except KeyError:
            return False
        return self._cancelled
```

`_run_pipeline_job` 头部（到 `try:` 为止）替换为：

```python
def _run_pipeline_job(job_id: int, payload: dict[str, Any]) -> None:
    log_buffer = _JobLogBuffer(job_id)

    def event_sink(stage: str, message: str, detail: dict[str, Any]) -> None:
        # 阶段事件带结构化 detail，单独落库；先冲缓冲以保证与命令输出的先后顺序。
        log_buffer.flush()
        STORE.record_pipeline_job_log(job_id, stage, message, detail)

    def log_sink(stream: str, line: str) -> None:
        log_buffer.add(stream, line)

    should_cancel = _CancelCheck(job_id)

    try:
```

函数末尾的 `except Exception` 块后追加：

```python
    finally:
        # 任务以任何路径结束（含中途 return）都必须落完缓冲里的最后几行日志。
        log_buffer.flush()
```

`finally` 是必需的——原函数有 4 处提前 `return`，缺了它会丢掉尾部日志，而尾部恰好是排障最需要的部分。

**验证**：新增 `tests/test_api.py::test_cancel_check_is_throttled`，断言 50 次轮询在节流窗口内只查库一次。

---

## B. 性能：模型摘要缓存

**问题**：`validate_model_package` 对每个 ONNX 无条件全文件 sha256，而管理台首页每次刷新都会触发 `GET /api/packages/scan` 全量扫描。500 包上限 × 数百 MB 会让扫描接口长时间同步阻塞 HTTP worker。

**改动 1**：`utils.py` 顶部加 `import threading`，`sha256_file` 整体替换：

```python
# 摘要缓存：键为路径，值为 (size, mtime_ns, digest)。
_DIGEST_CACHE: dict[str, tuple[int, int, str]] = {}
_DIGEST_CACHE_LOCK = threading.Lock()
_DIGEST_CACHE_MAX = 512


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024, *, use_cache: bool = True) -> str:
    """计算文件 sha256，按 (size, mtime_ns) 缓存。

    模型包校验对每个 ONNX 都要求摘要，而管理台首页每次刷新都会触发全量扫描；
    没有缓存时几百 MB 的模型会被反复整文件读取，扫描接口同步阻塞 HTTP worker。

    刚写入或复制出来的文件必须传 use_cache=False：shutil.copy2 会保留源文件
    mtime，同一路径先后放入 size 与 mtime 相同的两个文件时缓存会命中错误结果。
    """
    resolved = as_path(path)
    key = str(resolved)
    try:
        stat = resolved.stat()
        signature: tuple[int, int] | None = (stat.st_size, stat.st_mtime_ns)
    except OSError:
        signature = None
    if use_cache and signature is not None:
        with _DIGEST_CACHE_LOCK:
            cached = _DIGEST_CACHE.get(key)
        if cached is not None and cached[:2] == signature:
            return cached[2]
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    if signature is not None:
        with _DIGEST_CACHE_LOCK:
            if len(_DIGEST_CACHE) >= _DIGEST_CACHE_MAX:
                _DIGEST_CACHE.clear()
            _DIGEST_CACHE[key] = (signature[0], signature[1], value)
    return value
```

**改动 2（必须与改动 1 配套）**：`packaging/model_package.py` 第 406 行：

```python
    digest = sha256_file(destination_model, use_cache=False)
```

`create_model_package` 里 `shutil.copy2` **保留源文件 mtime**。同一目标路径先后打包两个 size 与 mtime 相同的模型时，第二次会命中第一次的缓存，把**错误的摘要写进模型卡**——这会直接击穿交付信任链，比原本的性能问题严重得多。

`validate_model_package:276` 读的是既有文件，走缓存安全且正确，性能收益全在那里。

**验证**：新增 `tests/test_utils.py`：

```python
from __future__ import annotations

import os
import shutil
from pathlib import Path

from vision_model_lab.utils import sha256_file


def test_sha256_cache_is_bypassed_for_freshly_copied_files(workspace_tmp_path: Path) -> None:
    """回归：copy2 保留 mtime，打包路径必须绕过缓存，否则模型卡会写入错误摘要。"""
    source_a = workspace_tmp_path / "a.bin"
    source_b = workspace_tmp_path / "b.bin"
    source_a.write_bytes(b"AAAA")
    source_b.write_bytes(b"BBBB")   # 同样长度
    os.utime(source_a, (0, 0))
    os.utime(source_b, (0, 0))      # 同样 mtime

    target = workspace_tmp_path / "target.bin"
    shutil.copy2(source_a, target)
    first = sha256_file(target, use_cache=False)
    shutil.copy2(source_b, target)

    assert sha256_file(target, use_cache=False) != first
```

---

## C. 一致性：版本号统一到 0.6.0

**问题**：`CHANGELOG.md` 已发布 `[0.6.0]`，而 `pyproject.toml` / `__init__.py` / 前端 `package.json` 都是 `0.5.0`，`README.md` 停在 `0.4.1` 且仍描述「可选 Bearer Token 鉴权」（实际已是强制登录认证）。

**改动**：五处字面量替换。

| 文件 | 行 | 改为 |
| --- | --- | --- |
| `pyproject.toml` | 7 | `version = "0.6.0"` |
| `src/vision_model_lab/__init__.py` | 5 | `__version__ = "0.6.0"` |
| `frontend/package.json` | 3 | `"version": "0.6.0",` |
| `frontend/package-lock.json` | 3 | `"version": "0.6.0",` |
| `frontend/package-lock.json` | 9 | `"version": "0.6.0",` |

`README.md` 第 3 行：

```markdown
当前版本：`0.6.0`。完整变更见 [CHANGELOG.md](CHANGELOG.md)。
```

`README.md` 第 16 行那条能力描述拆成两条：

```markdown
- local/S3/MinIO 对象存储入口、上传接口和误差样本摘要。
- 用户名密码登录与会话令牌鉴权：除 `/api/auth/login` 与 `/health` 外，全部 `/api` 接口要求认证；`VMLAB_AUTH_TOKEN` 静态令牌并行支持 CI 与脚本调用。
```

`docs/ARCHITECTURE.md` 的 API 清单在 `GET /health` 后插入 `POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`。

`docs/PRODUCTION_READINESS.md` 环境变量表在 `VMLAB_AUTH_TOKEN` 后补：

```markdown
| `VMLAB_ADMIN_PASSWORD` | 空 | 首次启动创建 admin 的口令；未设置时自动生成随机口令并打印到启动日志 |
| `VMLAB_SESSION_TTL_HOURS` | `24` | 登录会话有效期 |
| `VMLAB_LOGIN_MAX_FAILURES` | `5` | 同一用户名+IP 连续登录失败上限 |
| `VMLAB_LOGIN_LOCKOUT_SECONDS` | `300` | 达到失败上限后的锁定时长 |
| `VMLAB_LOG_RETENTION_DAYS` | `30` | 任务日志与审计事件保留天数 |
| `VMLAB_MAINTENANCE_INTERVAL_SECONDS` | `3600` | 周期维护间隔 |
| `VMLAB_EXTERNAL_COMMAND_ENV_PASSTHROUGH` | 空 | 显式放行给外部命令的环境变量名（逗号分隔） |
```

`docs/OPERATIONS.md:70` 与 `docs/PRODUCTION_READINESS.md:97` 那句已经过时的描述改为：

```markdown
- SQLite 使用 WAL journal mode（回落顺序 `WAL -> TRUNCATE -> DELETE`，并校验 PRAGMA 实际生效值）、busy timeout 和每线程长连接；多实例或多人生产部署仍建议迁移 PostgreSQL。
```

**新增门禁** `scripts/check_versions.py`：

```python
"""校验版本号在所有清单中保持一致。

版本已经漂移过一次：CHANGELOG 发布了 0.6.0，而 pyproject / __init__ / 前端
package.json 仍是 0.5.0，README 停在 0.4.1。这里把它变成可执行门禁。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    init_text = (ROOT / "src" / "vision_model_lab" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    if match is None:
        print("[版本] 无法从 src/vision_model_lab/__init__.py 解析 __version__", file=sys.stderr)
        return 2
    expected = match.group(1)
    problems: list[str] = []

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    found = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    if found is None or found.group(1) != expected:
        problems.append(f"pyproject.toml = {found.group(1) if found else '缺失'}")

    package_json = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    if package_json.get("version") != expected:
        problems.append(f"frontend/package.json = {package_json.get('version')}")

    lock_path = ROOT / "frontend" / "package-lock.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if lock.get("version") != expected:
            problems.append(f"frontend/package-lock.json = {lock.get('version')}")
        root_entry = lock.get("packages", {}).get("", {})
        if root_entry.get("version") != expected:
            problems.append(f'frontend/package-lock.json packages[""] = {root_entry.get("version")}')

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    latest = re.search(r"^## \[([^\]]+)\]", changelog, re.MULTILINE)
    if latest is None or latest.group(1) != expected:
        problems.append(f"CHANGELOG.md 最新条目 = {latest.group(1) if latest else '缺失'}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if f"当前版本：`{expected}`" not in readme:
        problems.append("README.md 未声明当前版本或与 __version__ 不一致")

    if problems:
        for problem in problems:
            print(f"[版本不一致] 期望 {expected}，但 {problem}", file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "version": expected}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`scripts/acceptance_check.py` 的 `checks.extend([...])` 列表首位插入：

```python
        run([sys.executable, "scripts/check_versions.py"]),
```

---

## D. CI 门禁

**问题 1（新发现，优先级最高）**：`dev` extra 里没有 `alembic` / `SQLAlchemy`，而 CI 装的是 `pip install -e ".[dev]"`。干净环境下 `tests/test_storage.py` 的两个 Alembic 用例**必然失败**——本次审查在干净 clone 上复现了这一点（补装 `[migrations]` 后才 72 passed）。

`pyproject.toml` 的 `dev` extra 改为：

```toml
dev = [
  "httpx>=0.27",
  "pytest>=8",
  "pytest-asyncio>=0.25",
  "ruff>=0.8",
  # 迁移相关测试需要 alembic；缺失时 test_storage 的两个用例必然失败。
  "alembic>=1.13",
  "SQLAlchemy>=2.0"
]
```

**问题 2**：CI 无 lint、无类型检查、无依赖漏洞扫描，docker 构建后无 smoke test。

`pyproject.toml` 末尾追加：

```toml
[tool.ruff]
line-length = 160
target-version = "py311"
src = ["src", "scripts", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "C4"]
ignore = [
  "E501",  # 长度由 line-length 兜底；报告类字典字面量刻意保持单行
  "B008",  # FastAPI 的 Depends/Query/Header 惯例就是写在默认值里
]

[tool.ruff.lint.per-file-ignores]
"migrations/versions/*" = ["I001"]  # Alembic 模板的 import 顺序由工具生成
```

> 引入 ruff 前先跑 `python -m ruff check --statistics src scripts tests`，把高频规则加进 `ignore` 或用 `--fix` 自动修，**再**开 CI 门禁。别让第一次引入 lint 就把 CI 变红。

`.github/workflows/ci.yml` 的 `backend` job 替换为：

```yaml
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install backend
        run: python -m pip install -e ".[dev]" -c constraints.txt
      - name: Lint
        run: python -m ruff check src scripts tests migrations
      - name: Test backend and contracts
        run: |
          export PYTHONDONTWRITEBYTECODE=1
          python -m pytest
          python scripts/acceptance_check.py --skip-pytest
      - name: Audit Python dependencies
        run: |
          python -m pip install pip-audit
          pip-audit --desc --strict

  typecheck:
    runs-on: ubuntu-latest
    # 存量代码尚未通过完整类型检查，先观察不阻塞；清零后去掉 continue-on-error。
    continue-on-error: true
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install -e ".[dev]" -c constraints.txt pyright
      - run: python -m pyright src
```

`docker` job 追加 smoke test：

```yaml
      - name: Smoke test image
        run: |
          docker run --rm vision-model-lab:ci python -c "from vision_model_lab.api import app; print(app.title)"
          docker run -d --name vmlab-ci -p 8080:8080 vision-model-lab:ci
          for _ in $(seq 1 30); do
            if curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then break; fi
            sleep 2
          done
          curl -fsS http://127.0.0.1:8080/health | grep -q '"status":"ok"'
          docker logs vmlab-ci
          docker rm -f vmlab-ci
```

---

## E. 多实例：job 归属与心跳

**问题**：`recover_orphaned_jobs` 启动时无条件把所有 `running` 任务标记 failed。文档推荐多实例部署用 PostgreSQL，但在多实例下这个逻辑必然误杀——新实例启动会直接杀掉其他实例正在跑的训练。

**改动 1**：`storage.py` 导入改为 `from datetime import datetime, timedelta, timezone`；`_initialize_connection` 的 `pipeline_jobs` 建表在 `cancelled_at TEXT,` 后加 `worker_id TEXT,` 和 `heartbeat_at TEXT,`；同方法末尾（`INSERT OR IGNORE INTO schema_migrations` 之前）插入 `self._ensure_pipeline_job_columns(connection)` 并新增：

```python
    def _ensure_pipeline_job_columns(self, connection: sqlite3.Connection) -> None:
        """存量库补列：CREATE TABLE IF NOT EXISTS 不会给已存在的表加列。"""
        existing = {row[1] for row in connection.execute("PRAGMA table_info(pipeline_jobs)")}
        for column in ("worker_id", "heartbeat_at"):
            if column not in existing:
                connection.execute(f"ALTER TABLE pipeline_jobs ADD COLUMN {column} TEXT")
```

**改动 2**：`mark_pipeline_job_running` 后追加两个方法，并把旧方法改为委托：

```python
    def claim_pipeline_job(self, job_id: int, worker_id: str) -> dict[str, Any]:
        """把 queued 任务认领为本 worker 的 running 任务。"""
        now = utc_now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE pipeline_jobs
                SET status='running', started_at=?, updated_at=?, worker_id=?, heartbeat_at=?
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, worker_id or None, now, job_id),
            )
        return self.get_pipeline_job(job_id)

    def heartbeat_pipeline_job(self, job_id: int, worker_id: str) -> None:
        now = utc_now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE pipeline_jobs SET heartbeat_at=?, updated_at=?
                WHERE id = ? AND worker_id = ? AND status IN ('running', 'cancellation_requested')
                """,
                (now, now, job_id, worker_id),
            )

    def mark_pipeline_job_running(self, job_id: int) -> dict[str, Any]:
        """兼容入口：不区分 worker 的认领，等价于匿名 worker。"""
        return self.claim_pipeline_job(job_id, "")
```

> **必须保留 `mark_pipeline_job_running`** —— `tests/test_storage.py` 有 5 处在用（36、120、146、157、187 行）。`worker_id or None` 把空串归一为 NULL，这样匿名认领的任务仍会被 `worker_id IS NULL` 分支正确回收。

**改动 3**：`recover_orphaned_jobs` 整体替换：

```python
    def recover_orphaned_jobs(
        self,
        *,
        worker_id: str = "",
        stale_after_seconds: int = 120,
        error: str = "orphaned by service restart",
    ) -> list[dict[str, Any]]:
        """回收孤儿任务：只处理本 worker 的遗留任务和心跳超时的任务。

        多实例部署下不得无条件回收全部 running 任务——那会让新实例启动时
        直接杀掉其他实例正在跑的训练。
        """
        now = datetime.now(timezone.utc)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        cutoff = (now - timedelta(seconds=stale_after_seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id FROM pipeline_jobs
                WHERE status IN ('running', 'cancellation_requested')
                  AND (worker_id = ? OR worker_id IS NULL OR heartbeat_at IS NULL OR heartbeat_at < ?)
                """,
                (worker_id, cutoff),
            ).fetchall()
            orphan_ids = [int(row["id"] if isinstance(row, dict) else row[0]) for row in rows]
            if orphan_ids:
                connection.execute(
                    f"""
                    UPDATE pipeline_jobs
                    SET status='failed', error=?, completed_at=?, updated_at=?
                    WHERE id IN ({','.join('?' * len(orphan_ids))})
                    """,
                    (error, now_iso, now_iso, *orphan_ids),
                )
        return [self.get_pipeline_job(job_id) for job_id in orphan_ids]
```

> `worker_id` **必须有默认值** —— `tests/test_storage.py:160` 调用的是无参数的 `recover_orphaned_jobs()`。

**改动 4**：PG 侧同步——`PostgresMetadataStore._initialize_connection` 的 `pipeline_jobs` 建表加 `worker_id TEXT,` 与 `heartbeat_at TIMESTAMPTZ,`，并覆盖补列方法（PG 无 `PRAGMA`）：

```python
    def _ensure_pipeline_job_columns(self, connection: Any) -> None:
        connection.execute("ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS worker_id TEXT")
        connection.execute("ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ")
```

**改动 5**：`storage.py` 顶部 `SCHEMA_VERSION` 更新为 `"20260726_060_job_heartbeat"`（当前值仍停在 `20260703_040`，已落后实际 schema 两个版本）。

**改动 6**：新增 `migrations/versions/20260726_060_job_heartbeat.py`：

```python
"""任务 worker 归属与心跳列。

Revision ID: 20260726_060
Revises: 20260717_050
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260726_060"
down_revision = "20260717_050"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bool(bind is not None and bind.dialect.name == "postgresql")


def _ts_type() -> sa.types.TypeEngine:
    return sa.DateTime(timezone=True) if _is_postgres() else sa.Text()


def _existing_columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("pipeline_jobs")}


def upgrade() -> None:
    # 运行时内置 DDL 可能已经补过列（服务先启动、后执行 migrate）。
    existing = _existing_columns()
    if "worker_id" not in existing:
        op.add_column("pipeline_jobs", sa.Column("worker_id", sa.Text(), nullable=True))
    if "heartbeat_at" not in existing:
        op.add_column("pipeline_jobs", sa.Column("heartbeat_at", _ts_type(), nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_jobs", "heartbeat_at")
    op.drop_column("pipeline_jobs", "worker_id")
```

**改动 7**：`api.py` 导入补 `os`、`socket`；常量区加：

```python
# 本实例标识：多实例部署时区分任务归属，避免启动回收误杀其他实例的运行中任务。
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
```

`_recover_jobs_on_startup` 里改为 `STORE.recover_orphaned_jobs(worker_id=WORKER_ID)`；`_run_pipeline_job` 里 `STORE.mark_pipeline_job_running(job_id)` 改为 `STORE.claim_pipeline_job(job_id, WORKER_ID)`；`_CancelCheck.__call__` 的查库分支顺带续心跳（不额外开路径）：

```python
        self._checked_at = now
        try:
            job = STORE.get_pipeline_job(self._job_id)
        except KeyError:
            return False
        try:
            STORE.heartbeat_pipeline_job(self._job_id, WORKER_ID)
        except Exception:  # noqa: BLE001 - 心跳失败不应中断任务
            logger.debug("heartbeat failed for job %s", self._job_id, exc_info=True)
        self._cancelled = job["status"] == "cancellation_requested"
        return self._cancelled
```

**验证**：

```python
def test_recover_orphaned_jobs_spares_other_live_workers() -> None:
    """回归：多实例启动回收不得杀掉其他实例心跳正常的任务。"""
    store = MetadataStore(":memory:")
    mine = int(store.create_pipeline_job("configs/a.yml", {})["id"])
    theirs = int(store.create_pipeline_job("configs/b.yml", {})["id"])
    store.claim_pipeline_job(mine, "host-a:1")
    store.claim_pipeline_job(theirs, "host-b:2")
    store.heartbeat_pipeline_job(theirs, "host-b:2")

    recovered = store.recover_orphaned_jobs(worker_id="host-a:1")

    assert [int(job["id"]) for job in recovered] == [mine]
    assert store.get_pipeline_job(mine)["status"] == "failed"
    assert store.get_pipeline_job(theirs)["status"] == "running"
```

---

## F. 部署：Dockerfile 与 compose

**问题 1**：`Dockerfile` 注释声称「源码改动不再触发依赖全量重装」，但 `COPY src ./src` 在 `pip install .` 之前，缓存优化实际未生效。

**问题 2**：`docker-compose.yml` 的 `postgres` / `minio` profile 实际不可用——镜像不含 `[postgres]` / `[s3]` extras，主服务也没有 `depends_on` 和 DSN 覆盖，但 `docs/PRODUCTION_READINESS.md:77` 把它写成「可选生产形态」。

**改动 1**：`Dockerfile` 前 27 行替换：

```dockerfile
ARG NODE_IMAGE=node:22-alpine
ARG PYTHON_IMAGE=python:3.12-slim
# 需要 PostgreSQL / S3 后端时以 --build-arg VMLAB_EXTRAS=postgres,s3 构建。
ARG VMLAB_EXTRAS=""

FROM ${NODE_IMAGE} AS frontend

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS backend
ARG VMLAB_EXTRAS

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VMLAB_WORKSPACE=/app \
    VMLAB_METADATA_DB=artifacts/vision_model_lab.sqlite3 \
    VMLAB_SERVE_FRONTEND=true

WORKDIR /app

# 依赖层：用占位包只装第三方依赖。原先 COPY src 在 pip install 之前，
# 导致任何源码改动都会触发依赖全量重装（注释声称的缓存优化并未生效）。
COPY pyproject.toml README.md constraints.txt ./
RUN mkdir -p src/vision_model_lab \
    && printf '__version__ = "0.0.0"\n' > src/vision_model_lab/__init__.py \
    && if [ -n "$VMLAB_EXTRAS" ]; then TARGET=".[$VMLAB_EXTRAS]"; else TARGET="."; fi \
    && pip install --no-cache-dir -c constraints.txt "$TARGET" \
    && pip uninstall -y vision-model-lab \
    && rm -rf src

# 源码层：只重装本项目自身，依赖层继续命中缓存。
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
```

> 占位包技巧依赖「依赖已装、自身被卸」这个状态。若 pip 行为不符，退路是手写 `requirements.txt` 并接受两份清单的漂移风险。

**改动 2**：`docker-compose.yml` 的 `build: .` 换成：

```yaml
    build:
      context: .
      args:
        # postgres / minio profile 需要这些可选依赖，否则切后端会在启动时报缺包。
        VMLAB_EXTRAS: postgres,s3
```

`environment` 里三项改为可覆盖：

```yaml
      VMLAB_METADATA_DB: ${VMLAB_METADATA_DB:-artifacts/vision_model_lab.sqlite3}
      VMLAB_STORAGE_BACKEND: ${VMLAB_STORAGE_BACKEND:-local}
      VMLAB_STORAGE_URI: ${VMLAB_STORAGE_URI:-artifacts/object-store}
```

**改动 3**：新增 `docker-compose.postgres.yml`：

```yaml
# 生产形态叠加文件：把主服务指向 compose 内的 PostgreSQL 与 MinIO。
#
#   docker compose -f docker-compose.yml -f docker-compose.postgres.yml \
#     --profile postgres --profile minio up --build
services:
  vision-model-lab:
    environment:
      VMLAB_METADATA_DB: postgresql://vmlab:${VMLAB_POSTGRES_PASSWORD:-vmlab-dev-only}@postgres:5432/vmlab
      VMLAB_STORAGE_BACKEND: minio
      VMLAB_STORAGE_URI: minio://vmlab/models
      VMLAB_S3_ENDPOINT_URL: http://minio:9000
      VMLAB_S3_ACCESS_KEY_ID: ${VMLAB_MINIO_ROOT_USER:-vmlab}
      VMLAB_S3_SECRET_ACCESS_KEY: ${VMLAB_MINIO_ROOT_PASSWORD:-vmlab-dev-only}
    depends_on:
      postgres:
        condition: service_healthy
      minio:
        condition: service_healthy
```

`docs/PRODUCTION_READINESS.md:77` 的命令同步改为上面的三段式写法。

---

## G. 依赖可复现性

**问题**：`pyproject.toml` 的依赖全是开区间 `>=`，无上界、无锁文件（前端有 `package-lock.json`，后端没有）。本次审查在干净环境安装时拿到了 `fastapi 0.140`、`pytest 9.1`、`starlette 1.3`、`numpy 2.5` 等远超项目开发时的版本——上游一次 breaking change 就会同时打挂 CI 和生产镜像。

**改动**：新增 `constraints.txt`：

```
# 依赖上界约束：pyproject 的 `>=` 保证最低可用版本，本文件锁定验证过的上界，
# 避免上游 breaking change 直接打进 CI 与生产镜像。
#
#   pip install -e ".[dev]" -c constraints.txt
#
# 升级流程：放宽单个上界 -> 跑 pytest 与 scripts/acceptance_check.py -> 通过后提交新上界。
fastapi<1.0
pydantic<3.0
PyYAML<7.0
onnx<2.0
onnxruntime<2.0
python-multipart<1.0
uvicorn<1.0
httpx<1.0
pytest<10.0
pytest-asyncio<2.0
ruff<1.0
boto3<2.0
psycopg<4.0
psycopg-pool<4.0
alembic<2.0
SQLAlchemy<3.0
```

---

## P1 附带项

### P1-1 认证每请求查两次库

中间件 `auth_middleware` 已解析一次身份，路由级 `Depends(require_auth)` 又解析一次，每次都查 `auth_sessions`。`api.py` 替换：

```python
def require_auth(request: Request) -> dict[str, Any]:
    identity = getattr(request.state, "identity", None)
    if identity is None:
        # 中间件未覆盖的路径回落到自行解析。
        identity = _resolve_bearer_identity(request.headers.get("Authorization"))
    if identity is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return identity


@app.middleware("http")
async def auth_middleware(request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
    """全局认证：除登录接口外，全部 /api 路径都要求有效令牌。

    /health 与前端静态资源保持公开（容器健康检查、登录页本身依赖它们）。
    解析结果缓存到 request.state，避免路由级 Depends(require_auth) 重复查库。
    """
    path = request.url.path
    if path.startswith("/api") and path not in PUBLIC_API_PATHS and request.method != "OPTIONS":
        identity = _resolve_bearer_identity(request.headers.get("Authorization"))
        if identity is None:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        request.state.identity = identity
    return await call_next(request)
```

`logout` 端点保留 `authorization: str | None = Header(default=None)` 参数不变。

### P1-2 日志与审计表无保留策略

训练日志逐行入库，`pipeline_job_logs` 会很快膨胀到 GB 级；`audit_events` 同理。`storage.py` 的 `purge_expired_auth_sessions` 后追加：

```python
    def purge_old_records(self, *, retention_days: int) -> dict[str, int]:
        """按保留期清理任务日志与审计事件。

        训练日志逐行入库，pipeline_job_logs 会很快膨胀到 GB 级。
        """
        if retention_days <= 0:
            return {"pipeline_job_logs": 0, "audit_events": 0}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        deleted: dict[str, int] = {}
        with self.connect() as connection:
            for table in ("pipeline_job_logs", "audit_events"):
                cursor = connection.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))
                deleted[table] = int(getattr(cursor, "rowcount", 0) or 0)
        return deleted
```

### P1-3 过期会话只在启动时清理一次

长期运行的服务会持续堆积。`settings.py` 的 `log_retention_days` / `maintenance_interval_seconds` 字段**已就绪但无消费方**，需要在 `api.py` 加周期任务：

```python
async def _maintenance_loop() -> None:
    """周期维护：清理过期会话与超期日志。

    原先只在启动时清理一次，长期运行的服务会持续堆积。
    """
    interval = SETTINGS.maintenance_interval_seconds
    while True:
        try:
            await asyncio.sleep(interval)
            await asyncio.to_thread(STORE.purge_expired_auth_sessions)
            await asyncio.to_thread(STORE.purge_old_records, retention_days=SETTINGS.log_retention_days)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 维护失败不应影响服务
            logger.exception("periodic maintenance failed")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    _bootstrap_admin_user()
    try:
        STORE.purge_expired_auth_sessions()
    except Exception:  # noqa: BLE001 - 清理失败不影响启动
        logger.exception("expired auth session purge failed")
    _recover_jobs_on_startup()
    maintenance = asyncio.create_task(_maintenance_loop())
    try:
        yield
    finally:
        maintenance.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance
        EXECUTOR.shutdown(wait=False, cancel_futures=True)
        STORE.close()
```

导入补 `asyncio` 与 `from contextlib import asynccontextmanager, suppress`。

`/health` 顺带暴露 journal mode 便于运维确认：

```python
        "metadata_journal_mode": STORE.journal_mode() if SETTINGS.metadata_db != ":memory:" else "memory",
```

---

## P2 backlog（未排期）

以下项在审查中发现，价值低于上面各项或改动面较大，按需推进：

- **前端令牌存 `localStorage`**（`api.ts:24`），XSS 可窃取。建议 httpOnly cookie + CSRF token，或至少配置 CSP。
- **`downloadArtifact` 用 blob 全量载入内存**（`api.ts:236`），几百 MB 的 ONNX 会撑爆浏览器。建议短时签名 URL。
- **401 自动登出只覆盖 `refresh()` 的 5 个请求**（`App.tsx:63`），其他页面 401 不会跳登录页。
- **`vmlab user set-password --password <明文>`** 会进 shell history 与 `ps` 输出（`cli.py:291`），应支持 stdin / 交互输入。
- **`_int_env` / `_bool_env` 在 `settings.py` 与 `local_tasks.py` 重复实现且行为不一致**（一个抛错、一个静默 fallback）；后者绕过 Settings 直接读 env。
- **`api.py` 模块级全局 `SETTINGS` / `STORE` / `EXECUTOR`**，测试只能 monkeypatch。建议 `app.state` + 依赖注入。
- **`pipeline.py:283` 的 `_example_dir_for` 用相对路径** `Path("artifacts/examples")`，依赖进程 CWD，与 `local_tasks.py` 已锚定 workspace 的做法不一致。
- **PostgreSQL 后端零集成测试**；且 PG 的 `created_at` 返回 datetime、SQLite 返回字符串，两后端响应格式实际不一致（与 `utc_now_iso` 注释宣称的「两后端输出一致」矛盾）。
- **前端零测试**（无 vitest / testing-library）。
- **`conftest.py` 用 `artifacts/test_runs` 而非 pytest `tmp_path`**，测试污染工作区且依赖 CWD。
- **合成基线模型（`synthetic_baseline`）仍可打包并注册进 model_registry**；信任门禁只做在 `metrics_source` 上，`onnx_source` 没有对应约束。
- **`update_user_password` 三次独立连接、非原子**（`storage.py:895`）——改密成功但撤销会话失败时旧令牌仍有效。
- **`object_store_from_settings` 每次上传新建 boto3 client**（`api.py:692`）。
- **列表接口无游标分页**，且 `list_pipeline_jobs` 把完整 `result_json` 塞进列表响应。

---

## 落地顺序建议

1. **D 的问题 1**（`dev` extra 补 alembic）—— 一行改动，当前 CI 应该是红的
2. **B**（摘要缓存 + `use_cache=False`）—— 改动小，且含一个会写错模型卡的正确性风险
3. **A**（日志缓冲 + 取消节流）—— `storage.py` 侧已就绪，只差调用方
4. **C**（版本统一）+ **D 其余**（ruff / CI）—— 防止文档与代码继续漂移
5. **E**（心跳）、**F**（部署）、**G**（依赖锁）—— 生产化前置条件
6. **P1-1 / P1-2 / P1-3** —— 可与上面任意批次合并

每批落盘后跑：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

全部完成后：

```powershell
.\.venv\Scripts\python.exe scripts\check_versions.py
.\.venv\Scripts\python.exe -m ruff check src scripts tests migrations
.\.venv\Scripts\python.exe scripts\acceptance_check.py --skip-pytest
cd frontend; npm run build
```

ARG NODE_IMAGE=node:22-alpine
ARG PYTHON_IMAGE=python:3.12-slim
# 需要这些后端时，通过 --build-arg SCENARA_MODEL_EXTRAS=postgres,s3 进行构建。
ARG SCENARA_MODEL_EXTRAS=""

FROM ${NODE_IMAGE} AS frontend

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS backend
ARG SCENARA_MODEL_EXTRAS

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCENARA_MODEL_WORKSPACE=/app \
    SCENARA_MODEL_METADATA_DB=artifacts/scenara_model.sqlite3 \
    SCENARA_MODEL_SERVE_FRONTEND=false

WORKDIR /app

# 使用占位包先安装第三方依赖，使仅源码变更时能够复用该 Docker 缓存层。
COPY pyproject.toml README.md constraints.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && mkdir -p src/scenara_model \
    && printf '__version__ = "0.0.0"\n' > src/scenara_model/__init__.py \
    && if [ -n "$SCENARA_MODEL_EXTRAS" ]; then TARGET=".[$SCENARA_MODEL_EXTRAS]"; else TARGET="."; fi \
    && pip install --no-cache-dir -c constraints.txt "$TARGET" \
    && pip uninstall -y scenara-model \
    && rm -rf src build

COPY src ./src
RUN pip install --no-cache-dir --no-deps .

COPY configs ./configs
COPY data ./data
COPY labeling ./labeling
COPY experiments ./experiments
COPY scripts ./scripts
COPY migrations ./migrations
COPY alembic.ini ./
COPY --from=frontend /app/frontend/dist ./frontend/dist

# 平台会执行外部训练命令，必须以非 root 运行以限制越权面。
RUN useradd --create-home --shell /usr/sbin/nologin scenara_model \
    && mkdir -p /app/artifacts /app/shared-models \
    && chown -R scenara_model:scenara_model /app
USER scenara_model

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"]

CMD ["uvicorn", "scenara_model.api:app", "--host", "0.0.0.0", "--port", "8080"]

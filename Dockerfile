ARG NODE_IMAGE=node:22-alpine
ARG PYTHON_IMAGE=python:3.12-slim
# Build with --build-arg VMLAB_EXTRAS=postgres,s3 when those backends are needed.
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

# Install third-party dependencies from a placeholder package so source-only changes
# keep this Docker layer cached.
COPY pyproject.toml README.md constraints.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && mkdir -p src/vision_model_lab \
    && printf '__version__ = "0.0.0"\n' > src/vision_model_lab/__init__.py \
    && if [ -n "$VMLAB_EXTRAS" ]; then TARGET=".[$VMLAB_EXTRAS]"; else TARGET="."; fi \
    && pip install --no-cache-dir -c constraints.txt "$TARGET" \
    && pip uninstall -y vision-model-lab \
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
RUN useradd --create-home --shell /usr/sbin/nologin vmlab \
    && mkdir -p /app/artifacts /app/shared-models \
    && chown -R vmlab:vmlab /app
USER vmlab

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"]

CMD ["uvicorn", "vision_model_lab.api:app", "--host", "0.0.0.0", "--port", "8080"]

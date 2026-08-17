# Python 版本锁定 3.13：3.14 过新，部分 AI 生态库支持滞后。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# 依赖单独成层：只要 pyproject 没变，改代码不会触发重装。
# editable 安装要求包目录在安装时就存在，所以先建空壳，稍后再 COPY 真实代码
# 覆盖上去（editable 指向 /app 路径，内容后到没关系）。
COPY pyproject.toml README.md ./
RUN mkdir -p apps worker packages agents adapters \
    && touch apps/__init__.py worker/__init__.py packages/__init__.py \
             agents/__init__.py adapters/__init__.py \
    && pip install --no-cache-dir -e ".[dev]"

COPY alembic.ini ./
COPY apps ./apps
COPY worker ./worker
COPY packages ./packages
COPY agents ./agents
COPY adapters ./adapters
COPY migrations ./migrations
COPY tests ./tests

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=5 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

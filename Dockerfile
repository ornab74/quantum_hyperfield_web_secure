# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip wheel setuptools \
    && /opt/venv/bin/pip install -r requirements.txt \
    && /opt/venv/bin/pip check

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    QNH_DATA_DIR=/var/data QNH_MODELS_DIR=/var/data/models \
    QNH_CONTAINER_READ_ONLY=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 hyperfield \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
       --shell /usr/sbin/nologin hyperfield \
    && mkdir -p /app /var/data/models \
    && chown -R 10001:10001 /app /var/data \
    && chmod 0700 /var/data && chmod 0750 /var/data/models
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=10001:10001 main.py core.py qnhctl.py ./
COPY --chown=10001:10001 templates ./templates
COPY --chown=10001:10001 static ./static
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=4s --start-period=25s --retries=4 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)" || exit 1
CMD ["gunicorn","main:app","--bind","0.0.0.0:8080","--workers","2","--worker-class","gthread","--threads","4","--timeout","120","--graceful-timeout","20","--keep-alive","5","--max-requests","800","--max-requests-jitter","120","--limit-request-line","4094","--limit-request-field_size","8190","--access-logfile","-","--error-logfile","-"]

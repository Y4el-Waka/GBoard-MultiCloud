FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS frontend-builder

ENV NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FUND=false

WORKDIR /build

COPY package.json package-lock.json tailwind.config.cjs ./
COPY scripts/build_frontend.mjs ./scripts/build_frontend.mjs
COPY frontend ./frontend
COPY templates ./templates
COPY static/dashboard.js static/metrics-history.js ./static/
COPY static/src ./static/src

RUN npm ci --ignore-scripts \
    && npm run build:frontend

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

LABEL org.opencontainers.image.title="CoreView Cloud Lab" \
      org.opencontainers.image.version="0.5c" \
      org.opencontainers.image.base.name="docker.io/library/python:3.12-slim-bookworm" \
      org.opencontainers.image.base.digest="sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system --gid 10001 coreview \
    && adduser --system --uid 10001 --ingroup coreview --home /nonexistent \
        --no-create-home coreview

COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes \
        --requirement requirements.lock \
    && python -m pip uninstall --yes pip \
    && rm -rf /root/.cache /usr/local/bin/pip*

COPY --chown=10001:10001 *.py ./
COPY --chown=10001:10001 metrics ./metrics
COPY --chown=10001:10001 templates ./templates
COPY --chown=10001:10001 static ./static
COPY --from=frontend-builder --chown=10001:10001 /build/static/vendor ./static/vendor
COPY --chown=10001:10001 scripts/docker_healthcheck.py ./scripts/docker_healthcheck.py

RUN mkdir -p /runtime /app/static/uploads/alert_sounds \
    && chown -R 10001:10001 /runtime /app/static/uploads

USER 10001:10001

EXPOSE 5067 9090

CMD ["gunicorn", "--bind", "0.0.0.0:5067", "--workers", "1", "--threads", "4", "--access-logfile", "-", "--error-logfile", "-", "app:app"]

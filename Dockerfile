FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system --gid 10001 coreview \
    && adduser --system --uid 10001 --ingroup coreview --home /nonexistent \
        --no-create-home coreview

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --requirement requirements.txt

COPY --chown=10001:10001 *.py ./
COPY --chown=10001:10001 metrics ./metrics
COPY --chown=10001:10001 templates ./templates
COPY --chown=10001:10001 static ./static
COPY --chown=10001:10001 scripts/docker_healthcheck.py ./scripts/docker_healthcheck.py

RUN mkdir -p /runtime /app/static/uploads/alert_sounds \
    && chown -R 10001:10001 /runtime /app/static/uploads

USER 10001:10001

EXPOSE 5067 9090

CMD ["gunicorn", "--bind", "0.0.0.0:5067", "--workers", "1", "--threads", "4", "--access-logfile", "-", "--error-logfile", "-", "app:app"]

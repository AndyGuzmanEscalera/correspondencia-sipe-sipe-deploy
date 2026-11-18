FROM python:3.12-slim-bookworm
ARG SOURCE_REVISION=5989c415243c8c7d9a7f75a4b922b99974d84a44
ARG RELEASE_VERSION=qa-5989c415243c
LABEL org.opencontainers.image.source="https://github.com/AndyGuzmanEscalera/correspondencia-sipe-sipe" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.version="${RELEASE_VERSION}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 STORAGE_ROOT=/app/storage
WORKDIR /app
COPY requirements.txt requirements-biometric.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp app \
    && install -d -o 10001 -g 10001 -m 0750 /app/storage
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
USER 10001:10001
EXPOSE 8000
# Compose provides the explicitly trusted Apache IP. No startup migration/seeds.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]

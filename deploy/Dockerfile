FROM node:24-bookworm-slim AS web
WORKDIR /web
COPY apps/web/package*.json ./
RUN --mount=type=secret,id=trust_ca \
    if [ -f /run/secrets/trust_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/trust_ca; fi; npm ci
COPY apps/web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
RUN --mount=type=secret,id=trust_ca,mode=0444 \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources && \
    set --; if [ -f /run/secrets/trust_ca ]; then set -- -o Acquire::https::CaInfo=/run/secrets/trust_ca; fi; \
    apt-get "$@" update && apt-get "$@" install -y --no-install-recommends ffmpeg libsndfile1 git build-essential cmake && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock pyproject.toml README.md LICENSE ./
RUN --mount=type=secret,id=trust_ca \
    if [ -f /run/secrets/trust_ca ]; then export PIP_CERT=/run/secrets/trust_ca; fi; \
    pip install --no-cache-dir setuptools==80.9.0 wheel==0.45.1 && pip install --no-cache-dir -r requirements.lock
COPY packages/ packages/
COPY services/ services/
COPY scripts/ scripts/
COPY docs/ docs/
COPY --from=web /web/dist apps/web/dist/
RUN pip --disable-pip-version-check install --no-build-isolation --no-deps -e .
CMD ["python", "-m", "uvicorn", "voice_workbench_api.app:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]

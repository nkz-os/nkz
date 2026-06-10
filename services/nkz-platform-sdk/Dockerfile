###############################################################################
# Base image for Nekazari module backends.
#
# Published as: ghcr.io/nkz-os/nkz/module-base:python-3.11-slim
#
# Derived modules write a 3-line Dockerfile:
#
#     FROM ghcr.io/nkz-os/nkz/module-base:python-3.11-slim
#     COPY api/requirements.txt .
#     RUN pip install --no-cache-dir -r requirements.txt
#     COPY api/ ./
#
# Default CMD launches `uvicorn module_app:app` — the dev's main module file
# must therefore be `module_app.py` and expose `app = ModuleApp(...)`. To use a
# different entrypoint, override CMD in the derived Dockerfile.
###############################################################################
FROM python:3.11-slim AS base

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install the SDK + uvicorn. The SDK pulls in fastapi/httpx/python-jose/cryptography
# transitively. Building from local source rather than PyPI so a fresh image
# always tracks the repo state — we publish to PyPI in a separate step.
COPY services/nkz-platform-sdk /tmp/nkz-platform-sdk
RUN pip install --no-cache-dir "uvicorn[standard]>=0.27.0" /tmp/nkz-platform-sdk \
    && rm -rf /tmp/nkz-platform-sdk /root/.cache/pip

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:5000/health || exit 1

CMD ["uvicorn", "module_app:app", "--host", "0.0.0.0", "--port", "5000"]

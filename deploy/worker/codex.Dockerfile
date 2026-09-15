# Build from the repository root. This is a worker image, NEVER the backend image.
# Pin the published output digest in LOMA_WORKER_IMAGE after containment tests.
FROM node:22-bookworm-slim AS native
RUN npm install --prefix /opt/native --omit=dev @openai/codex@0.153.3 \
    && mkdir /out \
    && find /opt/native/node_modules -type f -name codex -path '*/vendor/*' > /out/paths \
    && test "$(wc -l < /out/paths)" -eq 1 \
    && cp "$(cat /out/paths)" /out/codex \
    && chmod 755 /out/codex

FROM python:3.12-slim
RUN pip install --no-cache-dir aiohttp==3.14.3
COPY --from=native /out/codex /usr/local/bin/codex
WORKDIR /opt/worker
# Explicit source allowlist: no app, pools, tools, .env, accounts, config or DB.
COPY isolation/__init__.py isolation/protocol.py isolation/model_bridge.py \
     isolation/codex_worker.py isolation/worker_entry.py /opt/worker/isolation/
ENV PYTHONPATH=/opt/worker PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/workspace
USER 65532:65532
WORKDIR /workspace
ENTRYPOINT ["python", "-m", "isolation.worker_entry"]

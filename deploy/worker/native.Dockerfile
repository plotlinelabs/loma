# Build on a dedicated CI host, test containment, then pin the output digest.
# This image contains native CLIs, NOT the backend or personal-account stores.
FROM node:22-bookworm-slim AS native
RUN npm install --prefix /opt/native --omit=dev @openai/codex@0.153.3 @anthropic-ai/claude-code@2.1.261 opencode-ai@1.18.28 \
    && mkdir /out \
    && find /opt/native/node_modules -type f -name codex -path '*/vendor/*' > /out/paths \
    && test "$(wc -l < /out/paths)" -eq 1 \
    && cp "$(cat /out/paths)" /out/codex \
    && chmod 755 /out/codex

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir aiohttp==3.14.3
COPY --from=native /usr/local/bin/node /usr/local/bin/node
COPY --from=native /opt/native /opt/native
COPY --from=native /out/codex /usr/local/bin/codex
RUN ln -s /opt/native/node_modules/.bin/claude /usr/local/bin/claude \
    && ln -s /opt/native/node_modules/.bin/opencode /usr/local/bin/opencode
# Fail the build if a native binary or its shared-library dependencies are absent.
RUN codex --version | grep -qx 'codex-cli 0.153.3' \
    && claude --version | grep -qx '2.1.261 (Claude Code)' \
    && opencode --version | grep -qx '1.18.28'
WORKDIR /opt/worker
COPY isolation/__init__.py isolation/protocol.py isolation/model_bridge.py \
     isolation/codex_worker.py isolation/claude_worker.py isolation/opencode_worker.py \
     isolation/mcp_bridge.py isolation/worker_entry.py isolation/workspace_tools.py \
     isolation/workspace.py isolation/artifacts.py /opt/worker/isolation/
ENV PYTHONPATH=/opt/worker PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/workspace
USER 65532:65532
WORKDIR /workspace
ENTRYPOINT ["python", "-m", "isolation.worker_entry"]

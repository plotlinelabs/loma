# Login-only image. No backend, account stores, tool gateway or agent runner.
FROM node:22-bookworm-slim AS cli
RUN npm install --prefix /opt/claude --omit=dev @anthropic-ai/claude-code@2.1.261
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=cli /usr/local/bin/node /usr/local/bin/node
COPY --from=cli /opt/claude /opt/claude
RUN ln -s /opt/claude/node_modules/.bin/claude /usr/local/bin/claude \
    && claude --version | grep -qx '2.1.261 (Claude Code)'
COPY isolation/__init__.py isolation/claude_login_worker.py /opt/login/isolation/
ENV PYTHONPATH=/opt/login PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
WORKDIR /workspace
ENTRYPOINT ["python", "-m", "isolation.claude_login_worker"]

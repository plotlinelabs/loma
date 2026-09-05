FROM python:3.12-slim AS worker-base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm git curl ca-certificates \
    poppler-utils tesseract-ocr openssh-client \
    && OPENCODE_INSTALL_DIR=/usr/local/bin sh -c 'curl -fsSL https://opencode.ai/install | bash' \
    && opencode --version \
    && npm install -g @anthropic-ai/claude-code \
    && claude --version \
    # Codex CLI pinned: the app-server protocol is not stability-guaranteed,
    # so version bumps must go through CI (see agent/codex_runtime.py)
    && npm install -g @openai/codex@0.153.3 \
    && codex --version \
    && pip install --no-cache-dir uv \
    && uv --version \
    && npm install -g \
        mongodb-mcp-server \
        @notionhq/notion-mcp-server \
        @hubspot/mcp-server \
        @sentry/mcp-server \
        @lishenxydlgzs/aws-athena-mcp \
    && rm -rf /var/lib/apt/lists/* /root/.npm

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# This image contains runtimes and public dependencies, never application code,
# .env files, account directories, or backend signing/provider credentials.
COPY broker/sandbox_entry.py /usr/local/lib/loma_worker_entry.py
COPY tools/pptx_creator.py /opt/loma-render/tools/pptx_creator.py
RUN printf '1\n' > /.loma-worker-image && mkdir -p /run/loma /opt/loma-render/ppt/output

FROM worker-base AS backend
# Independent read-only image for each OCI worker, not the backend rootfs.
COPY --from=worker-base / /opt/loma-worker-rootfs
# Install the authenticated gVisor distribution only into the trusted backend.
RUN apt-get update && apt-get install -y --no-install-recommends gnupg \
    && curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o /usr/share/keyrings/gvisor.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor.gpg] https://storage.googleapis.com/gvisor/releases release main" > /etc/apt/sources.list.d/gvisor.list \
    && apt-get update && apt-get install -y --no-install-recommends runsc \
    && rm -rf /var/lib/apt/lists/*
COPY . .
ENV LOMA_WORKER_SANDBOX=runsc
ENV LOMA_WORKER_ROOTFS=/opt/loma-worker-rootfs
ENV PYTHONPATH=/app
ENV WEBHOOK_PORT=3000
# Dedicated non-root identity for isolated per-run agent workers. The backend
# (root in this image) drops every worker subprocess to this uid/gid, so
# malicious run code cannot read backend-owned files (.env, /root/.ssh-host,
# other runs' workspaces). See broker/worker.py and docs/execution-security.md.
RUN groupadd -g 990 loma-worker \
    && useradd -u 990 -g 990 -M -s /usr/sbin/nologin loma-worker \
    && chmod 700 /root
ENV LOMA_WORKER_UID=990
ENV LOMA_WORKER_GID=990
ENV LOMA_WORKER_UID_RANGE=200000-200999
# Persistent in-container workspace for cloning & running repos (named volume
# loma-workspace -> /opt/loma-workspace in docker-compose.yml). Lets long-running
# agent tasks keep their clone across container recreation, unlike /tmp's
# ephemeral writable overlay. Exposed as $LOMA_WORKSPACE_DIR to the agent process.
ENV LOMA_WORKSPACE_DIR=/opt/loma-workspace
RUN mkdir -p /opt/loma-workspace
# SSH config for reaching the ops host. The host's ~/.ssh is bind-mounted
# read-only at /root/.ssh-host (see docker-compose.yml); /root/.ssh itself
# stays writable so known_hosts can persist.
RUN mkdir -p /root/.ssh \
    && printf 'Host 98.83.133.237\n  User ubuntu\n  IdentityFile /root/.ssh-host/id_rsa\n  StrictHostKeyChecking accept-new\n' > /root/.ssh/config \
    && chmod 700 /root/.ssh && chmod 600 /root/.ssh/config
EXPOSE 3000
CMD ["python", "app.py"]

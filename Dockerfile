FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm git curl ca-certificates \
    poppler-utils tesseract-ocr openssh-client \
    && OPENCODE_INSTALL_DIR=/usr/local/bin sh -c 'curl -fsSL https://opencode.ai/install | bash' \
    && ln -sf /root/.opencode/bin/opencode /usr/local/bin/opencode \
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

COPY . .
ENV PYTHONPATH=/app
ENV WEBHOOK_PORT=3000
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

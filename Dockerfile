FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm git curl ca-certificates \
    poppler-utils tesseract-ocr \
    && OPENCODE_INSTALL_DIR=/usr/local/bin sh -c 'curl -fsSL https://opencode.ai/install | bash' \
    && ln -sf /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && opencode --version \
    && npm install -g @anthropic-ai/claude-code \
    && claude --version \
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
EXPOSE 3000
CMD ["python", "app.py"]

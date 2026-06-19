FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm git curl ca-certificates \
    && OPENCODE_INSTALL_DIR=/usr/local/bin sh -c 'curl -fsSL https://opencode.ai/install | bash' \
    && ln -sf /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && opencode --version \
    && npm install -g @anthropic-ai/claude-code \
    && claude --version \
    && pip install --no-cache-dir uv \
    && uv --version \
    && rm -rf /var/lib/apt/lists/* /root/.npm

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV PYTHONPATH=/app
ENV WEBHOOK_PORT=3000
EXPOSE 3000
CMD ["python", "app.py"]

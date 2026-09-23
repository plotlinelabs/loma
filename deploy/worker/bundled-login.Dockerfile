# Only immutable CLI/runtime files go in the chroot, never application code.
FROM node:22-bookworm-slim AS runtime
RUN npm install -g @anthropic-ai/claude-code@2.1.261 \
    && claude --version | grep -qx '2.1.261 (Claude Code)' \
    && rm -rf /root/.npm /tmp/* \
    && mkdir -p /sessions /dev \
    && touch /dev/null \
    && find / -xdev -type f -perm /6000 -exec chmod a-s {} +

FROM python:3.12-slim AS proxy
COPY isolation/__init__.py isolation/login_proxy.py /opt/login/isolation/
ENV PYTHONPATH=/opt/login PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
ENTRYPOINT ["python", "-m", "isolation.login_proxy"]

FROM python:3.12-slim AS broker
RUN apt-get update && apt-get install -y --no-install-recommends iptables \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir aiohttp==3.14.3
COPY --from=runtime / /sandbox/
# Docker resolver files must not override the egress proxy with external DNS.
RUN rm -f /sandbox/etc/resolv.conf && touch /sandbox/etc/resolv.conf \
    && mkdir -p /sandbox/sessions /sandbox/proc /run/loma-login
COPY isolation/__init__.py isolation/protocol.py isolation/supervisor.py \
    isolation/claude_login_worker.py isolation/bundled_login.py isolation/bundled_login_child.py /opt/login/isolation/
ENV PYTHONPATH=/opt/login PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/login
ENTRYPOINT ["python", "-m", "isolation.bundled_login"]

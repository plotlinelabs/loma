# Trusted supervisor image, NOT the model/worker image. Run on a dedicated host.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends docker.io ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/loma
RUN pip install --no-cache-dir aiohttp==3.14.3
# Supervisor needs only the wire protocol, never backend/account/tool modules.
COPY isolation/__init__.py isolation/protocol.py isolation/supervisor.py /srv/loma/isolation/
ENV PYTHONPATH=/srv/loma PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "isolation.supervisor"]

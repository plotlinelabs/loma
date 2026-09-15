# Trusted supervisor image, NOT the model/worker image. Run on a dedicated host.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends docker.io ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/loma
COPY requirements.txt /tmp/requirements.txt
# Match the existing application's aiohttp constraint without copying secrets,
# the app tree or its tool programs into this image.
RUN grep '^aiohttp' /tmp/requirements.txt > /tmp/transport.txt \
    && pip install --no-cache-dir -r /tmp/transport.txt
COPY isolation /srv/loma/isolation
ENV PYTHONPATH=/srv/loma PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "isolation.supervisor"]

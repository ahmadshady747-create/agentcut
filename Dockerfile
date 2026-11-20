FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENTCUT_HOME=/data/agentcut

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libharfbuzz0b \
    fontconfig \
    fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml VERSION README.md LICENSE ./
COPY src ./src
COPY fonts ./fonts

RUN pip install --no-cache-dir --no-build-isolation --no-deps . \
    && mkdir -p /data/agentcut

VOLUME ["/data/agentcut"]
ENTRYPOINT ["agentcut"]
CMD ["serve"]

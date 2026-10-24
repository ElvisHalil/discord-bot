# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

# Prevent Python from writing .pyc files to disk and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/botuser \
    XDG_CACHE_HOME=/tmp/.cache \
    HF_HOME=/home/botuser/.cache/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    HF_HUB_VERBOSITY=error

# Install minimal system dependencies, voice codecs (Opus/Sodium/FFmpeg) & sandboxed CLI utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
    git \
    jq \
    libopus0 \
    libsodium23 \
    procps \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Create an unprivileged user (UID/GID 1000) and isolated sandbox workspace
RUN groupadd -g 1000 botuser \
    && useradd -m -u 1000 -g 1000 -s /bin/bash botuser \
    && mkdir -p /home/botuser/workspace /home/botuser/.cache/huggingface /app/data \
    && chown -R botuser:botuser /home/botuser /app/data

WORKDIR /app

# Install Python dependencies as root so packages in /usr/local are read-only to botuser
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source owned by root:root (read-only to botuser so sandboxed
# commands executed by the bot can never tamper with the bot's own source code)
COPY --chown=root:root main.py README.md ./
COPY --chown=root:root src/ ./src/
COPY --chown=root:root tests/ ./tests/

# Drop privileges to the non-root user
ENV MPLCONFIGDIR=/tmp/matplotlib
USER 1000:1000

CMD ["python", "main.py"]


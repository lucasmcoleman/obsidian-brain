# Obsidian Brain MCP server — streamable-HTTP, self-contained image.
# Build context is this project dir so the brain modules are baked in.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OBSIDIAN_VAULT_PATH=/vault \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    MCP_PATH=/mcp

WORKDIR /app

# libgomp1: required by faiss-cpu wheels. curl: healthcheck. tzdata: lets the
# nightly refresh honor a local TZ (set via the TZ env in compose).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install deps first so the layer caches across code-only changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Application code (brain modules + MCP server).
COPY *.py ./

# Run as a non-root user. UID defaults to 1000 to match the
# host vault owner so the bind-mounted vault + backups stay writable; override
# with --build-arg BRAIN_UID=<host-uid> if your vault is owned differently.
ARG BRAIN_UID=1000
RUN useradd -m -u ${BRAIN_UID} -s /usr/sbin/nologin brain \
    && chown -R brain /app
USER brain

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["python", "mcp_server.py"]

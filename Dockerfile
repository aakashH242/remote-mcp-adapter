FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
LABEL io.modelcontextprotocol.server.name="io.github.aakashH242/remote-mcp-adapter"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
RUN set -eux; \
    groupadd --gid 1000 appuser; \
    useradd  --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin appuser; \
    mkdir -p /app; \
    chown -R appuser:appuser /app
WORKDIR /app
COPY [ "pyproject.toml",  "uv.lock",  "README.md",  "src",  "./" ]

RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
CMD ["python", "-m", "remote_mcp_adapter"]




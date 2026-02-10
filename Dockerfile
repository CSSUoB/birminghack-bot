FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS builder

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y git=1:2.* --no-install-recommends

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-group dev

COPY . .

FROM python:3.13-slim-trixie

COPY --from=builder --chown=app:app /app /app

ENV LANG=C.UTF-8 PATH="/app/.venv/bin:$PATH"

WORKDIR /app

ENTRYPOINT ["python", "-m", "main"]

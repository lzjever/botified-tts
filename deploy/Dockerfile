FROM ghcr.io/astral-sh/uv:0.10.8@sha256:f99c19c9683591761e0dc9d80db421b17d8c004adf4ac4031cac1fc92777f091 AS uv

FROM nvidia/cuda:12.6.3-runtime-ubuntu24.04@sha256:2c8193530ecc423e0f123d0c85b68a15d1395adcddabfc943e2523dbfde172e1

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/app/.venv/bin:/root/.local/bin:${PATH}" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        ffmpeg=7:6.1.1-3ubuntu5 \
        gcc \
        git \
        libc6-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN uv python install 3.12.13

ENV UV_HTTP_TIMEOUT=300

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=5s --retries=180 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).read()"]

CMD ["botified-tts"]

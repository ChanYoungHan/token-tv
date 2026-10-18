FROM node:22-bookworm-slim

ARG VCS_REF=unknown
LABEL org.opencontainers.image.source="https://github.com/ChanYoungHan/token-tv" \
    org.opencontainers.image.revision="${VCS_REF}" \
    org.opencontainers.image.licenses="MIT"

ARG CLAUDE_VERSION=2.1.296
ARG CODEX_VERSION=0.162.1

ENV PIPX_HOME=/opt/pipx \
    PIPX_BIN_DIR=/usr/local/bin \
    XDG_CONFIG_HOME=/data \
    CLAUDE_CONFIG_DIR=/data/token-tv/homes/claude_a \
    CODEX_HOME=/data/token-tv/homes/codex_a \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DISABLE_AUTOUPDATER=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv pipx ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global "@anthropic-ai/claude-code@${CLAUDE_VERSION}" "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force

WORKDIR /tmp/token-tv-build
COPY pyproject.toml README.md LICENSE ./
COPY token_tv ./token_tv
RUN pipx install . \
    && rm -rf /tmp/token-tv-build \
    && useradd --create-home --uid 10001 --shell /bin/bash tokentv \
    && mkdir -p /data/token-tv/homes/claude_a /data/token-tv/homes/codex_a \
    && chown -R tokentv:tokentv /data \
    && chmod 700 /data /data/token-tv/homes/claude_a /data/token-tv/homes/codex_a

COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/token-tv-entrypoint
USER tokentv
WORKDIR /home/tokentv
EXPOSE 8787
ENTRYPOINT ["token-tv-entrypoint"]
CMD ["token-tv", "run", "--host", "0.0.0.0"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=3).read()"

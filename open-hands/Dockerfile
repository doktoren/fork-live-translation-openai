# syntax=docker/dockerfile:1.16
# check=error=true

FROM docker.all-hands.dev/all-hands-ai/runtime:0.40-nikolaik
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y pipx \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
RUN pipx install mcp-server-fetch

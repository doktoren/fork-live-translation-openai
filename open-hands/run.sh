#!/bin/bash

set -e

docker build -t all-hands-runtime:0.40 .

docker run -it --rm --pull=always \
    -e SANDBOX_RUNTIME_CONTAINER_IMAGE=all-hands-runtime:0.40 \
    -e LOG_ALL_EVENTS=true \
    -e LLM_MODEL=anthropic/claude-sonnet-4-20250514 \
    -e LLM_API_KEY=$ANTHROPIC_API_KEY \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v ~/.openhands-state:/.openhands-state \
    -p 3000:3000 \
    --add-host host.docker.internal:host-gateway \
    --name openhands-app \
    docker.all-hands.dev/all-hands-ai/openhands:0.40

# Not possible :-(  -e GITHUB_TOKEN=$GITHUB_TOKEN
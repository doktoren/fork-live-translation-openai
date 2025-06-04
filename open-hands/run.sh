#!/bin/bash

set -e

mkdir -p $(pwd)/.openhands-state

export SANDBOX_VOLUMES=/home/jtk/workspace/fork-live-translation-openai:/workspace:rw

# Gah! It seems that most of these environment values are ignored :-(
docker run -it --rm --pull=always \
    -e SANDBOX_RUNTIME_CONTAINER_IMAGE=docker.all-hands.dev/all-hands-ai/runtime:0.40-nikolaik \
    -e SANDBOX_VOLUMES=$SANDBOX_VOLUMES \
    -e LOG_ALL_EVENTS=true \
    -e LLM_MODEL=anthropic/claude-sonnet-4-20250514 \
    -e LLM_BASE_URL=https://api.anthropic.com \
    -e LLM_API_KEY=$ANTHROPIC_API_KEY \
    -e GITHUB_TOKEN=$GITHUB_TOKEN \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v $(pwd)/.openhands-state:/.openhands-state \
    --add-host host.docker.internal:host-gateway \
    --network=host \
    --name openhands-app \
    docker.all-hands.dev/all-hands-ai/openhands:0.40

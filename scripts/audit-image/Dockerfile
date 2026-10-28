FROM node:24.21.0-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS node
FROM python:3.14@sha256:46a17be7cf526995fbf12177b40c248de30b40c1a509a4c7e5da954226f6885b
RUN apt-get update && apt-get install -y --no-install-recommends jq && rm -rf /var/lib/apt/lists/*
RUN git config --system --add safe.directory /target
COPY --from=node /usr/local/bin/node /usr/local/bin/node

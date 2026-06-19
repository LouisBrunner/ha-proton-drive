FROM mcr.microsoft.com/devcontainers/python:3.1.2-3.14-bookworm AS glibc
RUN sudo apt-get update \
    && sudo apt-get install -y --no-install-recommends ffmpeg libturbojpeg0 libpcap-dev \
    && sudo rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /project
USER vscode
ENTRYPOINT ["sleep", "infinity"]

FROM python:3.14.6-alpine3.24 AS musl
RUN apk add --no-cache g++ ffmpeg libjpeg-turbo-dev libpcap-dev linux-headers make
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN addgroup -S app && adduser -S -G app app
WORKDIR /project
USER app
ENTRYPOINT ["sleep", "infinity"]

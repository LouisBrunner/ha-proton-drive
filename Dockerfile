FROM ghcr.io/home-assistant/homeassistant-base:2026.05.0
RUN apk add --no-cache make
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN addgroup -S app && adduser -S -G app app
WORKDIR /project
USER app
ENV PATH=$PATH:/home/app/.local/bin
ENTRYPOINT ["sleep", "infinity"]

FROM ghcr.io/home-assistant/homeassistant-base:2026.05.0
RUN apk add --no-cache make==4.4.1-r3
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN addgroup -S app && adduser -S -G app app
WORKDIR /project
RUN touch /.indocker
RUN mkdir -p /home/app/.cache/uv && chown -R app:app /home/app/.cache/uv
USER app
HEALTHCHECK CMD true
ENTRYPOINT ["sleep", "infinity"]

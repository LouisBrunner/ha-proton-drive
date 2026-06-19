FROM mcr.microsoft.com/devcontainers/python:3.13 AS glibc
RUN sudo apt update && apt install ffmpeg libturbojpeg0 libpcap-dev -y
WORKDIR /project
USER vscode
ENTRYPOINT ["sleep", "infinity"]

FROM python:3.13-alpine3.24 AS musl
RUN apk add --no-cache g++ ffmpeg libjpeg-turbo-dev libpcap-dev
WORKDIR /project
ENTRYPOINT ["sleep", "infinity"]

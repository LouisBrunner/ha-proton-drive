FROM mcr.microsoft.com/devcontainers/python:3.13
RUN sudo apt update && apt install ffmpeg libturbojpeg0 libpcap-dev -y
WORKDIR /project
USER vscode
ENTRYPOINT ["sleep", "infinity"]

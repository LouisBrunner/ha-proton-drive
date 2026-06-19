"""Constants for the Proton Drive integration."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "proton_drive"

CONF_BACKUP_FOLDER = "backup_folder"

CLI_VERSION = "0.4.6"

CLI_BASE_URL_FORMAT: dict[str, str] = {
    "glibc": "https://proton.me/download/drive/cli/{version}/linux-{arch}/proton-drive",
    "musl": "https://github.com/LouisBrunner/proton-sdk/releases/download/v{version}/proton-drive-linux-{arch}-musl",
}
CLI_CHECKSUMS: dict[str, str] = {
    "glibc-arm64": (
        "92b48ccb82f6480759aba1021546ab487c2baef93c985a2fd362d5a576693326"
        "8cd039c546786efc641b5c2cdb600c1211e1d92f343059676b8461bb21d47117"
    ),
    "glibc-x64": (
        "d187409932742e6fdc6aae2995998f4c89ea51999283395bc8d0bdc5343a79d3"
        "1bf5a485d5af9adf3b7909fc92f2d2ef0b133edc4939d5faf1d096eb744425bb"
    ),
    "musl-arm64": (
        "a04559c10a2d59ebd015ff75ad76f4a1244e1f53f0c395693fa0d9ec94f6e377"
        "e3bd392147d915df7f8a30387933378c3fa5f5af87e23dc8efcc45008cecf70b"
    ),
    "musl-x64": (
        "85ad94c9b09afac6c20c3eb166943a178396ee2aeff849e0a9c27e79063615ea"
        "30ba9f50d48de6d3452a1562189a6dcea028cdac1f09a3986da78a99fb808e0d"
    ),
}

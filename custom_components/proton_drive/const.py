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
        "45fc7bb512286689efcd6a0dddc8087db6e308b83aa69c71257879ce5aa5c548"
        "81aaf3b635ac1963df4108158aa672b277c319bd8c35633516ec2f27f344a195"
    ),
    "musl-x64": (
        "ec52adaa4872221f29ed4122d485bbdb4caf89021171706974ac68bce3da1fbd"
        "c87dfe4bdff50e207561438ed5d302445bfab3ac83bab90e4950c7d9331a8b5a"
    ),
}

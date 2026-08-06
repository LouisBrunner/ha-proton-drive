"""Constants for the Proton Drive integration."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "proton_drive"

CONF_BACKUP_FOLDER = "backup_folder"
CONF_LAST_CLI_RUN = "last_cli_run"

CLI_VERSION = "0.5.0"

CLI_BASE_URL_FORMAT: dict[str, str] = {
    "glibc": "https://proton.me/download/drive/cli/{version}/linux-{arch}/proton-drive",
    "musl": "https://proton.me/download/drive/cli/{version}/linux-{arch}-musl/proton-drive",
}
CLI_CHECKSUMS: dict[str, str] = {
    "glibc-arm64": (
        "a679e1e09d29413452a6ac24664dbd249bcafa1fb208e24b9c04133cd97488bf6"
        "86d350cfcd2522742ac69de428142ac65cb56eb11f25260d3b4ffaa57d39054"
    ),
    "glibc-x64": (
        "d85edbc57412c92a9705b70a8d3a5c66ad933331554d6b922b912d6df29b4e5e9"
        "b0d7a940a594927dd4788e1f8db86d5e9a23f084f07dbd5327f7a9e51d61272"
    ),
    "musl-arm64": (
        "5dea0dffd08bd14570c7c50f1e85221b91312c04b0d6684109f909dcd568050db"
        "869e48158d93534c619f85e67731e3df4cf467a7ee81350b2f2af8e97a4a171"
    ),
    "musl-x64": (
        "faf5a227b054168eb3a4b4fee3264753976c1848ccab4554203082bc6bfabe455"
        "bd306c9277d41d5e86104e8b96312ed814aa6ae26a781b59d1d020a27732e61"
    ),
}

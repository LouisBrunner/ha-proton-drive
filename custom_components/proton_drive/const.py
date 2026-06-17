"""Constants for the Proton Drive integration."""

from logging import Logger, getLogger
from typing import Any, Protocol

LOGGER: Logger = getLogger(__package__)

DOMAIN = "proton_drive"

CONF_BACKUP_FOLDER = "backup_folder"

CLI_VERSION = "0.4.4"
CLI_DOWNLOAD_BASE = "https://proton.me/download/drive/cli"
CLI_CHECKSUMS: dict[str, str] = {
    "arm64": (
        "809b50357ea6ea01492ef68c101b17ce09393276d5058081b3864b696aec99f6"
        "830f9be357a37e895ba5c101c1a8c43884395a0422ea9b30cd3ec6c1bee39c2a"
    ),
    "x64": (
        "7ae6700ddd4479c976a787bba46dd610b0037c5b17bd71f06519ced9af6ddf75"
        "e7b9d9b7f87ad2daf8be981b7ac072960c5855b23429a1442fc8f389707ede6e"
    ),
}


# FIXME: delete when targeting later HA versions
class OnProgressCallback(Protocol):
    """Ponyfill for older HA versions."""

    def __call__(self, *, bytes_uploaded: int, **kwargs: Any) -> None:
        """Report upload progress."""

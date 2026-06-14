"""
Custom integration to integrate Proton Drive with Home Assistant.

For more details about this integration, please refer to
https://github.com/LouisBrunner/ha-proton-drive
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import instance_id

from .api import (
    ProtonDriveClient,
)
from .cli import (
    AuthError,
    CLIError,
    CLIStartupError,
    ProtonCLI,
)
from .const import CONF_BACKUP_FOLDER
from .data import DATA_BACKUP_AGENT_LISTENERS, ProtonDriveData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import ProtonDriveConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProtonDriveConfigEntry,
) -> bool:
    """Set up Proton Drive from a config entry."""
    if not entry.data.get(CONF_BACKUP_FOLDER):
        msg = "This is a legacy configuration, please delete it and recreate it from scratch."
        raise ConfigEntryNotReady(msg)

    try:
        cli = await ProtonCLI.create(hass)
    except CLIStartupError as e:
        raise ConfigEntryNotReady(str(e)) from e

    try:
        client = ProtonDriveClient(
            hass=hass,
            cli=cli,
            instance_id=await instance_id.async_get(hass),
            backup_folder=entry.data[CONF_BACKUP_FOLDER],
        )
    except AuthError as e:
        raise ConfigEntryAuthFailed(str(e)) from e
    except CLIError as e:
        raise ConfigEntryNotReady(str(e)) from e

    entry.runtime_data = ProtonDriveData(
        client=client,
    )

    def async_notify_backup_listeners() -> None:
        for listener in hass.data.get(DATA_BACKUP_AGENT_LISTENERS, []):
            listener()

    entry.async_on_unload(entry.async_on_state_change(async_notify_backup_listeners))

    return True


async def async_unload_entry(_hass: HomeAssistant, _entry: ProtonDriveConfigEntry) -> bool:
    """Unload a config entry."""
    return True

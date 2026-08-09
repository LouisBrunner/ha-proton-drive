"""
Custom integration to integrate Proton Drive with Home Assistant.

For more details about this integration, please refer to
https://github.com/LouisBrunner/ha-proton-drive
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import instance_id

from .api import (
    ProtonDriveClient,
)
from .cli import (
    APIUnavailableError,
    AuthError,
    CLIError,
    CLIStartupError,
    DownloadCLINetworkError,
    ProtonCLI,
)
from .const import CONF_BACKUP_FOLDER, LOGGER
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
        raise ConfigEntryError(msg)

    cli = await _create_cli(hass)

    if entry.title in ("CLI", "Proton Drive"):
        try:
            email = await cli.get_email()
            hass.config_entries.async_update_entry(entry, title=email)
        except CLIError:
            LOGGER.warning("Failed to fetch user email to update entry title")

    client = await _create_client(hass, entry, cli)

    entry.runtime_data = ProtonDriveData(
        client=client,
    )

    def async_notify_backup_listeners() -> None:
        for listener in hass.data.get(DATA_BACKUP_AGENT_LISTENERS, []):
            listener()

    entry.async_on_unload(entry.async_on_state_change(async_notify_backup_listeners))

    return True


async def _create_cli(hass: HomeAssistant) -> ProtonCLI:
    try:
        return await ProtonCLI.create(hass)
    except DownloadCLINetworkError as e:
        raise ConfigEntryNotReady(str(e)) from e
    except CLIStartupError as e:
        raise ConfigEntryError(str(e)) from e


async def _create_client(hass: HomeAssistant, entry: ProtonDriveConfigEntry, cli: ProtonCLI) -> ProtonDriveClient:
    try:
        return await ProtonDriveClient.create(
            hass=hass,
            cli=cli,
            instance_id=await instance_id.async_get(hass),
            backup_folder=entry.data[CONF_BACKUP_FOLDER],
        )
    except AuthError as e:
        raise ConfigEntryAuthFailed(str(e)) from e
    except APIUnavailableError as e:
        raise ConfigEntryNotReady(str(e)) from e
    except CLIError as e:
        raise ConfigEntryError(str(e)) from e


async def async_unload_entry(_hass: HomeAssistant, entry: ProtonDriveConfigEntry) -> bool:
    """Unload a config entry."""
    entry.runtime_data.client.aclose()
    return True

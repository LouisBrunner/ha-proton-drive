"""
Custom integration to integrate Proton Drive with Home Assistant.

For more details about this integration, please refer to
https://github.com/LouisBrunner/ha-proton-drive
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import instance_id

from custom_components.proton_drive.const import (
    CONF_ROOT_FOLDER,
    CONF_SHARE_ID,
)

from .api import (
    ProtonDriveAPIAuthenticationError,
    ProtonDriveAPICaptchaError,
    ProtonDriveAPIError,
    ProtonDriveAPIMFAError,
    ProtonDriveClient,
)
from .data import DATA_BACKUP_AGENT_LISTENERS, ProtonDriveData
from .helpers import create_credentials_from_data, serialize_credentials_to_data

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from proton import Credentials

    from .data import ProtonDriveConfigEntry


def update_config(
    hass: HomeAssistant, current: ProtonDriveConfigEntry, creds: Credentials
) -> None:
    """Update the config entry with new credentials."""

    @callback
    def async_update_entry() -> None:
        hass.config_entries.async_update_entry(
            current,
            data={
                **current.data,
                **serialize_credentials_to_data(creds),
            },
        )

    hass.add_job(async_update_entry)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProtonDriveConfigEntry,
) -> bool:
    """Set up Proton Drive from a config entry."""
    ProtonDriveClient.configure_logger()

    creds = create_credentials_from_data(entry.data)

    try:
        client = ProtonDriveClient(
            hass=hass,
            creds=creds,
            instance_id=await instance_id.async_get(hass),
            base_folder=entry.data[CONF_ROOT_FOLDER],
            share_id=entry.data[CONF_SHARE_ID],
            update_creds=lambda creds: update_config(hass, entry, creds),
        )
    except (
        ProtonDriveAPIAuthenticationError,
        ProtonDriveAPIMFAError,
        ProtonDriveAPICaptchaError,
    ) as e:
        raise ConfigEntryAuthFailed from e
    except ProtonDriveAPIError as e:
        raise ConfigEntryNotReady from e

    entry.runtime_data = ProtonDriveData(
        client=client,
    )

    def async_notify_backup_listeners() -> None:
        for listener in hass.data.get(DATA_BACKUP_AGENT_LISTENERS, []):
            listener()

    entry.async_on_unload(entry.async_on_state_change(async_notify_backup_listeners))

    return True


async def async_unload_entry(
    _hass: HomeAssistant, _entry: ProtonDriveConfigEntry
) -> bool:
    """Unload a config entry."""
    return True

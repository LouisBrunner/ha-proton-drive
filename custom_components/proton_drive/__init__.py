"""
Custom integration to integrate Proton Drive with Home Assistant.

For more details about this integration, please refer to
https://github.com/LouisBrunner/ha-proton-drive
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import instance_id
from homeassistant.loader import async_get_loaded_integration
from proton.proton import Credentials

from custom_components.proton_drive.const import (
    CONF_CREDS_ACCESS_TOKEN,
    CONF_CREDS_REFRESH_TOKEN,
    CONF_CREDS_SALTED_KEY_PASS,
    CONF_CREDS_UID,
    CONF_ROOT_FOLDER,
)

from .api import (
    ProtonDriveAPIAuthenticationError,
    ProtonDriveAPIError,
    ProtonDriveAPIMFAError,
    ProtonDriveClient,
)
from .data import DATA_BACKUP_AGENT_LISTENERS, ProtonDriveData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import ProtonDriveConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProtonDriveConfigEntry,
) -> bool:
    """Set up Proton Drive from a config entry."""
    creds = Credentials(
        UID=entry.data[CONF_CREDS_UID],
        AccessToken=entry.data[CONF_CREDS_ACCESS_TOKEN],
        RefreshToken=entry.data[CONF_CREDS_REFRESH_TOKEN],
        SaltedKeyPass=entry.data[CONF_CREDS_SALTED_KEY_PASS],
    )

    try:
        client = ProtonDriveClient(
            hass=hass,
            creds=creds,
            instance_id=await instance_id.async_get(hass),
            base_folder=entry.data[CONF_ROOT_FOLDER],
        )
    except (ProtonDriveAPIAuthenticationError, ProtonDriveAPIMFAError) as e:
        raise ConfigEntryAuthFailed from e
    except ProtonDriveAPIError as e:
        raise ConfigEntryNotReady from e

    entry.runtime_data = ProtonDriveData(
        client=client,
        integration=async_get_loaded_integration(hass, entry.domain),
    )

    def async_notify_backup_listeners() -> None:
        for listener in hass.data.get(DATA_BACKUP_AGENT_LISTENERS, []):
            listener()

    entry.async_on_unload(entry.async_on_state_change(async_notify_backup_listeners))

    return True

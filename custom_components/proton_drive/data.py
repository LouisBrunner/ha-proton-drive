"""Custom types for Proton Drive."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.util.hass_dict import HassKey

from .api import ProtonDriveClient
from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry

    from .api import ProtonDriveClient
    from .data import ProtonDriveConfigEntry


DATA_BACKUP_AGENT_LISTENERS: HassKey[list[Callable[[], None]]] = HassKey(
    f"{DOMAIN}.backup_agent_listeners"
)

type ProtonDriveConfigEntry = ConfigEntry[ProtonDriveData]


@dataclass
class ProtonDriveData:
    """Data for the Proton Drive integration."""

    client: ProtonDriveClient

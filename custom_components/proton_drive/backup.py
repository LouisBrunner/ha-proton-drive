"""Backup agent for Proton Drive."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.backup import (
    AgentBackup,
    BackupAgent,
    BackupAgentError,
    BackupNotFound,
    OnProgressCallback,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .api import ProtonDriveError
from .cli import CLIError
from .const import DOMAIN, LOGGER
from .data import DATA_BACKUP_AGENT_LISTENERS, ProtonDriveConfigEntry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine

    from homeassistant.core import HomeAssistant


async def async_get_backup_agents(
    hass: HomeAssistant,
) -> list[BackupAgent]:
    """Return a list of backup agents."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return [ProtonDriveBackupAgent(entry) for entry in entries]


@callback
def async_register_backup_agents_listener(
    hass: HomeAssistant,
    *,
    listener: Callable[[], None],
    **_kwargs: Any,
) -> Callable[[], None]:
    """
    Register a listener to be called when agents are added or removed.

    :return: A function to unregister the listener.
    """
    hass.data.setdefault(DATA_BACKUP_AGENT_LISTENERS, []).append(listener)

    @callback
    def remove_listener() -> None:
        hass.data[DATA_BACKUP_AGENT_LISTENERS].remove(listener)
        if not hass.data[DATA_BACKUP_AGENT_LISTENERS]:
            del hass.data[DATA_BACKUP_AGENT_LISTENERS]

    return remove_listener


class ProtonDriveBackupAgent(BackupAgent):
    """Proton Drive backup agent."""

    domain = DOMAIN
    unique_id = "CLI"

    def __init__(self, config_entry: ProtonDriveConfigEntry) -> None:
        """Initialize the cloud backup sync agent."""
        super().__init__()
        self.name = config_entry.title
        self._client = config_entry.runtime_data.client

    async def async_download_backup(
        self,
        backup_id: str,
        **_kwargs: Any,
    ) -> AsyncIterator[bytes]:
        """
        Download a backup file.

        :param backup_id: The ID of the backup that was returned in async_list_backups.
        :return: An async iterator that yields bytes.
        """
        LOGGER.debug("Downloading backup_id: %s", backup_id)
        try:
            # Do not await! We want to return it as-is so the caller can safely read the iterator
            # otherwise the file will be deleted before the caller can read the data.
            return self._client.download_backup(backup_id)
        except (ProtonDriveError, CLIError, HomeAssistantError, TimeoutError) as err:
            msg = f"Failed to download backup: {err}"
            raise BackupAgentError(msg) from err

    async def async_upload_backup(
        self,
        *,
        open_stream: Callable[[], Coroutine[Any, Any, AsyncIterator[bytes]]],
        backup: AgentBackup,
        on_progress: OnProgressCallback,
        **_kwargs: Any,
    ) -> None:
        """
        Upload a backup.

        :param open_stream: A function returning an async iterator that yields bytes.
        :param backup: Metadata about the backup that should be uploaded.
        :param on_progress: A callback to report the number of uploaded bytes.
        """
        try:
            await self._client.upload_backup(open_stream, backup, on_progress=on_progress)
        except (ProtonDriveError, CLIError, HomeAssistantError, TimeoutError) as err:
            msg = f"Failed to upload backup: {err}"
            raise BackupAgentError(msg) from err

    async def async_delete_backup(
        self,
        backup_id: str,
        **_kwargs: Any,
    ) -> None:
        """
        Delete a backup file.

        :param backup_id: The ID of the backup that was returned in async_list_backups.
        """
        LOGGER.debug("Deleting backup_id: %s", backup_id)
        try:
            await self._client.delete_backup(backup_id)
            LOGGER.debug("Deleted backup_id: %s", backup_id)
        except (ProtonDriveError, CLIError, HomeAssistantError, TimeoutError) as err:
            msg = f"Failed to delete backup: {err}"
            raise BackupAgentError(msg) from err

    async def async_list_backups(self, **_kwargs: Any) -> list[AgentBackup]:
        """List backups."""
        try:
            return await self._client.list_backups()
        except (ProtonDriveError, CLIError, HomeAssistantError, TimeoutError) as err:
            msg = f"Failed to list backups: {err}"
            raise BackupAgentError(msg) from err

    async def async_get_backup(
        self,
        backup_id: str,
        **_kwargs: Any,
    ) -> AgentBackup:
        """
        Return a backup.

        Raises BackupNotFound if the backup does not exist.
        """
        backups = await self.async_list_backups()
        for backup in backups:
            if backup.backup_id == backup_id:
                return backup
        msg = f"Backup {backup_id} not found"
        raise BackupNotFound(msg)

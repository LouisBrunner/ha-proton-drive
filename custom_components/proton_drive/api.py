"""Sample API Client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from proton.api import ProtonError, Session

from .const import LOGGER

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine

    from aiohttp import StreamReader
    from homeassistant.components.backup import AgentBackup
    from homeassistant.core import HomeAssistant


class ProtonDriveAPIError(Exception):
    """Exception to indicate a general API error."""


class ProtonDriveAPIConnectionError(
    ProtonDriveAPIError,
):
    """Exception to indicate a communication error."""


class ProtonDriveAPIAuthenticationError(
    ProtonDriveAPIError,
):
    """Exception to indicate an authentication error."""


class ProtonDriveClient:
    """Client for the Proton Drive API."""

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        instance_id: str,
        email: str,
        password: str,
    ) -> None:
        """Client for the Proton Drive API."""
        self._hass = hass
        self._instance_id = instance_id
        self._email = email
        self._password = password
        self._session = Session(
            api_url="https://drive.proton.me/api/",
            user_agent="HomeAssistant",
        )
        self._authenticated = False

    async def __handle_errors(
        self, *, call: Callable[[], Any], can_authenticate: bool = True
    ) -> Any:
        try:
            return await self._hass.async_add_executor_job(call)
        except ProtonError as e:
            LOGGER.exception("proton API call failed")
            if e.code in [8002]:
                raise ProtonDriveAPIAuthenticationError from e
            if e.code in [401]:
                LOGGER.debug("proton need refresh")
                self._session.refresh()
                return await self.__handle_errors(
                    call=call, can_authenticate=can_authenticate
                )
            if e.code in [403, 10013]:
                if can_authenticate:
                    LOGGER.debug("proton need reauthentication")
                    self._authenticated = False
                    await self.__ensure_authenticated()
                    return await self.__handle_errors(
                        call=call, can_authenticate=can_authenticate
                    )
                raise ProtonDriveAPIAuthenticationError from e
            if e.code in [503]:
                raise ProtonDriveAPIConnectionError from e
            raise ProtonDriveAPIError from e

    async def get_backup_file_id(self, backup_id: str) -> str | None:
        """Get a Proton Drive file ID based on the Home Assistant backup ID."""

    async def download_backup(self, file_id: str) -> StreamReader:
        """Download a Home Assistant backup using the Proton Drive file ID."""

    async def upload_backup(
        self,
        open_stream: Callable[[], Coroutine[Any, Any, AsyncIterator[bytes]]],
        backup: AgentBackup,
    ) -> None:
        """Upload a Home Assistant backup."""

    async def delete_backup(self, file_id: str) -> None:
        """Delete a Home Assistant backup using the Proton Drive file ID."""

    async def list_backups(self) -> list[AgentBackup]:
        """List Home Assistant backups."""

    async def __ensure_authenticated(self) -> None:
        if self._authenticated:
            return
        LOGGER.debug("trying to authenticate with API")
        await self.__handle_errors(
            can_authenticate=False,
            call=lambda: self._session.authenticate(self._email, self._password),
        )
        self._authenticated = True

    async def test_connection(self) -> None:
        """Test that we can connect to the API."""
        await self.__ensure_authenticated()

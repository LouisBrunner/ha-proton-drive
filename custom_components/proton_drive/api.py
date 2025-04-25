"""Proton Drive API."""

from __future__ import annotations

import json
from contextlib import (
    AsyncContextDecorator,
    ContextDecorator,
)
from typing import TYPE_CHECKING, Any, Self

import aiofiles
import aiofiles.os
import aiofiles.tempfile
from homeassistant.components.backup import AgentBackup
from homeassistant.components.backup.util import suggested_filename
from proton.proton import Credentials, Folder, Login, NewClient, Share

from .const import LOGGER

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine
    from types import TracebackType

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


class ProtonDriveAPIMFAError(
    ProtonDriveAPIError,
):
    """Exception to indicate a multi-factor authentication error."""


class _ErrorCatcher(ContextDecorator, AsyncContextDecorator):
    def __enter__(self) -> Self:
        """Enter the context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        """Exit the context manager."""
        return self.__handle_exceptions(exc_type, exc_val, exc_tb)

    async def __aenter__(self) -> Self:
        """Enter the context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        """Exit the context manager."""
        return self.__handle_exceptions(exc_type, exc_val, exc_tb)

    def __handle_exceptions(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> bool:
        if exc_type is None or not issubclass(exc_type, RuntimeError):
            return False
        LOGGER.exception("proton API call failed")
        e = exc_val
        if "Code=9001" in str(e):
            msg = f"MFA Error: {e}"
            raise ProtonDriveAPIMFAError(msg) from e
        if "Code=8002" in str(e) or "Code=10013" in str(e):
            msg = f"Authentication Error: {e}"
            raise ProtonDriveAPIAuthenticationError(msg) from e
        msg = f"API Error: {e}"
        raise ProtonDriveAPIError(msg) from e


class ProtonDriveClient:
    """Client for the Proton Drive API."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        hass: HomeAssistant,
        instance_id: str,
        creds: Credentials,
        base_folder: str,
        share_id: str,
        update_creds: Callable[[Credentials], None],
    ) -> None:
        """Client for the Proton Drive API."""
        self._hass = hass
        self._instance_id = instance_id
        with _ErrorCatcher():
            self._client = NewClient(
                creds,
                update_creds,
            )
            if share_id != "":
                self._client.SelectShare(share_id)
        self._folder = base_folder

    async def list_shares(self) -> list[Share]:
        """List available share drives."""
        return await self.__call_api(
            hass=self._hass, call=lambda: self._client.ListShares()
        )

    async def __create_root_folder_if_needed(self) -> Folder:
        """Create the Home Assistant folder if it doesn't exist."""
        return await self.__call_api(
            hass=self._hass, call=lambda: self._client.MakeRootFolder(self._folder)
        )

    async def get_backup_file_id(self, backup_id: str) -> str | None:
        """Get a Proton Drive link ID based on the Home Assistant backup ID."""
        folder = await self.__create_root_folder_if_needed()
        try:
            return await self.__call_api(
                hass=self._hass,
                call=lambda: folder.FindBackup(self._instance_id, backup_id),
            )
        except ProtonDriveAPIError:
            return None

    async def download_backup(self, link_id: str) -> AsyncIterator[bytes]:
        """Download a Home Assistant backup using the Proton Drive link ID."""
        file_path = await self.__call_api(
            hass=self._hass,
            call=lambda: self._client.DownloadFile(link_id),
        )
        try:
            async with aiofiles.open(file_path, "rb") as file:
                while chunk := await file.read(4096):
                    yield chunk
        finally:
            await aiofiles.os.remove(file_path)

    async def upload_backup(
        self,
        open_stream: Callable[[], Coroutine[Any, Any, AsyncIterator[bytes]]],
        backup: AgentBackup,
    ) -> None:
        """Upload a Home Assistant backup."""
        folder = await self.__create_root_folder_if_needed()
        async with aiofiles.tempfile.NamedTemporaryFile() as f:
            async for chunk in await open_stream():
                await f.write(chunk)
            await f.flush()

            await self.__call_api(
                hass=self._hass,
                call=lambda: folder.Upload(
                    self._instance_id,
                    backup.backup_id,
                    suggested_filename(backup),
                    json.dumps(backup.as_dict()),
                    str(f.name),
                ),
            )

    async def delete_backup(self, link_id: str) -> None:
        """Delete a Home Assistant backup using the Proton Drive link ID."""
        await self.__call_api(
            hass=self._hass,
            call=lambda: self._client.DeleteFile(link_id),
        )

    async def list_backups(self) -> list[AgentBackup]:
        """List Home Assistant backups."""
        folder = await self.__create_root_folder_if_needed()
        metadatas = await self.__call_api(
            hass=self._hass,
            call=lambda: folder.ListFilesMetadata(self._instance_id),
        )
        return [AgentBackup.from_dict(json.loads(metadata)) for metadata in metadatas]

    @classmethod
    async def __call_api(cls, *, hass: HomeAssistant, call: Callable[[], Any]) -> Any:
        async with _ErrorCatcher():
            return await hass.async_add_executor_job(call)

    @classmethod
    async def login(
        cls,
        *,
        hass: HomeAssistant,
        username: str,
        password: str,
        mfa: str | None = None,
    ) -> Credentials:
        """Get credentials from."""
        return await cls.__call_api(
            hass=hass,
            call=lambda: Login(username, password, mfa if mfa is not None else ""),
        )

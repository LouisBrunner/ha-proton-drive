"""Proton Drive API."""

from __future__ import annotations

import json
import logging
from contextlib import (
    AsyncContextDecorator,
    ContextDecorator,
)
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

import aiofiles
import aiofiles.os
import aiofiles.tempfile
import proton
from homeassistant.components.backup import AgentBackup
from homeassistant.components.backup.util import suggested_filename

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


class ProtonDriveAPICaptchaError(
    ProtonDriveAPIError,
):
    """Exception to indicate a CAPTCHA error."""


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
        if "Code=9001" in str(e) or "2fa code is required" in str(e):
            if "please complete CAPTCHA" in str(e):
                msg = f"CAPTCHA Error: {e}"
                raise ProtonDriveAPICaptchaError(msg) from e
            msg = f"MFA Error: {e}"
            raise ProtonDriveAPIMFAError(msg) from e
        if "Code=8002" in str(e) or "Code=10013" in str(e):
            msg = f"Authentication Error: {e}"
            raise ProtonDriveAPIAuthenticationError(msg) from e
        msg = f"API Error: {e}"
        raise ProtonDriveAPIError(msg) from e


class ProtonDriveClient:
    """Client for the Proton Drive API."""

    @classmethod
    def configure_logger(cls) -> None:
        """Configure the Proton Drive logger."""
        proton.configure_logger(LOGGER)

    def __init__(  # noqa: PLR0913
        self,
        *,
        hass: HomeAssistant,
        instance_id: str,
        creds: proton.Credentials,
        base_folder: str,
        share_id: str,
        update_creds: Callable[[proton.Credentials], None],
    ) -> None:
        """Client for the Proton Drive API."""
        self._hass = hass
        self._instance_id = instance_id
        with _ErrorCatcher():
            self._client = proton.Client(
                creds=creds,
                on_auth_change=update_creds,
                share_id=share_id if share_id != "" else None,
                log_level=ProtonDriveClient._get_log_level(),
            )
        self._folder = base_folder

        # can't use /tmp as backups might be bigger than RAM: https://github.com/LouisBrunner/ha-proton-drive/issues/47
        # same as Core: https://github.com/home-assistant/core/blob/4fdbe82df23113ea811e5095b844c3f927817b53/homeassistant/components/backup/manager.py#L1646
        self.__temp_backup_dir = Path(self._hass.config.path("tmp_backups"))

    async def list_shares(self) -> list[proton.Share]:
        """List available share drives."""
        return await self.__call_api(hass=self._hass, call=self._client.list_shares)

    async def __create_root_folder_if_needed(self) -> proton.Folder:
        """Create the Home Assistant folder if it doesn't exist."""
        return await self.__call_api(
            hass=self._hass, call=lambda: self._client.make_root_folder(self._folder)
        )

    async def download_backup(self, backup_id: str) -> AsyncIterator[bytes]:
        """Download a Home Assistant backup."""
        folder = await self.__create_root_folder_if_needed()
        file_path = await self.__call_api(
            hass=self._hass,
            call=lambda: folder.download(self._instance_id, backup_id),
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
        self.__temp_backup_dir.mkdir(exist_ok=True)
        async with aiofiles.tempfile.NamedTemporaryFile(
            dir=self.__temp_backup_dir
        ) as f:
            async for chunk in await open_stream():
                await f.write(chunk)
            await f.flush()

            await self.__call_api(
                hass=self._hass,
                call=lambda: folder.upload(
                    self._instance_id,
                    backup.backup_id,
                    name=suggested_filename(backup),
                    metadata_json=json.dumps(backup.as_dict()),
                    content_path=str(f.name),
                    max_tries=3,
                    chunk_size_bytes=1 * 1024 * 1024 * 1024,  # 1 GB
                ),
            )

    async def delete_backup(self, backup_id: str) -> None:
        """Delete a Home Assistant backup."""
        folder = await self.__create_root_folder_if_needed()
        await self.__call_api(
            hass=self._hass,
            call=lambda: folder.delete(self._instance_id, backup_id),
        )

    async def list_backups(self) -> list[AgentBackup]:
        """List Home Assistant backups."""
        folder = await self.__create_root_folder_if_needed()
        metadatas = await self.__call_api(
            hass=self._hass,
            call=lambda: folder.list_files_metadata(self._instance_id),
        )
        return [AgentBackup.from_dict(json.loads(metadata)) for metadata in metadatas]

    @classmethod
    async def __call_api(cls, *, hass: HomeAssistant, call: Callable[[], Any]) -> Any:
        async with _ErrorCatcher():
            return await hass.async_add_executor_job(call)

    @classmethod
    async def login(  # noqa: PLR0913
        cls,
        *,
        hass: HomeAssistant,
        username: str,
        password: str,
        mailbox_password: str | None = None,
        mfa: str | None = None,
        captcha_token: str | None = None,
    ) -> proton.Credentials:
        """Get credentials from."""
        return await cls.__call_api(
            hass=hass,
            call=lambda: proton.login(
                username=username,
                password=password,
                mailbox_password=mailbox_password,
                mfa=mfa,
                captcha_token=captcha_token,
                log_level=cls._get_log_level(),
            ),
        )

    @classmethod
    def _get_log_level(cls) -> str:
        """Get log level."""
        return "debug" if LOGGER.level == logging.DEBUG else "info"

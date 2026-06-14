"""Proton Drive API."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiofiles
import aiofiles.os
from homeassistant.components.backup import AgentBackup
from homeassistant.components.backup.util import suggested_filename

from .cli import (
    CLIError,
    NodeNotFoundError,
    ProtonCLI,
)
from .const import LOGGER, OnProgressCallback

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Coroutine

    from homeassistant.core import HomeAssistant


class ProtonDriveError(Exception):
    """Exception to indicate a general error."""


class ProtonDriveInvalidBackupError(ProtonDriveError):
    """Exception to indicate a backup has an invalid format."""


class ProtonDriveMissingBackupError(ProtonDriveError):
    """Exception to indicate a backup is missing."""


@dataclass
class Metadata:
    """Class to represent backup metadata."""

    proton_drive_version: str
    instance_id: str
    backup_id: str
    base_name: str
    metadata: dict  # AgentBackup.as_dict()
    chunks: int

    @classmethod
    def is_valid(cls, data: dict) -> bool:
        """Check if the given JSON string is valid metadata."""
        ver = data.get("proton_drive_version")
        return ver == "1.0.0"

    @classmethod
    def load(cls, data: dict) -> Metadata | dict:
        """
        Load metadata from a JSON string.

        It can either be a new style one with added info and chunking or older type.
        """
        if not cls.is_valid(data):
            return data
        return cls(
            proton_drive_version=data["proton_drive_version"],
            instance_id=data["instance_id"],
            backup_id=data["backup_id"],
            base_name=data["base_name"],
            metadata=data["metadata"],
            chunks=data["chunks"],
        )

    def as_dict(self) -> dict:
        """Return the metadata as a dictionary."""
        return {
            "proton_drive_version": self.proton_drive_version,
            "instance_id": self.instance_id,
            "backup_id": self.backup_id,
            "base_name": self.base_name,
            "metadata": self.metadata,
            "chunks": self.chunks,
        }


class ProtonDriveClient:
    """Client for the Proton Drive API."""

    METADATA_EXT = ".metadata.json"
    ARCHIVE_EXT = ".tar"
    PART_EXT = f"{ARCHIVE_EXT}.part"

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        cli: ProtonCLI,
        instance_id: str,
        backup_folder: str,
    ) -> None:
        """Use `await ProtonDriveClient.create(...)` to create an instance of this class."""
        self.__cli = cli
        self.__instance_id = instance_id
        self.__backup_folder = Path(backup_folder)
        self.__temp_backup_dir = Path(hass.config.path("tmp_backups"))

    @classmethod
    async def create(
        cls,
        *,
        hass: HomeAssistant,
        cli: ProtonCLI,
        instance_id: str,
        backup_folder: str,
    ) -> ProtonDriveClient:
        """Create and initialize a ProtonDriveClient instance."""
        me = cls(hass=hass, cli=cli, instance_id=instance_id, backup_folder=backup_folder)
        await me.__prepare()
        return me

    async def __prepare(self) -> None:
        try:
            await self.__cli.run("filesystem", "info", str(self.__backup_folder))
        except NodeNotFoundError:
            LOGGER.exception("Backup folder not found, trying to create")
            await self.__cli.run(
                "filesystem",
                "create-folder",
                str(self.__backup_folder.parent),
                self.__backup_folder.name,
            )
        await aiofiles.os.makedirs(self.__temp_backup_dir, exist_ok=True)

    async def download_backup(self, backup_id: str) -> AsyncIterator[bytes]:
        """Download a Home Assistant backup."""
        metadata_suffix = self.__make_metadata_path("", backup_id)

        metadata_file = None
        files = await self.__cli.list_files(str(self.__backup_folder))
        for filename in files:
            if filename.endswith(metadata_suffix):
                metadata_file = filename
                break

        if metadata_file is None:
            msg = f"Metadata file not found for backup_id: {backup_id}"
            raise ProtonDriveMissingBackupError(msg)

        metadata = await self.__read_metadata(metadata_file)

        if metadata is not Metadata or metadata.chunks <= 1:
            name = (
                self.__make_backup_path(metadata.base_name, backup_id)
                if metadata is Metadata
                else f"${metadata_file.removesuffix(self.METADATA_EXT)}{self.ARCHIVE_EXT}"
            )
            async with aiofiles.tempfile.NamedTemporaryFile(dir=self.__temp_backup_dir) as f:
                await self.__cli.run(
                    "filesystem",
                    "download",
                    self.__make_filepath(name),
                    str(f.name),
                )
                async with aiofiles.open(f.name, "rb") as file:
                    while chunk := await file.read(4096):
                        yield chunk
        else:
            for i in range(metadata.chunks):
                name = self.__make_chunk_path(metadata.base_name, backup_id, i)
                async with aiofiles.tempfile.NamedTemporaryFile(dir=self.__temp_backup_dir) as f:
                    await self.__cli.run(
                        "filesystem",
                        "download",
                        self.__make_filepath(name),
                        str(f.name),
                    )
                    async with aiofiles.open(f.name, "rb") as file:
                        while chunk := await file.read(4096):
                            yield chunk

    async def upload_backup(
        self,
        open_stream: Callable[[], Coroutine[Any, Any, AsyncIterator[bytes]]],
        backup: AgentBackup,
        on_progress: OnProgressCallback | None = None,
    ) -> None:
        """Upload a Home Assistant backup."""
        archive_name = suggested_filename(backup)
        base_name = archive_name.removesuffix(self.ARCHIVE_EXT)
        metadata_name = f"{base_name}{self.METADATA_EXT}"

        # TODO: no chunking, no retries, do we still need to do that or will the CLI be stable enough?

        # TODO: not counting the metadata in the progress
        async with self.__open_temp(metadata_name, "w") as f:
            await f.write(
                json.dumps(
                    Metadata(
                        proton_drive_version="1.0.0",
                        instance_id=self.__instance_id,
                        backup_id=backup.backup_id,
                        base_name=base_name,
                        metadata=backup.as_dict(),
                        chunks=1,
                    ).as_dict()
                ).encode("utf-8")
            )
            await f.flush()

            await self.__cli.run(
                "filesystem",
                "upload",
                str(f.name),
                str(self.__backup_folder),
            )

        async with self.__open_temp(archive_name, "wb") as f:
            async for chunk in await open_stream():
                await f.write(chunk)
                if on_progress is not None:
                    on_progress(bytes_uploaded=await f.tell() // 2)
            size = await f.tell()
            await f.flush()

            # TODO: upload progress feedback is missing
            await self.__cli.run(
                "filesystem",
                "upload",
                str(f.name),
                str(self.__backup_folder),
            )
            if on_progress is not None:
                on_progress(bytes_uploaded=size)

    async def delete_backup(self, backup_id: str) -> None:
        """Delete a Home Assistant backup."""
        metadata_suffix = self.__make_metadata_path("", backup_id)
        archive_suffix = self.__make_backup_path("", backup_id)

        metadata_file = None
        to_delete = []

        files = await self.__cli.list_files(str(self.__backup_folder))
        for filename in files:
            if filename.endswith(metadata_suffix):
                metadata_file = filename
                to_delete.append(filename)
            elif filename.endswith(archive_suffix):
                to_delete.append(filename)

        if metadata_file is None:
            LOGGER.warning("Metadata file not found for backup_id: %s", backup_id)
        else:
            metadata = self.__read_metadata(metadata_file)
            if metadata is Metadata:
                if metadata.chunks > 1:
                    to_delete.extend(
                        self.__make_chunk_path(metadata.base_name, backup_id, i) for i in range(metadata.chunks)
                    )
                else:
                    to_delete.append(self.__make_backup_path(metadata.base_name, backup_id))

        await self.__cli.run("filesystem", "trash", *[self.__make_filepath(f) for f in to_delete])

    async def list_backups(self) -> list[AgentBackup]:
        """List Home Assistant backups."""
        files = await self.__cli.list_files(str(self.__backup_folder))

        metadata_suffix = self.__make_metadata_path("", "")

        all_metadata = []
        for filename in files:
            if filename.endswith(metadata_suffix):
                try:
                    metadata = await self.__read_metadata(filename)
                    if isinstance(metadata, Metadata):
                        all_metadata.append(AgentBackup.from_dict(metadata.metadata))
                    else:
                        all_metadata.append(AgentBackup.from_dict(metadata))
                except CLIError:
                    LOGGER.exception("Failed to read metadata %s", filename)

        return all_metadata

    async def __read_metadata(self, metadata_file: str) -> Metadata | dict:
        async with aiofiles.tempfile.NamedTemporaryFile(dir=self.__temp_backup_dir) as f:
            await self.__cli.run(
                "filesystem",
                "download",
                self.__make_filepath(metadata_file),
                str(f.name),
            )
            data = await f.read()
            try:
                json_data = json.loads(data)
            except json.JSONDecodeError as e:
                LOGGER.exception("Failed to parse metadata JSON: %s", data)
                msg = "Failed to parse metadata JSON"
                raise ProtonDriveInvalidBackupError(msg) from e
            return Metadata.load(json_data)

    def __make_metadata_path(self, base: str, backup_id: str) -> str:
        return self.__make_path(base, backup_id, self.METADATA_EXT[1:])

    def __make_backup_path(self, base: str, backup_id: str) -> str:
        return self.__make_path(base, backup_id, self.ARCHIVE_EXT[1:])

    def __make_chunk_path(self, base: str, backup_id: str, chunk: int) -> str:
        return self.__make_path(base, backup_id, f"{chunk:02d}{self.PART_EXT}")

    def __make_path(self, base: str, backup_id: str, suffix: str) -> str:
        return f"{base}{backup_id}-{self.__instance_id}.{suffix}"

    def __make_filepath(self, filename: str) -> str:
        return str(self.__backup_folder / filename)

    @asynccontextmanager
    async def __open_temp(
        self, filename: str, mode: str
    ) -> AsyncGenerator[aiofiles.threadpool.binary.AsyncBufferedIOBase]:
        file = await aiofiles.open(self.__temp_backup_dir / filename, mode)
        try:
            yield file
        finally:
            await file.close()
            await aiofiles.os.remove(file.name)

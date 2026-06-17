"""Proton Drive API."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import aiofiles
import aiofiles.os
import aiofiles.ospath
from cachetools import TTLCache
from cachetools_async import cached
from homeassistant.components.backup import AgentBackup
from homeassistant.components.backup.util import suggested_filename

from .cli import (
    CLIError,
    ProtonCLI,
)
from .const import DOMAIN, LOGGER, OnProgressCallback

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
    READ_CHUNK = 4096
    ALLOWED_DELETE_PREFIXES: ClassVar[list[Path]] = [Path("/my-files/"), Path("/devices/"), Path("/photos/")]

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
        self.__temp_backup_dir = Path(
            hass.config.path(".cache", DOMAIN, "tmp_backups")
        )  # FIXME: use cache_path when targeting later HA

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
        if not await self.__cli.exists(self.__backup_folder):
            LOGGER.info("Backup folder not found, trying to create")
            await self.__cli.run(
                "filesystem",
                "create-folder",
                str(self.__backup_folder.parent),
                self.__backup_folder.name,
            )
        await aiofiles.os.makedirs(self.__temp_backup_dir, exist_ok=True)

    async def download_backup(self, backup_id: str) -> AsyncIterator[bytes]:
        """Download a Home Assistant backup."""
        metadata_suffix = self.__make_metadata_filename("", backup_id)

        LOGGER.debug("Looking for metadata file with suffix: %s", metadata_suffix)

        metadata_file = None
        files = await self.__cli.list_files(self.__backup_folder)
        for filename in files:
            if filename.endswith(metadata_suffix):
                metadata_file = filename
                break

        LOGGER.debug("Found metadata file: %s", metadata_file)

        if metadata_file is None:
            msg = f"Metadata file not found for backup_id: {backup_id}"
            raise ProtonDriveMissingBackupError(msg)

        metadata = await self.__read_metadata(metadata_file)

        LOGGER.debug("Read metadata: %s", metadata)

        if metadata is not Metadata or metadata.chunks <= 1:
            name = (
                self.__make_backup_filename(metadata.base_name, backup_id)
                if isinstance(metadata, Metadata)
                else f"${metadata_file.removesuffix(self.METADATA_EXT)}{self.ARCHIVE_EXT}"
            )
            LOGGER.debug("Downloading single backup file: %s", name)
            async with self.__temp_folder() as path:
                await self.__cli.run(
                    "filesystem",
                    "download",
                    self.__make_filepath(name),
                    str(path),
                )
                async with aiofiles.open(path / name, "rb") as file:
                    while chunk := await file.read(self.READ_CHUNK):
                        yield chunk
        else:
            for i in range(metadata.chunks):
                name = self.__make_chunk_filename(metadata.base_name, backup_id, i)
                LOGGER.debug("Downloading chunk %d/%d: %s", i + 1, metadata.chunks, name)
                async with self.__temp_folder() as path:
                    await self.__cli.run(
                        "filesystem",
                        "download",
                        self.__make_filepath(name),
                        str(path),
                    )
                    async with aiofiles.open(path / name, "rb") as file:
                        while chunk := await file.read(self.READ_CHUNK):
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
        archive_name = self.__make_backup_filename(base_name, backup.backup_id)
        metadata_name = self.__make_metadata_filename(base_name, backup.backup_id)

        LOGGER.debug("Uploading backup: %s / %s (%s)", metadata_name, archive_name, base_name)

        # TODO: no chunking, no retries, do we still need to do that or will the CLI be stable enough?

        async with self.__temp_folder() as path:
            await aiofiles.os.makedirs(path, exist_ok=True)

            meta_path = path / metadata_name
            archive_path = path / archive_name

            # TODO: not counting the metadata in the progress
            async with aiofiles.open(meta_path, "w") as f:
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
                    )
                )

            async with aiofiles.open(archive_path, "wb") as f:
                async for chunk in await open_stream():
                    await f.write(chunk)
                    if on_progress is not None:
                        on_progress(bytes_uploaded=await f.tell() // 2)
                size = await f.tell()

            try:
                # TODO: upload progress feedback is missing
                await asyncio.gather(
                    self.__cli.run("filesystem", "upload", str(meta_path), str(self.__backup_folder)),
                    self.__cli.run("filesystem", "upload", str(archive_path), str(self.__backup_folder)),
                )
            except CLIError:
                filenames = [Path(metadata_name), Path(archive_name)]
                to_delete = map(self.__make_filepath, filenames)
                to_delete = [f for f in filenames if await self.__cli.exists(f)]
                await self.__cli.trash(*to_delete)

                if not self.__can_delete_file(self.__backup_folder):
                    raise
                trash = Path("/trash")
                await self.__cli.run("filesystem", "delete", *[str(trash / f.name) for f in to_delete])
                raise

            if on_progress is not None:
                on_progress(bytes_uploaded=size)

    async def delete_backup(self, backup_id: str) -> None:
        """Delete a Home Assistant backup."""
        metadata_suffix = self.__make_metadata_filename("", backup_id)
        archive_suffix = self.__make_backup_filename("", backup_id)

        LOGGER.debug("Looking for metadata and archive files with suffixes: %s, %s", metadata_suffix, archive_suffix)

        metadata_file = None
        to_delete = []

        files = await self.__cli.list_files(self.__backup_folder)
        for filename in files:
            if filename.endswith(metadata_suffix):
                metadata_file = filename
                to_delete.append(filename)
            elif filename.endswith(archive_suffix):
                to_delete.append(filename)

        LOGGER.debug("Found metadata file: %s", metadata_file)
        LOGGER.debug("Found files to delete: %s", to_delete)

        if metadata_file is None:
            LOGGER.warning("Metadata file not found for backup_id: %s", backup_id)
        else:
            metadata = await self.__read_metadata(metadata_file)
            LOGGER.debug("Read metadata: %s", metadata)
            if isinstance(metadata, Metadata):
                if metadata.chunks > 1:
                    to_delete.extend(
                        self.__make_chunk_filename(metadata.base_name, backup_id, i) for i in range(metadata.chunks)
                    )
                else:
                    to_delete.append(self.__make_backup_filename(metadata.base_name, backup_id))

        to_delete = list(set(to_delete))
        to_delete = map(self.__make_filepath, to_delete)
        to_delete = [f for f in to_delete if await self.__cli.exists(f)]
        await self.__cli.trash(*to_delete)

    def __can_delete_file(self, filename: Path) -> bool:
        # FIXME: see https://github.com/ProtonDriveApps/sdk/blob/main/js/cli/src/commands/fileSystem/commandFileSystemTrash.ts#L3
        return any(filename.is_relative_to(prefix) for prefix in self.ALLOWED_DELETE_PREFIXES)

    async def list_backups(self) -> list[AgentBackup]:
        """List Home Assistant backups."""
        files = await self.__cli.list_files(self.__backup_folder)
        metadata_suffix = self.__make_metadata_filename("", "")
        LOGGER.debug("Looking for metadata files with suffix: %s", metadata_suffix)
        metadata_files = [f for f in files if f.endswith(metadata_suffix)]

        async def _fetch(filename: str) -> AgentBackup | None:
            try:
                metadata = await self.__read_metadata(filename)
                if isinstance(metadata, Metadata):
                    return AgentBackup.from_dict(metadata.metadata)
                return AgentBackup.from_dict(metadata)
            except CLIError:
                LOGGER.exception("Failed to read metadata %s", filename)
                return None

        results = await asyncio.gather(*[_fetch(f) for f in metadata_files])
        return [r for r in results if r is not None]

    @cached(cache=TTLCache(maxsize=1024, ttl=300))
    async def __read_metadata(self, metadata_file: str) -> Metadata | dict:
        async with self.__temp_folder() as path:
            await self.__cli.run(
                "filesystem",
                "download",
                self.__make_filepath(metadata_file),
                str(path),
            )
            async with aiofiles.open(path / metadata_file) as f:
                data = await f.read()
            try:
                json_data = json.loads(data)
            except json.JSONDecodeError as e:
                LOGGER.exception("Failed to parse metadata JSON: %s", data)
                msg = "Failed to parse metadata JSON"
                raise ProtonDriveInvalidBackupError(msg) from e
            return Metadata.load(json_data)

    def __make_metadata_filename(self, base: str, backup_id: str) -> str:
        return self.__make_filename(base, backup_id, self.METADATA_EXT[1:])

    def __make_backup_filename(self, base: str, backup_id: str) -> str:
        return self.__make_filename(base, backup_id, self.ARCHIVE_EXT[1:])

    def __make_chunk_filename(self, base: str, backup_id: str, chunk: int) -> str:
        return self.__make_filename(base, backup_id, f"{chunk:02d}{self.PART_EXT}")

    def __make_filename(self, base: str, backup_id: str, suffix: str) -> str:
        return f"{base}{backup_id}-{self.__instance_id}.{suffix}"

    def __make_filepath(self, filename: str | Path) -> str:
        return str(self.__backup_folder / filename)

    @asynccontextmanager
    async def __temp_folder(self) -> AsyncGenerator[Path]:
        filename = f"temp_{uuid.uuid4().hex}"
        filepath = self.__temp_backup_dir / filename
        try:
            yield filepath
        finally:
            if await aiofiles.ospath.exists(filepath):
                await self.__rmtree(filepath)

    @classmethod
    async def __rmtree(cls, path: Path) -> None:
        if await aiofiles.ospath.islink(path):
            await aiofiles.os.remove(path)
        elif await aiofiles.ospath.isdir(path):
            for entry in await aiofiles.os.listdir(path):
                full_path = path / entry
                await cls.__rmtree(full_path)
            await aiofiles.os.rmdir(path)
        else:
            await aiofiles.os.remove(path)

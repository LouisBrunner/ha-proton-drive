"""Wrapper for the Proton Drive CLI binary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import aiofiles
import aiofiles.os
import aiofiles.ospath
import aiohttp

from .const import CLI_CHECKSUMS, CLI_DOWNLOAD_BASE, CLI_VERSION, DOMAIN, LOGGER

if TYPE_CHECKING:
    from asyncio.subprocess import Process

    from homeassistant.core import HomeAssistant


class CLIStartupError(Exception):
    """Exception to indicate a failure to start the CLI."""


class UnsupportedPlatformError(CLIStartupError):
    """Exception to indicate an unsupported platform."""


class DownloadCLIError(CLIStartupError):
    """Exception to indicate a failure to download the CLI."""


class CLIError(Exception):
    """Exception to indicate a failure while running the CLI."""


class InvalidOutputError(CLIError):
    """Exception to indicate invalid output from the CLI."""


class NodeNotFoundError(CLIError):
    """Exception to indicate a node was not found in Proton Drive."""


class AuthError(CLIError):
    """Exception to indicate an authentication error with Proton Drive."""


class ProtonCLI:
    """Wrapper for the Proton Drive CLI binary."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Do not call this directly, use `await ProtonCLI.create(hass)` instead."""
        self.__path = hass.config.path(DOMAIN, "proton-drive")

    @classmethod
    async def create(cls, hass: HomeAssistant) -> ProtonCLI:
        """Create and initialize a ProtonCLI instance."""
        me = cls(hass)
        await me.__ainit(hass)
        return me

    async def __ainit(self, hass: HomeAssistant) -> None:
        cached = Path(self.__path)
        if not await aiofiles.ospath.exists(cached):
            await self.__download(hass, cached)

    @classmethod
    def __get_arch(cls) -> str:
        machine = platform.machine().lower()
        if machine in ("arm64", "aarch64"):
            arch = "arm64"
        elif machine in ("x86_64", "amd64"):
            arch = "x64"
        else:
            msg = f"Unsupported architecture: {machine}"
            raise UnsupportedPlatformError(msg)
        return arch

    @classmethod
    async def __download(cls, _hass: HomeAssistant, dest: Path) -> None:
        arch = cls.__get_arch()
        url = f"{CLI_DOWNLOAD_BASE}/{CLI_VERSION}/linux-{arch}/proton-drive"
        LOGGER.info("Downloading Proton Drive CLI v%s from %s", CLI_VERSION, url)

        await aiofiles.os.makedirs(dest.parent, exist_ok=True)

        tmp_path = dest.with_suffix(".tmp")

        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        try:
            async with aiohttp.ClientSession(connector=connector) as session, session.get(url) as resp:
                resp.raise_for_status()
                LOGGER.debug("Success, starting download to %s", tmp_path)

                try:
                    async with aiofiles.open(tmp_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            LOGGER.debug("Writing %d bytes to %s", len(chunk), tmp_path)
                            await f.write(chunk)
                except Exception as e:
                    if await aiofiles.ospath.exists(tmp_path):
                        await aiofiles.os.unlink(tmp_path)
                    msg = "Failed to write Proton Drive CLI to disk"
                    raise DownloadCLIError(msg) from e
        except aiohttp.ClientError as e:
            msg = "Failed to download Proton Drive CLI"
            raise DownloadCLIError(msg) from e

        try:
            tmp_path.chmod(0o755)
            cls.__verify_checksum(tmp_path, arch)
            await aiofiles.os.rename(tmp_path, dest)
        except Exception:
            if await aiofiles.ospath.exists(tmp_path):
                await aiofiles.os.unlink(tmp_path)
            raise

        LOGGER.info("Proton Drive CLI downloaded to %s", dest)

    @classmethod
    def __verify_checksum(cls, path: Path, arch: str) -> None:
        expected = CLI_CHECKSUMS.get(arch)
        if expected is None:
            msg = f"No checksum available for architecture: {arch}"
            raise UnsupportedPlatformError(msg)

        digest = hashlib.sha512(path.read_bytes()).hexdigest()
        if digest != expected:
            msg = f"Checksum mismatch for {path.name}, expected {expected}, got {digest}"
            raise DownloadCLIError(msg)

    async def __run(self, *args: str, wd: str | None = None) -> Process:
        argv = list(args)
        argv.append("--json")

        LOGGER.debug("Running: %s %s (wd=%s)", self.__path, " ".join(argv), wd)

        return await asyncio.create_subprocess_exec(
            self.__path,
            *argv,
            cwd=wd,
            env={**os.environ, "PROTON_DRIVE_UNSAFE_SECRETS": "true"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def list_files(self, folder: str) -> list[str]:
        """List files in a folder."""
        files = await self.run("filesystem", "list", folder)
        if type(files) is not list:
            msg = f"Expected list of files, got {type(files)}"
            raise InvalidOutputError(msg)
        if folder == "/":
            return [file.path for file in files]
        return [file.name.value for file in files]

    async def run(self, *args: str) -> Any:
        """Run a CLI command and return the parsed JSON output."""
        proc = await self.__run(*args)

        stdout, stderr = await proc.communicate()

        return self._process_run(proc, stdout, stderr)

    @classmethod
    def _process_run(cls, proc: Process, stdout: bytes, stderr: bytes) -> Any:
        LOGGER.debug("CLI Result %d: stdout=%s, stderr=%s", proc.returncode, stdout.decode(), stderr.decode())

        if proc.returncode != 0:
            msg = stderr.decode()
            if "Node not found" in msg:
                raise NodeNotFoundError(msg)
            if "You need to login first" in msg:
                raise AuthError(msg)
            raise CLIError(msg)

        json_output = stdout.decode().strip()
        if not json_output:
            return None

        try:
            return json.loads(json_output, object_hook=lambda d: SimpleNamespace(**d))
        except json.JSONDecodeError as e:
            msg = f"Failed to parse JSON output from CLI: {json_output}"
            raise InvalidOutputError(msg) from e

    class AuthFlow:
        """Represents an authentication flow."""

        def __init__(self, *, url: str, proc: Process) -> None:
            """Initialize an AuthFlow object."""
            self.__url = url
            self.__result = self.__wait_task(proc)

        def get_url(self) -> str:
            """Get the URL to open in the browser for authentication."""
            return self.__url

        def is_done(self) -> bool:
            """Check if the authentication flow is done."""
            return self.__result.done()

        def has_error(self) -> bool:
            """Check if the authentication flow resulted in an error."""
            return self.get_error() is not None

        def get_error(self) -> str | None:
            """Get the error message if the authentication flow resulted in an error."""
            if not self.is_done():
                msg = "Auth flow is not done yet"
                raise RuntimeError(msg)
            exc = self.__result.exception()
            return str(exc) if exc else None

        def __wait_task(self, proc: Process) -> asyncio.Future:
            fut = asyncio.Future()
            self.__task = asyncio.create_task(self.__finish(fut, proc))
            return fut

        async def __finish(self, fut: asyncio.Future, proc: Process) -> None:
            stdout_res = b""
            stderr_res = b""

            async def _read_stream(stream: asyncio.StreamReader | None, *, stdout: bool) -> None:
                nonlocal stdout_res, stderr_res
                if stream is None:
                    return
                out = await stream.read()
                if stdout:
                    stdout_res += out
                else:
                    stderr_res += out

            await asyncio.gather(
                _read_stream(proc.stdout, stdout=True),
                _read_stream(proc.stderr, stdout=False),
            )

            await proc.wait()

            try:
                res = ProtonCLI._process_run(proc, stdout_res, stderr_res)
                fut.set_result(res)
            except CLIError as e:
                fut.set_exception(e)

    async def start_auth_flow(self) -> AuthFlow:
        """Start an authentication flow and return an AuthFlow object."""
        proc = await self.__run("auth", "login")

        async def _read_url(proc: Process) -> str:
            assert proc.stdout is not None  # noqa: S101
            try:
                async with asyncio.timeout(10):
                    line = await proc.stdout.readline()
                    LOGGER.debug("auth login: %s", line)
                    line_parsed = json.loads(line.decode().strip())
                    return line_parsed["signInUrl"]
            except TimeoutError as e:
                LOGGER.exception("Timed out waiting for auth URL from CLI")
                msg = "Timed out waiting for auth URL from CLI"
                raise AuthError(msg) from e
            except json.JSONDecodeError as e:
                LOGGER.exception("Failed to parse JSON output from CLI")
                msg = "Failed to parse JSON output from CLI"
                raise InvalidOutputError(msg) from e
            except KeyError as e:
                LOGGER.exception("Missing 'signInUrl' in CLI output")
                msg = "Missing 'signInUrl' in CLI output"
                raise InvalidOutputError(msg) from e

        return self.AuthFlow(url=await _read_url(proc), proc=proc)

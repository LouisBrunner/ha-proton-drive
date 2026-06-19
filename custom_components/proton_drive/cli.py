"""Wrapper for the Proton Drive CLI binary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import aiofiles
import aiofiles.os
import aiofiles.ospath
import aiohttp

from .const import (
    CLI_BASE_URL_FORMAT,
    CLI_CHECKSUMS,
    CLI_VERSION,
    DOMAIN,
    LOGGER,
)

if TYPE_CHECKING:
    from asyncio.subprocess import Process

    from homeassistant.core import HomeAssistant


class CLIStartupError(Exception):
    """Exception to indicate a failure to start the CLI."""


class UnsupportedPlatformError(CLIStartupError):
    """Exception to indicate an unsupported platform."""


class DownloadCLIError(CLIStartupError):
    """Exception to indicate a failure to download the CLI."""


class DownloadCLINetworkError(DownloadCLIError):
    """Exception to indicate a failure to download the CLI because of the network."""


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

    API_RESPONSE_SUCCESS = 1000
    READ_CHUNK = 65536

    def __init__(self, hass: HomeAssistant) -> None:
        """Do not call this directly, use `await ProtonCLI.create(hass)` instead."""
        self.__xdg = hass.config.cache_path(DOMAIN, "xdg")
        self.__path = hass.config.cache_path(DOMAIN, "proton-drive")

    @classmethod
    async def create(cls, hass: HomeAssistant) -> ProtonCLI:
        """Create and initialize a ProtonCLI instance."""
        me = cls(hass)
        await me.__ainit(hass)
        return me

    async def __ainit(self, hass: HomeAssistant) -> None:
        cached = Path(self.__path)
        LOGGER.debug("Checking for cached Proton Drive CLI at %s", cached)

        if await aiofiles.ospath.exists(cached) and not await self.__is_valid(cached):
            LOGGER.info("Cached Proton Drive CLI is invalid, redownloading")
            await aiofiles.os.unlink(cached)

        if not await aiofiles.ospath.exists(cached):
            await self.__download(hass, cached)

        try:
            await self.run("version", is_json=False)
        except CLIError as e:
            raise CLIStartupError(str(e)) from e

    @classmethod
    def __get_platform(cls) -> tuple[str, str]:
        machine = platform.machine().lower()
        variant = "musl" if any(Path("/lib").glob("ld-musl-*.so*")) else "glibc"
        if machine in ("arm64", "aarch64"):
            arch = "arm64"
        elif machine in ("x86_64", "amd64"):
            arch = "x64"
        else:
            msg = f"Unsupported architecture: {machine}"
            raise UnsupportedPlatformError(msg)
        return variant, arch

    @classmethod
    async def __download(cls, _hass: HomeAssistant, dest: Path) -> None:
        variant, arch = cls.__get_platform()

        url = CLI_BASE_URL_FORMAT[variant].format(arch=arch, version=CLI_VERSION)

        LOGGER.info("Downloading Proton Drive CLI v%s from %s", CLI_VERSION, url)

        await aiofiles.os.makedirs(dest.parent, exist_ok=True)

        tmp_path = dest.with_suffix(".tmp")

        try:
            async with cls.__http_session() as session, session.get(url) as resp:
                resp.raise_for_status()
                LOGGER.debug("Success, starting download to %s", tmp_path)

                try:
                    async with aiofiles.open(tmp_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(cls.READ_CHUNK):
                            LOGGER.debug("Writing %d bytes to %s", len(chunk), tmp_path)
                            await f.write(chunk)
                except Exception as e:
                    if await aiofiles.ospath.exists(tmp_path):
                        await aiofiles.os.unlink(tmp_path)
                    msg = "Failed to write Proton Drive CLI to disk"
                    raise DownloadCLIError(msg) from e
        except (aiohttp.ClientError, TimeoutError) as e:
            msg = "Failed to download Proton Drive CLI"
            raise DownloadCLINetworkError(msg) from e

        try:
            tmp_path.chmod(0o755)
            await cls.__verify_checksum(tmp_path, variant, arch)
            await aiofiles.os.rename(tmp_path, dest)
        except Exception:
            if await aiofiles.ospath.exists(tmp_path):
                await aiofiles.os.unlink(tmp_path)
            raise

        LOGGER.info("Proton Drive CLI downloaded to %s", dest)

    @classmethod
    def __http_session(cls) -> aiohttp.ClientSession:
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        timeout = aiohttp.ClientTimeout(total=300, connect=30)
        return aiohttp.ClientSession(connector=connector, timeout=timeout)

    async def __is_valid(self, path: Path) -> bool:
        variant, arch = self.__get_platform()
        try:
            await self.__verify_checksum(path, variant, arch)
        except CLIStartupError:
            LOGGER.exception("Failed to verify checksum for %s", path)
            return False
        return True

    @classmethod
    async def __verify_checksum(cls, path: Path, variant: str, arch: str) -> None:
        expected = CLI_CHECKSUMS.get(f"{variant}-{arch}")
        if expected is None:
            msg = f"No checksum available for architecture: {arch}"
            raise UnsupportedPlatformError(msg)

        async with aiofiles.open(path, "rb") as f:
            data = await f.read()

        digest = hashlib.sha512(data).hexdigest()
        if digest != expected:
            msg = f"Checksum mismatch for {path.name}, expected {expected}, got {digest}"
            raise DownloadCLIError(msg)

    @dataclass
    class _CLIRun:
        id: str
        process: Process

    async def __run(self, *args: str) -> _CLIRun:
        argv = list(args)
        argv.append("--json")

        uid = uuid4().hex[:8]
        LOGGER.debug("[%s] Running: %s %s", uid, self.__path, " ".join(argv))

        try:
            proc = await asyncio.create_subprocess_exec(
                self.__path,
                *argv,
                env={
                    **os.environ,
                    "PROTON_DRIVE_UNSAFE_SECRETS": "true",
                    "XDG_CACHE_HOME": self.__xdg,
                    "XDG_DATA_HOME": self.__xdg,
                    "XDG_STATE_HOME": self.__xdg,
                },
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as e:
            LOGGER.exception("[%s] Failed to run Proton Drive CLI: %s", uid, e)
            msg = f"Failed to run Proton Drive CLI: {e}"
            raise CLIError(msg) from e
        return self._CLIRun(id=uid, process=proc)

    async def __get_auth(self) -> SimpleNamespace:
        path = Path(self.__xdg) / "proton-drive-cli" / "auth-session.json"
        async with aiofiles.open(path) as f:
            data = await f.read()
        return json.loads(data, object_hook=lambda d: SimpleNamespace(**d))

    async def list_files(self, folder: Path) -> list[str]:
        """List files in a folder."""
        LOGGER.debug("Listing files in folder: %s", folder)
        files = await self.run("filesystem", "list", str(folder))
        if type(files) is not list:
            msg = f"Expected list of files, got {type(files)}"
            raise InvalidOutputError(msg)
        if folder == "/":
            return [file.path for file in files]
        return [file.name.value for file in files]

    async def exists(self, path: Path | str) -> bool:
        """Check if a file or folder exists."""
        try:
            await self.stat(path)
        except NodeNotFoundError:
            return False
        return True

    async def stat(self, path: Path | str) -> SimpleNamespace:
        """Get file or folder metadata."""
        return await self.run("filesystem", "info", str(path))

    def __get_share(self, path: Path | str) -> str:
        path = Path(path)
        if path.is_relative_to("/my-files"):
            return "/my-files"
        parts = Path(path).parts
        if len(parts) < 3:  # noqa: PLR2004
            msg = f"Invalid path: {path}"
            raise ValueError(msg)
        return f"/{parts[1]}/{parts[2]}"

    @classmethod
    def __get_link_id(cls, uid: str) -> str:
        return uid.rsplit("~", maxsplit=1)[-1]

    async def trash(self, *files: Path | str) -> None:
        """Move files to the trash."""
        if not files:
            return
        LOGGER.info("Trashing files: %s", files)
        parent = Path(files[0]).parent
        if not all(Path(f).parent == parent for f in files):
            msg = "All files must be in the same folder to trash them"
            raise ValueError(msg)
        infos = await asyncio.gather(*(self.stat(f) for f in files))
        parent_id = self.__get_link_id(infos[0].parentUid)
        share = await self.stat(self.__get_share(files[0]))
        res = await self.__api_call(
            "POST",
            f"/drive/shares/{share.deprecatedShareId}/folders/{parent_id}/trash_multiple",
            {"LinkIDs": [self.__get_link_id(i.uid) for i in infos]},
        )
        for r in res.Responses:
            if r.Response.Code != self.API_RESPONSE_SUCCESS:
                msg = f"Failed to trash {r.LinkID}: {r.Response.Message}"
                raise CLIError(msg)

    async def __api_call(self, verb: str, path: str, body: dict) -> SimpleNamespace:
        try:
            auth = await self.__get_auth()
            headers = {
                "Authorization": f"Bearer {auth.session.accessToken}",
                "Content-Type": "application/json",
                "x-pm-uid": auth.session.uid,
                "x-pm-appversion": "web-drive@5.0.0",
            }
            async with self.__http_session() as session:
                uid = uuid4().hex[:8]
                LOGGER.debug("[%s] Making API call: %s %s, %s with body: %s", uid, verb, path, headers, body)
                res = await session.request(
                    verb,
                    f"https://mail.proton.me/api{path}",
                    headers=headers,
                    json=body,
                )
                data = await res.text()
                LOGGER.debug("[%s] API call response: %s %s %s", uid, res.status, res.headers, data)
                res.raise_for_status()
        except (aiohttp.ClientError, TimeoutError) as e:
            msg = f"Failed to make API call: {verb} {path}"
            raise CLIError(msg) from e
        return json.loads(data, object_hook=lambda d: SimpleNamespace(**d))

    async def run(self, *args: str, is_json: bool = True) -> Any:
        """Run a CLI command and return the parsed JSON output."""
        run = await self.__run(*args)
        stdout, stderr = await run.process.communicate()
        return self._process_run(run, stdout, stderr, is_json=is_json)

    @classmethod
    def _process_run(cls, run: _CLIRun, stdout: bytes, stderr: bytes, *, is_json: bool = True) -> Any:
        LOGGER.debug(
            "[%s] CLI Result %d: stdout=%s, stderr=%s",
            run.id,
            run.process.returncode,
            stdout.decode(),
            stderr.decode(),
        )

        if run.process.returncode != 0:
            msg = stderr.decode()
            if "Node not found" in msg:
                raise NodeNotFoundError(msg)
            if "You need to login first" in msg:
                raise AuthError(msg)
            raise CLIError(msg)

        json_output = stdout.decode().strip()
        if not json_output:
            return None

        if not is_json:
            return json_output

        try:
            return json.loads(json_output, object_hook=lambda d: SimpleNamespace(**d))
        except json.JSONDecodeError as e:
            msg = f"Failed to parse JSON output from CLI: {json_output}"
            raise InvalidOutputError(msg) from e

    class AuthFlow:
        """Represents an authentication flow."""

        FLOW_TIMEOUT = 5 * 60

        def __init__(self, *, url: str, run: ProtonCLI._CLIRun) -> None:
            """Initialize an AuthFlow object."""
            self.__url = url
            self.__result = asyncio.create_task(self.__finish(run))

        def get_url(self) -> str:
            """Get the URL to open in the browser for authentication."""
            return self.__url

        def task(self) -> asyncio.Task[Any]:
            """Get the authentication flow task."""
            return self.__result

        async def __finish(self, run: ProtonCLI._CLIRun) -> None:
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

            try:
                async with asyncio.timeout(self.FLOW_TIMEOUT):
                    await asyncio.gather(
                        _read_stream(run.process.stdout, stdout=True),
                        _read_stream(run.process.stderr, stdout=False),
                    )
                    await run.process.wait()
            except TimeoutError as e:
                msg = "Auth flow timed out"
                raise CLIError(msg) from e

            return ProtonCLI._process_run(run, stdout_res, stderr_res)

    async def start_auth_flow(self) -> AuthFlow:
        """Start an authentication flow and return an AuthFlow object."""
        run = await self.__run("auth", "login")

        async def _read_url(run: ProtonCLI._CLIRun) -> str:
            LOGGER.debug("Waiting for auth URL from CLI...")
            assert run.process.stdout is not None  # noqa: S101
            try:
                async with asyncio.timeout(10):
                    line = await run.process.stdout.readline()
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

        return self.AuthFlow(url=await _read_url(run), run=run)

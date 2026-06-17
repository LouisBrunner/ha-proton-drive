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

    API_RESPONSE_SUCCESS = 1000

    def __init__(self, hass: HomeAssistant) -> None:
        """Do not call this directly, use `await ProtonCLI.create(hass)` instead."""
        self.__xdg = hass.config.path(".cache", DOMAIN, "xdg")
        self.__path = hass.config.path(
            ".cache", DOMAIN, f"proton-drive-{CLI_VERSION}"
        )  # FIXME: use cache_path when targeting later HA

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

        try:
            async with cls.__http_session() as session, session.get(url) as resp:
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
            await cls.__verify_checksum(tmp_path, arch)
            await aiofiles.os.rename(tmp_path, dest)
        except Exception:
            if await aiofiles.ospath.exists(tmp_path):
                await aiofiles.os.unlink(tmp_path)
            raise

        LOGGER.info("Proton Drive CLI downloaded to %s", dest)

    @classmethod
    def __http_session(cls) -> aiohttp.ClientSession:
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        return aiohttp.ClientSession(connector=connector)

    @classmethod
    async def __verify_checksum(cls, path: Path, arch: str) -> None:
        expected = CLI_CHECKSUMS.get(arch)
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
    class CLIRun:
        """Represents a running CLI command."""

        id: str
        process: Process

    async def __run(self, *args: str, wd: str | None = None) -> CLIRun:
        argv = list(args)
        argv.append("--json")

        uid = uuid4().hex[:8]
        LOGGER.debug("[%s] Running: %s %s (wd=%s)", uid, self.__path, " ".join(argv), wd)

        proc = await asyncio.create_subprocess_exec(
            self.__path,
            *argv,
            cwd=wd,
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
        return self.CLIRun(id=uid, process=proc)

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
        return json.loads(data, object_hook=lambda d: SimpleNamespace(**d))

    async def run(self, *args: str) -> Any:
        """Run a CLI command and return the parsed JSON output."""
        run = await self.__run(*args)
        stdout, stderr = await run.process.communicate()
        return self._process_run(run, stdout, stderr)

    @classmethod
    def _process_run(cls, run: CLIRun, stdout: bytes, stderr: bytes) -> Any:
        LOGGER.debug(
            "[%s] CLI Result %d: stdout=%s, stderr=%s", run.id, run.process.returncode, stdout.decode(), stderr.decode()
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

        try:
            return json.loads(json_output, object_hook=lambda d: SimpleNamespace(**d))
        except json.JSONDecodeError as e:
            msg = f"Failed to parse JSON output from CLI: {json_output}"
            raise InvalidOutputError(msg) from e

    class AuthFlow:
        """Represents an authentication flow."""

        def __init__(self, *, url: str, run: ProtonCLI.CLIRun) -> None:
            """Initialize an AuthFlow object."""
            self.__url = url
            self.__result = self.__wait_task(run)

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

        def __wait_task(self, run: ProtonCLI.CLIRun) -> asyncio.Future:
            fut = asyncio.Future()
            self.__task = asyncio.create_task(self.__finish(fut, run))
            return fut

        async def __finish(self, fut: asyncio.Future, run: ProtonCLI.CLIRun) -> None:
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
                _read_stream(run.process.stdout, stdout=True),
                _read_stream(run.process.stderr, stdout=False),
            )
            await run.process.wait()

            try:
                res = ProtonCLI._process_run(run, stdout_res, stderr_res)
                fut.set_result(res)
            except CLIError as e:
                fut.set_exception(e)

    async def start_auth_flow(self) -> AuthFlow:
        """Start an authentication flow and return an AuthFlow object."""
        run = await self.__run("auth", "login")

        async def _read_url(run: ProtonCLI.CLIRun) -> str:
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

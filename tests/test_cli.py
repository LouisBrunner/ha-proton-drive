"""Tests for ProtonCLI's auth-staleness detection and critical-section serialization."""

from __future__ import annotations

import asyncio
import json
import time
from http import HTTPStatus
from pathlib import Path
from typing import Self

import aiohttp
import pytest
from proton_drive.cli import AuthError, CLIError, ProtonCLI
from proton_drive.const import CONF_LAST_CLI_RUN

CRITICAL_CALL_DURATION_S = 0.3
CRITICAL_EXTRA_CALLS = 5
DEV_NULL = Path("/dev/null")


class FakeConfigEntry:
    """Stand-in for a Home Assistant ConfigEntry, exposing only what ProtonCLI reads/writes."""

    def __init__(self, data: dict | None = None) -> None:
        """Initialize with an optional starting `data` dict."""
        self.data = data if data is not None else {}


class FakeConfigEntries:
    """Stand-in for `hass.config_entries`, tracking how many updates were made."""

    def __init__(self) -> None:
        """Initialize the update counter."""
        self.update_calls = 0

    def async_update_entry(self, entry: FakeConfigEntry, *, data: dict) -> None:
        """Record the call and replace the entry's data, like the real Home Assistant method."""
        self.update_calls += 1
        entry.data = data


class FakeHass:
    """Stand-in for a Home Assistant instance, exposing only `config_entries`."""

    def __init__(self) -> None:
        """Initialize with a fresh `FakeConfigEntries`."""
        self.config_entries = FakeConfigEntries()


class FakeResponse:
    """Stand-in for an aiohttp ClientResponse with a fixed status."""

    def __init__(self, status: int) -> None:
        """Initialize with the HTTP status to simulate."""
        self.status = status
        self.headers: dict[str, str] = {}

    async def text(self) -> str:
        """Return an empty JSON body."""
        return "{}"

    def raise_for_status(self) -> None:
        """Raise like aiohttp does for 4xx/5xx statuses."""
        if self.status >= HTTPStatus.BAD_REQUEST:
            raise aiohttp.ClientResponseError(status=self.status)


class FakeSession:
    """Stand-in for an aiohttp ClientSession returning a fixed-status response to any request."""

    def __init__(self, status: int) -> None:
        """Initialize with the HTTP status every request should return."""
        self.__status = status

    async def __aenter__(self) -> Self:
        """Support `async with`."""
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Support `async with`."""
        return

    async def request(self, _verb: str, _url: str, **_kwargs: object) -> FakeResponse:
        """Return the fixed-status fake response, ignoring the request details."""
        return FakeResponse(self.__status)


def make_cli(
    binary_path: Path,
    *,
    xdg: Path | None = None,
    entry: FakeConfigEntry | None = None,
    hass: FakeHass | None = None,
) -> ProtonCLI:
    """Build a ProtonCLI around a stand-in binary, bypassing `create()` (needs a real Home Assistant instance)."""
    cli = object.__new__(ProtonCLI)
    for name, value in {
        "_ProtonCLI__path": str(binary_path),
        "_ProtonCLI__xdg": str(xdg) if xdg is not None else "/tmp",  # noqa: S108
        "_ProtonCLI__integration_version": "test",
        "_ProtonCLI__reap_tasks": set(),
        "_ProtonCLI__hass": hass,
        "_ProtonCLI__entry": entry,
        "_ProtonCLI__critical_condition": asyncio.Condition(),
        "_ProtonCLI__critical_lock": asyncio.Lock(),
        "_ProtonCLI__critical_active": False,
        "_ProtonCLI__critical_successes_remaining": 0,
    }.items():
        setattr(cli, name, value)
    return cli


def write_mock(tmp_path: Path, name: str, body: str) -> Path:
    """Write an executable shell script standing in for the real CLI binary."""
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o700)
    return script


async def test_touches_auth_false_skips_gating(tmp_path: Path) -> None:
    """A `touches_auth=False` call must never serialize or mark, even with a stale entry."""
    script = write_mock(tmp_path, "ok.sh", "exit 0")
    entry = FakeConfigEntry(data={})
    hass = FakeHass()
    cli = make_cli(script, entry=entry, hass=hass)

    await cli.run(is_json=False, timeout_s=1, retries=0, touches_auth=False)

    assert hass.config_entries.update_calls == 0
    assert cli._ProtonCLI__critical_active is False


async def test_is_auth_stale_with_no_entry() -> None:
    """With no config entry, staleness must never trigger."""
    cli = make_cli(DEV_NULL, entry=None)
    assert cli._ProtonCLI__is_auth_stale() is False


async def test_is_auth_stale_missing_timestamp() -> None:
    """A missing last-run timestamp must be considered stale."""
    entry = FakeConfigEntry(data={})
    cli = make_cli(DEV_NULL, entry=entry, hass=FakeHass())
    assert cli._ProtonCLI__is_auth_stale() is True


async def test_is_auth_stale_fresh_timestamp() -> None:
    """A fresh last-run timestamp must not be considered stale."""
    entry = FakeConfigEntry(data={CONF_LAST_CLI_RUN: time.time()})
    cli = make_cli(DEV_NULL, entry=entry, hass=FakeHass())
    assert cli._ProtonCLI__is_auth_stale() is False


async def test_is_auth_stale_old_timestamp() -> None:
    """A last-run timestamp older than the threshold must be considered stale."""
    entry = FakeConfigEntry(data={CONF_LAST_CLI_RUN: time.time() - ProtonCLI.STALE_AUTH_THRESHOLD_S - 1})
    cli = make_cli(DEV_NULL, entry=entry, hass=FakeHass())
    assert cli._ProtonCLI__is_auth_stale() is True


async def test_mark_ran_throttle() -> None:
    """Repeated marks within the throttle window must only write once."""
    entry = FakeConfigEntry(data={})
    hass = FakeHass()
    cli = make_cli(DEV_NULL, entry=entry, hass=hass)

    await cli._ProtonCLI__mark_ran()
    assert hass.config_entries.update_calls == 1

    await cli._ProtonCLI__mark_ran()
    assert hass.config_entries.update_calls == 1

    entry.data[CONF_LAST_CLI_RUN] = time.time() - ProtonCLI.MARK_RAN_THROTTLE_S - 1
    await cli._ProtonCLI__mark_ran()
    assert hass.config_entries.update_calls == 2  # noqa: PLR2004


async def test_critical_section_serializes_until_success_streak(tmp_path: Path) -> None:
    """While auth looks stale, calls must serialize until N succeed, then run in parallel again."""
    script = write_mock(tmp_path, "sleep.sh", 'sleep "$1"')
    entry = FakeConfigEntry(data={})
    hass = FakeHass()
    cli = make_cli(script, entry=entry, hass=hass)

    n_calls = ProtonCLI.STALE_AUTH_REQUIRED_SUCCESSES + CRITICAL_EXTRA_CALLS
    start = time.monotonic()
    await asyncio.gather(
        *(cli.run(str(CRITICAL_CALL_DURATION_S), is_json=False, timeout_s=5, retries=0) for _ in range(n_calls))
    )
    elapsed = time.monotonic() - start

    required = ProtonCLI.STALE_AUTH_REQUIRED_SUCCESSES
    min_expected = required * CRITICAL_CALL_DURATION_S * 0.7
    max_expected = required * CRITICAL_CALL_DURATION_S + CRITICAL_CALL_DURATION_S * 3

    assert min_expected <= elapsed <= max_expected
    assert cli._ProtonCLI__critical_active is False


async def test_critical_section_resets_streak_on_failure(tmp_path: Path) -> None:
    """A failed auth-touching call mid-streak must reset the required successes, not just skip one."""
    ok_script = write_mock(tmp_path, "ok.sh", "exit 0")
    fail_script = write_mock(tmp_path, "fail.sh", "echo boom >&2\nexit 1")
    entry = FakeConfigEntry(data={})
    hass = FakeHass()
    cli = make_cli(ok_script, entry=entry, hass=hass)

    for _ in range(ProtonCLI.STALE_AUTH_REQUIRED_SUCCESSES - 1):
        await cli.run(is_json=False, timeout_s=1, retries=0)

    assert cli._ProtonCLI__critical_successes_remaining == 1

    cli._ProtonCLI__path = str(fail_script)
    with pytest.raises(CLIError):
        await cli.run(is_json=False, timeout_s=1, retries=0)

    assert cli._ProtonCLI__critical_successes_remaining == ProtonCLI.STALE_AUTH_REQUIRED_SUCCESSES
    assert cli._ProtonCLI__critical_active is True


@pytest.mark.parametrize("status", [HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN])
async def test_api_call_401_403_raise_auth_error(
    tmp_path: Path,
    status: HTTPStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 401/403 from Proton's API must surface as AuthError."""
    _write_auth_session(tmp_path)
    cli = make_cli(tmp_path / "unused", xdg=tmp_path)
    monkeypatch.setattr(ProtonCLI, "_ProtonCLI__http_session", classmethod(lambda _cls: FakeSession(status)))

    with pytest.raises(AuthError):
        await cli.get_email()


async def test_api_call_500_stays_cli_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-auth error status must stay a plain CLIError, not AuthError."""
    _write_auth_session(tmp_path)
    cli = make_cli(tmp_path / "unused", xdg=tmp_path)
    monkeypatch.setattr(
        ProtonCLI,
        "_ProtonCLI__http_session",
        classmethod(lambda _cls: FakeSession(HTTPStatus.INTERNAL_SERVER_ERROR)),
    )

    with pytest.raises(CLIError) as exc_info:
        await cli.get_email()
    assert type(exc_info.value) is CLIError


def _write_auth_session(xdg: Path) -> None:
    session_dir = xdg / "proton-drive-cli"
    session_dir.mkdir(parents=True)
    session_dir.joinpath("auth-session.json").write_text(
        json.dumps({"session": {"uid": "u", "accessToken": "t"}, "userKeyPassword": "p"}),
    )

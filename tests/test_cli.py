"""Tests for ProtonCLI's auth-staleness detection and critical-section serialization."""

from __future__ import annotations

import asyncio
import json
import os
import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Self

import aiohttp
import pytest
from proton_drive.cli import AuthError, CLIError, ProtonCLI

if TYPE_CHECKING:
    from pathlib import Path

CALL_DURATION_S = 0.3
EXTRA_PARALLEL_CALLS = 5


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


def make_cli(binary_path: Path, *, xdg: Path | None = None) -> ProtonCLI:
    """Build a ProtonCLI around a stand-in binary, bypassing `create()` (needs a real Home Assistant instance)."""
    cli = object.__new__(ProtonCLI)
    for name, value in {
        "_ProtonCLI__path": str(binary_path),
        "_ProtonCLI__xdg": str(xdg) if xdg is not None else "/tmp",  # noqa: S108
        "_ProtonCLI__integration_version": "test",
        "_ProtonCLI__reap_tasks": set(),
        "_ProtonCLI__critical_condition": asyncio.Condition(),
        "_ProtonCLI__critical_lock": asyncio.Lock(),
        "_ProtonCLI__critical_active": False,
        "_ProtonCLI__critical_successes_remaining": 0,
    }.items():
        setattr(cli, name, value)
    return cli


def patch_http_session(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    """Patch ProtonCLI's HTTP session factory to return a fixed-status FakeSession."""
    monkeypatch.setattr(ProtonCLI, "_ProtonCLI__http_session", classmethod(lambda _cls: FakeSession(status)))


def write_mock(tmp_path: Path, name: str, body: str) -> Path:
    """Write an executable one-line shell script standing in for the real CLI binary."""
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o700)
    return script


def write_sleep_maybe_fail_mock(tmp_path: Path) -> Path:
    """Write a mock that sleeps `$1` seconds then fails only if `$2` is 'fail'."""
    return write_mock(
        tmp_path,
        "sleep_maybe_fail.sh",
        'sleep "$1"; [ "$2" = "fail" ] && { echo boom >&2; exit 1; }; exit 0',
    )


def write_auth_session(xdg: Path, *, age_s: float | None = None) -> Path:
    """Write a fake auth-session.json, optionally backdating its mtime by `age_s`."""
    session_dir = xdg / "proton-drive-cli"
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "auth-session.json"
    path.write_text(json.dumps({"session": {"uid": "u", "accessToken": "t"}, "userKeyPassword": "p"}))
    if age_s is not None:
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))
    return path


async def _run_n_parallel(cli: ProtonCLI, n: int, *, flag: str = "ok") -> float:
    """Run `n` calls concurrently against the sleep-maybe-fail mock, returning elapsed wall time."""
    start = time.monotonic()
    calls = (cli.run(str(CALL_DURATION_S), flag, is_json=False, timeout_s=5, retries=0) for _ in range(n))
    await asyncio.gather(*calls)
    return time.monotonic() - start


async def test_no_auth_file_runs_fully_in_parallel(tmp_path: Path) -> None:
    """With no auth-session.json at all, calls must run in parallel, not serialize."""
    script = write_sleep_maybe_fail_mock(tmp_path)
    cli = make_cli(script, xdg=tmp_path)

    elapsed = await _run_n_parallel(cli, ProtonCLI.STALE_AUTH_REQUIRED_SUCCESSES + EXTRA_PARALLEL_CALLS)

    assert elapsed < CALL_DURATION_S * 3


async def test_fresh_auth_file_runs_fully_in_parallel(tmp_path: Path) -> None:
    """A recently written auth-session.json must not trigger serialization."""
    script = write_sleep_maybe_fail_mock(tmp_path)
    write_auth_session(tmp_path)
    cli = make_cli(script, xdg=tmp_path)

    elapsed = await _run_n_parallel(cli, ProtonCLI.STALE_AUTH_REQUIRED_SUCCESSES + EXTRA_PARALLEL_CALLS)

    assert elapsed < CALL_DURATION_S * 3


async def test_critical_section_serializes_until_success_streak(tmp_path: Path) -> None:
    """While auth looks stale, calls must serialize until N succeed, then run in parallel again."""
    script = write_sleep_maybe_fail_mock(tmp_path)
    write_auth_session(tmp_path, age_s=ProtonCLI.STALE_AUTH_THRESHOLD_S + 1)
    cli = make_cli(script, xdg=tmp_path)

    required = ProtonCLI.STALE_AUTH_REQUIRED_SUCCESSES
    elapsed = await _run_n_parallel(cli, required + EXTRA_PARALLEL_CALLS)

    min_expected = required * CALL_DURATION_S * 0.5
    max_expected = required * CALL_DURATION_S + CALL_DURATION_S * 3.5
    assert min_expected <= elapsed <= max_expected


async def test_critical_section_requires_fresh_streak_after_failure(tmp_path: Path) -> None:
    """A failure mid-streak must require a full fresh streak of N successes, not just one more."""
    script = write_sleep_maybe_fail_mock(tmp_path)
    write_auth_session(tmp_path, age_s=ProtonCLI.STALE_AUTH_THRESHOLD_S + 1)
    cli = make_cli(script, xdg=tmp_path)

    required = ProtonCLI.STALE_AUTH_REQUIRED_SUCCESSES
    for _ in range(required - 1):
        await cli.run(str(CALL_DURATION_S), "ok", is_json=False, timeout_s=5, retries=0)
    with pytest.raises(CLIError):
        await cli.run(str(CALL_DURATION_S), "fail", is_json=False, timeout_s=5, retries=0)

    elapsed = await _run_n_parallel(cli, required)
    assert elapsed >= required * CALL_DURATION_S * 0.5


async def test_critical_section_wakes_waiters_after_failure(tmp_path: Path) -> None:
    """A failure mid-streak must still wake other tasks parked on the condition, not hang them."""
    script = write_sleep_maybe_fail_mock(tmp_path)
    write_auth_session(tmp_path, age_s=ProtonCLI.STALE_AUTH_THRESHOLD_S + 1)
    cli = make_cli(script, xdg=tmp_path)

    n_calls = ProtonCLI.STALE_AUTH_REQUIRED_SUCCESSES + 2

    async def call(index: int) -> None:
        flag = "fail" if index == 1 else "ok"
        await cli.run(str(CALL_DURATION_S), flag, is_json=False, timeout_s=5, retries=0)

    results = await asyncio.wait_for(
        asyncio.gather(*(call(i) for i in range(n_calls)), return_exceptions=True),
        timeout=n_calls * CALL_DURATION_S * 20 + 5,
    )

    assert isinstance(results[1], CLIError)
    for i, result in enumerate(results):
        if i != 1:
            assert result is None


@pytest.mark.parametrize("status", [HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN])
async def test_api_call_401_403_raise_auth_error(
    tmp_path: Path,
    status: HTTPStatus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 401/403 from Proton's API must surface as AuthError."""
    write_auth_session(tmp_path)
    cli = make_cli(tmp_path / "unused", xdg=tmp_path)
    patch_http_session(monkeypatch, status)

    with pytest.raises(AuthError):
        await cli.get_email()


async def test_api_call_500_stays_cli_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-auth error status must stay a plain CLIError, not AuthError."""
    write_auth_session(tmp_path)
    cli = make_cli(tmp_path / "unused", xdg=tmp_path)
    patch_http_session(monkeypatch, HTTPStatus.INTERNAL_SERVER_ERROR)

    with pytest.raises(CLIError) as exc_info:
        await cli.get_email()
    assert type(exc_info.value) is CLIError

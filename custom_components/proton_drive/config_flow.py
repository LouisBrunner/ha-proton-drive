"""Adds config flow for Blueprint."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode

from .cli import CLIError, CLIStartupError, ProtonCLI
from .const import (
    CLI_VERSION,
    CONF_BACKUP_FOLDER,
    CONF_LAST_CLI_RUN,
    DOMAIN,
    LOGGER,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine, Generator, Mapping

_DEFAULT_FOLDER = Path("/my-files")


def _form_backup_folder(*, backup_folder: str, options: list[str]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_BACKUP_FOLDER, default=backup_folder): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    custom_value=True,
                    mode=SelectSelectorMode.DROPDOWN,
                    sort=True,
                )
            ),
        },
    )


class ProtonDriveFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Proton Drive."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self.__backup_folder: str = str(_DEFAULT_FOLDER)
        self.__post_cli = "failed"
        self.__cli: ProtonCLI | None = None
        self.__cli_task: ProtonDriveFlowHandler._HASSTask[ProtonCLI] | None = None
        self.__auth_task: tuple[ProtonCLI.AuthFlow, ProtonDriveFlowHandler._HASSTask[Any]] | None = None
        self.__auth_flow_task: ProtonDriveFlowHandler._HASSTask[ProtonCLI.AuthFlow] | None = None
        self.__error: tuple[str, str] | None = None

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        for entry in self._async_current_entries():
            if entry.unique_id == DOMAIN:
                return self.async_abort(reason="already_configured")
        return await self.async_step_auth_start(user_input)

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow started when the user needs to reauthenticate."""
        if CONF_BACKUP_FOLDER not in entry_data:
            return self.async_abort(reason="legacy")
        return await self.async_step_auth_start()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Reconfigure the backup folder."""
        entry = self._get_reconfigure_entry()
        folder = entry.data.get(CONF_BACKUP_FOLDER)
        if folder is None:
            return self.async_abort(reason="legacy")
        self.__backup_folder = folder
        self.__post_cli = "backup_folder"
        return await self.async_step_prepare_cli(user_input)

    async def async_step_auth_start(
        self,
        _user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the authentication start step."""
        self.__post_cli = "auth_prepare"
        return await self.async_step_prepare_cli()

    async def async_step_prepare_cli(
        self,
        _user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the authentication start step."""
        if self.__cli is not None:
            return self.async_show_progress_done(next_step_id=self.__post_cli)

        if self.__cli_task is None:
            LOGGER.debug("Starting CLI init")
            entry = None
            if self.source == config_entries.SOURCE_REAUTH:
                entry = self._get_reauth_entry()
            elif self.source == config_entries.SOURCE_RECONFIGURE:
                entry = self._get_reconfigure_entry()
            self.__cli_task = self.__async_create_task(ProtonCLI.create(self.hass, entry))
        assert self.__cli_task is not None  # noqa: S101

        if not self.__cli_task.done():
            LOGGER.debug("CLI task in progress")
            return self.async_show_progress(
                step_id="prepare_cli",
                progress_action="downloading_cli",
                progress_task=self.__cli_task.task(),
                description_placeholders={"version": CLI_VERSION},
            )

        try:
            LOGGER.debug("CLI task is done")
            self.__cli = await self.__cli_task
            LOGGER.debug("CLI task worked: %s", self.__cli)
            return self.async_show_progress_done(next_step_id=self.__post_cli)
        except CLIStartupError as e:
            LOGGER.exception("CLI startup failed")
            return await self.__progress_failed(reason="cli_failed", message=str(e))

    async def async_step_auth_prepare(
        self,
        _user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the authentication prepare step."""
        if self.__cli is None:
            return self.async_show_progress_done(next_step_id="auth_start")
        if self.__auth_task is not None:
            return self.async_show_progress_done(next_step_id=self.__get_auth_step_id())

        if self.__auth_flow_task is None:
            LOGGER.debug("Starting auth flow task")
            self.__auth_flow_task = self.__async_create_task(self.__cli.start_auth_flow())
        assert self.__auth_flow_task is not None  # noqa: S101

        if not self.__auth_flow_task.done():
            LOGGER.debug("Auth flow task in progress")
            return self.async_show_progress(
                step_id="auth_prepare",
                progress_action="starting_auth_flow",
                progress_task=self.__auth_flow_task.task(),
            )

        try:
            LOGGER.debug("Auth flow task is done")
            task = await self.__auth_flow_task
            LOGGER.debug("Auth flow task worked: %s", task)
            self.__auth_task = (task, self._HASSTask(task.task()))
            LOGGER.debug("Auth flow task worked: %s", self.__auth_task)
            return self.async_show_progress_done(next_step_id=self.__get_auth_step_id())
        except CLIError as e:
            LOGGER.exception("Auth flow task failed")
            return await self.__progress_failed(reason="cannot_auth", message=str(e))

    async def async_step_auth_user(
        self,
        _user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the authentication user step."""
        if self.__cli is None:
            return self.async_show_progress_done(next_step_id="auth_start")
        if self.__auth_task is None:
            return self.async_show_progress_done(next_step_id="auth_prepare")

        auth, task = self.__auth_task
        if not task.done():
            LOGGER.debug("Auth task in progress")
            return self.async_show_progress(
                step_id=self.__get_auth_step_id(),
                progress_action="awaiting_user",
                progress_task=task.task(),
                description_placeholders={
                    "url": auth.get_url(),
                },
            )

        try:
            LOGGER.debug("Auth task is done")
            await task
            LOGGER.debug("Auth task worked")
            return self.async_show_progress_done(next_step_id="after_auth")
        except CLIError as e:
            LOGGER.exception("Auth task failed")
            return await self.__progress_failed(reason="failed_auth", message=str(e))

    async def async_step_reauth_user(
        self,
        _user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the reauthentication user step."""
        return await self.async_step_auth_user()

    async def async_step_after_auth(self, _user_input: dict | None = None) -> config_entries.ConfigFlowResult:
        """Handle the after-auth step."""
        if self.source == config_entries.SOURCE_REAUTH:
            return await self.__finish()
        return await self.async_step_backup_folder()

    async def async_step_backup_folder(self, user_input: dict | None = None) -> config_entries.ConfigFlowResult:
        """Handle the backup folder step."""
        if user_input is not None:
            self.__backup_folder = user_input[CONF_BACKUP_FOLDER]
            return await self.__finish()

        assert self.__cli is not None  # noqa: S101
        options = await self.__get_folder_options(self.__backup_folder)
        return self.async_show_form(
            step_id="backup_folder",
            data_schema=_form_backup_folder(backup_folder=self.__backup_folder, options=options),
        )

    async def __get_folder_options(self, backup_folder: str) -> list[str]:
        assert self.__cli is not None  # noqa: S101

        current = Path(backup_folder)
        extra = [p for p in (current, current.parent) if p not in (Path("/"), _DEFAULT_FOLDER)]

        all_paths = [Path("/"), _DEFAULT_FOLDER, *extra]
        results = await asyncio.gather(
            *(self.__cli.list_files(p, folders_only=True) for p in all_paths),
            return_exceptions=True,
        )

        options: list[str] = []
        for folder_path, result in zip(all_paths, results, strict=True):
            if isinstance(result, BaseException):
                LOGGER.warning("Failed to list folders in %s: %s", folder_path, result)
                continue
            if folder_path == Path("/"):
                options.extend(result)
            else:
                options.extend(str(folder_path / name) for name in result)
        return options

    async def __progress_failed(self, reason: str, message: str) -> config_entries.ConfigFlowResult:
        self.__error = (reason, message)
        return self.async_show_progress_done(next_step_id="failed")

    async def async_step_failed(self, _user_input: dict | None = None) -> config_entries.ConfigFlowResult:
        """Handle the failed step."""
        if self.__error is None:
            reason, message = "unknown", "Unknown error"
        else:
            reason, message = self.__error
        return self.async_abort(reason=reason, description_placeholders={"error": message})

    def __get_auth_step_id(self) -> str:
        return "reauth_user" if self.source == config_entries.SOURCE_REAUTH else "auth_user"

    async def __finish(self) -> config_entries.ConfigFlowResult:
        await self.async_set_unique_id(unique_id=DOMAIN)
        if self.source in (
            config_entries.SOURCE_RECONFIGURE,
            config_entries.SOURCE_REAUTH,
        ):
            entry = None
            updates = {}
            if self.source == config_entries.SOURCE_RECONFIGURE:
                entry = self._get_reconfigure_entry()
                updates = {
                    CONF_BACKUP_FOLDER: self.__backup_folder,
                }
            else:
                entry = self._get_reauth_entry()
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                entry,
                data_updates={
                    **updates,
                    CONF_LAST_CLI_RUN: time.time(),
                },
            )

        self._abort_if_unique_id_configured()
        assert self.__cli is not None  # noqa: S101
        title = "CLI"
        try:
            title = await self.__cli.get_email()
        except CLIError:
            LOGGER.warning("Failed to fetch user email for entry title")
        return self.async_create_entry(
            title=title,
            data={
                CONF_BACKUP_FOLDER: self.__backup_folder,
                CONF_LAST_CLI_RUN: time.time(),
            },
        )

    def __async_create_task[T](self, coro: Coroutine[None, None, T]) -> _HASSTask[T]:
        return self._HASSTask(self.hass.async_create_task(coro))

    class _HASSTask[T]:
        MINIMUM_ASYNC_DURATION_S = 1

        def __init__(self, task: asyncio.Task[T]) -> None:
            self.__task = task
            self.__first = True
            self.__started = time.monotonic()

        def task(self) -> asyncio.Task[T]:
            return self.__task

        def done(self) -> bool:
            # FIXME: crazy, but if we don't show the progress at least once, HomeAssistant breaks
            if self.__first:
                self.__first = False
                return False
            return self.__task.done()

        def __await__(self) -> Generator[None, None, T]:
            return self.__wait().__await__()

        async def __wait(self) -> T:
            # FIXME: crazy, but if you go too fast, HomeAssistant breaks
            elapsed = time.monotonic() - self.__started
            if elapsed < self.MINIMUM_ASYNC_DURATION_S:
                await asyncio.sleep(self.MINIMUM_ASYNC_DURATION_S - elapsed)
            return await self.__task

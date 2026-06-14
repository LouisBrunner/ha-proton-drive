"""Adds config flow for Blueprint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .cli import ProtonCLI
from .const import (
    CLI_VERSION,
    CONF_BACKUP_FOLDER,
    DOMAIN,
)

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Mapping


def _form_config_auth() -> vol.Schema:
    return vol.Schema({})


def _form_backup_folder(*, backup_folder: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_BACKUP_FOLDER, default=(backup_folder)): selector.TextSelector(),
        }
    )


class ProtonDriveFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Proton Drive."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self.__backup_folder: str = "/my-files"
        self.__cli: ProtonCLI | None = None
        self.__cli_task: asyncio.Task[ProtonCLI] | None = None
        self.__auth_task: ProtonCLI.AuthFlow | None = None
        self.__auth_flow_task: asyncio.Task[ProtonCLI.AuthFlow] | None = None
        self.__init_error: tuple[str, str] | None = None

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return await self.async_step_auth(user_input)

    async def async_step_auth(
        self,
        _user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the authentication progress step."""
        result = self.__ensure_cli()
        if result is not None:
            return result

        result = self.__ensure_auth_flow()
        if result is not None:
            return result

        return self.async_show_progress_done(next_step_id="auth_form")

    async def async_step_auth_form(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Show the authentication URL form."""
        errors: dict = {}
        description_placeholders: dict = {}

        if self.__init_error is not None:
            errors["base"], description_placeholders["error"] = self.__init_error
        elif self.__auth_task is not None:
            if self.__auth_task.is_done():
                if self.__auth_task.has_error():
                    errors["base"] = "failed_auth"
                    description_placeholders["error"] = self.__auth_task.get_error()
                else:
                    return await self.__async_step_after_auth()
            elif user_input is not None:
                self.__init_error = None
                errors["base"] = "pending_auth"

        url = "unknown"
        if self.__auth_task is not None:
            url = self.__auth_task.get_url()
        elif self.__init_error is not None:
            url = "Unavailable due to error"
        description_placeholders["url"] = url

        step_id = "reauth_confirm" if self.source == config_entries.SOURCE_REAUTH else "auth_form"
        return self.async_show_form(
            step_id=step_id,
            data_schema=_form_config_auth(),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_reauth_confirm(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the reauth confirmation form submission."""
        return await self.async_step_auth_form(user_input)

    def __ensure_cli(self) -> config_entries.ConfigFlowResult | None:
        if self.__cli is not None:
            return None
        if self.__cli_task is None:
            self.__cli_task = self.hass.async_create_task(ProtonCLI.create(self.hass))
        if not self.__cli_task.done():
            return self.async_show_progress(
                step_id="auth",
                progress_action="downloading_cli",
                progress_task=self.__cli_task,
                description_placeholders={"version": CLI_VERSION},
            )
        exc = self.__cli_task.exception()
        if exc is not None:
            self.__init_error = ("cli_failed", str(exc))
        else:
            self.__cli = self.__cli_task.result()
        return None

    def __ensure_auth_flow(self) -> config_entries.ConfigFlowResult | None:
        if self.__cli is None or self.__auth_task is not None:
            return None
        if self.__auth_flow_task is None:
            self.__auth_flow_task = self.hass.async_create_task(self.__cli.start_auth_flow())
        if not self.__auth_flow_task.done():
            return self.async_show_progress(
                step_id="auth",
                progress_action="starting_auth_flow",
                progress_task=self.__auth_flow_task,
            )
        exc = self.__auth_flow_task.exception()
        if exc is not None:
            self.__init_error = ("cannot_auth", str(exc))
        else:
            self.__auth_task = self.__auth_flow_task.result()
        return None

    async def async_step_backup_folder(self, user_input: dict | None = None) -> config_entries.ConfigFlowResult:
        """Handle the backup folder step."""
        if user_input is not None:
            self.__backup_folder = user_input[CONF_BACKUP_FOLDER]
            return await self.__finish()

        return self.async_show_form(
            step_id="backup_folder",
            data_schema=_form_backup_folder(backup_folder=self.__backup_folder),
        )

    async def __async_step_after_auth(self) -> config_entries.ConfigFlowResult:
        if self.source == config_entries.SOURCE_REAUTH:
            return await self.__finish()
        return await self.async_step_backup_folder()

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow started when the user needs to reauthenticate."""
        if CONF_BACKUP_FOLDER not in entry_data:
            return self.async_abort(reason="legacy")
        return await self.async_step_auth()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Reconfigure the backup folder."""
        entry = self._get_reconfigure_entry()
        folder = entry.data.get(CONF_BACKUP_FOLDER)
        if folder is None:
            return self.async_abort(reason="legacy")
        self.__backup_folder = folder
        return await self.async_step_backup_folder(user_input)

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
                data_updates=updates,
            )

        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title="Proton Drive",
            data={
                CONF_BACKUP_FOLDER: self.__backup_folder,
            },
        )

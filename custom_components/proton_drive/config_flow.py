"""Adds config flow for Blueprint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from custom_components.proton_drive.cli import CLIError, CLIStartupError, ProtonCLI

from .const import (
    CONF_BACKUP_FOLDER,
    DOMAIN,
)

if TYPE_CHECKING:
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
        self.__auth_task: ProtonCLI.AuthFlow | None = None
        self.__auth_url: str | None = None

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return await self.__async_step_auth(user_input)

    async def __async_step_auth(
        self,
        user_input: dict | None = None,
        *,
        errors: dict | None = None,
        description_placeholders: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        errors = errors or {}
        description_placeholders = description_placeholders or {}

        if self.__cli is None:
            try:
                self.__cli = await ProtonCLI.create(self.hass)
            except CLIStartupError as e:
                errors["base"] = "cli_failed"
                description_placeholders["error"] = str(e)

        if self.__cli is not None and self.__auth_task is None:
            try:
                self.__auth_task = await self.__cli.start_auth_flow()
            except CLIError as e:
                errors["base"] = "cannot_auth"
                description_placeholders["error"] = str(e)

        if self.__auth_task is not None:
            if self.__auth_task.is_done():
                if self.__auth_task.has_error():
                    errors["base"] = "failed_auth"
                    description_placeholders["error"] = self.__auth_task.get_error()
                else:
                    return await self.__async_step_after_auth()
            elif user_input is not None:
                errors["base"] = "pending_auth"
                description_placeholders["error"] = self.__auth_url

        description_placeholders["url"] = self.__auth_task.get_url() if self.__auth_task is not None else "unknown"

        return self.async_show_form(
            step_id="auth",
            data_schema=_form_config_auth(),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def __async_step_backup_folder(self, user_input: dict | None = None) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._backup_folder = user_input[CONF_BACKUP_FOLDER]
            return await self.__finish()

        return self.async_show_form(
            step_id="backup_folder",
            data_schema=_form_backup_folder(backup_folder=self._backup_folder),
        )

    async def __async_step_after_auth(self) -> config_entries.ConfigFlowResult:
        if self.source == config_entries.SOURCE_REAUTH:
            return await self.___finish()
        return await self.__async_step_backup_folder()

    async def async_step_reauth(
        self,
        _entry_data: Mapping[str, Any],
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow started when the user needs to reauthenticate."""
        return await self.__async_step_auth()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> config_entries.ConfigFlowResult:
        """Reconfigure the backup folder."""
        entry = self._get_reconfigure_entry()
        self.__backup_folder = entry.data[CONF_BACKUP_FOLDER]
        return await self.__async_step_backup_folder(user_input)

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

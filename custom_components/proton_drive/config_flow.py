"""Adds config flow for Blueprint."""
# ruff: noqa: S101

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers import selector
from slugify import slugify

from .api import (
    ProtonDriveAPIAuthenticationError,
    ProtonDriveAPIConnectionError,
    ProtonDriveAPIError,
    ProtonDriveAPIMFAError,
    ProtonDriveClient,
)
from .const import CONF_MFA_CODE, CONF_ROOT_FOLDER, DOMAIN

if TYPE_CHECKING:
    from collections.abc import Mapping

    from proton.proton import Credentials

CONFIG_AUTH = vol.Schema(
    {
        vol.Required(CONF_EMAIL): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.EMAIL, autocomplete="email"
            ),
        ),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
                autocomplete="current-password",
            ),
        ),
    },
)

CONFIG_REAUTH = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
                autocomplete="current-password",
            ),
        ),
    },
)

CONFIG_MFA = vol.Schema(
    {
        vol.Required(CONF_MFA_CODE): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT, autocomplete="one-time-code"
            ),
        ),
    },
)

CONFIG_FOLDERS = vol.Schema(
    {
        vol.Required(CONF_ROOT_FOLDER): str,
    },
)


class ProtonDriveFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Proton Drive."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._email = None
        self._password = None
        self._mfa_code = None
        self._root_folder = None
        self._creds = None

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors, description_placeholders = {}, {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._password = user_input[CONF_PASSWORD]

            try:
                self._creds = await self._authenticate(
                    email=self._email,
                    password=self._password,
                )
                return await self.async_step_folders()
            except ProtonDriveAPIAuthenticationError as error:
                errors["base"] = "invalid_auth"
                description_placeholders["error"] = str(error)
            except ProtonDriveAPIConnectionError as error:
                errors["base"] = "cannot_connect"
                description_placeholders["error"] = str(error)
            except ProtonDriveAPIMFAError:
                return await self.async_step_mfa()
            except ProtonDriveAPIError as error:
                errors["base"] = "unknown"
                description_placeholders["error"] = str(error)

        return self.async_show_form(
            step_id="user",
            data_schema=CONFIG_AUTH,
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_mfa(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle a multi-factor authentication (MFA) flow."""
        errors, description_placeholders = {}, {}

        if user_input is not None:
            self._mfa = user_input[CONF_MFA_CODE]

            assert self._email is not None
            assert self._password is not None

            try:
                self._creds = await self._authenticate(
                    email=self._email,
                    password=self._password,
                    mfa=self._mfa,
                )
                return await self.async_step_folders()
            except ProtonDriveAPIAuthenticationError as error:
                errors["base"] = "invalid_auth"
                description_placeholders["error"] = str(error)
            except ProtonDriveAPIConnectionError as error:
                errors["base"] = "cannot_connect"
                description_placeholders["error"] = str(error)
            except ProtonDriveAPIError as error:
                errors["base"] = "unknown"
                description_placeholders["error"] = str(error)

        return self.async_show_form(
            step_id="mfa",
            data_schema=CONFIG_MFA,
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_folders(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Select folders to sync."""
        if user_input is not None:
            self._root_folder = user_input["folders"]

            return await self._finish()

        return self.async_show_form(
            step_id="folders",
            data_schema=CONFIG_FOLDERS,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> config_entries.ConfigFlowResult:
        """Select folders to sync."""
        self._email = entry_data[CONF_EMAIL]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=CONFIG_REAUTH,
            )
        user_input[CONF_EMAIL] = self._email
        return await self.async_step_user(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Reconfigure part of the flow."""
        entry = self._get_reconfigure_entry()
        self._email = entry.data[CONF_EMAIL]
        self._root_folder = entry.data[CONF_ROOT_FOLDER]
        return await self.async_step_folders(user_input)

    async def _authenticate(
        self, *, email: str, password: str, mfa: str | None = None
    ) -> Credentials:
        """Validate credentials."""
        return await ProtonDriveClient.login(
            hass=self.hass, username=email, password=password, mfa=mfa
        )

    async def _finish(self) -> config_entries.ConfigFlowResult:
        assert self._email is not None
        assert self._root_folder is not None

        await self.async_set_unique_id(unique_id=slugify(self._email))
        if self.source in (
            config_entries.SOURCE_RECONFIGURE,
            config_entries.SOURCE_REAUTH,
        ):
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                data_updates={
                    CONF_ROOT_FOLDER: self._root_folder,
                },
            )

        assert self._creds is not None

        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=self._email,
            data={
                CONF_EMAIL: self._email,
                CONF_ROOT_FOLDER: self._root_folder,
            },
        )

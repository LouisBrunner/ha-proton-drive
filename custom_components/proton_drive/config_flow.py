"""Adds config flow for Blueprint."""
# ruff: noqa: S101

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers import instance_id, selector
from slugify import slugify

from .api import (
    ProtonDriveAPIAuthenticationError,
    ProtonDriveAPIConnectionError,
    ProtonDriveAPIError,
    ProtonDriveAPIMFAError,
    ProtonDriveClient,
)
from .const import CONF_MFA_CODE, CONF_ROOT_FOLDER, CONF_SHARE_ID, DOMAIN
from .helpers import create_credentials_from_data, serialize_credentials_to_data

if TYPE_CHECKING:
    from collections.abc import Mapping

    from proton.proton import Credentials, Share


def form_config_auth(*, email: str | None) -> vol.Schema:
    """Create a form schema for the authentication step."""
    return vol.Schema(
        {
            vol.Required(CONF_EMAIL, default=email): selector.TextSelector(
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


def form_config_reauth() -> vol.Schema:
    """Create a form schema for the reauthentication step."""
    return vol.Schema(
        {
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD,
                    autocomplete="current-password",
                ),
            ),
        },
    )


def form_config_mfa() -> vol.Schema:
    """Create a form schema for the MFA step."""
    return vol.Schema(
        {
            vol.Required(CONF_MFA_CODE): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.TEXT, autocomplete="one-time-code"
                ),
            ),
        },
    )


def form_config_folders(
    *, share_id: str | None, root_folder: str | None, shares: list[Share]
) -> vol.Schema:
    """Create a form schema for the folders config step."""
    return vol.Schema(
        {
            vol.Required(CONF_SHARE_ID, default=share_id): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    options=[
                        selector.SelectOptionDict(label=share.Name, value=share.ShareID)
                        for share in shares
                    ],
                ),
            ),
            vol.Optional(CONF_ROOT_FOLDER, default=(root_folder or "")): str,
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
        self._share_id = None
        self._root_folder = None

        self._creds = None
        self._shares = None

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
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

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._password = user_input[CONF_PASSWORD]

            try:
                self._creds = await self.__authenticate(
                    email=self._email,
                    password=self._password,
                )
                return await self.__async_step_after_auth()
            except ProtonDriveAPIAuthenticationError as error:
                errors["base"] = "invalid_auth"
                description_placeholders["error"] = str(error)
            except ProtonDriveAPIConnectionError as error:
                errors["base"] = "cannot_connect"
                description_placeholders["error"] = str(error)
            except ProtonDriveAPIMFAError:
                return await self.__async_step_mfa()
            except ProtonDriveAPIError as error:
                errors["base"] = "unknown"
                description_placeholders["error"] = str(error)

        return self.async_show_form(
            step_id="auth",
            data_schema=form_config_auth(
                email=(user_input or {}).get(CONF_EMAIL, self._email)
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def __async_step_mfa(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        errors, description_placeholders = {}, {}

        if user_input is not None:
            self._mfa = user_input[CONF_MFA_CODE]

            assert self._email is not None
            assert self._password is not None

            try:
                self._creds = await self.__authenticate(
                    email=self._email,
                    password=self._password,
                    mfa=self._mfa,
                )
                return await self.__async_step_after_auth()
            except (ProtonDriveAPIAuthenticationError, ProtonDriveAPIMFAError) as error:
                errors["base"] = "invalid_auth"
                description_placeholders["error"] = str(error)
                return await self.__async_step_auth(
                    None,
                    errors=errors,
                    description_placeholders=description_placeholders,
                )
            except ProtonDriveAPIConnectionError as error:
                errors["base"] = "cannot_connect"
                description_placeholders["error"] = str(error)
            except ProtonDriveAPIError as error:
                errors["base"] = "unknown"
                description_placeholders["error"] = str(error)

        return self.async_show_form(
            step_id="mfa",
            data_schema=form_config_mfa(),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def __async_step_after_auth(self) -> config_entries.ConfigFlowResult:
        if self.source == config_entries.SOURCE_REAUTH:
            return await self.__finish()
        return await self.__async_step_folders()

    def __update_creds(self, creds: Credentials) -> None:
        self._creds = creds

    async def __async_step_folders(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        errors, description_placeholders = {}, {}

        if self._shares is None:
            try:
                self._shares = await ProtonDriveClient(
                    hass=self.hass,
                    creds=self._creds,
                    instance_id=await instance_id.async_get(self.hass),
                    base_folder="",
                    share_id="",
                    update_creds=self.__update_creds,
                ).list_shares()
            except ProtonDriveAPIError as error:
                errors["base"] = "unknown"
                description_placeholders["error"] = str(error)
        assert self._shares is not None

        if user_input is not None:
            self._share_id = user_input[CONF_SHARE_ID]
            self._root_folder = user_input[CONF_ROOT_FOLDER]

            return await self.__finish()

        return self.async_show_form(
            step_id="folders",
            data_schema=form_config_folders(
                shares=self._shares,
                share_id=(user_input or {}).get(CONF_SHARE_ID, self._share_id),
                root_folder=(user_input or {}).get(CONF_ROOT_FOLDER, self._root_folder),
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow started when the user need to reauthenticate."""
        self._email = entry_data[CONF_EMAIL]
        return await self.__async_step_reauth_confirm()

    async def __async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=form_config_reauth(),
            )
        user_input[CONF_EMAIL] = self._email
        return await self.async_step_user(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Reconfigure part of the flow."""
        entry = self._get_reconfigure_entry()
        self._email = entry.data[CONF_EMAIL]
        self._creds = create_credentials_from_data(entry.data)
        self._share_id = entry.data[CONF_SHARE_ID]
        self._root_folder = entry.data[CONF_ROOT_FOLDER]
        return await self.__async_step_folders(user_input)

    async def __authenticate(
        self, *, email: str, password: str, mfa: str | None = None
    ) -> Credentials:
        """Validate credentials."""
        return await ProtonDriveClient.login(
            hass=self.hass, username=email, password=password, mfa=mfa
        )

    async def __finish(self) -> config_entries.ConfigFlowResult:
        assert self._email is not None

        await self.async_set_unique_id(unique_id=slugify(self._email))
        if self.source in (
            config_entries.SOURCE_RECONFIGURE,
            config_entries.SOURCE_REAUTH,
        ):
            entry = None
            updates = {}
            if self.source == config_entries.SOURCE_RECONFIGURE:
                entry = self._get_reconfigure_entry()
                updates = {
                    CONF_SHARE_ID: self._share_id,
                    CONF_ROOT_FOLDER: self._root_folder,
                }
            else:
                entry = self._get_reauth_entry()
                updates = {
                    CONF_EMAIL: self._email,
                    **serialize_credentials_to_data(self._creds),
                }
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                entry,
                data_updates=updates,
            )

        assert self._creds is not None

        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=self._email,
            data={
                CONF_EMAIL: self._email,
                CONF_SHARE_ID: self._share_id,
                CONF_ROOT_FOLDER: self._root_folder,
                **serialize_credentials_to_data(self._creds),
            },
        )

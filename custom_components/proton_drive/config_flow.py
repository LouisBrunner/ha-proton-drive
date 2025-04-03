"""Adds config flow for Blueprint."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers import instance_id, selector
from slugify import slugify

from .api import (
    ProtonDriveAPIAuthenticationError,
    ProtonDriveAPIConnectionError,
    ProtonDriveAPIError,
    ProtonDriveClient,
)
from .const import DOMAIN

CONF_MFA_CODE = "mfa_code"

class ProtonDriveFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Proton Drive."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors, description_placeholders = {}, {}

        if user_input is not None:
            try:
                await self._test_credentials(
                    email=user_input[CONF_EMAIL],
                    password=user_input[CONF_PASSWORD],
                )
            except ProtonDriveAPIAuthenticationError as error:
                errors["base"] = "invalid_auth"
                description_placeholders["error"] = str(error)
            except ProtonDriveAPIConnectionError as error:
                errors["base"] = "cannot_connect"
                description_placeholders["error"] = str(error)
            except ProtonDriveAPIError as error:
                errors["base"] = "unknown"
                description_placeholders["error"] = str(error)
            else:
                await self.async_set_unique_id(
                    unique_id=slugify(user_input[CONF_EMAIL])
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_EMAIL],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_EMAIL,
                        default=(user_input or {}).get(CONF_EMAIL, vol.UNDEFINED),
                    ): selector.TextSelector(
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
                    vol.Optional(

                    )
                },
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def _test_credentials(self, email: str, password: str) -> None:
        """Validate credentials."""
        client = ProtonDriveClient(
            hass=self.hass,
            instance_id=await instance_id.async_get(self.hass),
            email=email,
            password=password,
        )
        await client.test_connection()

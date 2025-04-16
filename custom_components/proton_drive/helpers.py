"""Helper functions for Proton Drive."""

from types import MappingProxyType
from typing import Any

from proton.proton import Credentials

from .const import (
    CONF_CREDS_ACCESS_TOKEN,
    CONF_CREDS_REFRESH_TOKEN,
    CONF_CREDS_SALTED_KEY_PASS,
    CONF_CREDS_UID,
)


def create_credentials_from_data(data: MappingProxyType[str, Any]) -> Credentials:
    """Create a Credentials object from data."""
    return Credentials(
        UID=data[CONF_CREDS_UID],
        AccessToken=data[CONF_CREDS_ACCESS_TOKEN],
        RefreshToken=data[CONF_CREDS_REFRESH_TOKEN],
        SaltedKeyPass=data[CONF_CREDS_SALTED_KEY_PASS],
    )


def serialize_credentials_to_data(credentials: Credentials) -> dict[str, Any]:
    """Create a dictionary from Credentials object."""
    return {
        CONF_CREDS_UID: credentials.UID,
        CONF_CREDS_ACCESS_TOKEN: credentials.AccessToken,
        CONF_CREDS_REFRESH_TOKEN: credentials.RefreshToken,
        CONF_CREDS_SALTED_KEY_PASS: credentials.SaltedKeyPass,
    }

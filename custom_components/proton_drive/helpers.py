"""Helper functions for Proton Drive."""

from types import MappingProxyType
from typing import Any

from proton import Credentials

from .const import (
    CONF_CREDS_ACCESS_TOKEN,
    CONF_CREDS_REFRESH_TOKEN,
    CONF_CREDS_SALTED_KEY_PASS,
    CONF_CREDS_UID,
)


def create_credentials_from_data(data: MappingProxyType[str, Any]) -> Credentials:
    """Create a Credentials object from data."""
    return Credentials(
        uid=data[CONF_CREDS_UID],
        access_token=data[CONF_CREDS_ACCESS_TOKEN],
        refresh_token=data[CONF_CREDS_REFRESH_TOKEN],
        salted_key_pass=data[CONF_CREDS_SALTED_KEY_PASS],
    )


def serialize_credentials_to_data(credentials: Credentials) -> dict[str, Any]:
    """Create a dictionary from Credentials object."""
    return {
        CONF_CREDS_UID: credentials.uid,
        CONF_CREDS_ACCESS_TOKEN: credentials.access_token,
        CONF_CREDS_REFRESH_TOKEN: credentials.refresh_token,
        CONF_CREDS_SALTED_KEY_PASS: credentials.salted_key_pass,
    }

"""Constants for the Proton Drive integration."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "proton_drive"

CONF_MFA_CODE = "mfa_code"
CONF_ROOT_FOLDER = "root_folder"

CONF_CREDS_UID = "creds_uid"
CONF_CREDS_ACCESS_TOKEN = "creds_access_token"  # noqa: S105
CONF_CREDS_REFRESH_TOKEN = "creds_refresh_token"  # noqa: S105
CONF_CREDS_SALTED_KEY_PASS = "creds_salted_key_pass"  # noqa: S105

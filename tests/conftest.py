"""Stub the third-party/HA modules `proton_drive`'s package import chain needs, without installing them."""

from __future__ import annotations

import sys
import types
from typing import Any


def _new_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def _install_fake_homeassistant() -> None:
    if "homeassistant.loader" in sys.modules:
        return

    homeassistant = _new_module("homeassistant")

    exceptions = _new_module("homeassistant.exceptions")
    exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
    exceptions.ConfigEntryError = type("ConfigEntryError", (Exception,), {})
    exceptions.ConfigEntryNotReady = type("ConfigEntryNotReady", (Exception,), {})

    helpers = _new_module("homeassistant.helpers")
    _new_module("homeassistant.helpers.instance_id")

    async def async_get_integration(*_args: object, **_kwargs: object) -> Any:
        msg = "stubbed for tests; ProtonCLI.create() is not exercised directly"
        raise NotImplementedError(msg)

    loader = _new_module("homeassistant.loader")
    loader.async_get_integration = async_get_integration

    components = _new_module("homeassistant.components")
    backup = _new_module("homeassistant.components.backup")
    backup.AgentBackup = type("AgentBackup", (), {})
    backup.OnProgressCallback = object
    backup_util = _new_module("homeassistant.components.backup.util")
    backup_util.suggested_filename = lambda *_args, **_kwargs: ""

    util = _new_module("homeassistant.util")
    hass_dict = _new_module("homeassistant.util.hass_dict")

    class HassKey:
        """Stand-in for homeassistant.util.hass_dict.HassKey, just needs to be constructible."""

        def __init__(self, name: str) -> None:
            self.name = name

    hass_dict.HassKey = HassKey

    homeassistant.exceptions = exceptions
    homeassistant.helpers = helpers
    homeassistant.loader = loader
    homeassistant.components = components
    components.backup = backup
    backup.util = backup_util
    homeassistant.util = util
    util.hass_dict = hass_dict


def _install_fake_aiohttp() -> None:
    if "aiohttp" in sys.modules:
        return

    aiohttp = _new_module("aiohttp")

    class ClientError(Exception):
        """Stand-in for aiohttp.ClientError."""

    class ClientResponseError(ClientError):
        """Stand-in for aiohttp.ClientResponseError, keeping only the `.status` attribute cli.py reads."""

        def __init__(
            self,
            request_info: object = None,
            history: tuple[object, ...] = (),
            *,
            code: int | None = None,
            status: int | None = None,
            message: str = "",
            headers: object = None,
        ) -> None:
            super().__init__(message)
            self.request_info = request_info
            self.history = history
            self.status = status if status is not None else code
            self.message = message
            self.headers = headers

    aiohttp.ClientError = ClientError
    aiohttp.ClientResponseError = ClientResponseError


_install_fake_homeassistant()
_install_fake_aiohttp()

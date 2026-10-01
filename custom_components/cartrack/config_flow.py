"""Config flow for Cartrack."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    CartrackAuthError,
    CartrackClient,
    CartrackConnectionError,
    CartrackError,
    CartrackRateLimitError,
)
from .const import (
    CONF_REGION,
    CONF_SCAN_INTERVAL_MOVING,
    CONF_SCAN_INTERVAL_PARKED,
    CONF_STALE_TIMEOUT,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL_MOVING,
    DEFAULT_SCAN_INTERVAL_PARKED,
    DEFAULT_STALE_TIMEOUT,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    REGIONS,
)
from .coordinator import CartrackConfigEntry

_LOGGER = logging.getLogger(__name__)

REGION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(value=code, label=f"{name} ({code})")
            for code, name in REGIONS.items()
        ],
        mode=SelectSelectorMode.DROPDOWN,
        custom_value=True,
    )
)
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


async def _async_validate(
    flow: ConfigFlow, username: str, password: str, region: str
) -> tuple[dict[str, str], int]:
    """Try the credentials. Returns (errors, vehicle_count)."""
    client = CartrackClient(
        async_get_clientsession(flow.hass), username, password, region
    )
    try:
        vehicles = await client.async_get_vehicle_status()
    except CartrackAuthError:
        return {"base": "invalid_auth"}, 0
    except CartrackRateLimitError:
        return {"base": "rate_limited"}, 0
    except CartrackConnectionError:
        return {"base": "cannot_connect"}, 0
    except CartrackError:
        _LOGGER.exception("Unexpected Cartrack error")
        return {"base": "unknown"}, 0
    return {}, len(vehicles)


class CartrackConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle adding a Cartrack account."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for API credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            region = user_input[CONF_REGION].strip().lower()
            await self.async_set_unique_id(f"{region}_{username.lower()}")
            self._abort_if_unique_id_configured()

            errors, count = await _async_validate(
                self, username, user_input[CONF_PASSWORD], region
            )
            if not errors and count == 0:
                errors = {"base": "no_vehicles"}
            if not errors:
                return self.async_create_entry(
                    title=user_input.get("name") or username,
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REGION: region,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                vol.Required(CONF_REGION, default=DEFAULT_REGION): REGION_SELECTOR,
                vol.Optional("name"): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Credentials stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, _ = await _async_validate(
                self,
                entry.data[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                entry.data[CONF_REGION],
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: CartrackConfigEntry) -> OptionsFlow:
        """Polling options."""
        return CartrackOptionsFlow()


def _seconds_selector(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            unit_of_measurement="s",
            mode=NumberSelectorMode.BOX,
        )
    )


class CartrackOptionsFlow(OptionsFlow):
    """Tune polling intervals."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show polling options."""
        if user_input is not None:
            return self.async_create_entry(
                data={key: int(value) for key, value in user_input.items()}
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL_PARKED,
                    default=options.get(
                        CONF_SCAN_INTERVAL_PARKED, DEFAULT_SCAN_INTERVAL_PARKED
                    ),
                ): _seconds_selector(MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL),
                vol.Required(
                    CONF_SCAN_INTERVAL_MOVING,
                    default=options.get(
                        CONF_SCAN_INTERVAL_MOVING, DEFAULT_SCAN_INTERVAL_MOVING
                    ),
                ): _seconds_selector(MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL),
                vol.Required(
                    CONF_STALE_TIMEOUT,
                    default=options.get(CONF_STALE_TIMEOUT, DEFAULT_STALE_TIMEOUT),
                ): _seconds_selector(30, 3600),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

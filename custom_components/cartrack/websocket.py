"""Websocket commands used by the Cartrack route card.

``cartrack/route`` reads a day's positions from VictoriaMetrics when the
account has it configured (long-term history); without it the card reads
Home Assistant's recorder history itself. ``cartrack/trips`` adds Cartrack's
own trip list, which neither history source knows about.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from . import victoriametrics as vm
from .api import CartrackError, CartrackForbiddenError, CartrackRateLimitError
from .const import CONF_VM_PASSWORD, CONF_VM_URL, CONF_VM_USERNAME, DOMAIN

TRACKER_SUFFIX = "_tracker"

DAY_SCHEMA = {
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("date"): str,
}


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the commands once per Home Assistant start."""
    websocket_api.async_register_command(hass, ws_route)
    websocket_api.async_register_command(hass, ws_trips)


class _Problem(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _resolve(hass: HomeAssistant, msg: dict[str, Any]) -> tuple[ConfigEntry, str, date]:
    """Config entry, vehicle key and day for a tracker + date message."""
    entry = er.async_get(hass).async_get(msg["entity_id"])
    if (
        entry is None
        or entry.platform != DOMAIN
        or not entry.unique_id.endswith(TRACKER_SUFFIX)
    ):
        raise _Problem(
            websocket_api.ERR_NOT_FOUND,
            f"{msg['entity_id']} is not a Cartrack device tracker",
        )
    config_entry = hass.config_entries.async_get_entry(entry.config_entry_id or "")
    if config_entry is None or config_entry.state is not ConfigEntryState.LOADED:
        raise _Problem(
            websocket_api.ERR_NOT_FOUND, "The Cartrack account is not loaded"
        )
    try:
        day = date.fromisoformat(msg["date"])
    except ValueError as err:
        raise _Problem(websocket_api.ERR_INVALID_FORMAT, "Bad date") from err
    return config_entry, entry.unique_id.removesuffix(TRACKER_SUFFIX), day


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    """Start and end of a day in Home Assistant's time zone."""
    tz = dt_util.get_default_time_zone()
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)


@websocket_api.websocket_command({vol.Required("type"): "cartrack/route", **DAY_SCHEMA})
@websocket_api.async_response
async def ws_route(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """A day's positions for one vehicle from VictoriaMetrics."""
    try:
        config_entry, _, day = _resolve(hass, msg)
    except _Problem as err:
        connection.send_error(msg["id"], err.code, str(err))
        return
    url = config_entry.options.get(CONF_VM_URL)
    if not url:
        connection.send_error(
            msg["id"], "not_configured", "No VictoriaMetrics URL set for this account"
        )
        return

    start, end = _day_bounds(day)
    try:
        points = await vm.async_get_positions(
            async_get_clientsession(hass),
            url,
            msg["entity_id"].split(".", 1)[1],
            start,
            min(end, dt_util.now()),
            config_entry.options.get(CONF_VM_USERNAME),
            config_entry.options.get(CONF_VM_PASSWORD),
        )
    except vm.VictoriaMetricsError as err:
        connection.send_error(msg["id"], "victoriametrics_error", str(err))
        return
    connection.send_result(
        msg["id"],
        {
            "entity_id": msg["entity_id"],
            "date": day.isoformat(),
            "source": "victoriametrics",
            "points": points,
        },
    )


@websocket_api.websocket_command({vol.Required("type"): "cartrack/trips", **DAY_SCHEMA})
@websocket_api.async_response
async def ws_trips(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Cartrack's trips for one vehicle (by its device tracker) and local day."""
    try:
        config_entry, key, day = _resolve(hass, msg)
    except _Problem as err:
        connection.send_error(msg["id"], err.code, str(err))
        return

    coordinator = config_entry.runtime_data
    vehicle = coordinator.data.get(key)
    if vehicle is None:
        connection.send_error(
            msg["id"], websocket_api.ERR_NOT_FOUND, "Vehicle not on the account"
        )
        return

    start, end = _day_bounds(day)
    try:
        trips = await coordinator.client.async_get_trips(
            vehicle.registration,
            start,
            end - timedelta(seconds=1),
            dt_util.get_default_time_zone(),
        )
    except CartrackForbiddenError:
        connection.send_error(
            msg["id"],
            "forbidden",
            "These Cartrack API credentials have no access to trips",
        )
        return
    except CartrackRateLimitError as err:
        connection.send_error(
            msg["id"], "rate_limited", f"Rate limited, retry in {err.retry_after}s"
        )
        return
    except CartrackError as err:
        connection.send_error(msg["id"], websocket_api.ERR_UNKNOWN_ERROR, str(err))
        return

    connection.send_result(
        msg["id"],
        {
            "entity_id": msg["entity_id"],
            "date": day.isoformat(),
            "trips": [trip.as_dict() for trip in trips],
        },
    )

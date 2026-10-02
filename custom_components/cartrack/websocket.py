"""Websocket command used by the Cartrack route card.

Routes themselves come from Home Assistant's recorder history of the device
trackers; this only adds Cartrack's own trip list, which history cannot know.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .api import CartrackError, CartrackForbiddenError, CartrackRateLimitError
from .const import DOMAIN

TRACKER_SUFFIX = "_tracker"


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the commands once per Home Assistant start."""
    websocket_api.async_register_command(hass, ws_trips)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "cartrack/trips",
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("date"): str,
    }
)
@websocket_api.async_response
async def ws_trips(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Cartrack's trips for one vehicle (by its device tracker) and local day."""
    entry = er.async_get(hass).async_get(msg["entity_id"])
    if (
        entry is None
        or entry.platform != DOMAIN
        or not entry.unique_id.endswith(TRACKER_SUFFIX)
    ):
        connection.send_error(
            msg["id"],
            websocket_api.ERR_NOT_FOUND,
            f"{msg['entity_id']} is not a Cartrack device tracker",
        )
        return
    config_entry = hass.config_entries.async_get_entry(entry.config_entry_id or "")
    if config_entry is None or config_entry.state is not ConfigEntryState.LOADED:
        connection.send_error(
            msg["id"], websocket_api.ERR_NOT_FOUND, "The Cartrack account is not loaded"
        )
        return
    try:
        day = date.fromisoformat(msg["date"])
    except ValueError:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, "Bad date")
        return

    coordinator = config_entry.runtime_data
    vehicle = coordinator.data.get(entry.unique_id.removesuffix(TRACKER_SUFFIX))
    if vehicle is None:
        connection.send_error(
            msg["id"], websocket_api.ERR_NOT_FOUND, "Vehicle not on the account"
        )
        return

    tz = dt_util.get_default_time_zone()
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    try:
        trips = await coordinator.client.async_get_trips(
            vehicle.registration, start, end - timedelta(seconds=1), tz
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

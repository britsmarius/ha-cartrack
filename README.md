# Cartrack for Home Assistant

A Home Assistant integration that reads your vehicles straight from the
[Cartrack Fleet API](https://developer.cartrack.com/docs/fleet-api-general/overview).
No Traccar, no database, no bridge add-ons: Home Assistant polls Cartrack itself.

## What you get

Each vehicle on the account becomes a device named after its registration, with:

| Entity | Notes |
|---|---|
| Location (device tracker) | GPS position; works with zones, the map card and person tracking |
| Speed | km/h |
| Address | Street address as reported by Cartrack |
| Battery voltage | Vehicle battery / external supply voltage |
| Odometer | Shown in km |
| Ignition | On while the ignition is on |
| Moving | On while the vehicle is driving with fresh data |
| Last update, Tracker battery | Diagnostic |
| Heading, Altitude | Diagnostic, disabled by default |

Polling speeds up while any vehicle on the account is moving and slows down
when everything is parked (defaults: 10 s moving, 60 s parked, adjustable).
Cartrack allows about 60 requests per minute per account, and rate-limit
replies are respected.

Several accounts (for example a personal and a business account) can be added
side by side; each is its own entry with its own credentials.

## Requirements

- A Cartrack account with Fleetweb access.
- API credentials: sign in to Fleetweb for your region (South Africa:
  `https://fleetweb-za.cartrack.com`) and open **Settings → API Settings**.
  The username is your main account username. Generate a **subuser** API
  password for Home Assistant; it is read-only and can be revoked on its own.
- Home Assistant 2025.1 or newer.

If your login does not open Fleetweb, or API Settings is missing, ask
Cartrack to enable Fleet API access on your account.

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories**.
2. Add `https://github.com/britsmarius/ha-cartrack`, category **Integration**.
3. Install **Cartrack**, then restart Home Assistant.

### Manual

Copy `custom_components/cartrack` into your Home Assistant `config/custom_components/`
folder and restart.

## Setup

**Settings → Devices & services → Add integration → Cartrack**, then enter the
API username, API password and region. Repeat for each Cartrack account.

Polling intervals are under the integration's **Configure** button.

## Troubleshooting

- **Invalid credentials:** check the region; a South African account only
  works against the `za` API.
- **Missing or odd values:** download diagnostics from the integration's ⋮ menu
  and open an issue with it. Location, registration and credentials are
  redacted from the file.
- Debug logging:

  ```yaml
  logger:
    logs:
      custom_components.cartrack: debug
  ```

## Disclaimer

Not affiliated with or endorsed by Cartrack or Karooooo. This integration only
reads data; it never sends commands to a vehicle.

# AgriTrack Mobile API

AgriTrack exposes a mobile-first JSON API at `/api/mobile/v1`. Android clients
must talk to Flask over HTTP(S); they should never connect directly to the
database.

## Deployment Checklist

1. Run database migrations:

   ```powershell
   $env:FLASK_APP = "manage.py"
   flask db upgrade
   ```

2. Configure production secrets and storage:

   ```powershell
   $env:FLASK_ENV = "production"
   $env:SECRET_KEY = "<long-random-secret>"
   $env:DATABASE_URL = "postgresql+psycopg://..."
   ```

3. Create a mobile user and assign farm access:

   ```powershell
   flask mobile-create-user --email field@example.com --name "Field User" --password "<temporary-password>"
   flask mobile-assign-farm field@example.com "Demo Farm" --role manager
   ```

4. Serve the Flask app behind HTTPS before using real phones in the field.

5. Operational token commands:

   ```powershell
   flask mobile-set-password field@example.com --password "<new-password>"
   flask mobile-revoke-tokens field@example.com
   flask mobile-prune-expired-tokens --dry-run
   flask mobile-prune-expired-tokens
   ```

## Device Testing

- Android emulator to local Flask: use `http://10.0.2.2:5000`.
- Physical phone on the same LAN: run Flask on `0.0.0.0`, then use the PC LAN IP,
  for example `http://192.168.1.25:5000`.
- Production devices: use only an HTTPS URL, for example
  `https://agritrack.example.com`.

Local development example:

```powershell
$env:FLASK_APP = "manage.py"
flask run --host 0.0.0.0 --port 5000
```

## Auth

Every endpoint except login requires:

```http
Authorization: Bearer <token>
```

### Login

`POST /api/mobile/v1/auth/login`

Request:

```json
{
  "email": "field@example.com",
  "password": "password",
  "device_name": "Pixel Field Phone"
}
```

Response:

```json
{
  "token": "opaque-token",
  "token_type": "Bearer",
  "expires_at": "2026-08-16T10:00:00+00:00",
  "user": {
    "id": "user-id",
    "email": "field@example.com",
    "name": "Field User",
    "active": true,
    "farm_roles": []
  },
  "farms": []
}
```

### Logout

`POST /api/mobile/v1/auth/logout`

Response:

```json
{
  "status": "ok"
}
```

## Bootstrap

`GET /api/mobile/v1/bootstrap`

Use this after login to discover farm access, animal group types, supported sync
commands, and server time.

Important response fields:

```json
{
  "server_time": "2026-05-18T10:00:00+00:00",
  "user": {},
  "farms": [],
  "animal_group_types": [],
  "sync": {
    "supported_command_types": [
      "rainfall.create",
      "mob_event.create",
      "stock_count.record",
      "mob.move",
      "water_asset_status.update"
    ],
    "max_commands_per_request": 100
  }
}
```

## Farm Snapshot

`GET /api/mobile/v1/farms/<farm_id>/snapshot`

Use this as the Android read model. Store the response locally so field users can
keep working when signal drops.

Response groups:

```json
{
  "server_time": "2026-05-18T10:00:00+00:00",
  "farm": {},
  "paddocks": [],
  "mobs": [],
  "active_grazing": [],
  "rainfall": [],
  "mob_events": [],
  "water_assets": [],
  "water_connections": []
}
```

## Offline Sync Commands

`POST /api/mobile/v1/sync/commands`

Android should create a unique `client_command_id` before saving an offline
action locally. If the same command is sent again, Flask returns the original
result with `"duplicate": true`.

Request:

```json
{
  "commands": [
    {
      "client_command_id": "device-uuid-1",
      "type": "rainfall.create",
      "farm_id": "farm-id",
      "payload": {
        "recorded_on": "2026-05-18",
        "mm": 8.5,
        "note": "Front moved in"
      }
    }
  ]
}
```

Result shape:

```json
{
  "results": [
    {
      "client_command_id": "device-uuid-1",
      "type": "rainfall.create",
      "status": "applied",
      "duplicate": false,
      "response": {
        "rainfall_id": "rainfall-id"
      },
      "error": null
    }
  ]
}
```

Supported command types:

- `rainfall.create`: `recorded_on`, `mm`, optional `note`, optional `source`.
- `mob_event.create`: `mob_id`, `description`, `tags`, optional `event_at`.
- `stock_count.record`: `mob_id`, `animal_group_type_id`, `quantity`, optional `note`.
- `mob.move`: `mob_id`, `allocations`, optional `destination_farm_id`, optional `event_time`.
- `water_asset_status.update`: `water_asset_id`, and at least one of `active`, `status`, `water_level`.

## Error Shape

Errors use a stable JSON envelope:

```json
{
  "error": {
    "code": "missing_token",
    "message": "Bearer token is required"
  }
}
```

Common codes:

- `missing_token`
- `invalid_token`
- `expired_token`
- `revoked_token`
- `invalid_credentials`
- `forbidden`
- `not_found`
- `invalid_payload`
- `invalid_command`
- `unsupported_command`

## Android MVP Flow

1. Store the bearer token in encrypted storage.
2. Fetch `/bootstrap`.
3. Let the user pick a farm.
4. Fetch `/farms/<farm_id>/snapshot` and cache it locally.
5. Save field actions to a local offline queue with `client_command_id`.
6. Replay queued actions with `/sync/commands` when connectivity returns.
7. Mark commands as applied or failed based on the server result.

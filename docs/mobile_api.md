# AgriTrack Mobile API

AgriTrack exposes a mobile-first JSON API at `/api/mobile/v1`. Android clients
must talk to Flask over HTTP(S); they should never connect directly to the
database.

## Deployment Checklist

1. Run database migrations:

   ```powershell
   $env:FLASK_APP = "manage.py"
   .\.venv\Scripts\python -m flask db upgrade
   ```

2. Configure production secrets and storage:

   ```powershell
   $env:FLASK_ENV = "production"
   $env:SECRET_KEY = "<long-random-secret>"
   $env:DATABASE_URL = "postgresql+psycopg://..."
   ```

   For private farm-LAN use, keep SQLite and set:

   ```powershell
   $env:DATABASE_URL = "sqlite:///agritrack.db"
   $env:BACKUP_DIR = "backups"
   ```

3. Create a mobile user and assign farm access:

   ```powershell
   .\.venv\Scripts\python -m flask user-create --email field@example.com --name "Field User" --password "<temporary-password>"
   .\.venv\Scripts\python -m flask user-assign-farm field@example.com "Demo Farm" --role manager
   ```

4. Serve the Flask app behind HTTPS before using real phones in the field.

5. Operational token commands:

   ```powershell
   .\.venv\Scripts\python -m flask user-set-password field@example.com --password "<new-password>"
   .\.venv\Scripts\python -m flask mobile-revoke-tokens field@example.com
   .\.venv\Scripts\python -m flask mobile-prune-expired-tokens --dry-run
   .\.venv\Scripts\python -m flask mobile-prune-expired-tokens
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
.\.venv\Scripts\python -m flask run --host 0.0.0.0 --port 5000
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
    "farm_roles": [
      {
        "farm_id": "farm-id",
        "farm_name": "Demo Farm",
        "role": "manager",
        "active": true
      }
    ]
  },
  "farms": [
    {
      "id": "farm-id",
      "name": "Demo Farm",
      "timezone": "Africa/Johannesburg",
      "active": true,
      "role": "manager"
    }
  ]
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

### Ping

`GET /api/mobile/v1/ping`

Use this authenticated endpoint for the mobile connection badge. A successful
response means the backend is reachable and the stored bearer token is accepted.

Response:

```json
{
  "status": "ok",
  "server_time": "2026-05-18T10:00:00+00:00",
  "user": {},
  "farms": []
}
```

## Bootstrap

`GET /api/mobile/v1/bootstrap`

Use this after login to discover farm access, animal group types, supported sync
commands, and server time.

Each item in `farms` includes the user's role on that farm as a label for the
mobile farm switcher. Mobile roles are informational in this API version: any
active farm assignment continues to grant the same mobile actions.

Important response fields:

```json
{
  "server_time": "2026-05-18T10:00:00+00:00",
  "user": {},
  "farms": [
    {
      "id": "farm-id",
      "name": "Demo Farm",
      "timezone": "Africa/Johannesburg",
      "active": true,
      "role": "manager"
    }
  ],
  "animal_group_types": [],
  "sync": {
    "supported_command_types": [
      "rainfall.create",
      "mob.create",
      "mob_event.create",
      "paddock_event.create",
      "water_asset_event.create",
      "stock_count.record",
      "mob.move",
      "mob.transfer",
      "task.create",
      "task.status.update",
      "task.comment.create",
      "paddock.update",
      "gate.update",
      "water_asset_status.update"
    ],
    "max_commands_per_request": 100
  },
  "form_options": {
    "species_options": [],
    "sex_options_by_species": {},
    "age_class_options_by_species": {},
    "task_statuses": [],
    "task_priorities": [],
    "water_status_options_by_type": {},
    "water_level_asset_types": [],
    "water_level_options": []
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
  "active_grazing_by_paddock": [],
  "rainfall": [],
  "mob_events": [],
  "paddock_events": [],
  "water_asset_events": [],
  "water_asset_state_history": [],
  "water_assets": [],
  "water_connections": [],
  "gates": [],
  "task_spaces": [],
  "tasks": [],
  "calendar_activities": [],
  "calendar_items": [],
  "decision_feed": [],
  "map_features": [],
  "map_warnings": []
}
```

The snapshot is the Android read model for one selected farm. It represents
current working state: open tasks, upcoming calendar items, current grazing
allocation, current water state, recent rainfall, and decision hints. It also
includes recent structured water asset state history. The migration that adds
history creates one `baseline` row per existing water asset using the state at
upgrade time; the server does not synthesize older state rows.

`active_grazing_by_paddock` groups current grazing allocations for paddock
detail views. Each mob row includes `mob_id`, `mob_name`, `allocation_pct`, and
`start_at`; Android uses `start_at` to show how many days the mob has been
grazing that paddock.

`water_asset_state_history` rows include `water_asset_id`, `change_type`
(`baseline`, `created`, or `updated`), `changed_at`, previous `active` /
`status` / `water_level`, and the current `active` / `status` / `water_level`
snapshot after the change.

`gates` contains active paddock gates with `id`, `paddock_a_id`,
`paddock_b_id`, paddock names, `status` (`open` or `closed`), `source`,
optional coordinates, shared boundary length, and `last_state_changed_at`.
`map_features` also includes gate point features with `feature_type: "gate"`
when coordinates are available.

### Mobile Map Data

`GET /api/mobile/v1/farms/<farm_id>/map-data`

Returns a mobile-authenticated GeoJSON feature collection for a farm. The farm
snapshot also includes a cached `map_features` summary for offline use.

`GET /api/mobile/v1/map-data`

Returns a combined feature collection for all farms assigned to the mobile user.

### Task Photo Attachments

`POST /api/mobile/v1/farms/<farm_id>/tasks/<task_id>/attachments`

Uploads a task photo as multipart form data. Required fields are
`client_attachment_id` and `file`; optional fields are `caption`, `captured_at`,
and `sha256`. Uploads are idempotent for each mobile user and
`client_attachment_id`.

Successful responses include:

```json
{
  "duplicate": false,
  "attachment": {
    "id": "attachment-id",
    "task_id": "task-id",
    "client_attachment_id": "device-photo-id",
    "original_filename": "trough.jpg",
    "content_type": "image/jpeg",
    "byte_size": 12345,
    "sha256": "hex-digest",
    "caption": "North trough",
    "captured_at": "2026-05-18T10:00:00+00:00",
    "created_at": "2026-05-18T10:00:02+00:00"
  }
}
```

Task objects in snapshots include `attachment_count` and a lightweight
`attachments` array.

### Note Photo Attachments

Note images can be attached to mob, paddock, and water asset notes after the
note event exists on the server:

- `POST /api/mobile/v1/farms/<farm_id>/mob-events/<event_id>/attachments`
- `POST /api/mobile/v1/farms/<farm_id>/paddock-events/<event_id>/attachments`
- `POST /api/mobile/v1/farms/<farm_id>/water-asset-events/<event_id>/attachments`

Uploads use the same multipart fields as task photos: required
`client_attachment_id` and `file`; optional `caption`, `captured_at`, and
`sha256`. Uploads are idempotent for each mobile user and
`client_attachment_id`.

Mob, paddock, and water asset event objects in snapshots include
`attachment_count` and a lightweight `attachments` array. Image bytes can be
fetched with:

`GET /api/mobile/v1/farms/<farm_id>/note-attachments/<attachment_id>`

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
- `mob.create`: `name`, optional `origin_note`.
- `mob_event.create`: `mob_id`, `description`, optional `tags`, optional `event_at`.
- `paddock_event.create`: `paddock_id`, `description`, optional `tags`, optional `event_at`.
- `water_asset_event.create`: `water_asset_id`, `description`, optional `tags`, optional `event_at`.
- `stock_count.record`: `mob_id`, `quantity`, optional `note`, plus either `"animal_group_type_id"` for an existing group or `"animal_group_type"` with `species`, `breed`, `sex`, and `age_class` for a new group.
- `mob.move`: `mob_id`, `allocations`, optional `destination_farm_id`, optional `event_time`.
- `mob.transfer`: `source_mob_id`, `destination_mob_id`, `transfers`, optional `note`, optional `event_time`.
- `task.create`: `heading`, `description`, optional `space_id`, optional `due_date`, optional entity link ids.
- `task.status.update`: `task_id`, `status`, optional `note`, optional `changed_at`; `note` is required when `status` is `closed`.
- `task.comment.create`: `task_id`, `body`.
- `paddock.update`: `paddock_id`, optional `status`, optional `notes`, optional `tags`.
- `gate.update`: `gate_id`, `status` (`open` or `closed`), optional `event_time`, and optional `closure_choices` rows shaped as `{ "mob_id": "...", "component_paddock_id": "..." }` when closing a gate that splits a mob across disconnected camp groups.
- `water_asset_status.update`: `water_asset_id`, and at least one of `active`, `status`, `water_level`.

Android should keep these commands in a durable outbox until the backend returns
`"status": "applied"`. Failed commands with terminal validation errors such as
`invalid_command` or `unsupported_command` should be dropped locally because the
payload cannot succeed on retry. Other failed commands remain local with their
last error so the field user can retry after fixing data or connectivity.

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
3. Cache the accessible farm list and show a farm switcher using each
   `name`/`role` pair.
4. Fetch `/farms/<farm_id>/snapshot` for every accessible farm and cache each
   snapshot locally for offline switching. If a farm snapshot fails but
   bootstrap succeeds, keep the login and show cached snapshots that did load.
5. Save field actions to a local offline queue with `client_command_id`.
6. Replay queued actions with `/sync/commands` when connectivity returns and
   on the foreground sync interval, default 5 minutes.
7. Mark commands as applied or failed based on the server result.
8. Refresh cached snapshots for every farm that had applied commands; update
   the visible dashboard immediately when the selected farm was refreshed.

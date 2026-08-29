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
      "fence_event.create",
      "incident.create",
      "stock_count.record",
      "mob.move",
      "mob.transfer",
      "shearer.create",
      "shearing_session.create",
      "shearing_session.update",
      "shearing_entry.record",
      "shearing_entry.delete",
      "shearing_bale_code.upsert",
      "shearing_bale.record",
      "shearing_bale.delete",
      "task.create",
      "task.status.update",
      "task.comment.create",
      "paddock.update",
      "gate.update",
      "water_asset_status.update",
      "fence_section.update"
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
  "incidents": [],
  "water_asset_state_history": [],
  "water_assets": [],
  "water_connections": [],
  "gates": [],
  "shearers": [],
  "shearing_bale_codes": [],
  "shearing_sessions": [],
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
current working state: open tasks, upcoming calendar items, incident notes,
current grazing allocation, current water state, recent rainfall, and decision hints. It also
includes recent structured water asset state history. The migration that adds
history creates one `baseline` row per existing water asset using the state at
upgrade time; the server does not synthesize older state rows.

Each positive mob balance includes `cohort_id`, `reproductive_state`,
`expected_litter_size`, `lactation_state`, and `offspring_at_foot`. Older rows
without cohort data serialize these fields as `null` / `not_recorded`. Android
uses the cohort ID when posting a count so multiple cohorts with the same animal
group type are not combined accidentally.

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

`shearers` contains reusable global shearer records that can work across farms.
`shearing_bale_codes` contains the species-global wool/mohair bale code catalog.
`shearing_sessions` contains sheep/goat shearing sessions with nested daily
`entries`, nested `bales`, total quantity and payout, plus breakdown arrays by
shearer, animal type, date, and bale code. Payouts are calculated from the
session `lootjie_rate`; only animal groups with `sex: "ram"` and `age_class` of
`adult` or `old` use the 2x ram multiplier. Bale revenue stays separate from
shearer payouts. `bale_money_totals.average_price_per_kg` is calculated from
priced bales only, while unpriced bales still count toward `total_kg`.

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
- `fence_event.create`: `fence_section_id`, `description`, optional `event_type`, optional `tags`, optional `condition_after`, optional `materials`, optional `event_at`.
- `incident.create`: `occurred_on`, `category`, `note`, optional `tags`, optional `reported_by`.
- `stock_count.record`: `mob_id`, `quantity`, optional `note`, plus either `"animal_group_type_id"` for an existing group or `"animal_group_type"` with `species`, `breed`, `sex`, and `age_class` for a new group. Existing cohort-aware balances should also send `cohort_id`; omitting it is accepted only when the mob has at most one matching cohort.
- `mob.move`: `mob_id`, `allocations`, optional `destination_farm_id`, optional `event_time`.
- `mob.transfer`: `source_mob_id`, `destination_mob_id`, `transfers`, optional `note`, optional `event_time`.
- `shearer.create`: client-generated `id`, `name`, optional `active`.
- `shearing_session.create`: client-generated `id`, `name`, `species` (`Sheep` or `Goat`), `start_date`, optional `end_date`, `lootjie_rate`, optional `notes`.
- `shearing_session.update`: `session_id`, and at least one editable field: `name`, `species`, `start_date`, `end_date`, `lootjie_rate`, `notes`, or `status`.
- `shearing_entry.record`: `session_id`, `work_date`, `shearer_id`, `quantity`, optional client-generated or existing `id`, optional `note`, plus either `animal_group_type_id` or an `animal_group_type` object with `species`, `breed`, `sex`, and `age_class`. Reusing an existing `id` updates that row, including date, shearer, animal type, count, and note. Quantity `0` removes the matching daily row.
- `shearing_entry.delete`: `session_id` and `entry_id`.
- `shearing_bale_code.upsert`: client-generated `id`, `species` (`Sheep` or `Goat`), `code`, optional `active`, optional `line_type`, `age_group`, `fineness_grade`, `length_code`, `fineness_micron`, `clean_yield_percent`, `style_character`, `consistency`, `color`, `vegetable_matter`, `fault`, `description`, and `notes`.
- `shearing_bale.record`: client-generated `id`, `session_id`, positive `weight_kg`, optional `bale_code_id`, optional ad-hoc `code_text`, optional `bale_number`, optional `price_per_kg`, optional `total_price`, and optional `notes`. If only one price field is supplied, the server calculates the other; both fields may be blank until sale prices are known.
- `shearing_bale.delete`: `session_id` and `bale_id`.
- `task.create`: `heading`, `description`, optional `space_id`, optional `due_date`, optional entity link ids.
- `task.status.update`: `task_id`, `status`, optional `note`, optional `changed_at`; `note` is required when `status` is `closed`.
- `task.comment.create`: `task_id`, `body`.
- `paddock.update`: `paddock_id`, optional `status`, optional `notes`, optional `tags`.
- `gate.update`: `gate_id`, `status` (`open` or `closed`), optional `event_time`, and optional `closure_choices` rows shaped as `{ "mob_id": "...", "component_paddock_id": "..." }` when closing a gate that splits a mob across disconnected camp groups.
- `water_asset_status.update`: `water_asset_id`, and at least one of `active`, `status`, `water_level`.
- `fence_section.update`: `fence_section_id`, and at least one editable fence field such as `condition`, `notes`, `electric_wire`, build fields, or suitability ratings.

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

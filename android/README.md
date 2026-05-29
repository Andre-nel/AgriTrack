# AgriTrack Android Field App

This is a native Kotlin + Jetpack Compose client for the Flask mobile API at
`/api/mobile/v1`. The emulator base URL defaults to `http://10.0.2.2:5000`.

## What It Does

- Logs in with email, password, and device name.
- Stores the bearer token with Android Keystore-backed encryption.
- Fetches `/bootstrap`, stores all accessible farms with role labels, and
  prefetches each accessible farm snapshot for offline switching.
- Stores current-state farm snapshots in a local SQLite read model keyed by farm
  for offline reference.
- Presents a field dashboard with entries for farm state, map features,
  calendar, mobs, paddocks, water assets, rainfall, decision hints, and sync.
- Queues rainfall, mob creation, mob notes, paddock notes, multi-paddock mob
  moves, stock counts, mob transfers, task updates, paddock edits, and water
  asset updates with unique `client_command_id` values.
- Replays queued commands through `/sync/commands`, removes applied results,
  drops terminal validation failures, keeps retryable failures with their last
  error, and refreshes snapshots for farms with applied syncs.
- Attempts foreground auto-sync when connected on the configured interval,
  default 5 minutes, and when connectivity returns.
- Shows pending, applied, and failed sync counts after each sync.

## Local Smoke Test

1. Open the `android/` folder in Android Studio.
2. Sync Gradle and let Android Studio download the Kotlin and Compose tooling.
3. Apply database migrations:

   ```powershell
   $env:FLASK_APP = "manage.py"
   .\.venv\Scripts\python -m flask db upgrade
   ```

4. Create a user and assign farm access:

   ```powershell
   .\.venv\Scripts\python -m flask user-create --email field@example.com --name "Field User" --password "<temporary-password>"
   .\.venv\Scripts\python -m flask user-assign-farm field@example.com "Demo Farm" --role manager
   ```

   Assign additional farms with the same command. The Android switcher shows
   each farm with its role label, for example `Demo Farm - Manager`.

5. Run Flask for emulator access:

   ```powershell
   $env:FLASK_APP = "manage.py"
   .\.venv\Scripts\python -m flask run --host 0.0.0.0 --port 5000
   ```

6. Launch the Android app in an emulator and log in with:

   ```text
   http://10.0.2.2:5000
   ```

7. Confirm the app prefetches accessible farm snapshots, switch farms when more
   than one is assigned, open each home action, submit rainfall, move a mob,
   record a stock count adjustment, then tap **Sync Now**.
8. Confirm the pending count drops after applied results return.

On a physical device on the same Wi-Fi network, start the private LAN server
from the repo root:

   ```powershell
   $env:SECRET_KEY = "<long-private-random-value>"
   .\scripts\lan.ps1
   ```

Then replace the base URL with your PC LAN address, for example:

   ```text
   http://192.168.1.25:5000
   ```

For farm-LAN testing, `http://` is supported. Remote/private-cloud devices
should use HTTPS only. The app now rejects base URLs that include `/api` paths,
query strings, fragments, or unsupported schemes.

## Project Shape

- `MainActivity.kt` hosts the Compose dashboard, entity screens, field forms,
  decision feed, sync status, and foreground auto-sync hooks.
- `data/MobileApiClient.kt` keeps the `/api/mobile/v1` HTTP contract stable.
- `data/SecureTokenStore.kt` stores the bearer token.
- `data/LocalFieldStore.kt` stores farm snapshots as normalized SQLite rows,
  remembers the accessible farm switcher list, and keeps a durable outbox.
- `data/SnapshotCache.kt` and `data/OfflineCommandQueue.kt` remain as
  compatibility helpers for older tests and migration safety.
- `data/MobileRepository.kt` coordinates API calls, token storage, snapshot
  storage, outbox writes, and sync replay.

No Gradle wrapper is checked in yet. Use Android Studio's bundled Gradle or
generate a wrapper once the local Android toolchain version is locked.

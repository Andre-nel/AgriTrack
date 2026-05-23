# AgriTrack Android Field App

This is a native Kotlin + Jetpack Compose client for the Flask mobile API at
`/api/mobile/v1`. The emulator base URL defaults to `http://10.0.2.2:5000`.

## What It Does

- Logs in with email, password, and device name.
- Stores the bearer token with Android Keystore-backed encryption.
- Fetches `/bootstrap` and the first accessible farm snapshot.
- Stores the latest current-state farm snapshot in a local SQLite read model for
  offline reference.
- Presents a field dashboard with entries for farm state, map features,
  calendar, mobs, paddocks, water assets, rainfall, decision hints, and sync.
- Queues rainfall, mob moves, stock counts, mob transfers, task updates,
  paddock edits, and water asset updates with unique `client_command_id` values.
- Replays queued commands through `/sync/commands`, removes applied results,
  drops terminal validation failures, keeps retryable failures with their last
  error, and refreshes the snapshot after applied syncs.
- Attempts foreground auto-sync when connected on the configured interval,
  default 5 minutes, and when connectivity returns.
- Shows pending, applied, and failed sync counts after each sync.

## Local Smoke Test

1. Open the `android/` folder in Android Studio.
2. Sync Gradle and let Android Studio download the Kotlin and Compose tooling.
3. Apply database migrations:

   ```powershell
   $env:FLASK_APP = "manage.py"
   flask db upgrade
   ```

4. Create a mobile user and assign farm access:

   ```powershell
   flask mobile-create-user --email field@example.com --name "Field User" --password "<temporary-password>"
   flask mobile-assign-farm field@example.com "Demo Farm" --role manager
   ```

5. Run Flask for emulator access:

   ```powershell
   $env:FLASK_APP = "manage.py"
   flask run --host 0.0.0.0 --port 5000
   ```

6. Launch the Android app in an emulator and log in with:

   ```text
   http://10.0.2.2:5000
   ```

7. Confirm the app loads a farm snapshot, open each home action, submit rainfall,
   move a mob, record a stock count adjustment, then tap **Sync Now**.
8. Confirm the pending count drops after applied results return.

On a physical device on the same Wi-Fi network, replace the base URL with
   your PC LAN address, for example:

   ```text
   http://192.168.1.25:5000
   ```

Production devices should use HTTPS only.

## Project Shape

- `MainActivity.kt` hosts the Compose dashboard, entity screens, field forms,
  decision feed, sync status, and foreground auto-sync hooks.
- `data/MobileApiClient.kt` keeps the `/api/mobile/v1` HTTP contract stable.
- `data/SecureTokenStore.kt` stores the bearer token.
- `data/LocalFieldStore.kt` stores the current snapshot as normalized SQLite
  rows plus a durable outbox.
- `data/SnapshotCache.kt` and `data/OfflineCommandQueue.kt` remain as
  compatibility helpers for older tests and migration safety.
- `data/MobileRepository.kt` coordinates API calls, token storage, snapshot
  storage, outbox writes, and sync replay.

No Gradle wrapper is checked in yet. Use Android Studio's bundled Gradle or
generate a wrapper once the local Android toolchain version is locked.

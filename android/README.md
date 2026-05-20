# AgriTrack Android MVP

This is a small native Android client scaffold for the Flask mobile API at
`/api/mobile/v1`.

## What It Does

- Logs in with email, password, and device name.
- Stores the bearer token with Android Keystore-backed encryption.
- Fetches `/bootstrap` and the first accessible farm snapshot.
- Queues a sample offline rainfall command with a unique `client_command_id`.
- Replays queued commands through `/sync/commands` and removes applied results.

## Run Locally

1. Open the `android/` folder in Android Studio.
2. Make sure the Flask app is running:

   ```powershell
   $env:FLASK_APP = "manage.py"
   flask run --host 0.0.0.0 --port 5000
   ```

3. In the emulator, use the default base URL:

   ```text
   http://10.0.2.2:5000
   ```

4. On a physical device on the same Wi-Fi network, replace the base URL with
   your PC LAN address, for example:

   ```text
   http://192.168.1.25:5000
   ```

Production devices should use HTTPS only.

## Current Limitations

- The UI is intentionally minimal and field-ops focused.
- It queues a sample rainfall command to prove offline sync plumbing; richer
  screens for mob events, stock counts, moves, and water status can reuse the
  same queue.
- No Gradle wrapper is checked in yet. Use Android Studio's bundled Gradle or
  generate a wrapper once the Android toolchain version is locked.

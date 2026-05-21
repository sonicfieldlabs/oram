# ORAM macOS App

The macOS app lives in `apps/macos` and is a SwiftUI shell around the local
Python ORAM daemon.

## Development Build

```bash
apps/macos/script/build_and_run.sh --no-open
```

The script builds the SwiftPM executable and stages:

```text
apps/macos/dist/ORAM.app
```

Use the Codex Run action or omit `--no-open` to launch the app.

## Runtime Model

The app:

- reads `~/Library/Application Support/ORAM/oram-daemon.json`
- launches the bundled `uv` helper, or the developer `uv` on PATH, when no
  daemon is reachable
- uses the bundled `Contents/Resources/oram-python` project when launched from
  the repository DMG
- stores provider keys directly in Keychain
- calls the daemon over localhost
- never displays a stored provider key by default
- bundles the ORAM logo as an app resource and dashboard header image

## App Sections

- Welcome
- Provider setup
- Engine status
- Recorder
- Summoner
- Listening
- Library
- Settings
- Security

## Provider Setup

The Provider setup sheet supports ElevenLabs and Stability AI. Keys are written
directly to macOS Keychain under `wtf.momoto.oram` and the daemon refreshes its
engine registry after Keychain changes, so Stable Audio becomes selectable after
the Stability key is saved.

## DAW Movement

The library browser exposes generated sound files as draggable file URLs. DAWs
that accept Finder-style file drags can import ORAM-generated WAV files directly
from the library list.

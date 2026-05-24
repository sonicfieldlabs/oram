# ORAM

ORAM is an alpha local-first sound workstation for recording, looping, sampling,
listening, and generating sound material.

It started as a speech-operated terminal looper for synthetic sound studies. The
current app has several surfaces around the same core idea:

- Python CLI/TUI instrument
- localhost dashboard and daemon
- native macOS SwiftUI app
- early JUCE DAW plugin target for Audio Unit, VST3, and Standalone builds

ORAM is not a DAW replacement, not a chatbot, and not a hosted prompt-to-song
service. It is a small instrument where local audio, structured commands, and
optional BYOK providers become constrained audio actions.

```text
recorder -> looper -> sampler -> engine router -> local archive
```

## Alpha Status

ORAM is in alpha. The CLI/TUI, dashboard, daemon, and macOS app are working
development surfaces. The AU/VST3 plugin now builds, installs, validates with
Apple's `auval` on macOS, and has a native realtime layer core, but it still
needs DAW-by-DAW validation before it should be treated as a reliable production
plugin.

Expect API changes, rough edges, unsigned builds, and platform-specific audio
behavior while the app stabilizes.

Current known alpha gaps:

- AU validation passes locally with `auval`, but Logic, Ableton, Reaper, and
  other host scans still need explicit smoke tests.
- VST3 build generation works, including JUCE module-info generation, but DAW
  host smoke tests are still required.
- The plugin imports generated audio, serializes native layer state, and applies
  a useful subset of ORAM structured actions; full DSP parity with the Python
  engine is not complete.
- Granular effects, true independent pitch/time-stretch, audio-to-audio
  generation, and listening workflows remain app/core-first rather than fully
  native in the plugin.
- Release plugin packages are ad-hoc signed by default; Developer ID signing
  and notarization require local Apple credentials.

## Current Formats

| Format | Path | State |
| --- | --- | --- |
| Python CLI/TUI | `src/oram` | Working development instrument |
| Browser dashboard | `oram dashboard` | Working local test/control surface |
| Local daemon | `oram daemon` | Working localhost API for app/plugin control |
| macOS app | `apps/macos` | Working SwiftUI shell around the Python daemon |
| macOS DMG | `releases/macos/ORAM.dmg` | Unsigned development package |
| Audio Unit | `plugins/oram-plugin` | Alpha build; installs and passes `auval` |
| VST3 | `plugins/oram-plugin` | Alpha build with JUCE module-info generation |
| Standalone plugin app | `plugins/oram-plugin` | Alpha debug/release build |

## What It Does

- Records host or microphone audio into four layers.
- Loops, overdubs, mutes, solos, clears, pans, and volumes layers.
- Parses typed or spoken commands into structured ORAM actions.
- Applies offline DSP such as reverse, pitch, filter, reverb, granular, trim,
  fades, and spatial transforms.
- Generates local mock sound by default.
- Optionally routes generation to BYOK providers such as ElevenLabs and
  Stability AI.
- Writes generated sounds into `~/Music/ORAM Library`.
- Archives sessions with mix/stem WAVs, command logs, metadata, waveform text,
  and listening reports.

## Local-First Model

The macOS app About view describes ORAM as a "Local-first BYOK sound
workstation." That is the intended boundary:

- No ORAM cloud account is required.
- The app talks to a localhost daemon.
- Provider keys are stored in macOS Keychain for the packaged app.
- Local Mock remains available without cloud credentials.
- Telemetry is off by default.
- Generated sounds and archives stay in the local ORAM Library.
- Daemon mutation routes use a local bearer token when auth is enabled.

## Install

Requires Python 3.11+.

```bash
cd oram
pip install -e ".[dev]"
```

For Whisper STT support:

```bash
pip install -e ".[dev,stt]"
```

For the optional dashboard and daemon:

```bash
pip install -e ".[dev,web]"
```

The development workflow also supports `uv`:

```bash
uv sync --extra dev --extra web
uv run pytest
```

## Run The Python Instrument

```bash
oram                              # start with defaults
oram run                          # equivalent explicit subcommand
oram --list-devices               # show audio devices
oram --mock-audio                 # run without audio hardware
oram --input-device 2             # select input device
oram --session-name grey_chapel   # name this session
oram --session-dir ./sessions     # choose archive directory
oram --no-stt                     # keyboard only, no voice
oram dashboard --mock-audio       # browser dashboard on localhost
oram daemon --mock-audio          # local app/plugin daemon on 127.0.0.1
oram export ./oram_sessions/oram_0001
```

## Keyboard Controls

```text
space     push-to-talk
r         record selected layer
o         overdub selected layer
1-4       select layer
m         mute selected layer
M         solo selected layer
x         clear selected layer (repeat to confirm)
s         save session
e         export mix
l         generate listening report
k         hard-silence audio
i         toggle prompt/audio input mode
tab       cycle mode
q         quit
```

## macOS App

The native app in `apps/macos` is a SwiftUI shell around the local Python ORAM
daemon. It launches or discovers `oram daemon`, stores provider keys in macOS
Keychain, controls recording/generation/listening/library workflows, and writes
generated sounds to the ORAM Library.

Build and run locally:

```bash
apps/macos/script/build_and_run.sh --no-open
open apps/macos/dist/ORAM.app
```

Refresh the unsigned development DMG:

```bash
apps/macos/script/package_unsigned.sh
```

Repository development package:

```text
releases/macos/ORAM.dmg
```

## DAW Plugin

The plugin lives in `plugins/oram-plugin`. It is a JUCE CMake project with a
native realtime layer engine. The plugin uses the ORAM daemon only for
control-plane work: parsing commands, generation, provider discovery, and
library access.

Supported build targets:

- Audio Unit: `ORAM.component`
- VST3: `ORAM.vst3`
- Standalone debug app: `ORAM.app`

Current plugin features:

- mono-in/mono-out and stereo-in/stereo-out effect layouts
- four native layers with record, overdub, mute, solo, clear, volume, and pan
- loop regions and host-input monitoring
- typed ORAM command parsing through the daemon
- generation through Local Mock, ElevenLabs, or Stability routing, then WAV
  import into native plugin layers
- native state serialization for parameters and layer audio
- basic plugin-side DSP actions including reverse, speed/pitch-ratio, filters,
  reverb/spatial-far, fades, and trim

Build:

```bash
plugins/oram-plugin/scripts/build.sh
```

Install debug AU/VST3 into the current user's macOS plugin folders:

```bash
plugins/oram-plugin/scripts/install_debug_macos.sh
```

Validate the installed debug plugin on macOS:

```bash
plugins/oram-plugin/scripts/validate_macos.sh
```

Create ad-hoc signed release zips and checksums:

```bash
plugins/oram-plugin/scripts/package_macos.sh
```

Set `ORAM_PLUGIN_SIGN_IDENTITY` and `ORAM_PLUGIN_NOTARY_PROFILE` to use
Developer ID signing and Apple notarization.

The plugin discovers the daemon through:

```text
~/Library/Application Support/ORAM/oram-daemon.json
```

Plugin architecture notes: [docs/architecture/daw-plugin-architecture.md](docs/architecture/daw-plugin-architecture.md).

## BYOK Provider Setup

Packaged app usage stores keys in Keychain:

```text
service: wtf.momoto.oram
account: provider:elevenlabs
account: provider:stability
```

Terminal setup:

```bash
oram credentials set elevenlabs
oram credentials set stability
oram credentials status
oram credentials test elevenlabs
oram credentials test stability
```

Mock mode remains the default. ElevenLabs and Stability AI are optional. See:

- [docs/providers/elevenlabs.md](docs/providers/elevenlabs.md)
- [docs/providers/stability.md](docs/providers/stability.md)

## Configuration

Environment variables:

```text
ORAM_SAMPLE_RATE=48000
ORAM_BLOCK_SIZE=512
ORAM_INPUT_DEVICE=
ORAM_OUTPUT_DEVICE=
ORAM_SESSION_DIR=./oram_sessions
ORAM_STT_BACKEND=mock
ORAM_GENERATOR_BACKEND=mock
ORAM_LLM_BACKEND=none
ORAM_AUTO_LISTEN=false
ORAM_DEFAULT_LISTENING_ROUTE=hybrid
ORAM_DEFAULT_ENGINE=auto
ORAM_DASHBOARD_TOKEN=
ELEVENLABS_API_KEY=
STABILITY_API_KEY=
```

Precedence: CLI args > env vars > config file > defaults.

Safe defaults: `generator_backend=mock`, `llm_backend=none`,
`auto_listen=false`. Cloud credentials are never required for the core looper.

See [.env.example](.env.example) for a complete template.

## Security

ORAM is local-first: no Momoto server is required, no telemetry is enabled by
default, and provider credentials are never exposed in local state responses.

The dashboard and daemon bind to `127.0.0.1` by default.

To expose the dashboard on the local network:

```bash
export ORAM_DASHBOARD_TOKEN=<local-token>
oram dashboard --allow-lan --mock-audio
```

- Token authentication: when `ORAM_DASHBOARD_TOKEN` is set, all POST endpoints
  require `Authorization: Bearer <token>`.
- WebSocket auth: when a token is set, WebSocket connections must include
  `?token=<token>` in the URL.
- Origin checking: WebSocket connections from non-localhost origins are rejected
  unless `--allow-lan` is used.
- No secrets in state: `/api/state` and daemon `/state` do not expose provider
  keys.

Run:

```bash
oram doctor --privacy
```

Privacy notes: [docs/security/local-first-privacy.md](docs/security/local-first-privacy.md).

## Architecture

Core architecture:

- [architecture.md](architecture.md)
- [docs/architecture/local-engine-boundary.md](docs/architecture/local-engine-boundary.md)
- [docs/architecture/daw-plugin-architecture.md](docs/architecture/daw-plugin-architecture.md)

Command grammar:

- [commands.md](commands.md)

Session archive format:

- [session_format.md](session_format.md)

## Development Checks

```bash
uv run ruff check src/oram_daemon/server.py tests/test_daemon_api.py
uv run pytest
plugins/oram-plugin/scripts/build.sh
plugins/oram-plugin/scripts/validate_macos.sh
plugins/oram-plugin/scripts/package_macos.sh
```

The current debug plugin artifacts are produced under:

```text
plugins/oram-plugin/build/ORAMPlugin_artefacts/Debug/
```

## Manual Smoke Test

1. `oram --list-devices`
2. `oram --mock-audio`
3. `oram` with a microphone
4. Press `r`, make sound, stop recording
5. Confirm loop playback
6. Press `2`, record a second layer
7. Mute layer 1
8. Reverse layer 2
9. Generate a mock bed
10. Export session
11. Open exported WAV
12. Inspect `session.json`, `commands.log`, and `listening_report.md`

## Troubleshooting

No audio devices found: check system audio permissions. On macOS, grant terminal
or ORAM access in System Settings > Privacy & Security > Microphone.

Audio dropouts: try increasing block size with `--block-size 1024`.

STT not working: run with `--no-stt` for keyboard-only mode. Check that Whisper
is installed with `pip install -e ".[stt]"`.

Plugin does not see the daemon: start `oram daemon --mock-audio` and confirm
`~/Library/Application Support/ORAM/oram-daemon.json` exists.

Audio Unit discovery issues: run `plugins/oram-plugin/scripts/validate_macos.sh`,
restart the DAW or AudioComponent registrar, then retry host/plugin scanning.

Import errors: ensure you installed from the project root.

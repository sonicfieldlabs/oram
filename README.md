# ORAM

> "We seem to have tuned circuits within us"
> — Daphne Oram, *An Individual Note of Music, Sound and Electronics*

ORAM is a local-first sound workstation for recording, looping, sampling,
listening, and generating sound material. It is built from the idea that sound
is never at rest.

Every recorded layer becomes a flowing circuit: a small river of frequencies,
tensions, residues and resonances that can be listened to, transformed, summoned
and returned. The user does not simply control the system. They tune with it.

```text
record → resonate → listen → transform → generate → return
```

Named after **Daphne Oram** — pioneer of electronic music, inventor of the
Oramics machine, and composer who built instruments to hear what did not yet
exist.

## What ORAM Is

ORAM started as a speech-operated terminal looper for synthetic sound studies.
The current app has several surfaces around the same core idea:

- Python CLI/TUI instrument
- localhost dashboard and daemon
- native macOS SwiftUI app
- early JUCE DAW plugin target for Audio Unit, VST3, and Standalone builds

ORAM is not a DAW replacement, not a chatbot, and not a hosted prompt-to-song
service. It is a small instrument where local audio, structured commands, and
optional BYOK providers become constrained audio actions.

Not stable objects, but flowing resonant states. Not notes, but unstable
entities. Not fixed frequencies, but living circuits of relation.

```text
layers    = circuits
loops     = resonant fields
prompts   = tuning gestures
generation = summoning
analysis  = listening back
effects   = tension-shaping
archive   = trace of a state
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

## Core Flow

The performer records sound. The sound loops. The performer speaks or types.
The agent listens to the speech, parses intent, and translates it into a
constrained audio action. The sound changes. The performer listens again. The
process repeats as a cycle of recording, transformation, and listening.

ORAM does not only generate sound. It listens for states of tension. Every loop
is treated as a resonant body: unstable, mutable, recursive, alive. The user is
not outside the circuit. The listener, the microphone, the machine, the model,
and the sound form one tuned system.

```text
recorder → looper → sampler → engine router → local archive
```

## What It Does

- Records host or microphone audio into layered circuits (up to four layers).
- Loops, overdubs, mutes, solos, clears, pans, and volumes layers.
- Parses typed or spoken commands into structured ORAM actions.
- Applies offline DSP tension-shaping: reverse, pitch, speed, filter, reverb,
  chorus, delay, flanger, phaser, distortion, bitcrush, stutter, granular,
  normalize, trim, fades, and spatial transforms.
- Listens back — spectral analysis of pitch, BPM, key, harmonics, and frequency
  character through local FFT, LLM-based interpretation, or hybrid routes.
- Generates sound through Local SA3 by default when the local service is
  available, or summons textures through BYOK providers (ElevenLabs, Stability
  AI, fal Stable Audio).
- Writes generated sounds into `~/Music/ORAM Library`.
- Archives sessions as traces of a state: mix/stem WAVs, command logs, metadata,
  waveform text, and listening reports.

### DSP Effects (Tension-Shaping)

| Effect | Description |
| --- | --- |
| `reverse` | full buffer reverse |
| `pitch` | pitch shifting |
| `speed` | time-stretch / speed ratio |
| `filter` | lowpass, highpass, bandpass with frequency and Q |
| `reverb` | convolution / algorithmic reverb |
| `chorus` | chorus modulation |
| `delay` | delay with feedback |
| `flanger` | flanger |
| `phaser` | phaser |
| `distortion` | distortion / saturation |
| `bitcrush` | bit reduction / sample rate reduction |
| `stutter` | stutter / glitch buffer repeat |
| `granular` | granular synthesis / processing |
| `normalize` | loudness normalization |
| `trim` | silence trimming, region selection |
| `fade` | fade in / out curves |
| `spatial` | spatial positioning: near, far, wide |

### Listening (Analysis)

ORAM treats listening as an active circuit, not passive reception. The body is
also a resonator, a filter, a modulation system — a circuit that becomes
activated by external vibrations.

Three listening routes:

| Route | Engine | Description |
| --- | --- | --- |
| `spectral` | local FFT | frequency content, waveform statistics, amplitude dynamics |
| `llm` | BYOK provider | spectral data sent to an LLM for natural language interpretation |
| `hybrid` | both | combines spectral and LLM analysis into a unified listening report |

Listening produces `listening_report.md` files with layer-by-layer analysis,
frequency content, amplitude/dynamics statistics, waveform visualization, and
optional natural language interpretation.

### Generation (Summoning)

Generation is always non-vocal — ORAM produces sound textures, effects, and
music, never speech. Available engines:

| Engine | Type | Description |
| --- | --- | --- |
| `mock` | local | test tones, noise, or silence for development |
| `elevenlabs` | BYOK | sound generation via ElevenLabs API |
| `stability` | BYOK | audio generation via Stability AI API |
| `fal` | BYOK | Stable Audio generation via fal |

Prompt types: `generate [prompt]`, `generate bed [prompt]`,
`generate texture [prompt]`, `generate hit [prompt]`, `regenerate`.

### Session Archive (Trace of a State)

Each session archives as a complete trace:

```text
session_name/
├── session.json          # metadata, timestamps, engine config
├── commands.log          # chronological command log
├── mix.wav               # stereo mixdown
├── stems/
│   ├── layer_1.wav
│   ├── layer_2.wav
│   ├── layer_3.wav
│   └── layer_4.wav
├── generated/            # generated audio files
├── listening_report.md   # analysis report
└── waveform.txt          # ASCII waveform visualization
```

## Command Grammar

All interaction flows through structured commands — tuning gestures that resolve
to bounded audio actions. Poetic language is accepted as input but must resolve
to validated operations.

### Recording and Transport

```text
record [layer N]          # capture into layer
overdub [layer N]         # layer new audio over existing
stop                      # stop recording / transport
play / pause              # transport controls
arm / disarm              # arm layer for recording
```

### Layer Control

```text
mute layer N              # mute a circuit
solo layer N              # isolate a circuit
clear layer N             # erase layer audio
set layer N volume 0.8    # set amplitude
set layer N pan -0.5      # set stereo position
```

### Effects (Tension-Shaping)

```text
reverse layer N           # reverse audio buffer
set speed N ratio         # time-stretch / speed
filter layer N lowpass 800 # filter with frequency
reverb layer N            # reverb wash
chorus / delay / flanger / phaser
distortion / bitcrush     # saturation / reduction
stutter / granular        # glitch / granular synthesis
trim / fade in / fade out
normalize
spatial:far / spatial:near / spatial:wide
```

### Generation (Summoning)

```text
generate [prompt]         # summon a texture
generate bed [prompt]     # summon a bed layer
generate texture [prompt] # summon a texture
generate hit [prompt]     # summon a percussive hit
regenerate                # re-summon with last prompt
```

### Listening (Listening Back)

```text
listen                    # analyze current state
listen layer N            # listen to a specific circuit
listen mix                # listen to the full mix
describe                  # describe what's sounding
listen --route hybrid|spectral|llm
```

### Session and System

```text
save / export / archive   # persist the trace
name [session_name]       # name the session
load session [name]       # recall a previous state
credentials set|status|test [provider]
mode [mode_name] / status / help / quit
```

## Local-First Model

The macOS app About view describes ORAM as a "Local-first BYOK sound
workstation." That is the intended boundary:

- No ORAM cloud account is required.
- The app talks to a localhost daemon.
- Provider keys are stored in macOS Keychain for the packaged app.
- Local SA3 is the default local generation path; Local Mock remains available
  only as an explicit fallback.
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

## Dashboard Controls

The browser dashboard (click the `oram` title to open the About modal) provides
a visual control surface with:

```text
⏺  record from mic into selected layer
⊕  overdub onto selected layer
fx open DSP transforms (reverse, granulate, reverb…)
✦  summon — listen to what's sounding and generate a new layer
◉  export mix
⊘  hard-silence capture/layers/pending output
+  add layer
```

Layer corners: top-left select/mute (right-click: solo), top-right export,
bottom-left generate, bottom-right clear. Waveform drag sets loop region.
Volume strip supports drag, scroll, double-click for unity.

Keyboard shortcuts in the dashboard:

```text
1-4       select layer
r         record
o         overdub
k         kill
g         generate
l         listen
m         mute
u         unmute all
⌘K        command palette
/         focus prompt
esc       close
```

## macOS App

The native app in `apps/macos` is a SwiftUI shell around the local Python ORAM
daemon. It launches or discovers `oram daemon`, stores provider keys in macOS
Keychain, controls recording/generation/listening/library workflows, and writes
generated sounds to the ORAM Library.

Views: Record, Generate, Listen, Library, Settings, About.

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
- generation through Local SA3, ElevenLabs, or Stability routing, then WAV
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

```text
record → resonate → listen → transform → generate → return
```

Core architecture:

- [architecture.md](architecture.md)
- [docs/architecture/local-engine-boundary.md](docs/architecture/local-engine-boundary.md)
- [docs/architecture/daw-plugin-architecture.md](docs/architecture/daw-plugin-architecture.md)

Command grammar:

- [commands.md](commands.md)

Session archive format:

- [session_format.md](session_format.md)

Design principles:

- [concept.md](concept.md)

## Design Principles

**Constrained vocabulary**: commands map to bounded actions. Poetic language is
accepted as input but must resolve to structured, validated operations.

**Offline transforms**: expensive DSP happens outside the realtime audio
callback. The callback only does bounded, predictable work.

**Graceful degradation**: if STT fails, keyboard controls remain. If the LLM
is unavailable, the deterministic parser handles commands. If generation fails,
existing loops continue.

**Minimal identity**: the interface is monochrome-first, lowercase, austere.
The complexity lives inside the loop, not in the chrome.

**Sound as material**: ORAM treats sound not as notation or signal, but as
a changing field of tensions. Not stable objects, but flowing resonant states.
Not notes, but unstable entities. Not fixed frequencies, but living circuits
of relation.

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

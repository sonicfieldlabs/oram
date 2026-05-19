# oram

a speech-operated terminal looper for synthetic sound studies.

oram is not a DAW, not a chatbot, and not a prompt-to-song app. it is a minimal
instrument where you record sound, loop it, give spoken commands, and an agent
translates those commands into constrained audio actions.

```
a terminal instrument that listens before it plays.
```

## install

requires python 3.11+.

```bash
cd oram
pip install -e ".[dev]"
```

for whisper STT support:

```bash
pip install -e ".[dev,stt]"
```

for the optional dashboard:

```bash
pip install -e ".[dev,web]"
```

## run

```bash
oram                              # start with defaults
oram run                          # equivalent explicit subcommand
oram --list-devices               # show audio devices
oram --mock-audio                 # run without audio hardware
oram --input-device 2             # select input device
oram --session-name grey_chapel   # name this session
oram --session-dir ./sessions     # choose archive directory
oram --no-stt                     # keyboard only, no voice
oram dashboard --mock-audio       # browser dashboard (localhost only)
oram dashboard --allow-lan        # expose on LAN (requires token)
oram export ./oram_sessions/oram_0001
```

## keyboard controls

```
space     push-to-talk (toggle: start/stop command capture)
r         record selected layer
o         overdub selected layer
1-4       select layer
m         mute selected layer
M         solo selected layer
x         clear selected layer (repeat to confirm)
s         save session
e         export mix
l         generate listening report
i         toggle prompt/audio input mode
tab       cycle mode
q         quit
```

## core loop

```
audio input
-> record
-> loop
-> listen
-> voice command / STT
-> intent parser
-> structured action
-> DSP / SFX / generated texture
-> mixer
-> audio output
-> re-listening
-> archive
```

## architecture

see [architecture.md](architecture.md) for module responsibilities and the
threading model.

## commands

see [commands.md](commands.md) for the full command grammar.

## session archive

each performance creates a session folder with mix, stems, metadata, command
log, text waveform, and listening report. see [session_format.md](session_format.md).

## configuration

environment variables:

```
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
```

precedence: cli args > env vars > config file > defaults.

safe defaults: `generator_backend=mock`, `llm_backend=none`, `auto_listen=false`.
cloud credentials are never required for the core looper.

see `.env.example` for a complete template.

## security

the dashboard binds to `127.0.0.1` by default (localhost only).

to expose on the local network:

```bash
# set a token first
export ORAM_DASHBOARD_TOKEN=my-secret-token

# then allow LAN access
oram dashboard --allow-lan --mock-audio
```

- **token authentication**: when `ORAM_DASHBOARD_TOKEN` is set, all POST
  endpoints require `Authorization: Bearer <token>`. GET endpoints (state
  polling) remain open.
- **WebSocket auth**: when a token is set, WebSocket connections must include
  `?token=<token>` in the URL.
- **origin checking**: WebSocket connections from non-localhost origins are
  rejected unless `--allow-lan` is used.
- **no secrets in state**: `/api/state` never exposes API keys or tokens.

## manual smoke test

1. `oram --list-devices`
2. `oram --mock-audio`
3. `oram` with a microphone
4. press `r`, make sound, stop recording
5. confirm loop playback
6. press `2`, record a second layer
7. mute layer 1
8. reverse layer 2
9. generate a mock bed
10. export session
11. open exported WAV
12. inspect session.json, commands.log, and listening_report.md

## dashboard

the optional dashboard is a compact browser control surface for local testing:

```bash
oram dashboard --mock-audio
```

it exposes the same command parser as the terminal app. the terminal instrument
remains the primary interface.

**security**: the dashboard binds to localhost only. use `--allow-lan` with
`ORAM_DASHBOARD_TOKEN` to expose on LAN.

## troubleshooting

**no audio devices found**: check that your system audio permissions allow
terminal access to microphone. on macOS, grant terminal access in System
Settings > Privacy & Security > Microphone.

**audio dropouts**: try increasing block size with `--block-size 1024`.

**STT not working**: run with `--no-stt` for keyboard-only mode. check that
whisper is installed with `pip install -e ".[stt]"`.

**import errors**: ensure you installed with `pip install -e ".[dev]"` from the
project root.

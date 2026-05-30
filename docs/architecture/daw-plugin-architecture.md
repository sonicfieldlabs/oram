# ORAM DAW Plugin Architecture

## Decision

ORAM DAW plugins will use a native realtime audio core inside the host process
and will use the existing local Python daemon only for control-plane work.

The first plugin target is a JUCE CMake project that builds:

- Audio Unit on macOS
- VST3 on macOS and other desktop platforms
- Standalone app for debugging

JUCE is a separate dependency boundary because JUCE modules are AGPLv3 or
commercially licensed. ORAM's Python package remains MIT licensed.

## Why

A DAW host calls plugin audio code from a realtime render callback. That callback
must never do network I/O, launch Python, call provider APIs, allocate large
buffers, write files, or wait on locks. ORAM already follows this rule in the
Python engine, and the plugin keeps the same split:

```text
host render callback
  -> native layer recorder/looper/mixer
  -> lock-free/control-queue buffer swaps

plugin editor/control thread
  -> localhost ORAM daemon
  -> parser, provider generation, listening, library, credentials
```

## Ownership

The plugin owns all audible realtime layer state while it is loaded in a DAW:

- host input capture
- four ORAM layers
- playheads
- mute/solo/volume/pan
- loop regions
- generated/imported audio buffers
- plugin state serialization

The daemon owns slow and shared ORAM services:

- command parsing into structured ORAM actions
- provider discovery and credentials
- text-to-audio and audio-to-audio generation
- listening/analysis
- generated sound library
- redaction and local auth

The daemon must not mutate plugin audio state directly. It returns actions,
files, metadata, and status. The plugin applies compatible actions to its own
native state.

## Plugin-Safe Daemon Routes

These routes are for DAW plugins and other clients that own their own audio
state:

```text
POST /actions/parse
POST /plugin/generate
POST /plugin/stable-audio/render
```

`POST /actions/parse` returns the structured action for a command without
routing it through `ActionRouter`.

`POST /plugin/generate` calls the configured engine and stores the resulting WAV
in the ORAM Library without assigning it to daemon layers. The response contains
the sound record, including a local file path the plugin can load on its control
thread and swap into native playback between render blocks.

`POST /plugin/stable-audio/render` is the SA3-specific companion route for
agentic looper workflows. It accepts ORAM mode controls such as `mode`,
`source_layer` or `init_audio_path`, `duration`, `seed`, `noise_depth`,
`inpaint_start`, `inpaint_end`, `variation_count`, and LoRA stack fields. The
plugin/Max/standalone client remains responsible for loading the returned WAV
and swapping it into its own realtime layer state.

## First Plugin Slice

The initial plugin is an effect-style looper:

- mono input to mono output and stereo input to stereo output
- record host input into the selected layer
- overdub into a layer
- play active layers mixed with the host input according to a dry/wet control
- mute, solo, volume, pan
- set loop regions
- generate via daemon and import returned WAV
- render Stable Audio 3 clips through daemon and import returned WAV
- parse text commands via daemon and apply supported actions locally
- serialize parameters and native layer audio through host plugin state

Later slices can add MIDI trigger/sampler behavior, host tempo sync, AUv3, and
offline render support.

## Non-Goals For The First Slice

- Embedding CPython in the plugin
- Running provider generation from the audio callback
- Making the DAW plugin a host for other plugins
- Sharing live daemon layer state with the plugin
- Replacing the existing CLI, TUI, dashboard, or macOS app
- Full native parity for granular effects, independent pitch/time-stretch,
  listening workflows, or audio-to-audio generation

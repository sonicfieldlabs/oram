# ORAM Plugin

Native DAW plugin target for ORAM. This is alpha software: it builds, installs,
and passes local macOS `auval` validation, but still needs host-by-host DAW
smoke testing before release use.

This target keeps realtime audio inside the plugin process and uses the local
ORAM daemon only for control-plane work such as command parsing, generation,
provider discovery, and library storage.

## Build

Install JUCE through CMake, or place a JUCE checkout at:

```text
plugins/oram-plugin/JUCE
```

Then configure and build:

```bash
plugins/oram-plugin/scripts/build.sh
```

On macOS this builds Audio Unit, VST3, and Standalone formats.

To install the debug AU/VST3 into the current user's plugin folders:

```bash
plugins/oram-plugin/scripts/install_debug_macos.sh
```

To validate the installed debug plugin:

```bash
plugins/oram-plugin/scripts/validate_macos.sh
```

To create release zips and checksums:

```bash
plugins/oram-plugin/scripts/package_macos.sh
```

Release packages are ad-hoc signed by default. Set `ORAM_PLUGIN_SIGN_IDENTITY`
to a Developer ID Application identity and `ORAM_PLUGIN_NOTARY_PROFILE` to a
notarytool keychain profile when making distributable builds.

## Current Features

- Audio Unit, VST3, and Standalone targets.
- Mono and stereo effect layouts.
- Four native realtime layers with record, overdub, mute, solo, clear, volume,
  pan, and loop regions.
- Host-input monitoring and loop-level controls.
- Command parsing through the local daemon, applied to plugin-owned state.
- Generation through Local SA3, ElevenLabs, or Stability routing, with returned
  WAV files imported into native layers.
- State serialization for parameters and layer audio.
- Basic plugin-side DSP actions: reverse, speed/pitch-ratio, lowpass/highpass,
  reverb/spatial-far, fade in/out, and trim.

## Runtime Boundary

- The audio callback records host input, advances layer playheads, and mixes
  native layer buffers only.
- The editor/control thread may call the daemon over localhost.
- Generated audio is returned as an ORAM Library WAV path and imported into a
  native plugin layer outside the render callback.
- The plugin does not share live daemon layer state.
- The plugin owns its DAW session state and serializes it through the host.

## Local Daemon

Run the daemon from the repository while developing:

```bash
uv run --extra web oram daemon --host 127.0.0.1 --port auto
```

The plugin discovers it from:

```text
~/Library/Application Support/ORAM/oram-daemon.json
```

## Remaining Alpha Work

- DAW smoke tests across Logic, Ableton, Reaper, and other hosts.
- VST3 validator/host validation beyond JUCE module-info generation.
- Native parity for granular effects, true independent pitch/time-stretch,
  listening workflows, and audio-to-audio generation.
- Developer ID signing and notarized distribution packages.

# Changelog

## v0.4.1

- Published the post-0.4 local API and storage hardening already carried by
  `main`, including stricter file, control, LoRA, strain, render, and daemon
  request boundaries.
- Included dashboard security repairs, bounded metadata handling, clean public
  packaging, deterministic CI dependency setup, and repository ownership.
- Refreshed the documented Sonic Field Labs stack boundary to the current
  AKOÚŌ, Earworm, Oída, GERM, Akousmata, and Algophony releases.

## v0.4.0

A wide audit-and-repair pass across every surface: a reworked audio engine, a
much larger and higher-quality effect set, and fixes throughout the CLI, web
dashboard, daemon, macOS app, and AU/VST3 plugin.

### Audio engine

- Playheads now accumulate **fractionally** — non-integer speeds no longer
  truncate sub-sample position every block, removing pitch drift and block-rate
  zipper on speed/pitch playback.
- Region playback interpolates with **4-point Catmull-Rom** instead of linear.
- The master limiter **ramps its gain across the block** instead of stepping it,
  so it no longer clicks at block boundaries.
- Overdub mixing is vectorized and only rescales when it would actually clip, so
  repeated overdubs stop ducking the existing material.
- Mix export renders through layer **proxies with private playheads** — bouncing
  during a live performance no longer rewinds the layers you are hearing. Exports
  are 24-bit PCM.

### Effects — new and reworked

New effects, wired end to end (schema → grammar → router → DSP → CLI/dashboard):
**delay** (damped feedback, ping-pong, loop-aware tail), **chorus**, **flanger**,
**phaser**, **distortion** (oversampled soft/warm/fuzz), **bitcrush**, **stutter**,
**normalize** (peak/RMS), **bandpass**, and **spatial near / wide**.

Quality upgrades to existing effects:

- **pitch** now preserves duration by default (phase-vocoder time-stretch +
  anti-aliased resample), accurate to under a cent — pitched loops stay in time.
- **reverb** replaced the 4-comb slapback with a **Freeverb-style** 8-comb +
  4-allpass network with a real, loop-aware tail and stereo width.
- **speed** removed the aliasing ×2/÷2 fast paths — every ratio is anti-aliased
  polyphase now.
- **filter** gained a resonant biquad mode (`q`) and a **bandpass**.
- **granular** sprays grains in a local window with per-grain size variation
  instead of shuffling across the whole buffer.
- `stretch_breathe` uses the pitch-preserving stretch.

New one-click **fx panel** in the dashboard (grouped motion / tone / space /
character) and matching command-palette entries.

### Command plane

- `remove_effect` / **undo** now restores the pre-effect audio and DSP metadata
  (single-level).
- New commands: `regenerate`, `name <session>`, `mode <name>`, `mute/unmute all`,
  relative volume (`softer` / `louder`), `listen` with route aliases
  (`spectral` → technical, `llm` → descriptive), explicit `filter … <hz> q <n>`,
  and `speed <ratio>`.
- `analyze_mix` now honors `target` and `focus`; the short-buffer spectral
  analysis no longer collapses to a false 0 Hz centroid.

### CLI / core

- `--mock-audio` is honored again (it was force-cleared outside tests).
- Typed commands via `/` in the TUI; a transient STT failure no longer takes the
  instrument down mid-performance.
- Session archives write `lineage.json` and no longer drop renamed/generated
  stems on `oram export`.
- Provider keys set with `oram credentials set` now persist to the macOS
  Keychain instead of a process-ephemeral env var.

### Web dashboard

- Full new FX set exposed as grouped one-click chips.
- Fixed: single-key shortcuts firing while typing in fields; `l` now listens
  (was toggling auto-mode); double-click-for-unity now resets to true 1.0× and
  the unity guide line sits at the right height; a missing **export-mix** button;
  a leaked keydown listener on the expanded volume overlay; dead DOM references.
- Heavy DSP and blocking calls are offloaded off the event loop, so the 12 fps
  state broadcast no longer stalls during transforms, generation, or settings
  changes. Failed commands now report an error status instead of success.

### Daemon

- Generation / analysis / settings routes run off the event loop.
- Layer-slot reservation and snapshot restore are serialized, closing a
  double-generate / undo-during-generate race.
- New authenticated `POST /credentials/set` so the macOS app can hand provider
  keys to the daemon (which cannot read the app's Keychain items).
- Export paths sanitize layer names; master-recording filenames no longer collide
  within the same second.

### macOS app

- Quitting no longer orphans the daemon (and its Stable Audio sidecar): `stop()`
  signals the real daemon PID even when the app attached to an existing one.
- The header logo is decoded once instead of on every 12 fps state update.
- The Settings window is wired to the daemon and seeded from live state.
- Saving a provider key pushes it to the daemon so generation can use it.

### AU / VST3 plugin

- **Fixed a crash**: corrupt/truncated saved state could force a multi-gigabyte
  allocation; state reads are now bounded and validated.
- Native **filter** upgraded to a zero-phase Butterworth biquad; native
  **reverb** upgraded to a Schroeder comb/allpass network — both processed off
  the audio lock.
- Per-block **gain smoothing** on volume/pan/mute/solo removes zipper and clicks.
- `trim` honors its amount; processor state accepts same-or-older versions;
  cached parameter pointers avoid per-block string lookups.
- Version bumped to 0.4.0; release packaging adds hardened-runtime + timestamp
  when a Developer ID identity is provided.

### Stable Audio 3 local server

- The three morph/inpaint/continue routes no longer block the event loop.
- Per-provider locking prevents concurrent requests from swapping the loaded
  model mid-inference.
- The launcher health-checks readiness before use and defaults to the mock
  provider unless MLX + weights are actually present, so generation works out of
  the box on a machine with no model weights. The client no longer pays a wasted
  404 probe on every render.

### Housekeeping

- Removed dead modules (legacy looper/sampler behaviors, stub STT/summon
  adapters).
- `.env.example` documents every key the code reads and fixes the model-name
  drift.
- 443 tests pass; new coverage for the reworked engine, every new effect, and the
  new command plane.

## v0.3

Audio engine and release-build stabilization; macOS UI parity.

## v0.2

Second development release.

## v0.1

First tagged release.

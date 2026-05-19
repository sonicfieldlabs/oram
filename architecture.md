# oram — architecture

## module responsibilities

```
oram/
  app.py            main run loop, wiring, lifecycle
  config.py         configuration from env/cli/defaults
  types.py          LoopLayer, OramSession, Mode, CommandLogEntry

  audio/
    device.py       audio device listing and selection
    engine.py       AudioEngine protocol, MockAudioEngine, RealAudioEngine
    layer.py        layer state machine and operations
    mixer.py        per-block mixing: mute/solo/volume/pan/sum/limiter
    recorder.py     input capture to pending buffer, layer assignment
    export.py       WAV export via soundfile

  dsp/
    reverse.py      buffer reversal
    speed.py        speed ratio resampling (0.5x, 1.0x, 2.0x)
    pitch.py        pitch shift ±12 semitones
    filter.py       lowpass / highpass
    reverb.py       schroeder-style reverb
    fades.py        fade in/out, trim
    granular.py     grain split, jitter, overlap-add

  command/
    keyboard.py     key event handling and routing
    push_to_talk.py PTT command capture control
    grammar.py      text normalization, layer/duration extraction, regex rules
    parser.py       priority parser chain
    router.py       action dispatcher to engine command queue
    schemas.py      pydantic discriminated union for all action types

  agent/
    controller.py   agent orchestration (rule parser + optional LLM)
    rules.py        deterministic rule definitions
    llm_adapter.py  LLM fallback via CLI providers (codex, opencode)

  stt/
    base.py         STTAdapter protocol
    mock.py         mock adapter for testing
    whisper_local.py local whisper (base/small model)

  summon/
    base.py         SoundGenerator protocol
    mock.py         procedural noise/drone/ambience
    elevenlabs_sfx.py optional API generator

  ears/
    analyzer.py     local audio analysis (RMS, spectral, onset, fatigue)
    report.py       markdown listening report generation

  archive/
    session.py      session folder and file management
    log.py          JSONL command log and session.json
    waveform_text.py text waveform with unicode blocks

  tui/
    app.py          rich live display
    meters.py       level meter rendering
    waveform.py     buffer waveform summary
    theme.py        monochrome theme and visual vocabulary

  web/
    server.py       optional FastAPI dashboard
    static/         compact browser control surface
```

## threading model

the critical architecture rule: never run AI, STT, file export, network calls,
or heavy DSP inside the realtime audio callback.

The current Python MVP routes validated actions through `ActionRouter` on the
UI/server thread, while slow work is delegated to background workers. The audio
engines still expose a command queue boundary for a future control-thread pass,
but the current router does not depend on it.

```
keyboard / dashboard / push-to-talk
             |
             v
        STT / parser / agent
             |
             v
        validated action
             |
             v
        ActionRouter
             |
             +--> cheap state changes
             +--> DSP / generation / archive workers
             |
             v
        realtime callback reads layer state
```

### realtime callback (audio thread)

the callback runs on the audio thread managed by sounddevice. it must only do
bounded, predictable work:

- read input blocks from device
- write output blocks to device
- copy from ring buffers
- apply cheap mixer operations (volume, pan, mute, sum)
- update atomic/simple state (playhead positions)

### router / control path

handles:

- layer selection, mute, solo, clear
- triggering offline DSP (on a worker)
- starting/stopping recording
- buffer swaps after offline processing completes

### worker threads / async

all slow work runs outside the callback:

- STT transcription
- LLM fallback parsing
- DSP transformations (reverse, pitch, granular, etc.)
- sound generation (mock or API)
- file export (WAV writing)
- session archiving
- listening analysis

when a worker completes, it publishes a status message and swaps the completed
buffer into the target layer outside the realtime callback.

## data flow

```
mic input --> [callback] --> recording buffer (pending)
                         --> mixer output --> speakers

recording stop --> concatenate --> normalize --> assign to layer

voice command --> [engine PTT capture] --> STT --> text
text --> parser --> validated action --> ActionRouter
ActionRouter --> engine state / DSP worker

DSP worker --> new buffer --> atomic swap into layer

export command --> WAV writer (worker) --> session folder
```

## state ownership

- `OramSession` owns all `LoopLayer` instances and the mode/selection state
- layers own their audio buffers (numpy float32 arrays)
- the mixer reads layer state but does not own it
- validated actions are the only way UI/STT/LLM input mutates audio state
- no shared mutable state without explicit synchronization

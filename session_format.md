# oram — session format

## archive structure

every performance creates a session folder:

```
oram_sessions/
  oram_0001/
    mix.wav
    stems/
      layer_1.wav
      layer_2.wav
      layer_3.wav
      layer_4.wav
      generated_bed.wav
    session.json
    commands.log
    listening_report.md
    waveform.txt
```

## session.json

```json
{
  "session": "oram_0001",
  "date": "2026-05-14T11:17:00-05:00",
  "scene": "grey_chapel",
  "sample_rate": 48000,
  "bpm": 72,
  "inputs": ["microphone"],
  "layers": [
    {
      "id": 1,
      "name": "layer_1",
      "duration_seconds": 8.0,
      "muted": false,
      "effects": ["granular"]
    }
  ],
  "commands": [
    "record eight seconds",
    "reverse the second layer",
    "add distant rain",
    "make everything softer"
  ],
  "outputs": {
    "mix": "mix.wav",
    "stems": "stems/",
    "waveform": "waveform.txt",
    "listening_report": "listening_report.md"
  }
}
```

## commands.log (JSONL)

append-only, one JSON object per line:

```json
{"time":"2026-05-14T11:18:01-05:00","raw":"reverse layer one","action":{"action":"apply_effect","target":"layer_1","effect":"reverse"},"status":"ok"}
{"time":"2026-05-14T11:18:15-05:00","raw":"add distant rain","action":{"action":"generate_layer","prompt":"distant rain","duration":16},"status":"ok"}
```

## waveform.txt

text waveform using unicode block characters:

```
L1  ▁▂▃▅▆▇▆▅▃▂▁▁▂▃▅▇▆▅▂▁   08.0s
L2  .  .   .* .    .  ** .   16.0s
L3  ▁▁▂▃▅▆▅▃▂▁▁▂▃▅▇▆▅▃▂▁   12.0s
L4  --------------------------------
```

## listening_report.md

```markdown
# oram listening report

session: oram_0007
scene: grey_chapel
date: 2026-05-13

## oram hears

- dense mid-frequency loop
- low spatial depth
- artificial room tone
- speech residue possible in layer 2
- repetition fatigue likely after 42 seconds

## layer notes

- L1: 8.0s, reversed, granular, moderate RMS
- L2: 16.0s, generated bed, low level
```

## export behavior

- `mix.wav`: stereo float32 WAV at session sample rate
- stems: one WAV per active layer, same format
- generated layers are exported as normal `layer_N.wav` stems and marked in
  `session.json`
- export does not delete previous session files on failure
- `oram export ./oram_sessions/oram_0001` refreshes `mix.wav`, `waveform.txt`,
  and `listening_report.md` from an existing archive

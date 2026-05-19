# oram — command grammar

all commands, whether keyboard, parsed text, or LLM-assisted, become a
structured action before touching the engine.

## parser priority

1. normalize text: lowercase, strip punctuation, collapse whitespace.
2. extract layer references: `one`→1, `two`→2, `three`→3, `four`→4.
3. extract durations: seconds first, then bars if BPM is available.
4. match deterministic regex/rules.
5. if no match and LLM is enabled, ask the LLM for one allowed action schema.
6. if still uncertain, do not execute. show "unrecognized command" in TUI.

poetic input is allowed, but output must be structured and bounded.

## transport commands

```
record eight seconds       -> record(duration=8.0)
record 8 seconds           -> record(duration=8.0)
record four bars           -> record(bars=4) (requires BPM)
stop recording             -> stop_recording()
loop this                  -> (set mode to loop)
overdub                    -> overdub(target=selected)
mute layer two             -> mute_layer(target=2)
solo layer one             -> solo_layer(target=1)
clear layer one            -> request clear confirmation for layer 1
clear layer one (repeat)   -> clear_layer(target=1)
select layer three         -> select_layer(target=3)
save scene                 -> save_session()
export mix                 -> export_mix()
```

## mix commands

```
set volume layer 1 0.50    -> set_volume(target=1, volume=0.5)
volume layer two 50        -> set_volume(target=2, volume=0.5)
set pan layer 3 -0.75      -> set_pan(target=3, pan=-0.75)
pan layer one left         -> set_pan(target=1, pan=-0.75)
pan layer two right        -> set_pan(target=2, pan=0.75)
pan layer two center       -> set_pan(target=2, pan=0.0)
make everything softer     -> reduce active layer volumes by 20%
```

## transform commands

```
reverse layer one          -> apply_effect(target=1, effect=reverse)
make layer one slower      -> apply_effect(target=1, effect=speed, speed=0.5)
make it slower             -> apply_effect(target=selected, effect=speed, speed=0.5)
pitch it down              -> apply_effect(target=selected, effect=pitch, semitones=-2)
pitch layer two up five    -> apply_effect(target=2, effect=pitch, semitones=5)
fade the end               -> apply_effect(target=selected, effect=fade_out)
trim the beginning         -> apply_effect(target=selected, effect=trim_start)
filter the voice           -> apply_effect(target=selected, effect=lowpass)
make it darker             -> apply_effect(target=selected, effect=lowpass)
make it thinner            -> apply_effect(target=selected, effect=highpass)
granulate softly           -> apply_effect(target=selected, effect=granular, density=0.3)
stretch it until it breathes -> apply_effect(target=selected, effect=stretch_breathe)
```

## spatial commands

```
move it far away           -> apply_effect(effect=spatial_far)
turn this into a small room -> apply_effect(effect=reverb, decay=short)
add more distance          -> apply_effect(effect=spatial_far)
make the room narrower     -> apply_effect(effect=reverb, narrow=true)
wash it in reverb          -> apply_effect(effect=reverb, wet=0.8)
```

## generative commands

```
add distant metallic rain  -> generate_layer(prompt="distant metallic rain")
create a room tone         -> generate_layer(prompt="room tone")
summon a synthetic forest  -> generate_layer(prompt="synthetic forest")
add a low unstable drone   -> generate_layer(prompt="low unstable drone")
make a fake field recording -> generate_layer(prompt="fake field recording")
generate a quiet machine   -> generate_layer(prompt="quiet machine ambience")
```

## listening commands

```
listen to the texture      -> analyze_mix()
describe the current mix   -> analyze_mix()
what is too dense          -> analyze_mix(focus="density")
what changed in layer two  -> analyze_mix(target=2)
find speech residue        -> analyze_mix(focus="speech")
analyze the loop           -> analyze_mix()
```

## action types

all 17 MVP action types:

- `record`
- `stop_recording`
- `overdub`
- `select_layer`
- `mute_layer`
- `solo_layer`
- `clear_layer`
- `set_volume`
- `set_pan`
- `apply_effect`
- `remove_effect`
- `generate_layer`
- `analyze_mix`
- `save_session`
- `export_mix`
- `set_mode`
- `quit`

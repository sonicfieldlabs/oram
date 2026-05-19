# oram — concept

## what oram is

oram is a speech-operated terminal looper for synthetic sound studies.

it is:

- a terminal looper
- a ritual sampler
- a listening machine
- a lowercase sound instrument
- a minimal agentic performance system
- a research tool for synthetic sound and agentic listening

the performer remains central. the agent interprets, transforms, listens, and
archives.

```
the agent should not replace performance. it should bend the loop.
```

## what oram is not

oram should not become:

- a DAW
- a multitrack timeline editor
- a plugin host
- a chatbot about music
- a generic text-to-music generator
- an always-on surveillance listener
- a large-model-dependent system for every operation

## core flow

the performer records sound. the sound loops. the performer speaks. the agent
listens to the speech, parses intent, and translates it into a constrained
audio action. the sound changes. the performer listens again. the process
repeats as a cycle of recording, transformation, and listening.

oram documents its own process: every command, every transformation, every
listening moment is archived with the audio.

## design principles

**constrained vocabulary**: commands map to bounded actions. poetic language is
accepted as input but must resolve to structured, validated operations.

**offline transforms**: expensive DSP happens outside the realtime audio
callback. the callback only does bounded, predictable work.

**graceful degradation**: if STT fails, keyboard controls remain. if the LLM
is unavailable, the deterministic parser handles commands. if generation fails,
existing loops continue.

**minimal identity**: the interface is monochrome-first, lowercase, austere.
the complexity lives inside the loop, not in the chrome.

## named after

daphne oram — pioneer of electronic music, inventor of the oramics machine,
and composer who built instruments to hear what did not yet exist.

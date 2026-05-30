# Stability AI Provider

ORAM can use Stability AI Stable Audio for text-to-music, text-to-sound,
audio-to-audio, inpaint, continuation, and LoRA-conditioned material generation
through the local engine registry.

## Configure In The macOS App

1. Open Provider Setup.
2. Paste the Stability AI API key.
3. Save to Keychain.
4. Pick Stable Audio in the generator selector.

The app writes the key directly to:

```text
service: wtf.momoto.oram
account: provider:stability
```

## Configure In The Terminal

```bash
oram credentials set stability
oram credentials status
oram credentials test stability
```

Then select the engine explicitly when using daemon/app generation:

```text
stability-stable-audio-3
```

The older Stable Audio 2.5 adapter remains available as:

```text
stability-stable-audio-25
```

## Stable Audio 3 Modes

ORAM exposes these SA3-oriented daemon modes:

```text
ORAM Generate     -> prompt to clip
ORAM Morph        -> source layer plus prompt with noise_depth
ORAM Continue     -> extend source layer; selected loop region can guide range
ORAM Inpaint      -> replace selected range or layer loop region
ORAM LoRA Mixer   -> prompt generation with LoRA A/B stack metadata
ORAM Latent       -> local sidecar mode for SAME latent workflows
```

Apps and external clients can inspect modes with:

```text
GET /stable-audio/modes
```

Render through ORAM-owned daemon layers:

```text
POST /stable-audio/render
```

Render for DAW, Max, or standalone clients that own their own audio state:

```text
POST /plugin/stable-audio/render
```

## Developer Fallback

`.env` remains supported for development:

```bash
# STABILITY_API_KEY=<developer-key>
# Optional: override the Stability API route if the public SA3 endpoint changes.
# ORAM_STABLE_AUDIO_API_URL=https://api.stability.ai/...
```

Keychain is preferred for packaged app usage.

## Local Stable Audio 3 Service

For Apple Silicon MLX or a local Python/CUDA sidecar, point ORAM at the local
service. ORAM defaults to the Germinator-compatible local server URL when loaded
from the environment:

```bash
ORAM_STABLE_AUDIO_SERVICE_URL=http://127.0.0.1:8765
ORAM_STABLE_AUDIO_LOCAL_PROVIDER=stable_audio_mlx
ORAM_STABLE_AUDIO_LOCAL_MODEL=sm-music
ORAM_STABLE_AUDIO_DECODER=same-s
```

ORAM sends a JSON payload to `/render` first. If that route is not available, it
uses Germinator-style mode routes:

```text
generate     -> /generate
morph        -> /audio-to-audio
inpaint      -> /inpaint
continue     -> /continue
lora_mixer   -> /generate with LoRA metadata
```

Responses may be raw audio, base64 audio, a local `audio_path`, a JSON object
containing an audio URL, or a Germinator `audio_files` result served through
`/files/...`. Model rendering must stay in this companion service, not in plugin
or realtime audio callbacks.

## Privacy

Stability AI receives the prompt and generation parameters needed for Stable
Audio when the Stability API engine is selected. Local service requests stay on
the configured service URL. ORAM does not send the key to Momoto or any
ORAM-operated server.

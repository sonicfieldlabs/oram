# Stability AI Provider

ORAM can use Stability AI Stable Audio for text-to-music and text-to-sound
material generation through the local engine registry.

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
stability-stable-audio-2
```

## Developer Fallback

`.env` remains supported for development:

```bash
# STABILITY_API_KEY=<developer-key>
```

Keychain is preferred for packaged app usage.

## Privacy

Stability AI receives the prompt and generation parameters needed for Stable
Audio. ORAM does not send the key to Momoto or any ORAM-operated server.

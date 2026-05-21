# ElevenLabs Provider

ORAM can use ElevenLabs for sound effects, voice, music, Scribe, voice changing,
voice design, and isolation through the provider engine registry.

## Configure In The macOS App

1. Open Provider Setup.
2. Paste the ElevenLabs API key.
3. Save to Keychain.
4. Refresh Engine.

The app writes the key directly to:

```text
service: wtf.momoto.oram
account: provider:elevenlabs
```

## Configure In The Terminal

```bash
oram credentials set elevenlabs
oram credentials status
oram credentials test elevenlabs
```

## Developer Fallback

`.env` remains supported for development:

```bash
# ELEVENLABS_API_KEY=<developer-key>
```

Keychain is preferred for packaged app usage.

## Privacy

ElevenLabs receives the prompt or audio payload needed for the selected provider
operation. ORAM does not send the key to Momoto or any ORAM-operated server.

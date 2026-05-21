# Local-First Privacy

ORAM is open source, local-first, and BYOK.

## Defaults

- No Momoto server is required.
- No telemetry is sent by default.
- Mock generation is available without any cloud credential.
- Provider keys are stored locally, with macOS Keychain as the packaged app
  path.
- The local daemon binds to `127.0.0.1` by default.

## Where Keys Live

Provider keys are stored in macOS Keychain:

```text
service: wtf.momoto.oram
account: provider:elevenlabs
account: provider:stability
```

Developers can still use `.env` as a fallback, but packaged app workflows should
use Keychain.

## Where Keys Are Sent

Provider keys are sent only to the configured provider endpoint. ElevenLabs
requests go to `https://api.elevenlabs.io`; Stability Stable Audio requests go
to `https://api.stability.ai`.

ORAM does not send provider keys to Momoto, ORAM infrastructure, or telemetry
services.

## Local Files

Generated sounds are written to:

```text
~/Music/ORAM Library/
```

Session archives and generated sound metadata include prompts, provider names,
models, tags, and file paths. They do not include provider credentials.

## Diagnostics

Run:

```bash
oram doctor --privacy
```

This reports credential status, network allowlist settings, and LAN warnings
without printing secrets.

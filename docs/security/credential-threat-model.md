# Credential Threat Model

## Scope

This document covers local provider credentials used by ORAM, including
ElevenLabs and Stability AI API keys. It applies to the CLI, daemon, dashboard,
macOS app, local library, and session archives.

## Assets

- Provider API keys, for example ElevenLabs and Stability AI.
- Local daemon control tokens.
- User-generated audio prompts and metadata.
- Session archives and the ORAM Library index.

## Trust Boundaries

- The macOS app and Python daemon run on the user's Mac.
- Provider credentials are stored in Apple Keychain by default on macOS.
- `.env` remains a developer fallback, not the packaged app path.
- ORAM does not send provider credentials to Momoto or any ORAM-operated server.
- Network calls are limited to explicitly configured providers.

## Storage Model

The macOS packaged app stores provider keys in Keychain:

```text
service: wtf.momoto.oram
account: provider:elevenlabs
account: provider:stability
```

The Python credential abstraction is provider-agnostic:

```text
CredentialStore
  MacOSKeychainCredentialStore
  EnvCredentialStore
  MemoryCredentialStore
```

Credential lookup order is Keychain first, `.env`/environment second. Tests use
memory stores.

## Threats And Controls

- API key appears in app state: daemon and dashboard state serializers never
  include secret fields.
- API key appears in logs: redaction masks API keys, Authorization headers,
  bearer tokens, dashboard tokens, and provider token names before writing logs.
- API key appears in archives: session JSON, command logs, listening reports,
  and generated library metadata store prompts and provider names but not keys.
- Browser or website controls the daemon: daemon binds to `127.0.0.1` by default
  and supports a local bearer token for mutation endpoints.
- LAN exposure: dashboard LAN mode requires an explicit token. Daemon LAN mode
  is not a default path.
- Packaged artifact leaks local secrets: build scripts and release checklists
  exclude `.env` files and known secret-bearing local files.

## Residual Risks

- Any process running as the same macOS user can request Keychain access if the
  user approves it.
- Local malware can read user files and memory outside ORAM's control.
- Provider APIs receive prompts and generated requests when users enable that
  provider.

## Validation

Automated tests should fail if known secret values appear in:

- daemon `/state`
- app/daemon logs
- `session.json`
- `commands.log`
- `listening_report.md`
- generated sound metadata

# Contributing

ORAM keeps the Python engine as the source of truth. New app, daemon, or plug-in
work should route through structured ORAM actions instead of bypassing the
parser/router boundary.

## Setup

```bash
uv sync --extra dev --extra web
uv run pytest
```

## macOS App

```bash
cd apps/macos
swift build
```

## Provider Contributions

- Add provider credentials through `oram_security.CredentialStore`.
- Never log provider keys.
- Register engines by capability in `oram.engines.registry`.
- Add tests proving state, logs, archives, and metadata do not contain secrets.

## Licensing

ORAM is MIT licensed. New dependencies should be compatible with that release
model or clearly documented as a separate boundary.

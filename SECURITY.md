# Security Policy

ORAM is local-first and BYOK. Provider credentials should be stored in macOS
Keychain for packaged app usage.

## Reporting Security Issues

Please open a security concern issue using the template in this repository, or
contact the maintainers privately before publishing exploit details.

Include:

- affected ORAM version or commit
- operating system version
- exact local command or app workflow
- whether provider credentials, daemon tokens, logs, or archives were exposed

## Supported Model

- No telemetry by default.
- No Momoto server by default.
- Local daemon binds to `127.0.0.1`.
- Dashboard LAN exposure requires a token.
- Session archives and ORAM Library metadata must not contain provider keys.

Run:

```bash
oram doctor --privacy
```

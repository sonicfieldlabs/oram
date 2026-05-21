# Local Engine Boundary

ORAM's Python engine remains the source of truth. The macOS app, dashboard, and
future DAW plug-ins speak to a local service boundary instead of mutating audio
state directly.

## Existing Engine Responsibilities

- `oram.config`: environment and CLI configuration.
- `oram.app`: terminal lifecycle wiring.
- `oram.command.parser`: deterministic command grammar and optional LLM fallback.
- `oram.command.router`: validated action dispatch, worker thread launch, and
  session mutation.
- `oram.audio`: realtime/mock engine, layer manager, mixer, recorder, export.
- `oram.engines`: provider registry, routing, adapters, and local mock engine.
- `oram.archive`: session archive, command log, waveform text, listening report.
- `oram.web`: optional dashboard using the same parser/router path.

## Daemon Boundary

The daemon exposes a localhost HTTP API that translates app or plug-in requests
into the same structured actions used by the terminal instrument.

Required routes:

```text
GET  /health
GET  /state
GET  /library
GET  /providers
GET  /credentials/status
POST /command
POST /generate
POST /record/start
POST /record/stop
POST /export
POST /analyze
POST /credentials/test
```

The library routes extend that surface:

```text
GET  /library/sounds
GET  /library/sounds/{id}
POST /library/sounds/{id}/favorite
POST /library/sounds/{id}/tags
POST /library/reveal
```

## State Rules

- `/state` is safe for UI refresh and must not expose credentials.
- `/credentials/status` reports configured state and last test status only.
- Generated sounds are written to the ORAM Library, then optionally assigned to
  an engine layer.
- Slow work runs outside the realtime audio callback.

## Daemon Metadata

`oram daemon` writes:

```text
~/Library/Application Support/ORAM/oram-daemon.json
```

Fields:

- `pid`
- `host`
- `port`
- `started_at`
- `version`
- `auth_token_configured`
- `metadata_path`

When daemon auth is enabled, the metadata file also contains a generated local
control token so the SwiftUI app can authenticate mutation requests. The file is
written with owner-only permissions. It is not a provider credential store.

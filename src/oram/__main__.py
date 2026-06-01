"""oram cli entrypoint."""

from __future__ import annotations

from pathlib import Path

import typer

from oram.config import OramConfig, load_dotenv

cli = typer.Typer(
    name="oram",
    help="a speech-operated terminal looper for synthetic sound studies",
    add_completion=False,
    no_args_is_help=False,
)
credentials_cli = typer.Typer(
    name="credentials",
    help="manage local provider credentials",
    add_completion=False,
)
cli.add_typer(credentials_cli)


def _run_app(
    list_devices: bool,
    input_device: int | None,
    output_device: int | None,
    session_name: str | None,
    session_dir: Path | None,
    no_stt: bool,
    mock_audio: bool,
    sample_rate: int | None,
    block_size: int | None,
) -> None:
    """build config and start the app."""
    load_dotenv()

    if list_devices:
        from oram.audio.device import list_audio_devices

        list_audio_devices()
        raise typer.Exit()

    config = OramConfig.from_env()
    # cli overrides
    if input_device is not None:
        config.input_device = input_device
    if output_device is not None:
        config.output_device = output_device
    if session_name is not None:
        config.session_name = session_name
    if session_dir is not None:
        config.session_dir = session_dir
    config.no_stt = no_stt
    config.mock_audio = mock_audio
    if sample_rate is not None:
        config.sample_rate = sample_rate
    if block_size is not None:
        config.block_size = block_size

    from oram.app import run as app_run

    app_run(config)


@cli.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    list_devices: bool = typer.Option(False, "--list-devices", help="list audio devices and exit"),
    input_device: int | None = typer.Option(None, "--input-device", help="input device index"),
    output_device: int | None = typer.Option(None, "--output-device", help="output device index"),
    session_name: str | None = typer.Option(None, "--session-name", help="name for this session"),
    session_dir: Path | None = typer.Option(None, "--session-dir", help="session archive directory"),
    no_stt: bool = typer.Option(False, "--no-stt", help="disable speech-to-text"),
    mock_audio: bool = typer.Option(False, "--mock-audio", help="run without audio hardware"),
    sample_rate: int | None = typer.Option(None, "--sample-rate", help="audio sample rate"),
    block_size: int | None = typer.Option(None, "--block-size", help="audio block size"),
) -> None:
    """start oram when no subcommand is supplied."""
    if ctx.invoked_subcommand is not None:
        return
    _run_app(
        list_devices=list_devices,
        input_device=input_device,
        output_device=output_device,
        session_name=session_name,
        session_dir=session_dir,
        no_stt=no_stt,
        mock_audio=mock_audio,
        sample_rate=sample_rate,
        block_size=block_size,
    )


@cli.command()
def run(
    list_devices: bool = typer.Option(False, "--list-devices", help="list audio devices and exit"),
    input_device: int | None = typer.Option(None, "--input-device", help="input device index"),
    output_device: int | None = typer.Option(None, "--output-device", help="output device index"),
    session_name: str | None = typer.Option(None, "--session-name", help="name for this session"),
    session_dir: Path | None = typer.Option(None, "--session-dir", help="session archive directory"),
    no_stt: bool = typer.Option(False, "--no-stt", help="disable speech-to-text"),
    mock_audio: bool = typer.Option(False, "--mock-audio", help="run without audio hardware"),
    sample_rate: int | None = typer.Option(None, "--sample-rate", help="audio sample rate"),
    block_size: int | None = typer.Option(None, "--block-size", help="audio block size"),
) -> None:
    """start oram."""
    _run_app(
        list_devices=list_devices,
        input_device=input_device,
        output_device=output_device,
        session_name=session_name,
        session_dir=session_dir,
        no_stt=no_stt,
        mock_audio=mock_audio,
        sample_rate=sample_rate,
        block_size=block_size,
    )


@cli.command()
def export(session_path: str = typer.Argument(..., help="path to session folder")) -> None:
    """refresh a session archive's mix, waveform, and listening report."""
    import os

    from oram.archive.safety import validate_export_path
    from oram.archive.session import refresh_session_folder

    # validate path is within allowed directories
    session_dir = Path(os.environ.get("ORAM_SESSION_DIR", "oram_sessions"))
    try:
        validated = validate_export_path(Path(session_path), session_dir=session_dir)
    except ValueError as exc:
        typer.echo(f"export blocked: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        folder = refresh_session_folder(validated)
    except Exception as exc:
        typer.echo(f"export failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"session archive ready: {folder}")
    raise typer.Exit()


@cli.command()
def dashboard(
    host: str = typer.Option("127.0.0.1", "--host", help="server host"),
    port: int = typer.Option(3333, "--port", help="server port"),
    allow_lan: bool = typer.Option(False, "--allow-lan", help="bind to 0.0.0.0 (exposes on LAN)"),
    mock_audio: bool = typer.Option(False, "--mock-audio", help="use mock audio engine instead of hardware"),
) -> None:
    """launch the web dashboard."""
    import os
    import socket

    load_dotenv()
    config = OramConfig.from_env()

    exposes_lan = allow_lan or host in ("0.0.0.0", "::")
    if exposes_lan:
        host = "0.0.0.0"
        token = config.dashboard_token or os.environ.get("ORAM_DASHBOARD_TOKEN", "")
        if not token:
            typer.echo(
                "ERROR: LAN dashboard binding requires ORAM_DASHBOARD_TOKEN. "
                "Set a token first, then open the dashboard with ?token=<token>.",
                err=True,
            )
            raise typer.Exit(code=2)

    typer.echo("oram dashboard")
    typer.echo(f"  local → http://localhost:{port}")

    # detect and print LAN IP only when actually binding to LAN
    if exposes_lan:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
            typer.echo(f"  lan   → http://{lan_ip}:{port}")
        except Exception:
            pass

    from oram.web.server import run_server

    run_server(host=host, port=port, allow_lan=exposes_lan, mock_audio=mock_audio)


@cli.command()
def daemon(
    host: str = typer.Option("127.0.0.1", "--host", help="server host"),
    port: str = typer.Option("auto", "--port", help="server port or 'auto'"),
    mock_audio: bool = typer.Option(False, "--mock-audio", help="use mock audio engine"),
    session_dir: Path | None = typer.Option(None, "--session-dir", help="session archive directory"),
    no_auth: bool = typer.Option(False, "--no-auth", help="disable local daemon bearer token"),
) -> None:
    """launch the local ORAM daemon for app/plugin control."""
    if host not in ("127.0.0.1", "localhost"):
        typer.echo("ERROR: oram daemon only supports localhost binding by default.", err=True)
        raise typer.Exit(code=2)

    from oram_daemon.server import run_daemon

    run_daemon(
        host="127.0.0.1" if host == "localhost" else host,
        port=port,
        mock_audio=mock_audio,
        session_dir=session_dir,
        auth_token="" if no_auth else None,
    )


@credentials_cli.command("set")
def credentials_set(
    provider: str = typer.Argument(..., help="provider id, e.g. elevenlabs"),
) -> None:
    """store a provider credential in the local credential vault."""
    import getpass

    from oram_security.credentials import default_credential_store

    secret = getpass.getpass(f"{provider} API key: ")
    store = default_credential_store()
    try:
        store.set_secret(provider, secret)
    except Exception as exc:
        typer.echo(f"credential write failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{provider}: stored in local credential vault")


@credentials_cli.command("status")
def credentials_status() -> None:
    """show provider credential status without revealing secrets."""
    from oram_security.credentials import default_credential_store

    store = default_credential_store()
    for provider in ("elevenlabs", "stability", "huggingface", "fal", "replicate"):
        status = store.status(provider)
        configured = "configured" if status.configured else "missing"
        typer.echo(f"{provider}: {configured} ({status.source})")


@credentials_cli.command("delete")
def credentials_delete(provider: str = typer.Argument(..., help="provider id, e.g. elevenlabs")) -> None:
    """delete a provider credential from the local credential vault."""
    from oram_security.credentials import default_credential_store

    store = default_credential_store()
    try:
        store.delete_secret(provider)
    except Exception as exc:
        typer.echo(f"credential delete failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"{provider}: deleted from local credential vault")


@credentials_cli.command("test")
def credentials_test(provider: str = typer.Argument(..., help="provider id, e.g. elevenlabs")) -> None:
    """test whether a provider credential is configured and usable."""
    from oram.config import OramConfig
    from oram_daemon.server import LocalOramService

    load_dotenv()
    service = LocalOramService(OramConfig.from_env(), mock_audio=True)
    try:
        result = service.test_credentials(provider)
    finally:
        service.shutdown()
    typer.echo(f"{provider}: {result.get('status')}")


@cli.command()
def doctor(
    privacy: bool = typer.Option(False, "--privacy", help="check local-first privacy configuration"),
) -> None:
    """run local ORAM diagnostics."""
    if not privacy:
        typer.echo("doctor checks: pass --privacy for privacy diagnostics")
        raise typer.Exit()

    import os

    from oram_security.credentials import default_credential_store
    from oram_security.network import allowed_hosts

    load_dotenv()
    cfg = OramConfig.from_env()
    store = default_credential_store()
    typer.echo("oram privacy doctor")
    typer.echo(f"  generator backend: {cfg.generator_backend}")
    typer.echo(f"  llm backend: {cfg.llm_backend}")
    typer.echo("  telemetry: off by default")
    typer.echo(f"  network allowlist: {', '.join(sorted(allowed_hosts()))}")
    typer.echo(f"  dashboard token: {'configured' if cfg.dashboard_token else 'missing'}")
    typer.echo(f"  ORAM_ALLOW_LAN: {'enabled' if os.environ.get('ORAM_ALLOW_LAN') else 'disabled'}")
    if os.environ.get("ORAM_ALLOW_LAN"):
        typer.echo("  WARNING: LAN mode can expose local control paths. Use a token.", err=True)
    for provider in ("elevenlabs", "stability", "huggingface", "fal", "replicate"):
        status = store.status(provider)
        typer.echo(f"  {provider}: {'configured' if status.configured else 'missing'} ({status.source})")


@cli.command()
def lineage(
    session_path: str = typer.Argument(..., help="path to a saved session directory"),
) -> None:
    """display the sonic genealogy tree for a saved session."""
    import json

    session_dir = Path(session_path)
    if not session_dir.is_dir():
        typer.echo(f"error: {session_path} is not a directory", err=True)
        raise typer.Exit(code=1)

    meta_file = session_dir / "session.json"
    if not meta_file.exists():
        typer.echo(f"error: no session.json in {session_path}", err=True)
        raise typer.Exit(code=1)

    data = json.loads(meta_file.read_text())
    layers_data = data.get("layers", [])

    if not layers_data:
        typer.echo("  no layers in session")
        raise typer.Exit()

    from oram.archive.lineage import format_lineage_text
    from oram.types import Layer

    # reconstruct Layer objects for format_lineage_text
    layers = []
    for ld in layers_data:
        l = Layer(
            id=ld.get("id", "?"),
            name=ld.get("name", ""),
            slot=ld.get("slot", 0),
        )
        if ld.get("source_type"):
            from oram.types import SourceType
            try:
                l.source_type = SourceType(ld["source_type"])
            except ValueError:
                pass
        l.parent_layer_id = ld.get("parent_layer_id")
        l.generation_depth = ld.get("generation_depth", 0)
        l.generation_prompt = ld.get("generation_prompt")
        l.is_generated = ld.get("is_generated", False)
        l.duration_seconds = ld.get("duration_seconds", 0.0)
        l.effects_applied = ld.get("effects_applied", [])
        # mark non-empty if has duration
        if l.duration_seconds > 0:
            import numpy as np
            l.buffer = np.zeros((1, 2), dtype=np.float32)  # sentinel
        layers.append(l)

    tree = format_lineage_text(layers)
    typer.echo(f"\n  oram lineage — {session_dir.name}")
    typer.echo(f"  {'─' * 40}")
    typer.echo(tree)
    typer.echo()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

"""oram.archive.session — session folder creation, export, and persistence."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import soundfile as sf

from oram.archive.safety import safe_segment
from oram.archive.waveform_text import session_waveform_text
from oram.audio.export import export_mix, export_stem
from oram.audio.layer import LayerManager
from oram.ears.analyzer import analyze_session
from oram.ears.report import save_report
from oram.types import OramSession, SourceType
from oram_security import redact_text


def _next_session_id(session_dir: Path) -> str:
    """generate the next session ID (oram_NNNN)."""
    existing = sorted(session_dir.glob("oram_*")) if session_dir.exists() else []
    if not existing:
        return "oram_0001"

    last = existing[-1].name
    try:
        num = int(last.split("_")[1])
        return f"oram_{num + 1:04d}"
    except (IndexError, ValueError):
        return f"oram_{len(existing) + 1:04d}"


def create_session_folder(
    session: OramSession,
    layer_manager: LayerManager,
    session_dir: Path,
    session_id: str | None = None,
) -> Path:
    """create a complete session archive folder.

    creates:
      session_dir/session_id/
        mix.wav
        stems/layer_N.wav
        session.json
        commands.log
        waveform.txt
        listening_report.md
    """
    if session_id is None:
        session_id = _next_session_id(session_dir)

    session_id = safe_segment(session_id, fallback="oram_unnamed")
    session.id = session_id
    folder = session_dir / session_id

    # atomic write: build in a temp dir, then swap
    session_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = None
    try:
        tmp_dir = Path(tempfile.mkdtemp(
            prefix=f".oram_{session_id}_",
            dir=str(session_dir),
        ))

        # export mix
        export_mix(layer_manager, tmp_dir / "mix.wav", session.sample_rate)

        # export stems
        stems_dir = tmp_dir / "stems"
        stems_dir.mkdir(exist_ok=True)
        for layer in layer_manager.layers:
            if not layer.is_empty:
                stem_name = f"{safe_segment(layer.name, fallback=f'layer_{layer.slot + 1}')}.wav"
                export_stem(layer_manager, layer.slot + 1, stems_dir / stem_name, session.sample_rate)

        # write text waveform and listening report
        (tmp_dir / "waveform.txt").write_text(
            session_waveform_text(layer_manager.layers, width=40) + "\n",
            encoding="utf-8",
        )
        report = analyze_session(session)
        save_report(report, tmp_dir / "listening_report.md", date=session.created_at)

        # write session.json — §5.1: schema versioning
        session_data = {
            "schema_version": "2.1",
            "session": session_id,
            "date": session.created_at.isoformat(),
            "scene": session.scene,
            "sample_rate": session.sample_rate,
            "bpm": session.bpm,
            "inputs": ["microphone"],
            "layers": [
                {
                    "id": layer.slot + 1,
                    "layer_id": layer.id,
                    "source_type": (
                        layer.source_type.value
                        if hasattr(layer.source_type, 'value')
                        else str(layer.source_type)
                    ),
                    "parent_layer_id": layer.parent_layer_id,
                    "generation_depth": layer.generation_depth,
                    "generation_prompt": redact_text(layer.generation_prompt),
                    "name": layer.name,
                    "duration_seconds": layer.duration_seconds,
                    "muted": layer.muted,
                    "effects": layer.effects_applied,
                }
                for layer in layer_manager.layers
                if not layer.is_empty
            ],
            "commands": [
                redact_text(cmd.raw_text) for cmd in session.commands if cmd.raw_text
            ],
            "outputs": {
                "mix": "mix.wav",
                "stems": "stems/",
                "waveform": "waveform.txt",
                "listening_report": "listening_report.md",
            },
        }

        with open(tmp_dir / "session.json", "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2)

        # write commands.log
        from oram.archive.log import write_command_log
        write_command_log(session.commands, tmp_dir / "commands.log")

        # atomic swap: remove old folder if re-saving, rename temp → final
        if folder.exists():
            shutil.rmtree(folder)
        tmp_dir.rename(folder)
        tmp_dir = None  # prevent cleanup since rename succeeded

    finally:
        # clean up temp dir if rename failed
        if tmp_dir is not None and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return folder


def refresh_session_folder(folder: Path) -> Path:
    """rebuild derived files for an existing session archive.

    This is used by the CLI `oram export SESSION_PATH` command. It loads stems
    from the archive, rerenders `mix.wav`, and regenerates text metadata.
    """
    folder = Path(folder)
    session_json = folder / "session.json"
    if not session_json.exists():
        raise FileNotFoundError(f"missing session.json: {session_json}")

    data = json.loads(session_json.read_text(encoding="utf-8"))
    sample_rate = int(data.get("sample_rate") or 48000)
    session = OramSession(
        id=data.get("session") or folder.name,
        scene=data.get("scene") or folder.name,
        sample_rate=sample_rate,
        bpm=data.get("bpm"),
    )

    manager = LayerManager(sample_rate=sample_rate, channels=2)
    layer_meta = {layer.get("name"): layer for layer in data.get("layers", [])}

    stems_dir = folder / "stems"
    if stems_dir.exists():
        for stem in sorted(stems_dir.glob("*.wav")):
            if not stem.stem.startswith("layer_"):
                continue
            try:
                layer_number = int(stem.stem.split("_", 1)[1])
            except (IndexError, ValueError):
                continue
            if not 1 <= layer_number <= len(manager.layers):
                continue

            audio, sr = sf.read(str(stem), dtype="float32", always_2d=True)
            if int(sr) != sample_rate:
                raise ValueError(f"stem sample rate mismatch: {stem}")
            layer = manager.layers[layer_number - 1]
            manager.assign_buffer(layer, audio)
            meta = layer_meta.get(layer.name, {})
            layer.muted = bool(meta.get("muted", False))
            layer.effects_applied = list(meta.get("effects", []))
            # restore source type from stored metadata
            st = meta.get("source_type", "recorded")
            try:
                layer.source_type = SourceType(st)
            except ValueError:
                layer.source_type = SourceType.RECORDED
            layer.is_generated = layer.source_type == SourceType.GENERATED
            layer.parent_layer_id = meta.get("parent_layer_id")
            layer.generation_depth = int(meta.get("generation_depth", 0))
            layer.generation_prompt = meta.get("generation_prompt")

    session.layers = manager.layers
    return create_session_folder(session, manager, folder.parent, folder.name)

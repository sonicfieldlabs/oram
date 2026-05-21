"""Persistent generated sound library with WAV files and SQLite index."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


def default_library_root() -> Path:
    override = os.environ.get("ORAM_LIBRARY_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "Music" / "ORAM Library"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sound_id() -> str:
    return f"oram_sound_{uuid.uuid4().hex[:12]}"


@dataclass
class SoundRecord:
    id: str
    created_at: str
    provider: str
    model: str
    prompt: str
    duration_seconds: float
    sample_rate: int
    format: str = "wav"
    source: str = "generated"
    tags: list[str] = field(default_factory=list)
    session_id: str | None = None
    favorite: bool = False
    path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "provider": self.provider,
            "model": self.model,
            "prompt": self.prompt,
            "duration_seconds": self.duration_seconds,
            "sample_rate": self.sample_rate,
            "format": self.format,
            "source": self.source,
            "tags": list(self.tags),
            "session_id": self.session_id,
            "favorite": self.favorite,
            "path": self.path,
        }


class OramLibrary:
    """Local durable storage for generated ORAM sounds."""

    def __init__(self, root: Path | None = None):
        self.root = (root or default_library_root()).expanduser()
        self.sessions_dir = self.root / "Sessions"
        self.sounds_dir = self.root / "Sounds"
        self.database_dir = self.root / "Database"
        self.exports_dir = self.root / "Exports"
        self.db_path = self.database_dir / "oram.sqlite"
        self.ensure()

    def ensure(self) -> None:
        for path in (self.sessions_dir, self.sounds_dir, self.database_dir, self.exports_dir):
            path.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sounds (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    sample_rate INTEGER NOT NULL,
                    format TEXT NOT NULL,
                    source TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    session_id TEXT,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    path TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _folder_for(self, sound_id: str, created_at: datetime) -> Path:
        return self.sounds_dir / f"{created_at.year:04d}" / f"{created_at.month:02d}" / sound_id

    def store_sound(
        self,
        audio: np.ndarray,
        sample_rate: int,
        *,
        prompt: str,
        provider: str,
        model: str,
        source: str = "generated",
        tags: list[str] | None = None,
        session_id: str | None = None,
        sound_id: str | None = None,
    ) -> SoundRecord:
        """Write generated audio and metadata to the library."""

        created = _utc_now()
        sid = sound_id or _sound_id()
        folder = self._folder_for(sid, created)
        folder.mkdir(parents=True, exist_ok=True)

        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim == 1:
            audio = np.column_stack([audio, audio])
        sound_path = folder / "sound.wav"
        sf.write(sound_path, audio, sample_rate)

        duration = float(audio.shape[0] / sample_rate) if sample_rate else 0.0
        record = SoundRecord(
            id=sid,
            created_at=created.isoformat(),
            provider=provider,
            model=model,
            prompt=prompt,
            duration_seconds=round(duration, 4),
            sample_rate=int(sample_rate),
            tags=tags or [],
            session_id=session_id,
            path=str(sound_path),
        )

        (folder / "metadata.json").write_text(json.dumps(record.as_dict(), indent=2), encoding="utf-8")
        (folder / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
        (folder / "waveform.txt").write_text(_waveform_text(audio) + "\n", encoding="utf-8")
        self._upsert(record)
        return record

    def _upsert(self, record: SoundRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sounds
                (id, created_at, provider, model, prompt, duration_seconds, sample_rate,
                 format, source, tags_json, session_id, favorite, path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.created_at,
                    record.provider,
                    record.model,
                    record.prompt,
                    record.duration_seconds,
                    record.sample_rate,
                    record.format,
                    record.source,
                    json.dumps(record.tags),
                    record.session_id,
                    1 if record.favorite else 0,
                    record.path,
                ),
            )
            conn.commit()

    def list_sounds(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, provider, model, prompt, duration_seconds,
                       sample_rate, format, source, tags_json, session_id, favorite, path
                FROM sounds
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [self._row_to_record(row).as_dict() for row in rows]

    def get_sound(self, sound_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, created_at, provider, model, prompt, duration_seconds,
                       sample_rate, format, source, tags_json, session_id, favorite, path
                FROM sounds
                WHERE id = ?
                """,
                (sound_id,),
            ).fetchone()
        return self._row_to_record(row).as_dict() if row else None

    def set_favorite(self, sound_id: str, favorite: bool) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute("UPDATE sounds SET favorite = ? WHERE id = ?", (1 if favorite else 0, sound_id))
            conn.commit()
        self._sync_metadata(sound_id)
        return self.get_sound(sound_id)

    def set_tags(self, sound_id: str, tags: list[str]) -> dict[str, Any] | None:
        normalized = sorted({tag.strip() for tag in tags if tag.strip()})
        with self._connect() as conn:
            conn.execute("UPDATE sounds SET tags_json = ? WHERE id = ?", (json.dumps(normalized), sound_id))
            conn.commit()
        self._sync_metadata(sound_id)
        return self.get_sound(sound_id)

    def export_sound(self, sound_id: str, fmt: str = "wav", destination: Path | None = None) -> Path:
        record = self.get_sound(sound_id)
        if not record:
            raise FileNotFoundError(sound_id)
        src = Path(record["path"])
        if fmt not in ("wav", "aiff"):
            raise ValueError("format must be wav or aiff")
        destination_dir = (destination or self.exports_dir).expanduser()
        destination_dir.mkdir(parents=True, exist_ok=True)
        out = destination_dir / f"{sound_id}.{fmt}"
        if fmt == "wav":
            shutil.copyfile(src, out)
        else:
            audio, sr = sf.read(src, always_2d=True, dtype="float32")
            sf.write(out, audio, sr, format="AIFF")
        return out

    def reveal(self, sound_id: str | None = None, path: str | None = None) -> Path:
        target = Path(path).expanduser() if path else None
        if sound_id:
            record = self.get_sound(sound_id)
            if not record:
                raise FileNotFoundError(sound_id)
            target = Path(record["path"])
        if target is None:
            target = self.root
        if os.environ.get("ORAM_REVEAL_IN_FINDER") == "1":
            subprocess.run(["/usr/bin/open", "-R", str(target)], check=False)
        return target

    def _sync_metadata(self, sound_id: str) -> None:
        record = self.get_sound(sound_id)
        if not record:
            return
        metadata_path = Path(record["path"]).parent / "metadata.json"
        metadata_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    @staticmethod
    def _row_to_record(row) -> SoundRecord:
        return SoundRecord(
            id=row[0],
            created_at=row[1],
            provider=row[2],
            model=row[3],
            prompt=row[4],
            duration_seconds=float(row[5]),
            sample_rate=int(row[6]),
            format=row[7],
            source=row[8],
            tags=json.loads(row[9] or "[]"),
            session_id=row[10],
            favorite=bool(row[11]),
            path=row[12],
        )


def _waveform_text(audio: np.ndarray, width: int = 80) -> str:
    if audio.size == 0:
        return "-" * width
    mono = np.mean(audio, axis=1) if audio.ndim > 1 else audio
    chunk = max(1, len(mono) // width)
    levels = "▁▂▃▄▅▆▇█"
    chars = []
    for i in range(width):
        start = i * chunk
        end = min(start + chunk, len(mono))
        if start >= len(mono):
            chars.append(" ")
            continue
        rms = float(np.sqrt(np.mean(mono[start:end] ** 2)))
        idx = max(0, min(len(levels) - 1, int(rms * (len(levels) * 2))))
        chars.append(levels[idx])
    return "".join(chars)

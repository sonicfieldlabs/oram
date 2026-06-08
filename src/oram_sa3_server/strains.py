from __future__ import annotations

import json
from threading import RLock
from uuid import uuid4

from oram_sa3_server.schemas import StrainCard
from oram_sa3_server.storage import StorageManager, safe_stem, utc_now_iso


class StrainRegistry:
    def __init__(self, storage: StorageManager) -> None:
        self.storage = storage
        self.strain_dir = storage.settings.output_root / "strains"
        self.registry_path = self.strain_dir / "strains.json"
        self._lock = RLock()
        self.strain_dir.mkdir(parents=True, exist_ok=True)

    def list_strains(self) -> list[StrainCard]:
        with self._lock:
            return self._load()

    def get_strain(self, strain_id: str) -> StrainCard:
        with self._lock:
            for strain in self._load():
                if strain.id == strain_id:
                    return strain
        raise KeyError(strain_id)

    def save_strain(self, strain: StrainCard) -> StrainCard:
        now = utc_now_iso()
        with self._lock:
            strains = self._load()
            existing = next((item for item in strains if item.id and item.id == strain.id), None)
            strain_id = strain.id or self._next_id(strain)
            created_at = strain.created_at or (existing.created_at if existing else None) or now
            saved = strain.model_copy(
                update={
                    "id": strain_id,
                    "created_at": created_at,
                    "updated_at": now,
                }
            )
            next_strains = [item for item in strains if item.id != strain_id]
            next_strains.append(saved)
            next_strains.sort(key=lambda item: (item.name.lower(), item.id or ""))
            self._write(next_strains)
        return saved

    def delete_strain(self, strain_id: str) -> None:
        with self._lock:
            strains = self._load()
            next_strains = [strain for strain in strains if strain.id != strain_id]
            if len(next_strains) == len(strains):
                raise KeyError(strain_id)
            self._write(next_strains)

    def resolve_paths(self, strain_ids: list[str], direct_paths: list[str] | None = None) -> list[str]:
        paths: list[str] = []
        direct_paths = direct_paths or []
        with self._lock:
            strains = self._load()
        by_id = {strain.id: strain for strain in strains if strain.id}
        for strain_id in strain_ids:
            strain = by_id.get(strain_id)
            if not strain:
                raise KeyError(strain_id)
            if not strain.path:
                raise ValueError(f"strain has no adapter path: {strain_id}")
            paths.append(strain.path)
        paths.extend(path for path in direct_paths if path)
        return list(dict.fromkeys(paths))

    def _next_id(self, strain: StrainCard) -> str:
        base = safe_stem(strain.name or strain.path, fallback="strain").lower()
        return f"strain_{base}_{uuid4().hex[:8]}"

    def _load(self) -> list[StrainCard]:
        if not self.registry_path.exists():
            return []
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        raw_strains = data.get("strains") if isinstance(data, dict) else data
        if not isinstance(raw_strains, list):
            return []
        strains: list[StrainCard] = []
        for item in raw_strains:
            if not isinstance(item, dict):
                continue
            try:
                strains.append(StrainCard(**item))
            except ValueError:
                continue
        return strains

    def _write(self, strains: list[StrainCard]) -> None:
        self.storage.write_json_atomic(
            self.registry_path,
            {
                "type": "strain_registry",
                "updated_at": utc_now_iso(),
                "strains": [strain.model_dump(mode="json") for strain in strains],
            },
            touch_library=True,
        )

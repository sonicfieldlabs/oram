"""tests for the persistent ORAM Library."""

from __future__ import annotations

import numpy as np

from oram_library import OramLibrary


def test_store_list_tag_favorite_and_export_sound(tmp_path):
    library = OramLibrary(tmp_path / "ORAM Library")
    audio = np.zeros((480, 2), dtype=np.float32)
    record = library.store_sound(
        audio,
        48000,
        prompt="quiet metallic rain",
        provider="local",
        model="local-mock",
        tags=["rain"],
    )

    assert record.id.startswith("oram_sound_")
    assert (tmp_path / "ORAM Library" / "Database" / "oram.sqlite").exists()
    assert library.get_sound(record.id)["prompt"] == "quiet metallic rain"
    assert len(library.list_sounds()) == 1

    updated = library.set_tags(record.id, ["texture", "rain", "texture"])
    assert updated["tags"] == ["rain", "texture"]
    favorite = library.set_favorite(record.id, True)
    assert favorite["favorite"] is True

    exported_wav = library.export_sound(record.id, fmt="wav")
    exported_aiff = library.export_sound(record.id, fmt="aiff")
    assert exported_wav.exists()
    assert exported_aiff.exists()

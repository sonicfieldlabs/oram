"""oram.audio.device — audio device listing and selection."""

from __future__ import annotations


def list_audio_devices() -> None:
    """list available audio devices."""
    try:
        import sounddevice as sd

        print("audio devices:")
        print()
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            direction = ""
            if dev["max_input_channels"] > 0:
                direction += "in"
            if dev["max_output_channels"] > 0:
                direction += "/out" if direction else "out"

            marker = ""
            default_in = sd.default.device[0]
            default_out = sd.default.device[1]
            if i == default_in:
                marker += " [default input]"
            if i == default_out:
                marker += " [default output]"

            print(f"  [{i}] {dev['name']}  ({direction}, {dev['default_samplerate']:.0f} Hz){marker}")

    except Exception as e:
        print(f"error listing devices: {e}")
        print("install sounddevice: pip install sounddevice")


def get_device_info(device_id: int | str | None = None) -> dict | None:
    """get info for a specific device."""
    try:
        import sounddevice as sd

        if device_id is None:
            return None
        return sd.query_devices(device_id)
    except Exception:
        return None

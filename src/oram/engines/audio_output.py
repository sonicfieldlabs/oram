"""Bound transport, encoded audio and decoded provider output before allocation."""
from __future__ import annotations

import base64
import binascii
import math
import os
import stat
from io import BytesIO
from pathlib import Path

import numpy as np

from oram_security.network import is_url_allowed

MAX_AUDIO_BYTES = 96 * 1024 * 1024
MAX_RESPONSE_BYTES = MAX_AUDIO_BYTES * 4 // 3 + 1024 * 1024
MAX_AUDIO_FRAMES = 24_000_000
MAX_AUDIO_SECONDS = 400.0
MAX_AUDIO_SAMPLE_RATE = 192_000
MAX_AUDIO_CHANNELS = 2


def bounded_request(client, method: str, url: str, *, allow_local: bool = False, **kwargs):
    """Read one admitted destination, without redirects or HTTP decompression."""
    import httpx

    if not is_url_allowed(url, allow_local=allow_local):
        raise ValueError("Provider URL is not allowed")
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Accept-Encoding"] = "identity"
    with client.stream(method, url, headers=headers, follow_redirects=False, **kwargs) as response:
        if not is_url_allowed(str(response.url), allow_local=allow_local):
            raise ValueError("Provider response destination is not allowed")
        if response.is_redirect:
            raise ValueError("Provider redirects are not accepted")
        if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
            raise ValueError("Compressed provider responses are not accepted")
        length = response.headers.get("content-length")
        if length is not None:
            try:
                size = int(length)
            except ValueError as exc:
                raise ValueError("Invalid provider content length") from exc
            if size < 0 or size > MAX_RESPONSE_BYTES:
                raise ValueError("Provider response exceeds the byte limit")
        content = bytearray()
        for chunk in response.iter_raw(chunk_size=64 * 1024):
            if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ValueError("Provider response exceeds the byte limit")
            content.extend(chunk)
        return httpx.Response(response.status_code, headers=response.headers,
                              content=bytes(content), request=response.request)


def decode_audio_bytes(content: bytes) -> tuple[np.ndarray, int]:
    import soundfile as sf

    if not content or len(content) > MAX_AUDIO_BYTES:
        raise ValueError("Provider audio exceeds the byte limit or is empty")
    try:
        with sf.SoundFile(BytesIO(content)) as source:
            frames, rate, channels = source.frames, source.samplerate, source.channels
            if not 1 <= channels <= MAX_AUDIO_CHANNELS:
                raise ValueError("Provider audio has too many channels")
            if not 1 <= rate <= MAX_AUDIO_SAMPLE_RATE:
                raise ValueError("Provider audio sample rate is invalid")
            duration = frames / rate
            if frames < 1 or frames > MAX_AUDIO_FRAMES or not math.isfinite(duration) or duration > MAX_AUDIO_SECONDS:
                raise ValueError("Provider audio exceeds the frame or duration limit")
            audio = source.read(frames=frames, dtype="float32", always_2d=True)
    except (sf.LibsndfileError, RuntimeError) as exc:
        raise ValueError("Provider audio cannot be decoded") from exc
    if len(audio) != frames or audio.shape[1] != channels or not np.isfinite(audio).all():
        raise ValueError("Provider audio is incomplete or contains non-finite samples")
    if channels == 1:
        audio = np.repeat(audio, 2, axis=1)
    return audio, int(rate)


def decode_audio_base64(value: str) -> tuple[np.ndarray, int]:
    encoded_limit = 4 * ((MAX_AUDIO_BYTES + 2) // 3)
    if len(value) > encoded_limit + 129:
        raise ValueError("Encoded provider audio exceeds the byte limit")
    if value.startswith("data:"):
        header, separator, value = value.partition(",")
        if not separator or len(header) > 128 or not header.endswith(";base64"):
            raise ValueError("Invalid provider audio data URI")
    if len(value) > encoded_limit:
        raise ValueError("Encoded provider audio exceeds the byte limit")
    try:
        content = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64 provider audio") from exc
    return decode_audio_bytes(content)


def decode_audio_file(path: str) -> tuple[np.ndarray, int]:
    # Opening a FIFO must not wait forever before the regular-file check.
    descriptor = os.open(Path(path).expanduser(), os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_AUDIO_BYTES:
            raise ValueError("Provider audio must be a bounded regular file")
        content = stream.read(MAX_AUDIO_BYTES + 1)
    return decode_audio_bytes(content)

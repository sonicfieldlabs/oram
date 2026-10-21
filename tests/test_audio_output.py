"""Provider bounds use synthetic WAVs and in-process HTTP transports only."""
import base64
import os
from io import BytesIO

import httpx
import numpy as np
import pytest
import soundfile as sf

from oram.engines import audio_output as output
from oram.engines.stable_audio import _parse_audio_payload
from oram_security.network import is_url_allowed


def wav(channels=1, rate=24000, values=None):
    stream = BytesIO()
    sf.write(stream, np.zeros((24, channels)) if values is None else values,
             rate, format="WAV", subtype="FLOAT")
    return stream.getvalue()


@pytest.mark.parametrize("url", [
    "http://api.stability.ai/generate", "ftp://api.stability.ai/file",
    "https://user:pass@api.stability.ai/file", "https://api.stability.ai/file#fragment",
    "https://api.stability.ai:0/file", "https://api.stability.ai:65536/file",
    "https://api.stability.ai\\@evil.example/file", "https://api.stability.ai/\nfile",
    "https://evil.example/file", "https://api.stability.ai.evil.example/file",
])
def test_cloud_url_rejects_unsafe_authority_and_scheme(url):
    assert not is_url_allowed(url)


def test_local_adapter_opt_in_and_custom_host(monkeypatch):
    for url in ("http://localhost:8000/render", "http://127.0.0.1:8000/render", "http://[::1]:8000/render"):
        assert not is_url_allowed(url)
        assert is_url_allowed(url, allow_local=True)
    monkeypatch.setenv("ORAM_NETWORK_ALLOWLIST", "render.example")
    assert is_url_allowed("https://render.example/render")
    assert not is_url_allowed("http://render.example/render")
    assert is_url_allowed("http://render.example/render", allow_local=True)


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        yield from self.chunks

    def close(self):
        self.closed = True


@pytest.mark.parametrize("headers,chunks", [
    ({"Content-Length": "9"}, [b"a"]),
    ({}, [b"1234", b"56789"]),
    ({"Content-Length": "broken"}, [b"a"]),
    ({"Content-Encoding": "gzip"}, [b"a"]),
])
def test_transport_limits_and_compression_fail_closed(monkeypatch, headers, chunks):
    monkeypatch.setattr(output, "MAX_RESPONSE_BYTES", 8)
    stream = Chunks(chunks)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers=headers, stream=stream))) as client:
        with pytest.raises(ValueError):
            output.bounded_request(client, "GET", "https://api.stability.ai/file")
    assert stream.closed


def test_redirect_is_not_followed_and_unsafe_urls_never_connect():
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(302, headers={"Location": "http://localhost/private"}, stream=Chunks([]))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match="redirect"):
            output.bounded_request(client, "GET", "https://api.stability.ai/file")
        with pytest.raises(ValueError, match="not allowed"):
            output.bounded_request(client, "GET", "http://localhost/private")
    assert len(calls) == 1


def test_success_and_error_status_survive_bounded_transport():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404, stream=Chunks([b"missing"])))) as client:
        response = output.bounded_request(client, "POST", "http://localhost:8000/render", allow_local=True, json={})
    assert response.status_code == 404
    assert response.content == b"missing"
    with pytest.raises(httpx.HTTPStatusError):
        response.raise_for_status()


def test_mono_wav_base64_and_local_file(tmp_path):
    data = wav()
    audio, rate = output.decode_audio_bytes(data)
    assert audio.shape == (24, 2) and audio.dtype == np.float32 and rate == 24000
    encoded = base64.b64encode(data).decode()
    assert output.decode_audio_base64("data:audio/wav;base64," + encoded)[0].shape == (24, 2)
    path = tmp_path / "audio.wav"
    path.write_bytes(data)
    assert output.decode_audio_file(str(path))[1] == rate


@pytest.mark.parametrize("data", [b"", b"not audio", wav(channels=3), wav(rate=192001), wav(values=np.full((24, 1), np.nan))])
def test_invalid_metadata_and_samples_rejected(data):
    with pytest.raises(ValueError):
        output.decode_audio_bytes(data)


@pytest.mark.parametrize("field,value", [("MAX_AUDIO_FRAMES", 12), ("MAX_AUDIO_SECONDS", 0.0001), ("MAX_AUDIO_BYTES", 12)])
def test_limits_are_checked_before_read_allocation(monkeypatch, field, value):
    monkeypatch.setattr(output, field, value)
    monkeypatch.setattr(sf.SoundFile, "read", lambda *a, **k: pytest.fail("allocated rejected audio"))
    with pytest.raises(ValueError):
        output.decode_audio_bytes(wav())


def test_oversized_base64_rejected_before_decode(monkeypatch):
    monkeypatch.setattr(output, "MAX_AUDIO_BYTES", 3)
    monkeypatch.setattr(base64, "b64decode", lambda *a, **k: pytest.fail("decoded rejected base64"))
    with pytest.raises(ValueError):
        output.decode_audio_base64("data:audio/wav;base64," + "A" * 200)


def test_malformed_base64_and_nested_payload_rejected():
    with pytest.raises(ValueError):
        output.decode_audio_base64("***")
    payload = {"audio": "ignored"}
    for _ in range(20):
        payload = [payload]
    with pytest.raises(ValueError, match="nesting"):
        _parse_audio_payload(payload, allow_local_paths=False)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO")
def test_fifo_rejected_without_waiting_for_a_writer(tmp_path):
    path = tmp_path / "fifo"
    os.mkfifo(path)
    with pytest.raises(ValueError, match="regular file"):
        output.decode_audio_file(str(path))

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time

log = logging.getLogger(__name__)

# Keep a reference to the running process so we can terminate it on exit
_sa3_server_process: subprocess.Popen | None = None
_sa3_server_port: int | None = None

# readiness poll: give a cold uvicorn boot time to bind before first use
_HEALTH_TIMEOUT_SECONDS = 12.0


def find_free_port(start_port: int = 8766) -> int:
    """Find an available port starting from start_port."""
    port = start_port
    while port < start_port + 100:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            port += 1
    # Fallback to dynamic port allocation
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_healthy(port: int, proc: subprocess.Popen, timeout: float) -> bool:
    """Poll /health until the server answers or the process dies."""
    import httpx

    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            resp = httpx.get(url, timeout=0.5)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def start_sa3_server() -> str:
    """Start ORAM's built-in Stable Audio 3 local server in the background.

    Returns the base URL of the started server (e.g. 'http://127.0.0.1:8766').

    On a dev machine with no MLX weights the MLX provider reports itself
    available but every render fails; we fall back to the always-working mock
    provider unless the caller pins a provider via GERMINATOR_ACTIVE_PROVIDER.
    """
    global _sa3_server_process, _sa3_server_port

    if _sa3_server_process is not None and _sa3_server_process.poll() is None:
        return f"http://127.0.0.1:{_sa3_server_port}"

    port = find_free_port(8766)
    log.info("Starting built-in Stable Audio 3 server on port %d...", port)

    # Resolve paths relative to oram package root
    oram_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) # src/ folder
    project_root = os.path.dirname(oram_root) # project root
    vendor_dir = os.path.join(project_root, "src", "oram_sa3_server", "vendor", "stable-audio-3")

    provider = os.environ.get("ORAM_SA3_PROVIDER") or _detect_default_provider(vendor_dir)
    default_model = "medium-mlx" if provider == "stable_audio_mlx" else "small-sfx"

    env = os.environ.copy()
    env["GERMINATOR_HOST"] = "127.0.0.1"
    env["GERMINATOR_PORT"] = str(port)
    env["GERMINATOR_ACTIVE_PROVIDER"] = provider
    env["GERMINATOR_DEFAULT_MODEL"] = default_model
    env["GERMINATOR_ALLOWED_INPUT_ROOTS"] = "output,/tmp,/var/folders,/private/tmp,/private/var/folders"
    env["GERMINATOR_OFFICIAL_REPO_DIR"] = vendor_dir
    env["GERMINATOR_MLX_REPO_DIR"] = vendor_dir

    # Launch uvicorn as a subprocess of the current python interpreter
    cmd = [
        sys.executable, "-m", "uvicorn",
        "oram_sa3_server.main:app",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-level", "warning"
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=oram_root
        )
        _sa3_server_process = proc
        _sa3_server_port = port

        # poll /health instead of a blind sleep so a slow-binding server is
        # not reported ready before it can answer
        if not _wait_until_healthy(port, proc, _HEALTH_TIMEOUT_SECONDS):
            if proc.poll() is not None:
                raise RuntimeError(f"Server exited immediately with code {proc.returncode}")
            log.warning("Stable Audio 3 server did not report healthy within %.0fs", _HEALTH_TIMEOUT_SECONDS)

        # Override ORAM's config environment variable for the local client adapter
        os.environ["ORAM_STABLE_AUDIO_SERVICE_URL"] = f"http://127.0.0.1:{port}"
        log.info(
            "Built-in Stable Audio 3 server launched (provider=%s) at http://127.0.0.1:%d",
            provider, port,
        )
        return f"http://127.0.0.1:{port}"
    except Exception as exc:
        log.error("Failed to start local Stable Audio 3 server: %s", exc)
        return ""


def _detect_default_provider(vendor_dir: str) -> str:
    """Pick the provider most likely to actually produce audio here.

    MLX needs the `mlx` runtime plus downloaded weights; without them every
    render errors, so we only default to it when both are present. Otherwise
    the mock provider keeps generation working out of the box.
    """
    try:
        import importlib.util

        if importlib.util.find_spec("mlx") is None:
            return "mock"
    except Exception:
        return "mock"

    weights_present = any(
        os.path.isdir(os.path.join(vendor_dir, "optimized", "mlx", sub))
        for sub in ("weights", "models_cache")
    ) or bool(os.environ.get("GERMINATOR_MLX_WEIGHTS_DIR"))
    return "stable_audio_mlx" if weights_present else "mock"


def stop_sa3_server() -> None:
    """Terminate the built-in Stable Audio 3 local server."""
    global _sa3_server_process
    if _sa3_server_process is not None:
        log.info("Terminating local Stable Audio 3 server subprocess...")
        try:
            _sa3_server_process.terminate()
            _sa3_server_process.wait(timeout=3.0)
        except Exception:
            try:
                _sa3_server_process.kill()
            except Exception:
                pass
        _sa3_server_process = None

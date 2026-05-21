"""Daemon discovery metadata for local app and plug-in clients."""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def app_support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "ORAM"


def daemon_metadata_path() -> Path:
    return app_support_dir() / "oram-daemon.json"


def find_available_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def write_daemon_metadata(
    *,
    host: str,
    port: int,
    version: str,
    auth_token_configured: bool,
    token: str | None = None,
    metadata_path: Path | None = None,
) -> Path:
    path = metadata_path or daemon_metadata_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "pid": os.getpid(),
        "host": host,
        "port": port,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "version": version,
        "auth_token_configured": bool(auth_token_configured),
        "metadata_path": str(path),
    }
    if token:
        payload["auth"] = {
            "enabled": True,
            "token": token,
            "source": "generated_runtime",
        }
    else:
        payload["auth"] = {
            "enabled": False,
            "source": "none",
        }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path

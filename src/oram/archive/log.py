"""oram.archive.log — JSONL command log and session metadata."""

from __future__ import annotations

import json
import os
from pathlib import Path

from oram.types import CommandLogEntry


def write_command_log(commands: list[CommandLogEntry], path: Path) -> None:
    """write commands as JSONL file."""
    with open(path, "w", encoding="utf-8") as f:
        for cmd in commands:
            entry = {
                "time": cmd.timestamp.isoformat(),
                "raw": cmd.raw_text,
                "action": cmd.action_json,
                "status": cmd.status,
            }
            if cmd.message:
                entry["message"] = cmd.message
            f.write(json.dumps(entry) + "\n")


def append_command(cmd: CommandLogEntry, path: Path) -> None:
    """append a single command to the log file."""
    entry = {
        "time": cmd.timestamp.isoformat(),
        "raw": cmd.raw_text,
        "action": cmd.action_json,
        "status": cmd.status,
    }
    if cmd.message:
        entry["message"] = cmd.message
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_command_log(path: Path) -> list[dict]:
    """read a JSONL command log file."""
    entries = []
    if not path.exists():
        return entries
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries

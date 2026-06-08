from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any


STABLE_AUDIO_MODEL_REPOS = {
    "small-sfx": "stabilityai/stable-audio-3-small-sfx",
    "small-music": "stabilityai/stable-audio-3-small-music",
    "medium": "stabilityai/stable-audio-3-medium",
}


def _token_present() -> bool:
    return bool(os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN"))


def _run_hf(args: list[str], *, timeout: float = 20.0) -> dict[str, Any]:
    executable = shutil.which("hf")
    if not executable:
        return {
            "available": False,
            "command": None,
            "returncode": None,
            "stdout": "",
            "stderr": "hf CLI was not found on PATH.",
        }

    command = [executable, *args]
    try:
        process = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "available": True,
            "command": " ".join(command),
            "returncode": -1,
            "stdout": exc.stdout or "",
            "stderr": f"hf command timed out after {timeout:.0f}s. {exc.stderr or ''}".strip(),
        }

    return {
        "available": True,
        "command": " ".join(command),
        "returncode": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
    }


def auth_status() -> dict[str, Any]:
    result = _run_hf(["auth", "whoami", "--format", "json"], timeout=10.0)
    status = {
        "hf_cli": shutil.which("hf"),
        "token_env_present": _token_present(),
        "logged_in": False,
        "account": None,
        "detail": None,
    }
    if not result["available"]:
        status["detail"] = result["stderr"]
        return status

    if result["returncode"] != 0:
        detail = (result["stderr"] or result["stdout"]).strip()
        status["detail"] = detail or "hf auth whoami failed."
        return status

    try:
        account = json.loads(result["stdout"] or "{}")
    except json.JSONDecodeError:
        account = {"raw": result["stdout"].strip()}

    status["logged_in"] = True
    status["account"] = account
    status["detail"] = "Logged in to Hugging Face CLI."
    return status


def model_access_status(repo_id: str) -> dict[str, Any]:
    result = _run_hf(["download", repo_id, "model_config.json", "--dry-run"], timeout=30.0)
    base = {
        "repo": repo_id,
        "file": "model_config.json",
        "command": result["command"],
        "returncode": result["returncode"],
    }
    if not result["available"]:
        return {**base, "status": "hf_missing", "detail": result["stderr"]}

    output = f"{result['stdout']}\n{result['stderr']}".strip()
    lowered = output.lower()
    if result["returncode"] == 0:
        return {**base, "status": "accessible", "detail": "Dry-run download succeeded."}
    if "access denied" in lowered or "requires approval" in lowered or "gated" in lowered:
        return {
            **base,
            "status": "requires_approval_or_login",
            "detail": output,
        }
    if "not logged in" in lowered or "401" in lowered or "unauthorized" in lowered:
        return {**base, "status": "not_logged_in", "detail": output}
    return {**base, "status": "error", "detail": output}


def stable_audio_hf_status(*, check_models: bool = False) -> dict[str, Any]:
    auth = auth_status()
    models = []
    if check_models:
        models = [
            {"model": model, **model_access_status(repo)}
            for model, repo in STABLE_AUDIO_MODEL_REPOS.items()
        ]

    next_steps = []
    if not auth["hf_cli"]:
        next_steps.append("Install the Hugging Face CLI: https://hf.co/cli")
    if not auth["logged_in"] and not auth["token_env_present"]:
        next_steps.append("Run `uv run hf auth login` with a Hugging Face read token.")
    inaccessible = [
        item for item in models if item.get("status") in {"requires_approval_or_login", "not_logged_in"}
    ]
    if inaccessible:
        next_steps.append(
            "Accept the Stability AI model terms on Hugging Face for small-sfx, "
            "small-music, and/or medium, then rerun this check."
        )

    return {
        "service": "huggingface",
        "auth": auth,
        "model_repos": STABLE_AUDIO_MODEL_REPOS,
        "models_checked": check_models,
        "models": models,
        "ready_for_python_provider_downloads": bool(
            auth["logged_in"]
            and models
            and all(item.get("status") == "accessible" for item in models)
        )
        if check_models
        else None,
        "next_steps": next_steps,
    }

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from oram_sa3_server.registry import settings


router = APIRouter()

SUPPORTED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}


class ImageToAudioAnalyzeRequest(BaseModel):
    image_base64: str = Field(min_length=1)
    mime_type: str = "image/png"
    mode: Literal["vision", "spectrogram"] = "vision"
    interpretation_mode: str = "cinematic"
    use_case: str = "sound_design"


def _vision_prompt(mode: str, use_case: str) -> str:
    return f"""You are an expert sound designer translating an image into a Stable Audio prompt.

Interpretation mode: {mode}
Use case: {use_case}

Return only JSON:
{{
  "imageSummary": "one sentence",
  "visualElements": [{{"element": "visible object", "sonicPotential": "possible sounds", "category": "Atmosphere | Foley | UI | Action | Material"}}],
  "acousticSpace": "room or space description",
  "materialTextures": ["texture"],
  "mood": {{"primary": "mood", "secondary": ["trait"]}},
  "soundCards": [
    {{
      "title": "short title",
      "prompt": "detailed audio-generation prompt, acoustic action, materials, space, no music, no dialogue",
      "durationSeconds": 4,
      "loop": false
    }}
  ]
}}
Keep prompts under 420 characters and describe sound, not pixels."""


def _mock_analysis() -> dict[str, Any]:
    return {
        "imageSummary": "Image interpreted as a textured visual source for a focused sound design prompt.",
        "visualElements": [
            {
                "element": "image structure",
                "sonicPotential": "layered texture, movement, implied space",
                "category": "Atmosphere",
            }
        ],
        "acousticSpace": "medium close space with restrained reflections",
        "materialTextures": ["grain", "air", "surface movement"],
        "mood": {"primary": "textural", "secondary": ["detailed", "controlled"]},
        "soundCards": [
            {
                "title": "Image-derived texture",
                "prompt": "Detailed textural sound derived from the image, close material movement, subtle air pressure, controlled dynamics, tactile surface detail, no music, no dialogue.",
                "durationSeconds": 6,
                "loop": False,
            }
        ],
        "fallback": True,
    }


def _json_from_text(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
    stripped = re.sub(r"```$", "", stripped).strip()
    try:
        value = json.loads(stripped)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.S)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


def _validate_inline_image(request: ImageToAudioAnalyzeRequest) -> str:
    mime_type = request.mime_type.lower().strip()
    if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported image type.")

    image_base64 = request.image_base64.strip()
    max_base64_chars = ((settings.max_image_upload_bytes + 2) // 3) * 4
    if len(image_base64) > max_base64_chars:
        raise HTTPException(
            status_code=413,
            detail=f"image exceeds the {settings.max_image_upload_bytes // (1024 * 1024)} MB limit",
        )
    try:
        decoded = base64.b64decode(image_base64, validate=True)
    except binascii.Error as exc:
        raise HTTPException(status_code=422, detail="image_base64 must be valid base64.") from exc
    if len(decoded) > settings.max_image_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"image exceeds the {settings.max_image_upload_bytes // (1024 * 1024)} MB limit",
        )
    return image_base64


async def _gemini_analysis(
    request: ImageToAudioAnalyzeRequest,
    *,
    image_base64: str,
) -> dict[str, Any] | None:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": _vision_prompt(request.interpretation_mode, request.use_case)},
                    {
                        "inline_data": {
                            "mime_type": request.mime_type,
                            "data": image_base64,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    async with httpx.AsyncClient(timeout=35) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
    text = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "")
    )
    return _json_from_text(text)


@router.post("/image-to-audio/analyze")
async def analyze_image_to_audio(request: ImageToAudioAnalyzeRequest) -> dict[str, Any]:
    image_base64 = _validate_inline_image(request)
    request = request.model_copy(update={"image_base64": image_base64})
    if request.mode == "spectrogram":
        return {
            **_mock_analysis(),
            "imageSummary": "Image will be interpreted directly as a spectrogram-like signal.",
            "mode": "spectrogram",
            "fallback": False,
        }
    try:
        analysis = await _gemini_analysis(request, image_base64=image_base64)
    except httpx.HTTPStatusError as exc:
        analysis = {
            **_mock_analysis(),
            "vision_error": f"Gemini request failed with HTTP {exc.response.status_code}.",
        }
    except httpx.HTTPError:
        analysis = {**_mock_analysis(), "vision_error": "Gemini request failed."}
    except Exception as exc:
        analysis = {**_mock_analysis(), "vision_error": f"{type(exc).__name__}: analysis failed."}
    if not analysis:
        analysis = _mock_analysis()
    cards = analysis.get("soundCards") if isinstance(analysis.get("soundCards"), list) else []
    primary = cards[0] if cards and isinstance(cards[0], dict) else {}
    return {
        **analysis,
        "mode": "vision",
        "prompt": primary.get("prompt") or _mock_analysis()["soundCards"][0]["prompt"],
        "duration": primary.get("durationSeconds") or 6,
    }

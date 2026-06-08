from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from oram_sa3_server.config import get_settings
from oram_sa3_server.identity import LEGACY_ENGINE_NAME, PRODUCT_DESCRIPTION, PRODUCT_NAME
from oram_sa3_server.routes import (
    audio_tools,
    audio_to_audio,
    continue_audio,
    control,
    diagnostics,
    files,
    generate,
    health,
    huggingface,
    image_to_audio,
    import_audio,
    inpaint,
    jobs,
    library,
    lora,
    micro,
    models,
    performance,
    strains,
    time_render,
)
from oram_sa3_server.performance import PerformanceMiddleware
from oram_sa3_server.security import LocalOriginAndHeadersMiddleware


settings = get_settings()

app = FastAPI(
    title=PRODUCT_NAME,
    description=(
        f"{PRODUCT_NAME} FastAPI sidecar for local Stable Audio 3 providers, "
        f"{PRODUCT_DESCRIPTION}, and legacy {LEGACY_ENGINE_NAME} clients."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Validate the Host header to block DNS-rebinding / cross-origin side-effect
# requests against this local oram_sa3_server. Override with GERMINATOR_ALLOWED_HOSTS.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(
    LocalOriginAndHeadersMiddleware,
    allowed_hosts=settings.allowed_hosts,
)
app.add_middleware(PerformanceMiddleware)

app.include_router(health.router)
app.include_router(diagnostics.router)
app.include_router(huggingface.router)
app.include_router(models.router)
app.include_router(performance.router)
app.include_router(control.router)
app.include_router(generate.router)
app.include_router(import_audio.router)
app.include_router(image_to_audio.router)
app.include_router(audio_tools.router)
app.include_router(audio_to_audio.router)
app.include_router(inpaint.router)
app.include_router(continue_audio.router)
app.include_router(lora.router)
app.include_router(strains.router)
app.include_router(micro.router)
app.include_router(jobs.router)
app.include_router(library.router)
app.include_router(files.router)
app.include_router(time_render.router)

# dashboard_dir = settings.project_root / "dashboard" / "static"
# app.mount("/dashboard/assets", StaticFiles(directory=dashboard_dir), name="dashboard-assets")


@app.get("/")
def root() -> dict[str, str]:
    return {
        "server": settings.server_name,
        "health": "/health",
        "diagnostics": "/diagnostics",
        "models": "/models",
        "docs": "/docs",
    }


# @app.get("/dashboard", include_in_schema=False)
# @app.get("/dashboard/", include_in_schema=False)
# def dashboard() -> FileResponse:
#     return FileResponse(dashboard_dir / "index.html")

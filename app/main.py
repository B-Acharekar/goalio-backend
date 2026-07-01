from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import football, matches, users, worldcup
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Firebase initializes lazily, allowing health checks even during credential rotation.
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(users.router, prefix="/api/v1")
app.include_router(football.router, prefix="/api/v1")
app.include_router(matches.router, prefix="/api/v1")
app.include_router(worldcup.router, prefix="/api/v1")
app.include_router(matches.router, prefix="/api", include_in_schema=False)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}

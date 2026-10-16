from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ..config import Settings, get_settings
from ..db import init_db, make_engine, make_session_factory
from .criteria import router as criteria_router
from .query import router as query_router

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)

    app = FastAPI(title="역세권 재개발 빌라 경매물건 탐색기", version="0.1.0")
    app.state.settings = settings
    app.state.session_factory = make_session_factory(engine)
    app.include_router(query_router, prefix="/api")
    app.include_router(criteria_router, prefix="/api")
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app

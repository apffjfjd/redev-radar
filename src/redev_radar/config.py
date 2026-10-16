"""환경설정. 인증키 등은 코드에 두지 않고 환경변수(.env)로만 받는다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATABASE_URL = "sqlite:///var/redev_radar.db"
DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
DEFAULT_TILE_ATTRIBUTION = "&copy; OpenStreetMap contributors"


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_go_kr_key: str | None
    tile_url: str
    tile_attribution: str


def _load_dotenv(path: Path) -> None:
    """.env 파일의 KEY=VALUE 를 환경변수로 읽는다. 이미 설정된 값은 덮어쓰지 않는다."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def get_settings() -> Settings:
    _load_dotenv(Path.cwd() / ".env")
    return Settings(
        database_url=os.environ.get("REDEV_DATABASE_URL") or DEFAULT_DATABASE_URL,
        data_go_kr_key=os.environ.get("REDEV_DATA_GO_KR_KEY") or None,
        tile_url=os.environ.get("REDEV_TILE_URL") or DEFAULT_TILE_URL,
        tile_attribution=os.environ.get("REDEV_TILE_ATTRIBUTION") or DEFAULT_TILE_ATTRIBUTION,
    )

"""저장소 연결과 공통 컬럼 타입."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import shapely
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Engine, LargeBinary, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class Geometry(TypeDecorator):
    """Shapely 도형을 WKB로 저장한다. 좌표계는 항상 WGS84(EPSG:4326, 경도·위도)."""

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: BaseGeometry | None, dialect) -> bytes | None:
        return None if value is None else value.wkb

    def process_result_value(self, value: bytes | None, dialect) -> BaseGeometry | None:
        return None if value is None else shapely.from_wkb(bytes(value))


def make_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    connect_args = {}
    if url.get_backend_name() == "sqlite":
        # API는 요청을 스레드풀에서 처리하므로 연결을 스레드 간에 넘길 수 있어야 한다.
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, connect_args=connect_args)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    from . import models  # noqa: F401  모델 등록

    Base.metadata.create_all(engine)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

from datetime import date

import pytest

from redev_radar.analysis import run_analysis
from redev_radar.collector import collect
from redev_radar.collector.sample import SampleAdapter
from redev_radar.config import Settings
from redev_radar.criteria import seed_criteria
from redev_radar.db import init_db, make_engine, make_session_factory

BASE_DATE = date(2026, 9, 30)


def _factory(path):
    engine = make_engine(f"sqlite:///{path}")
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def session(tmp_path):
    """기준값 시드만 등록된 빈 저장소."""
    with _factory(tmp_path / "test.db")() as s:
        seed_criteria(s)
        s.commit()
        yield s


@pytest.fixture(scope="session")
def analyzed(tmp_path_factory):
    """샘플 데이터를 수집·분석해 둔 저장소. (settings, session, run)"""
    path = tmp_path_factory.mktemp("db") / "analyzed.db"
    with _factory(path)() as s:
        seed_criteria(s)
        collect(s, SampleAdapter(), BASE_DATE)
        s.commit()
        run = run_analysis(s, BASE_DATE)
        assert run.status == "COMPLETED", run.error
        settings = Settings(
            database_url=f"sqlite:///{path}", data_go_kr_key=None, tile_url="", tile_attribution=""
        )
        yield settings, s, run

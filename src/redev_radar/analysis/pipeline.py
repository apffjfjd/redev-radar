"""단계형 분석 파이프라인.

각 단계는 독립 모듈이며 저장소를 통해서만 결과를 주고받는다. 의존 방향은 목록의
순서를 따르고 역방향 의존은 없다. 뒤 단계(사업성·경매·투자 분석 등)는 이 목록에
추가하는 방식으로 확장한다.
"""

from __future__ import annotations

import traceback
from datetime import date, datetime
from typing import Protocol

from sqlalchemy.orm import Session

from .. import models as m
from ..criteria import load_criteria_set
from .block_generator import BlockGenerator
from .context import RunContext, SpatialData, select_snapshots
from .physical import (
    AccessibilityAnalyzer,
    AgingAnalyzer,
    EligibilityAnalyzer,
    FarAnalyzer,
    SiteConditionAnalyzer,
    ZoneOverlapAnalyzer,
)
from .price import PriceAnalyzer


class Stage(Protocol):
    name: str

    def run(self, ctx: RunContext) -> None: ...


def default_stages() -> list[Stage]:
    return [
        BlockGenerator(),
        AgingAnalyzer(),
        SiteConditionAnalyzer(),
        AccessibilityAnalyzer(),
        FarAnalyzer(),
        ZoneOverlapAnalyzer(),
        EligibilityAnalyzer(),  # 앞 단계 지표를 기준값과 비교한다.
        PriceAnalyzer(),
    ]


def run_analysis(
    session: Session, base_date: date, note: str | None = None, stages: list[Stage] | None = None
) -> m.AnalysisRun:
    """분석 기준일로 파이프라인을 실행한다.

    실행 도중에는 RUNNING 상태이고 조회 계층은 COMPLETED 실행만 읽으므로, 분석 중에도
    화면은 직전 실행의 결과를 일관되게 본다. 실패하면 부분 결과를 버리고 FAILED로 남긴다.
    """
    criteria = load_criteria_set(session, base_date)
    run = m.AnalysisRun(
        base_date=base_date,
        status="RUNNING",
        started_at=datetime.now(),
        snapshot_ids=select_snapshots(session, base_date),
        criteria_snapshot=criteria.to_dict(),
        stages=[],
        warnings=[],
        note=note,
    )
    session.add(run)
    session.commit()

    ctx = RunContext(session, run, criteria, SpatialData(session, run.snapshot_ids))
    try:
        for stage in stages or default_stages():
            stage.run(ctx)
            session.flush()
            run.stages = [*run.stages, stage.name]
        run.status = "COMPLETED"
    except Exception as exc:
        session.rollback()
        run.status = "FAILED"
        run.error = f"{exc}\n{traceback.format_exc()}"
    run.finished_at = datetime.now()
    session.commit()
    return run

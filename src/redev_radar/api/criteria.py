"""Criteria Manager API — 기준값·가정값 등록·수정·조회 (FR-010).

이미 실행된 분석은 실행 시점의 사본을 갖고 있으므로 여기서 수정해도 바뀌지 않는다.
법령이 개정되면 기존 행을 고치지 말고 기존 행의 종료일을 넣은 뒤 새 시행일로 추가한다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Assumption, ProjectType, RegulationCriterion
from .deps import get_session

router = APIRouter(tags=["criteria"])


class CriterionIn(BaseModel):
    jurisdiction: str = Field("*", max_length=10)
    project_type: str = Field("*", max_length=30)
    item_code: str = Field(max_length=50)
    qualifier: str = Field("", max_length=50)
    requirement: Literal["REQUIRED", "OPTIONAL", "PARAM"]
    operator: Literal[">=", "<=", ">", "<", "=="] | None = None
    value: float
    unit: str | None = None
    law_name: str | None = None
    law_article: str | None = None
    effective_from: date
    effective_to: date | None = None
    verified: bool = False
    note: str | None = None


class AssumptionIn(BaseModel):
    key: str = Field(max_length=50)
    scenario: Literal["-", "LOW", "BASE", "HIGH"] = "-"
    qualifier: str = Field("", max_length=50)
    value: float
    unit: str | None = None
    source: str | None = None
    effective_from: date
    effective_to: date | None = None
    note: str | None = None


def _row(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _validate(body: CriterionIn | AssumptionIn) -> None:
    if body.effective_to and body.effective_to < body.effective_from:
        raise HTTPException(422, "종료일이 시행일보다 빠를 수 없습니다.")
    if isinstance(body, CriterionIn) and body.requirement != "PARAM" and body.operator is None:
        raise HTTPException(422, "필수·선택 요건에는 비교 연산자가 필요합니다.")


def _effective_at(query, model, as_of: date | None):
    if as_of is None:
        return query
    return query.where(model.effective_from <= as_of).where(
        or_(model.effective_to.is_(None), model.effective_to >= as_of)
    )


@router.get("/project-types")
def list_project_types(session: Session = Depends(get_session)) -> list[dict]:
    return [_row(p) for p in session.scalars(select(ProjectType).order_by(ProjectType.sort_order))]


@router.get("/criteria")
def list_criteria(
    project_type: str | None = None,
    item_code: str | None = None,
    jurisdiction: str | None = None,
    as_of: date | None = None,
    session: Session = Depends(get_session),
) -> list[dict]:
    """as_of를 주면 그 날짜에 시행 중인 기준만, 없으면 전체 이력을 돌려준다."""
    query = select(RegulationCriterion).order_by(
        RegulationCriterion.project_type,
        RegulationCriterion.item_code,
        RegulationCriterion.qualifier,
        RegulationCriterion.jurisdiction,
        RegulationCriterion.effective_from,
    )
    if project_type:
        query = query.where(RegulationCriterion.project_type == project_type)
    if item_code:
        query = query.where(RegulationCriterion.item_code == item_code)
    if jurisdiction:
        query = query.where(RegulationCriterion.jurisdiction == jurisdiction)
    return [_row(r) for r in session.scalars(_effective_at(query, RegulationCriterion, as_of))]


@router.post("/criteria", status_code=201)
def create_criterion(body: CriterionIn, session: Session = Depends(get_session)) -> dict:
    _validate(body)
    row = RegulationCriterion(**body.model_dump(), updated_at=datetime.now())
    session.add(row)
    session.flush()
    return _row(row)


@router.put("/criteria/{criterion_id}")
def update_criterion(criterion_id: int, body: CriterionIn, session: Session = Depends(get_session)) -> dict:
    _validate(body)
    row = session.get(RegulationCriterion, criterion_id)
    if row is None:
        raise HTTPException(404, "기준을 찾을 수 없습니다.")
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    row.updated_at = datetime.now()
    session.flush()
    return _row(row)


@router.get("/assumptions")
def list_assumptions(
    key: str | None = None, as_of: date | None = None, session: Session = Depends(get_session)
) -> list[dict]:
    query = select(Assumption).order_by(
        Assumption.key, Assumption.qualifier, Assumption.scenario, Assumption.effective_from
    )
    if key:
        query = query.where(Assumption.key == key)
    return [_row(r) for r in session.scalars(_effective_at(query, Assumption, as_of))]


@router.post("/assumptions", status_code=201)
def create_assumption(body: AssumptionIn, session: Session = Depends(get_session)) -> dict:
    _validate(body)
    row = Assumption(**body.model_dump(), updated_at=datetime.now())
    session.add(row)
    session.flush()
    return _row(row)


@router.put("/assumptions/{assumption_id}")
def update_assumption(assumption_id: int, body: AssumptionIn, session: Session = Depends(get_session)) -> dict:
    _validate(body)
    row = session.get(Assumption, assumption_id)
    if row is None:
        raise HTTPException(404, "가정값을 찾을 수 없습니다.")
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    row.updated_at = datetime.now()
    session.flush()
    return _row(row)

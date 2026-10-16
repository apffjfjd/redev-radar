from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from importlib import resources
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import Assumption, ProjectType, RegulationCriterion

SEED_EFFECTIVE_FROM = date(2020, 1, 1)


class MissingAssumption(LookupError):
    """분석 기준일에 유효한 가정값이 등록되어 있지 않다."""


@dataclass(frozen=True)
class Criterion:
    id: int
    jurisdiction: str
    project_type: str
    item_code: str
    qualifier: str
    requirement: str
    operator: str | None
    value: float
    unit: str | None
    law_name: str | None
    law_article: str | None
    effective_from: date
    verified: bool
    note: str | None

    def basis(self) -> dict:
        """판정 결과에 함께 남기는 적용 기준의 근거."""
        return {
            "criterion_id": self.id,
            "jurisdiction": self.jurisdiction,
            "law_name": self.law_name,
            "law_article": self.law_article,
            "effective_from": self.effective_from.isoformat(),
            "verified": self.verified,
        }


@dataclass(frozen=True)
class AssumptionValue:
    id: int
    key: str
    scenario: str
    qualifier: str
    value: float
    unit: str | None
    source: str | None
    note: str | None


def jurisdiction_chain(jurisdiction: str | None) -> list[str]:
    """구체적인 지자체부터 전국까지의 조회 순서. 예: 11680 → [11680, 11, *]"""
    chain = []
    if jurisdiction:
        if len(jurisdiction) >= 5:
            chain.append(jurisdiction[:5])
        if len(jurisdiction) >= 2:
            chain.append(jurisdiction[:2])
    chain.append("*")
    return chain


class CriteriaSet:
    """분석 기준일에 유효한 기준값·가정값의 고정된 사본.

    분석 실행은 시작 시점에 이 사본을 만들어 끝까지 사용하므로, 실행 도중
    기준값이 수정되어도 영향을 받지 않는다.
    """

    def __init__(
        self,
        base_date: date,
        criteria: list[Criterion],
        assumptions: list[AssumptionValue],
        project_types: list[dict],
    ):
        self.base_date = base_date
        self.project_types = project_types
        self._criteria = {(c.jurisdiction, c.project_type, c.item_code, c.qualifier): c for c in criteria}
        self._assumptions = {(a.key, a.scenario, a.qualifier): a for a in assumptions}

    def criterion(
        self, item_code: str, jurisdiction: str | None, project_type: str = "*", qualifier: str = ""
    ) -> Criterion | None:
        for j in jurisdiction_chain(jurisdiction):
            found = self._criteria.get((j, project_type, item_code, qualifier))
            if found:
                return found
        return None

    def project_criteria(self, project_type: str, jurisdiction: str | None) -> list[Criterion]:
        """사업방식에 적용할 기준 목록. 항목마다 가장 구체적인 지자체의 기준 하나만 고른다."""
        keys = sorted({(k[2], k[3]) for k in self._criteria if k[1] == project_type})
        found = (self.criterion(item, jurisdiction, project_type, qualifier) for item, qualifier in keys)
        return [c for c in found if c]

    def assumption(self, key: str, scenario: str = "-", qualifier: str = "") -> AssumptionValue | None:
        return self._assumptions.get((key, scenario, qualifier))

    def assumption_value(self, key: str, scenario: str = "-", qualifier: str = "") -> float:
        found = self.assumption(key, scenario, qualifier)
        if found is None:
            raise MissingAssumption(
                f"{self.base_date} 기준으로 유효한 가정값이 없습니다: {key}"
                + (f" [{scenario}]" if scenario != "-" else "")
                + (f" ({qualifier})" if qualifier else "")
            )
        return found.value

    def assumption_qualifiers(self, key: str) -> dict[str, float]:
        return {q: a.value for (k, s, q), a in self._assumptions.items() if k == key and s == "-"}

    def to_dict(self) -> dict:
        def row(obj) -> dict:
            return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in asdict(obj).items()}

        return {
            "base_date": self.base_date.isoformat(),
            "project_types": self.project_types,
            "criteria": [row(c) for c in self._criteria.values()],
            "assumptions": [row(a) for a in self._assumptions.values()],
        }


def _effective(model, base_date: date):
    return (
        select(model)
        .where(model.effective_from <= base_date)
        .where(or_(model.effective_to.is_(None), model.effective_to >= base_date))
        # 같은 키에 유효한 행이 여럿이면 시행일이 늦은 것이 남도록 오름차순으로 읽는다.
        .order_by(model.effective_from, model.id)
    )


def load_criteria_set(session: Session, base_date: date) -> CriteriaSet:
    criteria = [
        Criterion(
            id=r.id,
            jurisdiction=r.jurisdiction,
            project_type=r.project_type,
            item_code=r.item_code,
            qualifier=r.qualifier,
            requirement=r.requirement,
            operator=r.operator,
            value=r.value,
            unit=r.unit,
            law_name=r.law_name,
            law_article=r.law_article,
            effective_from=r.effective_from,
            verified=r.verified,
            note=r.note,
        )
        for r in session.scalars(_effective(RegulationCriterion, base_date))
    ]
    assumptions = [
        AssumptionValue(
            id=r.id,
            key=r.key,
            scenario=r.scenario,
            qualifier=r.qualifier,
            value=r.value,
            unit=r.unit,
            source=r.source,
            note=r.note,
        )
        for r in session.scalars(_effective(Assumption, base_date))
    ]
    project_types = [
        {"code": p.code, "name": p.name}
        for p in session.scalars(select(ProjectType).order_by(ProjectType.sort_order, ProjectType.code))
    ]
    return CriteriaSet(base_date, criteria, assumptions, project_types)


def seed_criteria(session: Session, path: str | Path | None = None) -> dict[str, int]:
    """시드 파일의 사업방식·기준값·가정값을 등록한다. 이미 등록된 종류는 건너뛴다."""
    if path is None:
        text = resources.files("redev_radar.criteria").joinpath("seed/criteria.json").read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    doc = json.loads(text)
    now = datetime.now()
    counts = {"project_types": 0, "criteria": 0, "assumptions": 0}

    def effective_from(row: dict) -> date:
        return date.fromisoformat(row["effective_from"]) if row.get("effective_from") else SEED_EFFECTIVE_FROM

    if session.scalar(select(ProjectType).limit(1)) is None:
        for row in doc.get("project_types", []):
            session.add(ProjectType(**row))
            counts["project_types"] += 1
    if session.scalar(select(RegulationCriterion).limit(1)) is None:
        for row in doc.get("criteria", []):
            session.add(
                RegulationCriterion(
                    **{**row, "effective_from": effective_from(row), "verified": row.get("verified", False)},
                    updated_at=now,
                )
            )
            counts["criteria"] += 1
    if session.scalar(select(Assumption).limit(1)) is None:
        for row in doc.get("assumptions", []):
            session.add(Assumption(**{**row, "effective_from": effective_from(row)}, updated_at=now))
            counts["assumptions"] += 1
    session.flush()
    return counts

"""정비사업 유형별 기본 요건 판정 (FR-004).

기준값은 코드에 두지 않고 Criteria Manager에서 분석 기준일·지자체로 조회한다.
법령상 요건의 예비 검토이며 개별 구역의 법적 판단이 아니다.
"""

from __future__ import annotations

import operator

from sqlalchemy import select

from ... import models as m
from ...criteria import Criterion
from ..context import RunContext

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"
INDETERMINATE, NO_CRITERIA = "INDETERMINATE", "NO_CRITERIA"
NOTICE = "예비 분석 결과이며 법적 검증 결과가 아닙니다. 기준값은 분석 기준일에 등록된 값을 적용했습니다."

# 기준 항목 → 비교 대상 지표
ITEM_METRICS = {
    "MIN_AREA": "BLOCK_AREA_M2",
    "MAX_AREA": "BLOCK_AREA_M2",
    "AGED_COUNT_RATIO": "AGED_COUNT_RATIO",
    "AGED_FLOOR_RATIO": "AGED_FLOOR_RATIO",
    "ROAD_ACCESS_RATIO": "ROAD_ACCESS_RATIO",
    "SMALL_PARCEL_RATIO": "SMALL_PARCEL_RATIO",
    "HOUSEHOLD_DENSITY": "HOUSEHOLD_DENSITY",
    "SEMI_BASEMENT_RATIO": "SEMI_BASEMENT_RATIO",
    "STATION_DISTANCE": "STATION_DISTANCE_M",
}
_OPERATORS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq}


def evaluate(criteria: list[Criterion], metrics: dict[str, float | None]) -> tuple[str, list[dict], dict]:
    """사업방식 하나의 기준 목록을 블록 지표와 비교해 (판정, 항목별 근거, 요약)을 돌려준다."""
    items = []
    for c in criteria:
        if c.requirement not in ("REQUIRED", "OPTIONAL"):
            continue
        metric_code = ITEM_METRICS.get(c.item_code)
        actual = metrics.get(metric_code) if metric_code else None
        compare = _OPERATORS.get(c.operator or "")
        if actual is None or compare is None:
            result = UNKNOWN
        else:
            result = PASS if compare(actual, c.value) else FAIL
        items.append(
            {
                "item_code": c.item_code,
                "requirement": c.requirement,
                "metric_code": metric_code,
                "actual": actual,
                "operator": c.operator,
                "threshold": c.value,
                "unit": c.unit,
                "result": result,
                "basis": c.basis(),
            }
        )
    items.sort(key=lambda i: (i["requirement"] != "REQUIRED", i["item_code"]))
    if not items:
        return NO_CRITERIA, [], {"reason": "분석 기준일에 적용할 기준이 등록되어 있지 않습니다.", "notice": NOTICE}

    def count(requirement: str, result: str) -> int:
        return sum(i["requirement"] == requirement and i["result"] == result for i in items)

    min_optional = next((int(c.value) for c in criteria if c.item_code == "MIN_OPTIONAL_COUNT"), 0)
    optional_pass, optional_unknown = count("OPTIONAL", PASS), count("OPTIONAL", UNKNOWN)
    if count("REQUIRED", FAIL) or optional_pass + optional_unknown < min_optional:
        status = FAIL
    elif count("REQUIRED", UNKNOWN) or optional_pass < min_optional:
        status = INDETERMINATE
    else:
        status = PASS
    summary = {
        "required_pass": count("REQUIRED", PASS),
        "required_fail": count("REQUIRED", FAIL),
        "required_unknown": count("REQUIRED", UNKNOWN),
        "optional_pass": optional_pass,
        "optional_total": sum(i["requirement"] == "OPTIONAL" for i in items),
        "min_optional": min_optional,
        "has_unverified_criteria": any(not i["basis"]["verified"] for i in items),
        "notice": NOTICE,
    }
    return status, items, summary


class EligibilityAnalyzer:
    name = "eligibility"

    def run(self, ctx: RunContext) -> None:
        codes = sorted(set(ITEM_METRICS.values()))
        metrics = ctx.metric_values(codes)
        restrictions = self._restrictions(ctx)
        unregistered: set[tuple[str, str | None]] = set()
        for block in ctx.blocks():
            for project_type in ctx.criteria.project_types:
                criteria = ctx.criteria.project_criteria(project_type["code"], block.jurisdiction)
                status, items, summary = evaluate(criteria, metrics.get(block.id, {}))
                if status == NO_CRITERIA:
                    unregistered.add((project_type["name"], block.jurisdiction))
                summary["restrictions"] = restrictions.get(block.id, [])
                ctx.session.add(
                    m.EligibilityResult(
                        run_id=ctx.run.id,
                        block_id=block.id,
                        project_type=project_type["code"],
                        status=status,
                        items=items,
                        summary=summary,
                    )
                )
        for name, jurisdiction in sorted(unregistered, key=str):
            ctx.warn(f"기준이 등록되지 않아 판정하지 못했습니다: {name} (지자체 {jurisdiction})")

    @staticmethod
    def _restrictions(ctx: RunContext) -> dict[int, list]:
        """겹치는 지구·구역·시설. 판정에는 반영하지 않고 검토 항목으로 함께 제시한다."""
        rows = ctx.session.execute(
            select(m.BlockMetric.block_id, m.BlockMetric.basis)
            .where(m.BlockMetric.run_id == ctx.run.id)
            .where(m.BlockMetric.code == "LAND_USE_RESTRICTIONS")
        )
        return {block_id: basis.get("restrictions", []) for block_id, basis in rows}

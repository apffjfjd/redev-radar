"""건축물 노후도 (FR-003).

사용승인일 기준의 예비 분석이다. 법적 노후·불량건축물 판정과 구분한다.
"""

from __future__ import annotations

from datetime import date

from shapely import STRtree

from ... import models as m
from ..context import BlockView, BuildingRec, RunContext

NOTICE = "사용승인일 기준 예비 분석 결과이며 법적 노후·불량건축물 판정이 아닙니다."
_RC_KEYWORDS = ("철근콘크리트", "철골", "강구조")
_AGE_BINS = [(0, 10), (10, 20), (20, 30), (30, 40)]


def structure_class(structure: str | None) -> str:
    return "RC" if structure and any(k in structure for k in _RC_KEYWORDS) else "OTHER"


def age_years(approval: date, base_date: date) -> float:
    return (base_date - approval).days / 365.25


class AgingAnalyzer:
    name = "aging"

    def run(self, ctx: RunContext) -> None:
        recent_years = ctx.criteria.assumption_value("RECENT_BUILD_YEARS")
        cluster_radius = ctx.criteria.assumption_value("AGED_CLUSTER_RADIUS_M")
        for block in ctx.blocks():
            self._analyze(ctx, block, recent_years, cluster_radius)

    def _analyze(self, ctx: RunContext, block: BlockView, recent_years: float, cluster_radius: float) -> None:
        buildings = ctx.block_buildings(block)
        known = [b for b in buildings if b.approval and b.approval <= ctx.base_date]
        unknown_count = len(buildings) - len(known)
        ages = {b.key: age_years(b.approval, ctx.base_date) for b in known}
        counts = {"buildings": len(buildings), "known_age": len(known), "unknown_age": unknown_count}

        ctx.add_metric(block, "BLDG_COUNT", len(buildings), "동", m.CONFIRMED, {**counts, "source": "건축물대장"})

        thresholds = {
            cls: ctx.criteria.criterion("AGING_YEARS", block.jurisdiction, qualifier=cls) for cls in ("RC", "OTHER")
        }
        if not all(thresholds.values()):
            ctx.warn(f"노후 경과연수 기준(AGING_YEARS)이 등록되지 않은 지자체가 있습니다: {block.jurisdiction}")
        if not known or not all(thresholds.values()):
            reason = "사용승인일이 확인된 건축물이 없습니다." if not known else "노후 경과연수 기준이 등록되어 있지 않습니다."
            for code, unit in (("AGED_COUNT_RATIO", "비율"), ("AGED_FLOOR_RATIO", "비율"), ("AGED_CLUSTERING", "비율")):
                ctx.add_metric(block, code, None, unit, m.ESTIMATED, {**counts, "reason": reason, "notice": NOTICE})
        else:
            aged = [b for b in known if ages[b.key] >= thresholds[structure_class(b.structure)].value]
            criteria_basis = {
                "aging_years": {cls: t.value for cls, t in thresholds.items()},
                "criteria": [t.basis() for t in thresholds.values()],
                "notice": NOTICE,
            }
            ctx.add_metric(
                block,
                "AGED_COUNT_RATIO",
                len(aged) / len(known),
                "비율",
                m.ESTIMATED,
                {**counts, "aged": len(aged), "formula": "노후 동수 / 사용승인일 확인 동수", **criteria_basis},
            )
            with_area = [b for b in known if b.gross_area]
            total_area = sum(b.gross_area for b in with_area)
            aged_keys = {b.key for b in aged}
            aged_area = sum(b.gross_area for b in with_area if b.key in aged_keys)
            ctx.add_metric(
                block,
                "AGED_FLOOR_RATIO",
                aged_area / total_area if total_area else None,
                "비율",
                m.ESTIMATED,
                {
                    **counts,
                    "aged_floor_area": round(aged_area, 1),
                    "total_floor_area": round(total_area, 1),
                    "formula": "노후 건축물 연면적 / 사용승인일·연면적 확인 건축물 연면적",
                    **criteria_basis,
                },
            )
            clustering, neighbors = self._clustering(known, aged_keys, cluster_radius)
            ctx.add_metric(
                block,
                "AGED_CLUSTERING",
                clustering,
                "비율",
                m.ESTIMATED,
                {
                    "radius_m": cluster_radius,
                    "aged_with_neighbors": neighbors,
                    "formula": "노후 건축물별 반경 내 이웃 건축물 중 노후 비율의 평균",
                    "notice": NOTICE,
                },
            )

        distribution = {f"{lo}-{hi - 1}년": sum(lo <= a < hi for a in ages.values()) for lo, hi in _AGE_BINS}
        distribution["40년 이상"] = sum(a >= 40 for a in ages.values())
        distribution["미상"] = unknown_count
        ctx.add_metric(
            block,
            "AVG_BUILDING_AGE",
            sum(ages.values()) / len(ages) if ages else None,
            "년",
            m.ESTIMATED,
            {**counts, "age_distribution": distribution, "notice": NOTICE},
        )
        recent = sum(a < recent_years for a in ages.values())
        ctx.add_metric(
            block,
            "RECENT_BUILD_RATIO",
            recent / len(ages) if ages else None,
            "비율",
            m.ESTIMATED,
            {**counts, "recent": recent, "recent_years": recent_years, "notice": NOTICE},
        )

    @staticmethod
    def _clustering(known: list[BuildingRec], aged_keys: set[str], radius: float) -> tuple[float | None, int]:
        tree = STRtree([b.point for b in known])
        shares = []
        for i, b in enumerate(known):
            if b.key not in aged_keys:
                continue
            neighbors = [j for j in tree.query(b.point, predicate="dwithin", distance=radius).tolist() if j != i]
            if neighbors:
                shares.append(sum(known[j].key in aged_keys for j in neighbors) / len(neighbors))
        return (sum(shares) / len(shares) if shares else None), len(shares)

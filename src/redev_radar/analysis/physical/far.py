"""현황 용적률과 개발 가능 용적률 비교 (FR-006).

추가 용적률 여력 = 가정한 개발 가능 용적률 - 현황 용적률. 예비 비교 지표이며
실제 사업계획상 실현 가능한 연면적과 구분한다.
"""

from __future__ import annotations

from shapely import STRtree

from ... import models as m
from ..context import BlockView, RunContext

SCENARIOS = ("LOW", "BASE", "HIGH")
MIN_ZONING_COVERAGE = 0.95  # 기준·가정이 확인된 용도지역이 블록의 이만큼을 덮어야 산출한다.
NOTICE = "예비 비교 지표이며 실제 사업계획상 실현 가능한 연면적과 다릅니다."
NOT_REFLECTED = ["공공기여 부담", "일조·높이·경관 제약", "도로 관련 제약", "용도지역 상향·인센티브의 실제 적용 가능성"]


class FarAnalyzer:
    name = "far"

    def run(self, ctx: RunContext) -> None:
        use_zones = [z for z in ctx.data.zoning_areas if z.category == "USE_ZONE"]
        overlays = [z for z in ctx.data.zoning_areas if z.category != "USE_ZONE"]
        zone_tree = STRtree([z.geom for z in use_zones])
        overlay_tree = STRtree([z.geom for z in overlays])
        if not use_zones:
            ctx.warn("용도지역 자료가 없어 개발 가능 용적률을 산출하지 못했습니다.")

        for block in ctx.blocks():
            current = self._current_far(ctx, block)
            shares: dict[str, float] = {}
            names: dict[str, str] = {}
            for i in zone_tree.query(block.geom, predicate="intersects").tolist():
                share = use_zones[i].geom.intersection(block.geom).area / block.area
                if share > 0.001:
                    shares[use_zones[i].key] = shares.get(use_zones[i].key, 0.0) + share
                    names[use_zones[i].key] = use_zones[i].name
            zoning_basis = {
                "zoning_shares": [
                    {"code": code, "name": names[code], "share": round(share, 3)}
                    for code, share in sorted(shares.items(), key=lambda kv: -kv[1])
                ]
            }
            constraints = [
                overlays[i].name for i in sorted(overlay_tree.query(block.geom, predicate="intersects").tolist())
            ]

            ordinance = {code: ctx.criteria.criterion("FAR_ORDINANCE", block.jurisdiction, qualifier=code) for code in shares}
            value, basis = self._weighted(shares, {code: c.value for code, c in ordinance.items() if c})
            ctx.add_metric(
                block,
                "ORDINANCE_FAR",
                value,
                "%",
                m.CONFIRMED if value is not None else m.ESTIMATED,
                {**zoning_basis, **basis, "criteria": [c.basis() for c in ordinance.values() if c]},
            )

            for scenario in SCENARIOS:
                assumed = {code: ctx.criteria.assumption("DEVELOPABLE_FAR", scenario, code) for code in shares}
                developable, basis = self._weighted(shares, {code: a.value for code, a in assumed.items() if a})
                basis = {
                    **zoning_basis,
                    **basis,
                    "scenario": scenario,
                    "assumptions": [
                        {"zoning": code, "value": a.value, "source": a.source} for code, a in assumed.items() if a
                    ],
                    "recorded_constraints": constraints,
                    "not_reflected": NOT_REFLECTED,
                    "notice": NOTICE,
                }
                ctx.add_metric(block, f"DEVELOPABLE_FAR_{scenario}", developable, "%", m.ESTIMATED, basis)
                headroom = None if developable is None or current is None else developable - current
                ctx.add_metric(
                    block,
                    f"FAR_HEADROOM_{scenario}",
                    headroom,
                    "%p",
                    m.ESTIMATED,
                    {
                        **basis,
                        "formula": "가정한 개발 가능 용적률 - 현황 용적률",
                        "developable_far": developable,
                        "current_far": current,
                    },
                )

    @staticmethod
    def _current_far(ctx: RunContext, block: BlockView) -> float | None:
        buildings = ctx.block_buildings(block)
        official = [b for b in buildings if b.far_area]
        fallback = [b for b in buildings if not b.far_area and b.gross_area]
        missing = len(buildings) - len(official) - len(fallback)
        floor_area = sum(b.far_area for b in official) + sum(b.gross_area for b in fallback)
        basis = {
            "floor_area_m2": round(floor_area, 1),
            "parcel_area_m2": round(block.parcel_area, 1),
            "buildings_using_gross_area": len(fallback),
            "buildings_without_area": missing,
            "formula": "용적률 산정용 연면적 합 / 필지 면적 합 × 100",
        }
        if not block.parcel_area or not buildings:
            ctx.add_metric(block, "CURRENT_FAR", None, "%", m.ESTIMATED, {**basis, "reason": "건축물 또는 필지 면적 자료가 없습니다."})
            return None
        value = floor_area / block.parcel_area * 100
        # 용적률 산정용 연면적이 없어 전체 연면적으로 대신한 건축물이 있으면 추정값이다.
        kind = m.ESTIMATED if fallback or missing else m.CONFIRMED
        ctx.add_metric(block, "CURRENT_FAR", value, "%", kind, basis)
        return value

    @staticmethod
    def _weighted(shares: dict[str, float], values: dict[str, float]) -> tuple[float | None, dict]:
        """용도지역 면적 비율로 가중평균한다. 값이 확인된 용도지역의 비율이 부족하면 산출하지 않는다."""
        covered = sum(share for code, share in shares.items() if code in values)
        if covered < MIN_ZONING_COVERAGE:
            missing = sorted(code for code in shares if code not in values)
            reason = (
                f"기준값이 등록되지 않은 용도지역이 있습니다: {', '.join(missing)}"
                if missing
                else "용도지역 자료가 블록을 충분히 덮지 못합니다."
            )
            return None, {"covered_share": round(covered, 3), "reason": reason}
        value = sum(shares[code] * values[code] for code in values) / covered
        return value, {"covered_share": round(covered, 3), "method": "용도지역 면적 비율 가중평균"}

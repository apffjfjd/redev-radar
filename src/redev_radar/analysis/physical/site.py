"""요건 판정에 쓰이는 대지·기반시설 현황 지표 (FR-004의 입력)."""

from __future__ import annotations

from shapely import STRtree

from ... import models as m
from ..context import BlockView, RunContext

ACCESS_TOLERANCE_M = 1.0  # 필지 경계와 도로 경계 사이의 측량 오차 허용


def is_housing(main_use: str | None) -> bool:
    return main_use is None or "주택" in main_use


class SiteConditionAnalyzer:
    name = "site_condition"

    def run(self, ctx: RunContext) -> None:
        roads = [r for r in ctx.data.roads if r.width]
        road_tree = STRtree([r.geom for r in roads])
        overlays = [z for z in ctx.data.zoning_areas if z.category != "USE_ZONE"]
        overlay_tree = STRtree([z.geom for z in overlays])
        parcel_geoms = {p.pnu: p.geom for p in ctx.data.parcels}

        for block in ctx.blocks():
            ctx.add_metric(
                block,
                "BLOCK_AREA_M2",
                block.area,
                "㎡",
                m.ESTIMATED,
                {
                    "parcel_area_m2": round(block.parcel_area, 1),
                    "parcel_count": len(block.pnus),
                    "notice": "후보 경계 기준 면적(내부 도로 포함)이며 실제 정비구역 경계와 다를 수 있습니다.",
                },
            )
            self._road_access(ctx, block, roads, road_tree, parcel_geoms)
            self._small_parcels(ctx, block)
            self._density(ctx, block)
            self._semi_basement(ctx, block)
            self._restrictions(ctx, block, overlays, overlay_tree)

    def _road_access(self, ctx: RunContext, block: BlockView, roads, road_tree: STRtree, parcel_geoms: dict) -> None:
        criterion = ctx.criteria.criterion("ACCESS_ROAD_WIDTH", block.jurisdiction)
        houses = [b for b in ctx.block_buildings(block) if is_housing(b.main_use)]
        if criterion is None or not houses:
            reason = "접도 도로폭 기준이 등록되어 있지 않습니다." if criterion is None else "주택이 없습니다."
            ctx.add_metric(block, "ROAD_ACCESS_RATIO", None, "비율", m.ESTIMATED, {"reason": reason})
            return
        wide = [i for i, r in enumerate(roads) if r.width >= criterion.value]
        reach = max((roads[i].width for i in wide), default=0) / 2 + ACCESS_TOLERANCE_M
        wide_set = set(wide)

        def has_access(geom) -> bool:
            for i in road_tree.query(geom, predicate="dwithin", distance=reach).tolist():
                if i in wide_set and geom.distance(roads[i].geom) <= roads[i].width / 2 + ACCESS_TOLERANCE_M:
                    return True
            return False

        accessible = sum(has_access(parcel_geoms.get(b.pnu, b.geom)) for b in houses)
        ctx.add_metric(
            block,
            "ROAD_ACCESS_RATIO",
            accessible / len(houses),
            "비율",
            m.ESTIMATED,
            {
                "houses": len(houses),
                "accessible": accessible,
                "road_width_m": criterion.value,
                "formula": "기준 폭 이상 도로에 접한 주택 동수 / 주택 동수",
                "criteria": [criterion.basis()],
                "notice": "도로 중심선과 폭으로 접도 여부를 근사했습니다.",
            },
        )

    def _small_parcels(self, ctx: RunContext, block: BlockView) -> None:
        criterion = ctx.criteria.criterion("SMALL_PARCEL_AREA", block.jurisdiction)
        parcels = ctx.block_parcels(block)
        if criterion is None:
            ctx.add_metric(
                block, "SMALL_PARCEL_RATIO", None, "비율", m.ESTIMATED, {"reason": "과소필지 면적 기준이 등록되어 있지 않습니다."}
            )
            return
        small = sum(p.area < criterion.value for p in parcels)
        ctx.add_metric(
            block,
            "SMALL_PARCEL_RATIO",
            small / len(parcels),
            "비율",
            m.ESTIMATED,
            {
                "parcels": len(parcels),
                "small": small,
                "small_parcel_area_m2": criterion.value,
                "formula": "기준 면적 미만 필지 수 / 필지 수",
                "criteria": [criterion.basis()],
            },
        )

    def _density(self, ctx: RunContext, block: BlockView) -> None:
        buildings = ctx.block_buildings(block)
        units = sum(max(b.households or 0, 1) for b in buildings)
        ctx.add_metric(
            block,
            "HOUSEHOLD_DENSITY",
            units / (block.area / 10_000),
            "호/ha",
            m.ESTIMATED,
            {
                "units": units,
                "area_ha": round(block.area / 10_000, 3),
                "formula": "호수(세대수가 있으면 세대수, 없으면 1동을 1호) / 구역 면적(ha)",
            },
        )

    def _semi_basement(self, ctx: RunContext, block: BlockView) -> None:
        houses = [b for b in ctx.block_buildings(block) if is_housing(b.main_use)]
        known = [b for b in houses if b.semi_basement is not None]
        with_basement = sum(bool(b.semi_basement) for b in known)
        basis = {"houses": len(houses), "known": len(known), "with_semi_basement": with_basement}
        if not known:
            basis["reason"] = "반지하 여부가 확인된 주택이 없습니다."
        ctx.add_metric(
            block, "SEMI_BASEMENT_RATIO", with_basement / len(known) if known else None, "비율", m.ESTIMATED, basis
        )

    def _restrictions(self, ctx: RunContext, block: BlockView, overlays, overlay_tree: STRtree) -> None:
        found = []
        for i in sorted(overlay_tree.query(block.geom, predicate="intersects").tolist()):
            ratio = overlays[i].geom.intersection(block.geom).area / block.area
            if ratio > 0.001:
                found.append({"category": overlays[i].category, "name": overlays[i].name, "overlap_ratio": round(ratio, 3)})
        ctx.add_metric(
            block,
            "LAND_USE_RESTRICTIONS",
            len(found),
            "건",
            m.CONFIRMED,
            {"restrictions": found, "notice": "겹치는 지구·구역·시설의 목록이며 개별 규제 내용은 별도 확인이 필요합니다."},
        )

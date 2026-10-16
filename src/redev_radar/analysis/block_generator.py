"""Block Generator — 후보 블록과 복수의 후보 경계 생성 (FR-002).

역으로부터의 거리로 필지를 잘라내지 않고, 도로로 둘러싸인 가구(街區) 단위로 필지를
묶는다. 어느 폭 이상의 도로를 블록 경계로 볼지를 달리해(가정값 BLOCK_ROAD_WIDTH_M의
qualifier) 같은 지역에 복수의 후보 경계를 만든다. 후보 경계 개수는 등록된 qualifier
개수로 제한된다.
"""

from __future__ import annotations

import hashlib
from collections import Counter

from shapely import STRtree, unary_union
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from .. import models as m
from ..criteria import MissingAssumption
from ..geo import polygons_of, to_wgs84
from .context import ParcelRec, RunContext

RESIDENTIAL_ZONING = {"ER1", "ER2", "GR1", "GR2", "GR3", "SEMI_R"}
ROAD_JIMOK = {"도", "도로"}


def _close_gaps(geom: BaseGeometry, gap: float) -> BaseGeometry:
    """필지 사이의 좁은 틈(블록 내부 골목)을 메운다."""
    return geom.buffer(gap, join_style="mitre").buffer(-gap, join_style="mitre")


def _share(geom: BaseGeometry, areas: list[BaseGeometry], tree: STRtree) -> float:
    covered = [areas[i].intersection(geom) for i in tree.query(geom, predicate="intersects")]
    return unary_union(covered).area / geom.area if covered else 0.0


class BlockGenerator:
    name = "block_generator"

    def run(self, ctx: RunContext) -> None:
        c = ctx.criteria
        parcels = [p for p in ctx.data.parcels if p.jimok not in ROAD_JIMOK]
        stations = ctx.data.stations
        if not parcels or not stations:
            ctx.warn("필지 또는 지하철역 자료가 없어 후보 블록을 생성하지 못했습니다.")
            return

        variants = c.assumption_qualifiers("BLOCK_ROAD_WIDTH_M")
        if not variants:
            raise MissingAssumption(f"{ctx.base_date} 기준으로 유효한 가정값이 없습니다: BLOCK_ROAD_WIDTH_M")
        radius = c.assumption_value("STATION_AREA_RADIUS_M")
        min_parcels = c.assumption_value("BLOCK_MIN_PARCELS")
        min_area = c.assumption_value("BLOCK_MIN_AREA_M2")
        min_station_share = c.assumption_value("BLOCK_MIN_STATION_SHARE")
        min_residential_share = c.assumption_value("BLOCK_MIN_RESIDENTIAL_SHARE")

        station_zone = unary_union([s.geom.buffer(radius) for s in stations])
        extent = station_zone.buffer(radius)
        parcels = [p for p in parcels if p.geom.intersects(extent)]
        if not parcels:
            ctx.warn("역세권 탐색 범위 안에 필지가 없습니다.")
            return
        points = [p.geom.representative_point() for p in parcels]
        bounds = unary_union([box(*p.geom.bounds) for p in parcels]).bounds
        extent = extent.intersection(box(*bounds).buffer(50))

        if any(r.width is None for r in ctx.data.roads):
            ctx.warn("폭 정보가 없는 도로는 블록 경계로 사용하지 않았습니다.")

        use_zones = [z for z in ctx.data.zoning_areas if z.category == "USE_ZONE"]
        residential = [z.geom for z in use_zones if z.key in RESIDENTIAL_ZONING]
        residential_tree = STRtree(residential)
        if not use_zones:
            ctx.warn("용도지역 자료가 없어 주거지역 비율 조건을 적용하지 않았습니다.")

        created: dict[str, list[tuple[m.CandidateBlock, BaseGeometry]]] = {}
        for variant, threshold in sorted(variants.items(), key=lambda kv: kv[1]):
            cutters = [
                r.geom.buffer(r.width / 2, quad_segs=4) for r in ctx.data.roads if r.width and r.width >= threshold
            ]
            faces = polygons_of(extent.difference(unary_union(cutters))) if cutters else polygons_of(extent)
            members: dict[int, list[int]] = {}
            parcel_idx, face_idx = STRtree(faces).query(points, predicate="within")
            for p, f in zip(parcel_idx.tolist(), face_idx.tolist()):
                members.setdefault(f, []).append(p)

            blocks = []
            for f, indices in sorted(members.items()):
                merged = _close_gaps(unary_union([parcels[i].geom for i in indices]), threshold / 2 + 1.0)
                # 필지가 서로 떨어진 덩어리는 각각 별도 블록으로 본다.
                for part in polygons_of(merged.intersection(faces[f])):
                    inside = [parcels[i] for i in indices if part.contains(points[i])]
                    if len(inside) < min_parcels or part.area < min_area:
                        continue
                    if part.intersection(station_zone).area / part.area < min_station_share:
                        continue
                    if use_zones and _share(part, residential, residential_tree) < min_residential_share:
                        continue
                    blocks.append((self._block(ctx, variant, part, inside), part))
            created[variant] = blocks

        self._assign_regions(created, coarsest=max(variants, key=variants.get))
        ctx.session.add_all(block for blocks in created.values() for block, _ in blocks)
        ctx.session.flush()

    def _block(self, ctx: RunContext, variant: str, geom: BaseGeometry, parcels: list[ParcelRec]) -> m.CandidateBlock:
        pnus = sorted(p.pnu for p in parcels)
        digest = hashlib.sha1("|".join(pnus).encode()).hexdigest()[:10]
        jurisdiction = Counter(p.sgg_cd or p.pnu[:5] for p in parcels).most_common(1)[0][0]
        return m.CandidateBlock(
            run_id=ctx.run.id,
            block_key=f"{variant}-{digest}",
            variant=variant,
            jurisdiction=jurisdiction,
            area_m2=geom.area,
            parcel_area_m2=sum(p.area for p in parcels),
            parcel_count=len(parcels),
            parcel_pnus=pnus,
            geom=to_wgs84(geom),
        )

    def _assign_regions(self, created: dict, coarsest: str) -> None:
        """가장 큰 경계의 블록을 '지역'으로 삼아, 같은 지역의 후보 경계들을 묶는다."""
        regions = created.get(coarsest, [])
        geoms = [geom for _, geom in regions]
        tree = STRtree(geoms)
        for blocks in created.values():
            for block, geom in blocks:
                hits = tree.query(geom.representative_point(), predicate="within")
                block.region_key = regions[int(hits[0])][0].block_key if len(hits) else block.block_key

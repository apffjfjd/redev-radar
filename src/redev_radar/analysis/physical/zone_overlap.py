"""기존 정비구역 중복 및 추진현황 (FR-007)."""

from __future__ import annotations

from shapely import STRtree, unary_union

from ... import models as m
from ..context import RunContext

MIN_OVERLAP_RATIO = 0.01  # 경계 오차 수준의 겹침은 중복으로 보지 않는다.


class ZoneOverlapAnalyzer:
    name = "zone_overlap"

    def run(self, ctx: RunContext) -> None:
        zones = ctx.data.redevelopment_zones
        if not zones:
            ctx.warn("기존 정비구역 자료가 없어 중복 여부를 확인하지 못했습니다.")
            return
        full_ratio = ctx.criteria.assumption_value("ZONE_OVERLAP_FULL_RATIO")
        tree = STRtree([z.geom for z in zones])

        for block in ctx.blocks():
            pieces, overlaps = [], []
            for i in sorted(tree.query(block.geom, predicate="intersects").tolist()):
                piece = zones[i].geom.intersection(block.geom)
                ratio = piece.area / block.area
                if ratio < MIN_OVERLAP_RATIO:
                    continue
                pieces.append(piece)
                zone = zones[i]
                overlaps.append({"zone": zone.name, "stage": zone.attrs["stage"], "overlap_ratio": round(ratio, 3)})
                ctx.session.add(
                    m.ZoneOverlap(
                        run_id=ctx.run.id,
                        block_id=block.id,
                        zone_key=zone.key,
                        zone_name=zone.name,
                        project_type=zone.attrs["project_type"],
                        stage=zone.attrs["stage"],
                        stage_date=zone.attrs["stage_date"],
                        overlap_ratio=ratio,
                        overlap_kind="FULL" if ratio >= full_ratio else "PARTIAL",
                    )
                )
            total = unary_union(pieces).area / block.area if pieces else 0.0
            ctx.add_metric(
                block,
                "ZONE_OVERLAP_RATIO",
                total,
                "비율",
                m.CONFIRMED,
                {"zones": overlaps, "full_ratio": full_ratio, "formula": "기존 정비구역과 겹치는 면적 / 블록 면적"},
            )

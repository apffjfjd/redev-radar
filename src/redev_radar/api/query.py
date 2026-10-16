"""Query API — 저장된 분석 결과를 읽기만 한다. 분석을 직접 수행하지 않는다."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from shapely.geometry import box, mapping
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog
from .. import models as m
from .deps import get_session

router = APIRouter(tags=["query"])


def _resolve_run(session: Session, run_id: int | None) -> m.AnalysisRun:
    """run_id가 없으면 가장 최근에 완료된 실행을 쓴다. 진행 중이거나 실패한 실행은 제공하지 않는다."""
    if run_id is None:
        run = session.scalar(
            select(m.AnalysisRun).where(m.AnalysisRun.status == "COMPLETED").order_by(m.AnalysisRun.id.desc()).limit(1)
        )
        if run is None:
            raise HTTPException(404, "완료된 분석 실행이 없습니다. 먼저 분석을 실행하세요.")
        return run
    run = session.get(m.AnalysisRun, run_id)
    if run is None or run.status != "COMPLETED":
        raise HTTPException(404, "완료된 분석 실행을 찾을 수 없습니다.")
    return run


def _run_summary(run: m.AnalysisRun) -> dict:
    return {
        "id": run.id,
        "base_date": run.base_date.isoformat(),
        "status": run.status,
        "started_at": run.started_at.isoformat(timespec="seconds"),
        "finished_at": run.finished_at.isoformat(timespec="seconds") if run.finished_at else None,
        "stages": run.stages,
        "warnings": run.warnings,
        "note": run.note,
    }


def _feature(geom, properties: dict) -> dict:
    return {"type": "Feature", "geometry": mapping(geom), "properties": properties}


def _collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _snapshot_rows(session: Session, run: m.AnalysisRun, model, dataset: str):
    ids = run.snapshot_ids.get(dataset, [])
    return session.scalars(select(model).where(model.snapshot_id.in_(ids)).order_by(model.id)).all() if ids else []


@router.get("/config")
def get_config(request: Request) -> dict:
    settings = request.app.state.settings
    return {"tile_url": settings.tile_url, "tile_attribution": settings.tile_attribution, "notices": catalog.NOTICES}


@router.get("/runs")
def list_runs(session: Session = Depends(get_session)) -> list[dict]:
    runs = session.scalars(select(m.AnalysisRun).order_by(m.AnalysisRun.id.desc()).limit(50))
    return [_run_summary(r) for r in runs]


@router.get("/meta")
def get_meta(run_id: int | None = None, session: Session = Depends(get_session)) -> dict:
    """화면 구성에 필요한 실행 정보와 코드 목록."""
    run = _resolve_run(session, run_id)
    variants = session.scalars(
        select(m.CandidateBlock.variant).where(m.CandidateBlock.run_id == run.id).distinct()
    ).all()
    widths = {
        a["qualifier"]: a["value"] for a in run.criteria_snapshot.get("assumptions", []) if a["key"] == "BLOCK_ROAD_WIDTH_M"
    }
    snapshots = session.scalars(
        select(m.CollectionSnapshot).where(
            m.CollectionSnapshot.id.in_([i for ids in run.snapshot_ids.values() for i in ids])
        )
    ).all()
    return {
        "run": _run_summary(run),
        "variants": [
            {"code": v, "road_width_m": widths.get(v)} for v in sorted(variants, key=lambda v: widths.get(v, 0))
        ],
        "project_types": run.criteria_snapshot.get("project_types", []),
        "metrics": catalog.metric_catalog(),
        "items": catalog.ITEMS,
        "eligibility_status": catalog.ELIGIBILITY_STATUS,
        "value_kinds": catalog.VALUE_KINDS,
        "verifications": catalog.VERIFICATIONS,
        "sources": sorted({s.source for s in snapshots}),
    }


@router.get("/blocks")
def list_blocks(
    run_id: int | None = None,
    variant: str | None = None,
    bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat"),
    session: Session = Depends(get_session),
) -> dict:
    run = _resolve_run(session, run_id)
    query = select(m.CandidateBlock).where(m.CandidateBlock.run_id == run.id).order_by(m.CandidateBlock.id)
    if variant:
        query = query.where(m.CandidateBlock.variant == variant)
    blocks = session.scalars(query).all()
    if bbox:
        try:
            view = box(*[float(v) for v in bbox.split(",")])
        except (TypeError, ValueError):
            raise HTTPException(422, "bbox는 'minLon,minLat,maxLon,maxLat' 형식이어야 합니다.")
        blocks = [b for b in blocks if b.geom.intersects(view)]
    ids = {b.id for b in blocks}

    metrics: dict[int, dict] = {}
    for block_id, code, value in session.execute(
        select(m.BlockMetric.block_id, m.BlockMetric.code, m.BlockMetric.value).where(m.BlockMetric.run_id == run.id)
    ):
        if block_id in ids:
            metrics.setdefault(block_id, {})[code] = value
    eligibility: dict[int, dict] = {}
    for block_id, project_type, status in session.execute(
        select(m.EligibilityResult.block_id, m.EligibilityResult.project_type, m.EligibilityResult.status).where(
            m.EligibilityResult.run_id == run.id
        )
    ):
        if block_id in ids:
            eligibility.setdefault(block_id, {})[project_type] = status

    return _collection(
        [
            _feature(
                b.geom,
                {
                    "id": b.id,
                    "block_key": b.block_key,
                    "variant": b.variant,
                    "region_key": b.region_key,
                    "parcel_count": b.parcel_count,
                    "metrics": metrics.get(b.id, {}),
                    "eligibility": eligibility.get(b.id, {}),
                },
            )
            for b in blocks
        ]
    )


@router.get("/blocks/{block_id}")
def get_block(block_id: int, session: Session = Depends(get_session)) -> dict:
    block = session.get(m.CandidateBlock, block_id)
    if block is None:
        raise HTTPException(404, "후보 블록을 찾을 수 없습니다.")
    run = _resolve_run(session, block.run_id)

    metrics = session.scalars(select(m.BlockMetric).where(m.BlockMetric.block_id == block.id).order_by(m.BlockMetric.id))
    eligibility = session.scalars(
        select(m.EligibilityResult).where(m.EligibilityResult.block_id == block.id).order_by(m.EligibilityResult.id)
    )
    overlaps = session.scalars(
        select(m.ZoneOverlap).where(m.ZoneOverlap.block_id == block.id).order_by(m.ZoneOverlap.overlap_ratio.desc())
    )
    # 같은 지역의 다른 후보 경계 (경계 설정에 따른 지표 차이 비교용)
    alternatives = session.scalars(
        select(m.CandidateBlock)
        .where(m.CandidateBlock.run_id == block.run_id)
        .where(m.CandidateBlock.region_key == block.region_key)
        .where(m.CandidateBlock.id != block.id)
        .order_by(m.CandidateBlock.id)
    ).all()
    alt_metrics: dict[int, dict] = {}
    for alt_id, code, value in session.execute(
        select(m.BlockMetric.block_id, m.BlockMetric.code, m.BlockMetric.value)
        .where(m.BlockMetric.block_id.in_([a.id for a in alternatives]))
        .where(m.BlockMetric.code.in_(["BLOCK_AREA_M2", "AGED_COUNT_RATIO", "FAR_HEADROOM_BASE"]))
    ):
        alt_metrics.setdefault(alt_id, {})[code] = value

    return {
        "id": block.id,
        "block_key": block.block_key,
        "variant": block.variant,
        "region_key": block.region_key,
        "jurisdiction": block.jurisdiction,
        "area_m2": block.area_m2,
        "parcel_area_m2": block.parcel_area_m2,
        "parcel_count": block.parcel_count,
        "geometry": mapping(block.geom),
        "run": {"id": run.id, "base_date": run.base_date.isoformat()},
        "metrics": [
            {
                "code": x.code,
                "value": x.value,
                "unit": x.unit,
                "value_kind": x.value_kind,
                "verification": x.verification,
                "basis": x.basis,
            }
            for x in metrics
        ],
        "eligibility": [
            {"project_type": e.project_type, "status": e.status, "items": e.items, "summary": e.summary}
            for e in eligibility
        ],
        "zone_overlaps": [
            {
                "zone_key": o.zone_key,
                "zone_name": o.zone_name,
                "project_type": o.project_type,
                "stage": o.stage,
                "stage_date": o.stage_date.isoformat() if o.stage_date else None,
                "overlap_ratio": o.overlap_ratio,
                "overlap_kind": o.overlap_kind,
            }
            for o in overlaps
        ],
        "alternatives": [
            {"id": a.id, "block_key": a.block_key, "variant": a.variant, "metrics": alt_metrics.get(a.id, {})}
            for a in alternatives
        ],
    }


@router.get("/blocks/{block_id}/transactions")
def get_block_transactions(block_id: int, session: Session = Depends(get_session)) -> list[dict]:
    if session.get(m.CandidateBlock, block_id) is None:
        raise HTTPException(404, "후보 블록을 찾을 수 없습니다.")
    rows = session.execute(
        select(m.Transaction, m.BlockTransaction.match_method)
        .join(m.BlockTransaction, m.BlockTransaction.transaction_id == m.Transaction.id)
        .where(m.BlockTransaction.block_id == block_id)
        .order_by(m.Transaction.deal_date.desc(), m.Transaction.id)
    )
    return [
        {
            "deal_date": tx.deal_date.isoformat(),
            "house_type": tx.house_type,
            "building_name": tx.building_name,
            "umd_nm": tx.umd_nm,
            "jibun": tx.jibun,
            "exclusive_area": tx.exclusive_area,
            "land_share_area": tx.land_share_area,
            "floor": tx.floor,
            "build_year": tx.build_year,
            "price_krw": tx.price_krw,
            "price_per_m2": round(tx.price_krw / tx.exclusive_area) if tx.exclusive_area else None,
            "match_method": method,
        }
        for tx, method in rows
    ]


@router.get("/layers/stations")
def layer_stations(run_id: int | None = None, session: Session = Depends(get_session)) -> dict:
    run = _resolve_run(session, run_id)
    radius = next(
        (a["value"] for a in run.criteria_snapshot.get("assumptions", []) if a["key"] == "STATION_AREA_RADIUS_M"), None
    )
    return _collection(
        [
            _feature(s.geom, {"station_key": s.station_key, "name": s.name, "lines": s.lines, "radius_m": radius})
            for s in _snapshot_rows(session, run, m.Station, m.DS_STATIONS)
        ]
    )


@router.get("/layers/redevelopment-zones")
def layer_zones(run_id: int | None = None, session: Session = Depends(get_session)) -> dict:
    run = _resolve_run(session, run_id)
    return _collection(
        [
            _feature(
                z.geom,
                {
                    "zone_key": z.zone_key,
                    "name": z.name,
                    "project_type": z.project_type,
                    "stage": z.stage,
                    "stage_date": z.stage_date.isoformat() if z.stage_date else None,
                },
            )
            for z in _snapshot_rows(session, run, m.RedevelopmentZone, m.DS_REDEVELOPMENT_ZONES)
        ]
    )


@router.get("/layers/zoning")
def layer_zoning(run_id: int | None = None, session: Session = Depends(get_session)) -> dict:
    run = _resolve_run(session, run_id)
    return _collection(
        [
            _feature(z.geom, {"category": z.category, "zoning_code": z.zoning_code, "zoning_name": z.zoning_name})
            for z in _snapshot_rows(session, run, m.ZoningArea, m.DS_ZONING_AREAS)
        ]
    )

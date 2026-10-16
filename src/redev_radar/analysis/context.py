"""분석 단계가 공유하는 실행 컨텍스트.

분석 단계는 외부 출처를 호출하지 않고, 데이터 계층이 적재한 자료 중 분석 기준일에
유효한 스냅샷만 읽는다. 단계 사이의 결과 전달은 저장소를 통해서만 이루어진다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import cached_property

from shapely import STRtree
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..criteria import CriteriaSet
from ..geo import to_metric


def select_snapshots(session: Session, base_date: date) -> dict[str, list[int]]:
    """(dataset, scope)별로 기준일 이전에 공개된 최신 스냅샷을 고른다.

    기준일 이후의 자료는 읽지 않으므로 과거 기준일 분석에 미래 정보가 섞이지 않는다.
    """
    rows = session.scalars(
        select(m.CollectionSnapshot)
        .where(m.CollectionSnapshot.as_of_date <= base_date)
        .where(m.CollectionSnapshot.status == "LOADED")
        .order_by(m.CollectionSnapshot.as_of_date, m.CollectionSnapshot.collected_at, m.CollectionSnapshot.id)
    )
    latest: dict[tuple[str, str], int] = {}
    for snapshot in rows:
        latest[(snapshot.dataset, snapshot.scope)] = snapshot.id
    selected: dict[str, list[int]] = {}
    for (dataset, _scope), snapshot_id in sorted(latest.items()):
        selected.setdefault(dataset, []).append(snapshot_id)
    return selected


@dataclass
class ParcelRec:
    pnu: str
    sgg_cd: str | None
    umd_nm: str | None
    jibun: str | None
    jimok: str | None
    area: float
    geom: BaseGeometry


@dataclass
class BuildingRec:
    key: str
    pnu: str | None
    approval: date | None
    structure: str | None
    main_use: str | None
    gross_area: float | None
    far_area: float | None
    households: int | None
    semi_basement: bool | None
    geom: BaseGeometry
    point: Point


@dataclass
class RoadRec:
    key: str
    width: float | None
    geom: BaseGeometry


@dataclass
class StationRec:
    key: str
    name: str
    lines: list[str]
    geom: Point
    entrances: list[Point] = field(default_factory=list)


@dataclass
class AreaRec:
    """용도지역·기존 정비구역처럼 면으로 표현되는 자료."""

    key: str
    name: str
    category: str | None
    geom: BaseGeometry
    attrs: dict = field(default_factory=dict)


class SpatialData:
    """분석 실행에 고정된 스냅샷의 자료를 미터 좌표계로 읽어 둔다."""

    def __init__(self, session: Session, snapshot_ids: dict[str, list[int]]):
        self._session = session
        self._ids = snapshot_ids

    def _rows(self, model, dataset: str):
        ids = self._ids.get(dataset, [])
        if not ids:
            return []
        return self._session.scalars(select(model).where(model.snapshot_id.in_(ids)).order_by(model.id)).all()

    @cached_property
    def parcels(self) -> list[ParcelRec]:
        records = {}
        for r in self._rows(m.Parcel, m.DS_PARCELS):
            geom = to_metric(r.geom)
            records[r.pnu] = ParcelRec(r.pnu, r.sgg_cd, r.umd_nm, r.jibun, r.jimok, r.area_m2 or geom.area, geom)
        return list(records.values())

    @cached_property
    def buildings(self) -> list[BuildingRec]:
        records = []
        for r in self._rows(m.Building, m.DS_BUILDINGS):
            geom = to_metric(r.geom)
            records.append(
                BuildingRec(
                    key=r.building_key,
                    pnu=r.pnu,
                    approval=r.use_approval_date,
                    structure=r.structure,
                    main_use=r.main_use,
                    gross_area=r.gross_floor_area,
                    far_area=r.far_floor_area,
                    households=r.household_count,
                    semi_basement=r.has_semi_basement,
                    geom=geom,
                    point=geom.representative_point(),
                )
            )
        return records

    @cached_property
    def roads(self) -> list[RoadRec]:
        return [RoadRec(r.road_key, r.width_m, to_metric(r.geom)) for r in self._rows(m.Road, m.DS_ROADS)]

    @cached_property
    def stations(self) -> list[StationRec]:
        stations = {
            r.station_key: StationRec(r.station_key, r.name, list(r.lines or []), to_metric(r.geom))
            for r in self._rows(m.Station, m.DS_STATIONS)
        }
        for e in self._rows(m.StationEntrance, m.DS_STATION_ENTRANCES):
            if e.station_key in stations:
                stations[e.station_key].entrances.append(to_metric(e.geom))
        return list(stations.values())

    @cached_property
    def zoning_areas(self) -> list[AreaRec]:
        return [
            AreaRec(r.zoning_code, r.zoning_name, r.category, to_metric(r.geom))
            for r in self._rows(m.ZoningArea, m.DS_ZONING_AREAS)
        ]

    @cached_property
    def redevelopment_zones(self) -> list[AreaRec]:
        return [
            AreaRec(
                r.zone_key,
                r.name,
                None,
                to_metric(r.geom),
                {"project_type": r.project_type, "stage": r.stage, "stage_date": r.stage_date},
            )
            for r in self._rows(m.RedevelopmentZone, m.DS_REDEVELOPMENT_ZONES)
        ]

    @cached_property
    def business_districts(self) -> list[AreaRec]:
        return [
            AreaRec(r.name, r.name, None, to_metric(r.geom))
            for r in self._rows(m.BusinessDistrict, m.DS_BUSINESS_DISTRICTS)
        ]

    @cached_property
    def transactions(self) -> list[m.Transaction]:
        return self._rows(m.Transaction, m.DS_TRANSACTIONS)


@dataclass
class BlockView:
    id: int
    key: str
    variant: str
    jurisdiction: str | None
    geom: BaseGeometry
    area: float
    parcel_area: float
    pnus: frozenset[str]


class RunContext:
    def __init__(self, session: Session, run: m.AnalysisRun, criteria: CriteriaSet, data: SpatialData):
        self.session = session
        self.run = run
        self.criteria = criteria
        self.data = data
        self.base_date: date = run.base_date
        self._blocks: list[BlockView] | None = None

    # ------------------------------------------------------------ 블록

    def blocks(self) -> list[BlockView]:
        """이 실행에서 저장된 후보 블록을 읽는다."""
        if self._blocks is None:
            rows = self.session.scalars(
                select(m.CandidateBlock).where(m.CandidateBlock.run_id == self.run.id).order_by(m.CandidateBlock.id)
            )
            self._blocks = [
                BlockView(
                    id=r.id,
                    key=r.block_key,
                    variant=r.variant,
                    jurisdiction=r.jurisdiction,
                    geom=to_metric(r.geom),
                    area=r.area_m2,
                    parcel_area=r.parcel_area_m2,
                    pnus=frozenset(r.parcel_pnus),
                )
                for r in rows
            ]
        return self._blocks

    @cached_property
    def _parcel_by_pnu(self) -> dict[str, ParcelRec]:
        return {p.pnu: p for p in self.data.parcels}

    @cached_property
    def _buildings_by_pnu(self) -> dict[str, list[BuildingRec]]:
        index: dict[str, list[BuildingRec]] = {}
        for b in self.data.buildings:
            if b.pnu and b.pnu in self._parcel_by_pnu:
                index.setdefault(b.pnu, []).append(b)
        return index

    @cached_property
    def _unlinked_buildings(self) -> tuple[list[BuildingRec], STRtree]:
        """필지와 연결되지 않은 건축물. 위치로 블록에 배정한다."""
        unlinked = [b for b in self.data.buildings if not (b.pnu and b.pnu in self._parcel_by_pnu)]
        return unlinked, STRtree([b.point for b in unlinked])

    def block_parcels(self, block: BlockView) -> list[ParcelRec]:
        return [self._parcel_by_pnu[pnu] for pnu in sorted(block.pnus) if pnu in self._parcel_by_pnu]

    def block_buildings(self, block: BlockView) -> list[BuildingRec]:
        buildings = [b for pnu in sorted(block.pnus) for b in self._buildings_by_pnu.get(pnu, [])]
        unlinked, tree = self._unlinked_buildings
        if unlinked:
            buildings.extend(unlinked[i] for i in sorted(tree.query(block.geom, predicate="contains")))
        return buildings

    # ------------------------------------------------------------ 결과 기록

    def add_metric(
        self,
        block: BlockView,
        code: str,
        value: float | None,
        unit: str | None,
        value_kind: str,
        basis: dict,
        verification: str = m.PRELIMINARY,
    ) -> None:
        self.session.add(
            m.BlockMetric(
                run_id=self.run.id,
                block_id=block.id,
                code=code,
                value=value,
                unit=unit,
                value_kind=value_kind,
                verification=verification,
                basis=basis,
            )
        )

    def metric_values(self, codes: list[str]) -> dict[int, dict[str, float | None]]:
        """앞 단계가 저장한 지표를 {block_id: {code: value}}로 읽는다."""
        self.session.flush()
        rows = self.session.execute(
            select(m.BlockMetric.block_id, m.BlockMetric.code, m.BlockMetric.value)
            .where(m.BlockMetric.run_id == self.run.id)
            .where(m.BlockMetric.code.in_(codes))
        )
        values: dict[int, dict[str, float | None]] = {}
        for block_id, code, value in rows:
            values.setdefault(block_id, {})[code] = value
        return values

    def warn(self, message: str) -> None:
        if message not in self.run.warnings:
            self.run.warnings = [*self.run.warnings, message]

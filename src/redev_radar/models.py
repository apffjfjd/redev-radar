"""저장 모델 (BD-0004 주요 Domain).

- 수집 자료는 덮어쓰지 않고 수집 스냅샷(CollectionSnapshot) 단위로 누적한다.
- 분석 결과는 분석 실행(AnalysisRun) 단위로 묶는다.
- 모든 산출값은 확정/추정 구분(value_kind), 예비/법적 검증 구분(verification), 산출 근거(basis)를 갖는다.
"""

from __future__ import annotations

from datetime import date, datetime

from shapely.geometry.base import BaseGeometry
from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, Geometry

# 산출값 구분
CONFIRMED = "CONFIRMED"  # 확정값: 공적 자료를 그대로 옮기거나 단순 집계한 값
ESTIMATED = "ESTIMATED"  # 추정값: 가정·근사가 개입된 값
PRELIMINARY = "PRELIMINARY"  # 예비 분석 결과
LEGAL_VERIFIED = "LEGAL_VERIFIED"  # 법적 검증 결과 (MVP에서는 산출하지 않는다)

# 데이터셋 이름
DS_ADMIN_AREAS = "admin_areas"
DS_PARCELS = "parcels"
DS_BUILDINGS = "buildings"
DS_ROADS = "roads"
DS_STATIONS = "stations"
DS_STATION_ENTRANCES = "station_entrances"
DS_ZONING_AREAS = "zoning_areas"
DS_REDEVELOPMENT_ZONES = "redevelopment_zones"
DS_BUSINESS_DISTRICTS = "business_districts"
DS_TRANSACTIONS = "transactions"


# ---------------------------------------------------------------- 수집


class CollectionSnapshot(Base):
    """한 번의 수집 결과. (dataset, scope)별로 기준일에 유효한 최신 스냅샷을 분석에 사용한다."""

    __tablename__ = "collection_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50))
    dataset: Mapped[str] = mapped_column(String(50))
    # 스냅샷이 담는 범위. 같은 (dataset, scope)의 새 스냅샷이 이전 것을 대체한다.
    scope: Mapped[str] = mapped_column(String(100), default="ALL")
    # 이 자료가 공개·유효했던 기준일. 이 날짜보다 이른 기준일의 분석에는 쓰이지 않는다.
    as_of_date: Mapped[date] = mapped_column(Date)
    collected_at: Mapped[datetime] = mapped_column(DateTime)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="LOADED")
    field_mapping: Mapped[dict | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_snapshot_lookup", "dataset", "scope", "as_of_date"),)


class RawRecord(Base):
    """수집 원본. 정규화 규칙이 바뀌어도 다시 정규화할 수 있도록 보관한다."""

    __tablename__ = "raw_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("collection_snapshot.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


# ---------------------------------------------------------------- 공간·건축물


class AdminArea(Base):
    __tablename__ = "admin_area"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("collection_snapshot.id"), index=True)
    code: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(100))
    geom: Mapped[BaseGeometry] = mapped_column(Geometry)


class Parcel(Base):
    __tablename__ = "parcel"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("collection_snapshot.id"), index=True)
    pnu: Mapped[str] = mapped_column(String(19), index=True)
    sgg_cd: Mapped[str | None] = mapped_column(String(5))
    umd_nm: Mapped[str | None] = mapped_column(String(50))
    jibun: Mapped[str | None] = mapped_column(String(20))
    jimok: Mapped[str | None] = mapped_column(String(10))
    area_m2: Mapped[float | None] = mapped_column(Float)
    geom: Mapped[BaseGeometry] = mapped_column(Geometry)


class Building(Base):
    __tablename__ = "building"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("collection_snapshot.id"), index=True)
    building_key: Mapped[str] = mapped_column(String(50))
    pnu: Mapped[str | None] = mapped_column(String(19), index=True)
    # 사용승인일 누락은 None으로 남기고 추정하지 않는다.
    use_approval_date: Mapped[date | None] = mapped_column(Date)
    structure: Mapped[str | None] = mapped_column(String(50))
    main_use: Mapped[str | None] = mapped_column(String(50))
    gross_floor_area: Mapped[float | None] = mapped_column(Float)
    far_floor_area: Mapped[float | None] = mapped_column(Float)  # 용적률 산정용 연면적
    ground_floors: Mapped[int | None] = mapped_column(Integer)
    underground_floors: Mapped[int | None] = mapped_column(Integer)
    household_count: Mapped[int | None] = mapped_column(Integer)
    has_semi_basement: Mapped[bool | None] = mapped_column(Boolean)
    geom: Mapped[BaseGeometry] = mapped_column(Geometry)


class Road(Base):
    __tablename__ = "road"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("collection_snapshot.id"), index=True)
    road_key: Mapped[str] = mapped_column(String(50))
    name: Mapped[str | None] = mapped_column(String(100))
    width_m: Mapped[float | None] = mapped_column(Float)
    geom: Mapped[BaseGeometry] = mapped_column(Geometry)


class Station(Base):
    __tablename__ = "station"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("collection_snapshot.id"), index=True)
    station_key: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(100))
    lines: Mapped[list] = mapped_column(JSON, default=list)
    geom: Mapped[BaseGeometry] = mapped_column(Geometry)


class StationEntrance(Base):
    __tablename__ = "station_entrance"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("collection_snapshot.id"), index=True)
    station_key: Mapped[str] = mapped_column(String(50))
    entrance_no: Mapped[str | None] = mapped_column(String(20))
    geom: Mapped[BaseGeometry] = mapped_column(Geometry)


class ZoningArea(Base):
    """용도지역(USE_ZONE), 개발제한(DEV_RESTRICTION), 도시계획시설(PLANNED_FACILITY), 그 밖의 지구·구역(OVERLAY)."""

    __tablename__ = "zoning_area"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("collection_snapshot.id"), index=True)
    category: Mapped[str] = mapped_column(String(20))
    zoning_code: Mapped[str] = mapped_column(String(20))
    zoning_name: Mapped[str] = mapped_column(String(100))
    geom: Mapped[BaseGeometry] = mapped_column(Geometry)


class RedevelopmentZone(Base):
    __tablename__ = "redevelopment_zone"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("collection_snapshot.id"), index=True)
    zone_key: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    project_type: Mapped[str | None] = mapped_column(String(100))
    stage: Mapped[str | None] = mapped_column(String(100))
    stage_date: Mapped[date | None] = mapped_column(Date)
    geom: Mapped[BaseGeometry] = mapped_column(Geometry)


class BusinessDistrict(Base):
    __tablename__ = "business_district"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("collection_snapshot.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    geom: Mapped[BaseGeometry] = mapped_column(Geometry)


# ---------------------------------------------------------------- 가격


class Transaction(Base):
    __tablename__ = "real_transaction"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("collection_snapshot.id"), index=True)
    deal_type: Mapped[str] = mapped_column(String(10), default="SALE")
    deal_date: Mapped[date] = mapped_column(Date)
    pnu: Mapped[str | None] = mapped_column(String(19))
    sgg_cd: Mapped[str | None] = mapped_column(String(5))
    umd_nm: Mapped[str | None] = mapped_column(String(50))
    jibun: Mapped[str | None] = mapped_column(String(20))
    house_type: Mapped[str | None] = mapped_column(String(20))
    building_name: Mapped[str | None] = mapped_column(String(100))
    exclusive_area: Mapped[float | None] = mapped_column(Float)
    land_share_area: Mapped[float | None] = mapped_column(Float)
    floor: Mapped[int | None] = mapped_column(Integer)
    build_year: Mapped[int | None] = mapped_column(Integer)
    price_krw: Mapped[int] = mapped_column(BigInteger)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------- 기준·가정


class ProjectType(Base):
    __tablename__ = "project_type"

    code: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class RegulationCriterion(Base):
    """법령·조례 기준값. 시행일·지자체·사업유형별로 이력을 쌓는다."""

    __tablename__ = "regulation_criterion"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 시도코드(2자리), 시군구코드(5자리) 또는 "*"(전국). 더 구체적인 지자체 기준이 우선한다.
    jurisdiction: Mapped[str] = mapped_column(String(10), default="*")
    # 사업방식 코드 또는 "*"(사업방식과 무관한 공통 기준)
    project_type: Mapped[str] = mapped_column(String(30), default="*")
    item_code: Mapped[str] = mapped_column(String(50))
    # 용도지역·구조 등 항목 내 세부 구분. 없으면 빈 문자열.
    qualifier: Mapped[str] = mapped_column(String(50), default="")
    # REQUIRED(필수 요건) / OPTIONAL(선택 요건) / PARAM(판정에 쓰이는 기준 수치)
    requirement: Mapped[str] = mapped_column(String(10))
    operator: Mapped[str | None] = mapped_column(String(2))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(20))
    law_name: Mapped[str | None] = mapped_column(String(200))
    law_article: Mapped[str | None] = mapped_column(String(200))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    # 법령 원문과 대조해 확인한 값인지 여부
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class Assumption(Base):
    """분석 가정값. 시나리오(LOW/BASE/HIGH) 또는 단일값("-")으로 관리한다."""

    __tablename__ = "assumption"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50))
    scenario: Mapped[str] = mapped_column(String(10), default="-")
    qualifier: Mapped[str] = mapped_column(String(50), default="")
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(20))
    source: Mapped[str | None] = mapped_column(String(200))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


# ---------------------------------------------------------------- 분석 결과


class AnalysisRun(Base):
    __tablename__ = "analysis_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    base_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    # 사용한 데이터 버전: {dataset: [snapshot_id, ...]}
    snapshot_ids: Mapped[dict] = mapped_column(JSON, default=dict)
    # 실행 시작 시점에 고정한 기준값·가정값 사본
    criteria_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    stages: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)


class CandidateBlock(Base):
    __tablename__ = "candidate_block"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), index=True)
    block_key: Mapped[str] = mapped_column(String(40))
    # 후보 경계 생성 규칙. 같은 지역(region_key)에 variant별로 복수의 후보 경계가 존재한다.
    variant: Mapped[str] = mapped_column(String(20))
    region_key: Mapped[str | None] = mapped_column(String(40), index=True)
    jurisdiction: Mapped[str | None] = mapped_column(String(10))
    area_m2: Mapped[float] = mapped_column(Float)  # 구역 면적 (내부 도로 포함)
    parcel_area_m2: Mapped[float] = mapped_column(Float)  # 필지 면적 합
    parcel_count: Mapped[int] = mapped_column(Integer)
    parcel_pnus: Mapped[list] = mapped_column(JSON, default=list)
    geom: Mapped[BaseGeometry] = mapped_column(Geometry)


class BlockMetric(Base):
    __tablename__ = "block_metric"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), index=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("candidate_block.id"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    # 산출할 수 없으면 None으로 두고 사유를 basis에 남긴다.
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(20))
    value_kind: Mapped[str] = mapped_column(String(20))
    verification: Mapped[str] = mapped_column(String(20))
    basis: Mapped[dict] = mapped_column(JSON, default=dict)


class EligibilityResult(Base):
    __tablename__ = "eligibility_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), index=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("candidate_block.id"), index=True)
    project_type: Mapped[str] = mapped_column(String(30))
    # PASS / FAIL / INDETERMINATE(자료 부족) / NO_CRITERIA(기준 미등록)
    status: Mapped[str] = mapped_column(String(20))
    items: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class ZoneOverlap(Base):
    __tablename__ = "zone_overlap"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), index=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("candidate_block.id"), index=True)
    zone_key: Mapped[str] = mapped_column(String(50))
    zone_name: Mapped[str] = mapped_column(String(200))
    project_type: Mapped[str | None] = mapped_column(String(100))
    stage: Mapped[str | None] = mapped_column(String(100))
    stage_date: Mapped[date | None] = mapped_column(Date)
    overlap_ratio: Mapped[float] = mapped_column(Float)  # 블록 면적 대비
    overlap_kind: Mapped[str] = mapped_column(String(10))  # FULL / PARTIAL


class BlockTransaction(Base):
    __tablename__ = "block_transaction"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_run.id"), index=True)
    block_id: Mapped[int] = mapped_column(ForeignKey("candidate_block.id"), index=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("real_transaction.id"))
    match_method: Mapped[str] = mapped_column(String(10))  # PNU / JIBUN

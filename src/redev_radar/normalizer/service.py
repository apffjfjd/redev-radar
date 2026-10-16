"""출처별로 형태가 다른 원본 레코드를 공통 스키마 모델로 바꾼다.

누락된 항목은 추정해 채우지 않고 None(누락)으로 남긴다. 필수 항목이 없거나
도형이 유효하지 않은 레코드는 건너뛰고 건수를 스냅샷에 기록한다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Callable

import shapely
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from sqlalchemy.orm import Session

from .. import models as m
from ..geo import to_metric

# 용도지역 명칭 → 기준값 조회용 코드
_ZONING_CODES = [
    ("제1종전용주거", "ER1"),
    ("제2종전용주거", "ER2"),
    ("제1종일반주거", "GR1"),
    ("제2종일반주거", "GR2"),
    ("제3종일반주거", "GR3"),
    ("준주거", "SEMI_R"),
    ("근린상업", "NC"),
    ("일반상업", "GC"),
    ("준공업", "SEMI_I"),
]


class SkipRecord(Exception):
    """정규화할 수 없는 레코드."""


def zoning_code_of(name: str) -> str:
    compact = name.replace(" ", "")
    for keyword, code in _ZONING_CODES:
        if keyword in compact:
            return code
    return "OTHER"


# ---------------------------------------------------------------- 값 변환


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float(value) -> float | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _int(value) -> int | None:
    number = _float(value)
    return None if number is None else int(number)


def _bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    text = _text(value)
    if text is None:
        return None
    if text.lower() in ("1", "true", "y", "yes", "t"):
        return True
    if text.lower() in ("0", "false", "n", "no", "f"):
        return False
    return None


def _date(value) -> date | None:
    text = _text(value)
    if text is None:
        return None
    digits = text.replace("-", "").replace(".", "").replace("/", "")
    try:
        return datetime.strptime(digits[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _require(value, field: str):
    if value is None:
        raise SkipRecord(f"필수 항목 누락: {field}")
    return value


def _geometry(record: dict, props: dict, allowed: tuple[str, ...]) -> BaseGeometry:
    raw = record.get("geometry")
    if raw:
        geom = shape(raw)
    else:
        lon, lat = _float(props.get("lon")), _float(props.get("lat"))
        if lon is None or lat is None:
            raise SkipRecord("도형 없음")
        geom = Point(lon, lat)
    if geom.is_empty:
        raise SkipRecord("빈 도형")
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    if geom.geom_type not in allowed:
        raise SkipRecord(f"허용되지 않는 도형 유형: {geom.geom_type}")
    return geom


_POLYGON = ("Polygon", "MultiPolygon")
_LINE = ("LineString", "MultiLineString")
_POINT = ("Point",)


def jibun_from_pnu(pnu: str) -> str | None:
    """PNU(19자리) 뒤 9자리(산 구분 1 + 본번 4 + 부번 4)에서 지번 문자열을 만든다."""
    if len(pnu) != 19 or not pnu.isdigit():
        return None
    mountain, main, sub = pnu[10], int(pnu[11:15]), int(pnu[15:19])
    jibun = f"{main}-{sub}" if sub else str(main)
    return f"산{jibun}" if mountain == "2" else jibun


# ---------------------------------------------------------------- 데이터셋별 정규화


def _admin_area(snapshot_id: int, record: dict, p: dict, seq: int):
    return m.AdminArea(
        snapshot_id=snapshot_id,
        code=_require(_text(p.get("code")), "code"),
        name=_text(p.get("name")) or "",
        geom=_geometry(record, p, _POLYGON),
    )


def _parcel(snapshot_id: int, record: dict, p: dict, seq: int):
    pnu = _require(_text(p.get("pnu")), "pnu")
    geom = _geometry(record, p, _POLYGON)
    return m.Parcel(
        snapshot_id=snapshot_id,
        pnu=pnu,
        sgg_cd=_text(p.get("sgg_cd")) or (pnu[:5] if len(pnu) >= 5 else None),
        umd_nm=_text(p.get("umd_nm")),
        jibun=_text(p.get("jibun")) or jibun_from_pnu(pnu),
        jimok=_text(p.get("jimok")),
        area_m2=_float(p.get("area_m2")) or to_metric(geom).area,
        geom=geom,
    )


def _building(snapshot_id: int, record: dict, p: dict, seq: int):
    pnu = _text(p.get("pnu"))
    return m.Building(
        snapshot_id=snapshot_id,
        building_key=_text(p.get("building_key")) or f"{pnu or 'B'}-{seq}",
        pnu=pnu,
        use_approval_date=_date(p.get("use_approval_date")),
        structure=_text(p.get("structure")),
        main_use=_text(p.get("main_use")),
        gross_floor_area=_float(p.get("gross_floor_area")),
        far_floor_area=_float(p.get("far_floor_area")),
        ground_floors=_int(p.get("ground_floors")),
        underground_floors=_int(p.get("underground_floors")),
        household_count=_int(p.get("household_count")),
        has_semi_basement=_bool(p.get("has_semi_basement")),
        geom=_geometry(record, p, _POLYGON + _POINT),
    )


def _road(snapshot_id: int, record: dict, p: dict, seq: int):
    return m.Road(
        snapshot_id=snapshot_id,
        road_key=_text(p.get("road_key")) or f"R-{seq}",
        name=_text(p.get("name")),
        width_m=_float(p.get("width_m")),
        geom=_geometry(record, p, _LINE),
    )


def _lines(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = _text(value)
    return [part.strip() for part in text.split(",") if part.strip()] if text else []


def _station(snapshot_id: int, record: dict, p: dict, seq: int):
    name = _require(_text(p.get("name")), "name")
    return m.Station(
        snapshot_id=snapshot_id,
        station_key=_text(p.get("station_key")) or name,
        name=name,
        lines=_lines(p.get("lines")),
        geom=_geometry(record, p, _POINT),
    )


def _station_entrance(snapshot_id: int, record: dict, p: dict, seq: int):
    return m.StationEntrance(
        snapshot_id=snapshot_id,
        station_key=_require(_text(p.get("station_key")), "station_key"),
        entrance_no=_text(p.get("entrance_no")),
        geom=_geometry(record, p, _POINT),
    )


def _zoning_area(snapshot_id: int, record: dict, p: dict, seq: int):
    name = _require(_text(p.get("zoning_name")), "zoning_name")
    category = _text(p.get("category")) or ("DEV_RESTRICTION" if "개발제한" in name else "USE_ZONE")
    return m.ZoningArea(
        snapshot_id=snapshot_id,
        category=category,
        zoning_code=_text(p.get("zoning_code")) or zoning_code_of(name),
        zoning_name=name,
        geom=_geometry(record, p, _POLYGON),
    )


def _redevelopment_zone(snapshot_id: int, record: dict, p: dict, seq: int):
    name = _require(_text(p.get("name")), "name")
    return m.RedevelopmentZone(
        snapshot_id=snapshot_id,
        zone_key=_text(p.get("zone_key")) or name,
        name=name,
        project_type=_text(p.get("project_type")),
        stage=_text(p.get("stage")),
        stage_date=_date(p.get("stage_date")),
        geom=_geometry(record, p, _POLYGON),
    )


def _business_district(snapshot_id: int, record: dict, p: dict, seq: int):
    return m.BusinessDistrict(
        snapshot_id=snapshot_id,
        name=_require(_text(p.get("name")), "name"),
        geom=_geometry(record, p, _POINT),
    )


def _transaction(snapshot_id: int, record: dict, p: dict, seq: int):
    return m.Transaction(
        snapshot_id=snapshot_id,
        deal_type=_text(p.get("deal_type")) or "SALE",
        deal_date=_require(_date(p.get("deal_date")), "deal_date"),
        pnu=_text(p.get("pnu")),
        sgg_cd=_text(p.get("sgg_cd")),
        umd_nm=_text(p.get("umd_nm")),
        jibun=_text(p.get("jibun")),
        house_type=_text(p.get("house_type")),
        building_name=_text(p.get("building_name")),
        exclusive_area=_float(p.get("exclusive_area")),
        land_share_area=_float(p.get("land_share_area")),
        floor=_int(p.get("floor")),
        build_year=_int(p.get("build_year")),
        price_krw=_require(_int(p.get("price_krw")), "price_krw"),
        cancelled=bool(_bool(p.get("cancelled"))),
    )


def _first(p: dict, *names: str):
    for name in names:
        if _text(p.get(name)) is not None:
            return p[name]
    return None


def _rtms_transaction(snapshot_id: int, record: dict, p: dict, seq: int):
    """실거래가 API 원본 item. 영문 필드명과 개편 이전 한글 필드명을 모두 받는다."""
    year = _int(_first(p, "dealYear", "년"))
    month = _int(_first(p, "dealMonth", "월"))
    day = _int(_first(p, "dealDay", "일"))
    amount = _float(_first(p, "dealAmount", "거래금액"))  # 만원
    if None in (year, month, day) or amount is None:
        raise SkipRecord("계약일 또는 거래금액 누락")
    return m.Transaction(
        snapshot_id=snapshot_id,
        deal_type="SALE",
        deal_date=date(year, month, day),
        pnu=None,
        sgg_cd=_text(_first(p, "sggCd", "지역코드")),
        umd_nm=_text(_first(p, "umdNm", "법정동")),
        jibun=_text(_first(p, "jibun", "지번")),
        house_type=_text(_first(p, "houseType", "주택유형")),
        building_name=_text(_first(p, "mhouseNm", "연립다세대")),
        exclusive_area=_float(_first(p, "excluUseAr", "전용면적")),
        land_share_area=_float(_first(p, "landAr", "대지권면적")),
        floor=_int(_first(p, "floor", "층")),
        build_year=_int(_first(p, "buildYear", "건축년도")),
        price_krw=int(amount * 10_000),
        cancelled=_text(_first(p, "cdealType", "해제여부")) is not None,
    )


Normalizer = Callable[[int, dict, dict, int], object]

_BY_DATASET: dict[str, Normalizer] = {
    m.DS_ADMIN_AREAS: _admin_area,
    m.DS_PARCELS: _parcel,
    m.DS_BUILDINGS: _building,
    m.DS_ROADS: _road,
    m.DS_STATIONS: _station,
    m.DS_STATION_ENTRANCES: _station_entrance,
    m.DS_ZONING_AREAS: _zoning_area,
    m.DS_REDEVELOPMENT_ZONES: _redevelopment_zone,
    m.DS_BUSINESS_DISTRICTS: _business_district,
    m.DS_TRANSACTIONS: _transaction,
}

# 출처 고유 형식을 그대로 받는 경우
_BY_SOURCE: dict[tuple[str, str], Normalizer] = {
    ("molit_rtms", m.DS_TRANSACTIONS): _rtms_transaction,
}


def normalize(session: Session, snapshot: m.CollectionSnapshot, records: list[dict]) -> tuple[int, int]:
    """레코드를 정규화해 적재하고 (적재 건수, 건너뛴 건수)를 돌려준다."""
    fn = _BY_SOURCE.get((snapshot.source, snapshot.dataset)) or _BY_DATASET.get(snapshot.dataset)
    if fn is None:
        raise ValueError(f"알 수 없는 데이터셋입니다: {snapshot.dataset}")
    mapping = snapshot.field_mapping or {}
    rows, skipped = [], 0
    for seq, record in enumerate(records):
        props = {mapping.get(k, k): v for k, v in (record.get("properties") or {}).items()}
        try:
            rows.append(fn(snapshot.id, record, props, seq))
        except SkipRecord:
            skipped += 1
    session.add_all(rows)
    return len(rows), skipped

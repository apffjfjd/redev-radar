import json
from datetime import date

import pytest
from sqlalchemy import select

from redev_radar import models as m
from redev_radar.analysis.context import select_snapshots
from redev_radar.collector import collect
from redev_radar.collector.file_adapter import FileAdapter
from redev_radar.collector.molit_rtms import RtmsError, parse_response
from redev_radar.normalizer.service import jibun_from_pnu, zoning_code_of

SQUARE = {"type": "Polygon", "coordinates": [[[127.0, 37.5], [127.001, 37.5], [127.001, 37.501], [127.0, 37.501], [127.0, 37.5]]]}


def _geojson(tmp_path, name, features):
    path = tmp_path / name
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    return path


def test_file_collection_keeps_raw_and_applies_field_mapping(session, tmp_path):
    path = _geojson(
        tmp_path,
        "parcels.geojson",
        [
            {"type": "Feature", "geometry": SQUARE, "properties": {"PNU": "1168010100100120003", "JIMOK": "대"}},
            {"type": "Feature", "geometry": SQUARE, "properties": {"JIMOK": "대"}},  # PNU 누락
        ],
    )
    adapter = FileAdapter(m.DS_PARCELS, path, scope="11680", field_mapping={"PNU": "pnu", "JIMOK": "jimok"})

    [snapshot] = collect(session, adapter, date(2026, 9, 1))

    assert (snapshot.record_count, snapshot.skipped_count) == (1, 1)
    assert len(session.scalars(select(m.RawRecord).where(m.RawRecord.snapshot_id == snapshot.id)).all()) == 2
    parcel = session.scalars(select(m.Parcel)).one()
    assert (parcel.sgg_cd, parcel.jibun, parcel.jimok) == ("11680", "12-3", "대")
    assert 8000 < parcel.area_m2 < 11000  # 도형에서 계산한 면적 (㎡)


def test_missing_approval_date_is_kept_as_missing(session, tmp_path):
    path = _geojson(
        tmp_path,
        "buildings.geojson",
        [
            {"type": "Feature", "geometry": SQUARE, "properties": {"pnu": "1", "use_approval_date": "19881203"}},
            {"type": "Feature", "geometry": SQUARE, "properties": {"pnu": "2", "use_approval_date": ""}},
        ],
    )
    collect(session, FileAdapter(m.DS_BUILDINGS, path), date(2026, 9, 1))

    dates = [b.use_approval_date for b in session.scalars(select(m.Building).order_by(m.Building.id))]
    assert dates == [date(1988, 12, 3), None]


def test_snapshots_accumulate_and_are_selected_as_of_base_date(session, tmp_path):
    path = _geojson(tmp_path, "p.geojson", [{"type": "Feature", "geometry": SQUARE, "properties": {"pnu": "1"}}])
    [old] = collect(session, FileAdapter(m.DS_PARCELS, path, scope="11680"), date(2024, 1, 1))
    [new] = collect(session, FileAdapter(m.DS_PARCELS, path, scope="11680"), date(2026, 1, 1))
    [other] = collect(session, FileAdapter(m.DS_PARCELS, path, scope="11110"), date(2025, 1, 1))

    assert select_snapshots(session, date(2023, 12, 31)) == {}
    assert select_snapshots(session, date(2024, 6, 1)) == {m.DS_PARCELS: [old.id]}
    # 기준일 이후에 수집된 자료는 쓰지 않는다.
    assert select_snapshots(session, date(2025, 6, 1)) == {m.DS_PARCELS: [other.id, old.id]}
    assert select_snapshots(session, date(2026, 6, 1)) == {m.DS_PARCELS: [other.id, new.id]}
    assert session.scalars(select(m.Parcel)).all().__len__() == 3  # 덮어쓰지 않고 누적


def test_unsupported_file_type(tmp_path):
    with pytest.raises(ValueError):
        list(FileAdapter(m.DS_PARCELS, tmp_path / "x.shp").fetch(date(2026, 1, 1)))


def test_pnu_and_zoning_helpers():
    assert jibun_from_pnu("1168010100100120000") == "12"
    assert jibun_from_pnu("1168010100200120003") == "산12-3"
    assert jibun_from_pnu("123") is None
    assert zoning_code_of("제2종 일반주거지역") == "GR2"
    assert zoning_code_of("자연녹지지역") == "OTHER"


RTMS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg></header>
<body><items>
<item><sggCd>11680</sggCd><umdNm>역삼동</umdNm><jibun>12-3</jibun><mhouseNm>샘플빌라</mhouseNm>
<houseType>다세대</houseType><excluUseAr>39.6</excluUseAr><landAr>21.5</landAr><floor>3</floor>
<buildYear>1994</buildYear><dealYear>2026</dealYear><dealMonth>8</dealMonth><dealDay>7</dealDay>
<dealAmount>32,500</dealAmount><cdealType> </cdealType></item>
<item><sggCd>11680</sggCd><umdNm>역삼동</umdNm><jibun>45</jibun><houseType>연립</houseType>
<excluUseAr>59.1</excluUseAr><dealYear>2026</dealYear><dealMonth>8</dealMonth><dealDay>21</dealDay>
<dealAmount>51,000</dealAmount><cdealType>O</cdealType></item>
</items><numOfRows>1000</numOfRows><pageNo>1</pageNo><totalCount>2</totalCount></body></response>"""


class _RtmsStub:
    source = "molit_rtms"

    def fetch(self, as_of):
        from redev_radar.collector.base import CollectedDataset

        items, _ = parse_response(RTMS_XML)
        yield CollectedDataset(dataset=m.DS_TRANSACTIONS, records=items, scope="11680:202608")


def test_rtms_items_are_normalized(session):
    collect(session, _RtmsStub(), date(2026, 9, 1))

    first, second = session.scalars(select(m.Transaction).order_by(m.Transaction.id)).all()
    assert (first.deal_date, first.price_krw, first.exclusive_area) == (date(2026, 8, 7), 325_000_000, 39.6)
    assert (first.sgg_cd, first.umd_nm, first.jibun, first.cancelled) == ("11680", "역삼동", "12-3", False)
    assert second.cancelled is True and second.house_type == "연립"


def test_rtms_error_response():
    xml = "<response><header><resultCode>30</resultCode><resultMsg>SERVICE KEY IS NOT REGISTERED</resultMsg></header></response>"
    with pytest.raises(RtmsError, match="SERVICE KEY"):
        parse_response(xml)

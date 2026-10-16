"""합성 샘플 데이터 어댑터.

인증키 없이 전체 흐름을 실행·검증하기 위한 가상의 동네를 만든다. 실제 지역의
자료가 아니며, 모든 명칭에 "[샘플]"을 붙인다. 같은 seed와 기준일이면 결과가 같다.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Iterable

from shapely.affinity import translate
from shapely.geometry import LineString, Point, box, mapping
from shapely.geometry.base import BaseGeometry

from .. import models as m
from ..geo import to_metric, to_wgs84
from .base import CollectedDataset

ORIGIN_LONLAT = (127.06, 37.59)
CELL = 100.0  # 도로 간격 (m)
SGG_CD = "11999"  # 실재하지 않는 시군구 코드 (서울 시도코드 11)
UMD_CD = "10100"
UMD_NM = "샘플동"


def _road_width(coord: float, west: bool) -> float:
    if coord % 400 == 0:
        return 20.0
    if coord % 200 == 0:
        return 8.0
    return 3.0 if west else 6.0  # 서쪽은 4m 미만 골목


class SampleAdapter:
    source = "sample"

    def __init__(self, cols: int = 12, rows: int = 8, seed: int = 4):
        self.cols = cols
        self.rows = rows
        self.seed = seed
        self.width = cols * CELL
        self.height = rows * CELL
        self.mid_x = self.width / 2
        origin = to_metric(Point(*ORIGIN_LONLAT))
        self._ox, self._oy = origin.x, origin.y

    # ------------------------------------------------------------ 공통

    def _feature(self, geom: BaseGeometry, **props) -> dict:
        placed = to_wgs84(translate(geom, self._ox, self._oy))
        return {"type": "Feature", "geometry": mapping(placed), "properties": props}

    def fetch(self, as_of: date) -> Iterable[CollectedDataset]:
        rng = random.Random(self.seed)
        parcels, buildings, transactions = self._lots(rng, as_of)
        datasets = {
            m.DS_ADMIN_AREAS: [self._feature(box(0, 0, self.width, self.height), code=SGG_CD, name="[샘플]구")],
            m.DS_ROADS: self._roads(),
            m.DS_PARCELS: parcels,
            m.DS_BUILDINGS: buildings,
            m.DS_STATIONS: [s for s, _ in self._stations()],
            m.DS_STATION_ENTRANCES: [e for _, entrances in self._stations() for e in entrances],
            m.DS_ZONING_AREAS: self._zoning(),
            m.DS_REDEVELOPMENT_ZONES: [
                self._feature(
                    box(0, 0, 250, 200),
                    zone_key="SAMPLE-Z1",
                    name="[샘플] 재개발 1구역",
                    project_type="주택재개발",
                    stage="조합설립인가",
                    stage_date=(as_of - timedelta(days=400)).isoformat(),
                )
            ],
            m.DS_BUSINESS_DISTRICTS: [
                {"properties": {"name": "도심(광화문 일대)", "lon": 126.977, "lat": 37.571}},
                {"properties": {"name": "강남(강남역 일대)", "lon": 127.028, "lat": 37.498}},
                {"properties": {"name": "여의도", "lon": 126.925, "lat": 37.525}},
            ],
            m.DS_TRANSACTIONS: transactions,
        }
        for dataset, records in datasets.items():
            yield CollectedDataset(dataset=dataset, records=records, scope="SAMPLE", note="합성 샘플 데이터")

    # ------------------------------------------------------------ 데이터셋

    def _roads(self) -> list[dict]:
        roads = []
        for i in range(self.cols + 1):
            x = i * CELL
            roads.append(
                self._feature(
                    LineString([(x, 0), (x, self.height)]),
                    road_key=f"V{i}",
                    name=f"[샘플] 세로{i}길",
                    width_m=_road_width(x, west=x < self.mid_x),
                )
            )
        for j in range(self.rows + 1):
            y = j * CELL
            # 가로 골목은 동·서 구간의 폭이 다르다.
            for part, (x0, x1) in (("W", (0, self.mid_x)), ("E", (self.mid_x, self.width))):
                roads.append(
                    self._feature(
                        LineString([(x0, y), (x1, y)]),
                        road_key=f"H{j}{part}",
                        name=f"[샘플] 가로{j}길",
                        width_m=_road_width(y, west=part == "W"),
                    )
                )
        return roads

    def _stations(self) -> list[tuple[dict, list[dict]]]:
        specs = [
            ("SAMPLE-A", "[샘플] 가온역", ["2호선", "5호선"], (400.0, 400.0), 4),
            ("SAMPLE-B", "[샘플] 나래역", ["7호선"], (self.width, 400.0), 2),
        ]
        offsets = [(-35, -35), (35, 35), (-35, 35), (35, -35)]
        result = []
        for key, name, lines, (x, y), entrance_count in specs:
            station = self._feature(Point(x, y), station_key=key, name=name, lines=lines)
            entrances = [
                self._feature(Point(x + dx, y + dy), station_key=key, entrance_no=str(n + 1))
                for n, (dx, dy) in enumerate(offsets[:entrance_count])
            ]
            result.append((station, entrances))
        return result

    def _zoning(self) -> list[dict]:
        w, h, mid = self.width, self.height, self.mid_x
        return [
            self._feature(box(0, 0, mid, h), zoning_name="제2종일반주거지역"),
            self._feature(box(mid, 0, w, h / 2), zoning_name="제3종일반주거지역"),
            self._feature(box(mid, h / 2, w, h), zoning_name="제1종일반주거지역"),
            self._feature(box(w - 400, h - 200, w, h), category="OVERLAY", zoning_name="[샘플] 고도지구"),
        ]

    def _lots(self, rng: random.Random, as_of: date) -> tuple[list[dict], list[dict], list[dict]]:
        parcels, buildings, transactions = [], [], []
        cell_no = 0
        for i in range(self.cols):
            for j in range(self.rows):
                cell_no += 1
                x0, y0 = i * CELL, j * CELL
                west = x0 + CELL / 2 < self.mid_x
                left = x0 + _road_width(x0, west) / 2
                right = x0 + CELL - _road_width(x0 + CELL, west) / 2
                bottom = y0 + _road_width(y0, west) / 2
                top = y0 + CELL - _road_width(y0 + CELL, west) / 2
                row_count, min_w, max_w = (6, 5.0, 9.0) if west else (4, 8.0, 14.0)
                depth = (top - bottom) / row_count
                lot_no = 0
                for r in range(row_count):
                    x = left
                    while right - x >= min_w:
                        lot_w = rng.uniform(min_w, max_w)
                        if right - (x + lot_w) < min_w:
                            lot_w = right - x
                        lot_no += 1
                        lot = box(x, bottom + r * depth, x + lot_w, bottom + (r + 1) * depth)
                        x += lot_w
                        pnu = f"{SGG_CD}{UMD_CD}1{cell_no:04d}{lot_no:04d}"
                        jibun = f"{cell_no}-{lot_no}"
                        parcels.append(
                            self._feature(lot, pnu=pnu, umd_nm=UMD_NM, jibun=jibun, jimok="대", area_m2=round(lot.area, 1))
                        )
                        if rng.random() < 0.08:
                            continue  # 나대지
                        building = self._building(rng, as_of, lot, pnu, west, y0 >= self.height / 2)
                        buildings.append(building)
                        transactions.extend(self._deals(rng, as_of, building["properties"], jibun, west))
        return parcels, buildings, transactions

    def _building(self, rng: random.Random, as_of: date, lot, pnu: str, west: bool, north: bool) -> dict:
        if west:
            year = rng.randint(1972, 1996) if rng.random() < 0.85 else rng.randint(2012, 2023)
        elif north:
            year = rng.randint(1990, 2015)
        else:
            year = rng.randint(1985, 2000) if rng.random() < 0.5 else rng.randint(2003, 2022)
        reinforced = rng.random() < (0.2 if year < 1990 else 0.85)
        floors = rng.randint(3, 5) if reinforced else rng.randint(2, 3)
        use = rng.choices(
            ["단독주택", "다가구주택", "다세대주택", "연립주택", "제2종근린생활시설"],
            weights=[25, 15, 45, 5, 10],
        )[0]
        households = {
            "단독주택": 1,
            "다가구주택": rng.randint(3, 6),
            "다세대주택": rng.randint(4, 12),
            "연립주택": rng.randint(8, 16),
        }.get(use, 0)
        footprint = lot.buffer(-1.0)
        if footprint.is_empty:
            footprint = lot
        semi_basement = None if rng.random() < 0.05 else rng.random() < (0.55 if year < 1995 else 0.05)
        far_area = footprint.area * floors
        approval = None if rng.random() < 0.04 else date(year, rng.randint(1, 12), rng.randint(1, 28))
        return self._feature(
            footprint,
            building_key=f"BLD-{pnu}",
            pnu=pnu,
            use_approval_date=approval.isoformat() if approval else None,
            structure="철근콘크리트구조" if reinforced else "연와조",
            main_use=use,
            gross_floor_area=round(far_area + (footprint.area if semi_basement else 0), 1),
            far_floor_area=round(far_area, 1),
            ground_floors=floors,
            underground_floors=1 if semi_basement else 0,
            household_count=households,
            has_semi_basement=semi_basement,
            build_year=year,
        )

    def _deals(self, rng: random.Random, as_of: date, building: dict, jibun: str, west: bool) -> list[dict]:
        if building["main_use"] not in ("다세대주택", "연립주택") or rng.random() > 0.35:
            return []
        deals = []
        for _ in range(rng.randint(1, 2)):
            months_ago = rng.uniform(0, 40)
            area = round(rng.uniform(28, 60), 2)
            per_m2 = (7_500_000 if west else 9_500_000) * (1 - 0.004 * months_ago) * rng.uniform(0.88, 1.12)
            deals.append(
                {
                    "properties": {
                        "deal_date": (as_of - timedelta(days=int(months_ago * 30.4))).isoformat(),
                        "sgg_cd": SGG_CD,
                        "umd_nm": UMD_NM,
                        "jibun": jibun,
                        "house_type": "다세대" if building["main_use"] == "다세대주택" else "연립",
                        "building_name": "[샘플]빌라",
                        "exclusive_area": area,
                        "land_share_area": round(area * rng.uniform(0.4, 0.7), 2),
                        "floor": rng.randint(1, building["ground_floors"]),
                        "build_year": building["build_year"],
                        "price_krw": int(round(area * per_m2, -6)),
                        "cancelled": rng.random() < 0.03,
                    }
                }
            )
        return deals

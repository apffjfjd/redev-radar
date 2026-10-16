"""좌표계 변환. 저장은 WGS84, 면적·거리 계산은 중부원점 TM(EPSG:5186, 미터)에서 한다."""

from __future__ import annotations

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry.base import BaseGeometry

WGS84 = 4326
METRIC = 5186

_to_metric = Transformer.from_crs(WGS84, METRIC, always_xy=True)
_to_wgs84 = Transformer.from_crs(METRIC, WGS84, always_xy=True)


def _apply(transformer: Transformer):
    def fn(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return fn


def to_metric(geom: BaseGeometry) -> BaseGeometry:
    return shapely.transform(geom, _apply(_to_metric))


def to_wgs84(geom: BaseGeometry) -> BaseGeometry:
    return shapely.transform(geom, _apply(_to_wgs84))


def polygons_of(geom: BaseGeometry) -> list[BaseGeometry]:
    """도형에서 면(Polygon)만 꺼낸다. 차집합·교집합 결과에 선·점이 섞이는 경우를 걸러낸다."""
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [p for g in geom.geoms for p in polygons_of(g)]
    return []

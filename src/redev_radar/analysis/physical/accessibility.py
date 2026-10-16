"""역세권 접근성 (FR-005)."""

from __future__ import annotations

import heapq

import shapely
from shapely import STRtree, unary_union
from shapely.geometry import Point

from ... import models as m
from ..context import RoadRec, RunContext, StationRec

SEGMENT_M = 25.0  # 도로 중심선을 이 길이 이하로 나눠 보행망 노드를 만든다.


class WalkNetwork:
    """도로 중심선으로 만든 보행망. 모든 도로를 보행 가능하다고 가정한다."""

    def __init__(self, roads: list[RoadRec]):
        self.nodes: list[tuple[float, float]] = []
        self._index: dict[tuple[float, float], int] = {}
        self.adjacency: list[list[tuple[int, float]]] = []
        if roads:
            noded = unary_union([shapely.segmentize(r.geom, SEGMENT_M) for r in roads])
            for line in shapely.get_parts(noded):
                coords = list(line.coords)
                for a, b in zip(coords, coords[1:]):
                    i, j = self._node(a), self._node(b)
                    length = ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
                    if i != j:
                        self.adjacency[i].append((j, length))
                        self.adjacency[j].append((i, length))
        self._tree = STRtree(shapely.points(self.nodes)) if self.nodes else None

    def _node(self, coord) -> int:
        key = (round(coord[0], 2), round(coord[1], 2))
        if key not in self._index:
            self._index[key] = len(self.nodes)
            self.nodes.append(key)
            self.adjacency.append([])
        return self._index[key]

    def snap(self, point: Point) -> tuple[int, float] | None:
        """가장 가까운 노드와 그곳까지의 직선거리."""
        if self._tree is None:
            return None
        i = int(self._tree.nearest(point))
        return i, point.distance(Point(self.nodes[i]))

    def distances_from(self, sources: list[tuple[int, float]], cutoff: float) -> dict[int, float]:
        """여러 출발 노드(노드, 초기 거리)에서의 최단 보행거리. cutoff를 넘는 노드는 담지 않는다."""
        best: dict[int, float] = {}
        queue = [(offset, node) for node, offset in sources]
        heapq.heapify(queue)
        while queue:
            dist, node = heapq.heappop(queue)
            if node in best or dist > cutoff:
                continue
            best[node] = dist
            for neighbor, length in self.adjacency[node]:
                if neighbor not in best:
                    heapq.heappush(queue, (dist + length, neighbor))
        return best


class AccessibilityAnalyzer:
    name = "accessibility"

    def run(self, ctx: RunContext) -> None:
        stations = ctx.data.stations
        if not stations:
            ctx.warn("지하철역 자료가 없어 접근성을 분석하지 못했습니다.")
            return
        radius = ctx.criteria.assumption_value("STATION_AREA_RADIUS_M")
        walk_max = ctx.criteria.assumption_value("WALK_MAX_DISTANCE_M")
        network = WalkNetwork(ctx.data.roads)
        walk_maps = {s.key: self._walk_map(network, s, walk_max) for s in stations}
        districts = ctx.data.business_districts
        if not districts:
            ctx.warn("업무지구 자료가 없어 업무지구 접근성을 분석하지 못했습니다.")

        for block in ctx.blocks():
            center = block.geom.representative_point() if not block.geom.contains(block.geom.centroid) else block.geom.centroid
            nearest = min(stations, key=lambda s: center.distance(s.geom))
            straight = center.distance(nearest.geom)
            station_basis = {"station": nearest.name, "lines": nearest.lines}

            ctx.add_metric(
                block,
                "STATION_DISTANCE_M",
                straight,
                "m",
                m.CONFIRMED,
                {**station_basis, "method": "블록 중심점 ~ 역 중심점 직선거리"},
            )
            ctx.add_metric(
                block,
                "STATION_EDGE_DISTANCE_M",
                min(block.geom.distance(s.geom) for s in stations),
                "m",
                m.CONFIRMED,
                {"method": "블록 경계의 가장 가까운 지점 ~ 역 중심점 직선거리"},
            )

            entrances = [(s, e) for s in stations for e in s.entrances]
            if entrances:
                station, entrance = min(entrances, key=lambda se: center.distance(se[1]))
                ctx.add_metric(
                    block,
                    "ENTRANCE_DISTANCE_M",
                    center.distance(entrance),
                    "m",
                    m.CONFIRMED,
                    {"station": station.name, "method": "블록 중심점 ~ 가장 가까운 출입구 직선거리"},
                )
            else:
                ctx.add_metric(block, "ENTRANCE_DISTANCE_M", None, "m", m.CONFIRMED, {"reason": "출입구 자료가 없습니다."})

            self._walk(ctx, block, center, network, stations, walk_maps, walk_max)

            ctx.add_metric(
                block,
                "TRANSFER_STATION",
                1.0 if len(nearest.lines) >= 2 else 0.0,
                "여부",
                m.CONFIRMED,
                {**station_basis, "method": "가장 가까운 역의 노선 수가 2개 이상이면 환승역"},
            )
            nearby = [s for s in stations if center.distance(s.geom) <= radius]
            lines = sorted({line for s in nearby for line in s.lines})
            ctx.add_metric(
                block,
                "LINE_COUNT",
                len(lines),
                "개",
                m.CONFIRMED,
                {"lines": lines, "stations": [s.name for s in nearby], "radius_m": radius},
            )

            if districts:
                distances = {d.name: round(center.distance(d.geom)) for d in districts}
                closest = min(distances, key=distances.get)
                ctx.add_metric(
                    block,
                    "CBD_DISTANCE_M",
                    distances[closest],
                    "m",
                    m.CONFIRMED,
                    {"district": closest, "distances_m": distances, "method": "블록 중심점 ~ 업무지구 기준점 직선거리"},
                )

    @staticmethod
    def _walk_map(network: WalkNetwork, station: StationRec, cutoff: float) -> dict[int, float]:
        targets = station.entrances or [station.geom]
        sources = [s for s in (network.snap(t) for t in targets) if s]
        return network.distances_from(sources, cutoff)

    @staticmethod
    def _walk(ctx: RunContext, block, center: Point, network: WalkNetwork, stations, walk_maps, walk_max: float) -> None:
        basis = {
            "method": "도로 중심선 보행망 최단경로 (블록 중심점 → 가장 가까운 도로 → 역 출입구)",
            "notice": "횡단보도·계단·보행 전용로를 반영하지 않은 추정값입니다.",
        }
        snapped = network.snap(center)
        best = None
        if snapped:
            node, offset = snapped
            reachable = [(walk_maps[s.key][node] + offset, s) for s in stations if node in walk_maps[s.key]]
            if reachable:
                best = min(reachable, key=lambda r: r[0])
        if best is None:
            basis["reason"] = f"도로망으로 {walk_max:.0f}m 이내에 도달할 수 있는 역이 없습니다."
            ctx.add_metric(block, "WALK_DISTANCE_M", None, "m", m.ESTIMATED, basis)
        else:
            basis["station"] = best[1].name
            basis["uses_entrances"] = bool(best[1].entrances)
            ctx.add_metric(block, "WALK_DISTANCE_M", best[0], "m", m.ESTIMATED, basis)

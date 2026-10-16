"""파일 기반 수집 어댑터.

공공 출처 상당수가 파일(SHP→GeoJSON, CSV)로 제공되고, 자동수집이 불가한 출처는
수동 입력·파일 업로드로 대체해야 하므로 기본 수집 경로로 둔다.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Iterable

from .base import CollectedDataset


class FileAdapter:
    source = "file"

    def __init__(
        self,
        dataset: str,
        path: str | Path,
        scope: str = "ALL",
        field_mapping: dict[str, str] | None = None,
    ):
        self.dataset = dataset
        self.path = Path(path)
        self.scope = scope
        self.field_mapping = field_mapping or None

    def fetch(self, as_of: date) -> Iterable[CollectedDataset]:
        suffix = self.path.suffix.lower()
        if suffix in (".geojson", ".json"):
            records = self._read_geojson()
        elif suffix == ".csv":
            records = self._read_csv()
        else:
            raise ValueError(f"지원하지 않는 파일 형식입니다: {self.path.name} (.geojson, .json, .csv)")
        yield CollectedDataset(
            dataset=self.dataset,
            records=records,
            scope=self.scope,
            field_mapping=self.field_mapping,
            note=f"file={self.path.name}",
        )

    def _read_geojson(self) -> list[dict]:
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        if doc.get("type") == "FeatureCollection":
            return list(doc.get("features", []))
        if doc.get("type") == "Feature":
            return [doc]
        raise ValueError(f"GeoJSON FeatureCollection이 아닙니다: {self.path.name}")

    def _read_csv(self) -> list[dict]:
        with self.path.open(encoding="utf-8-sig", newline="") as f:
            return [{"properties": dict(row)} for row in csv.DictReader(f)]

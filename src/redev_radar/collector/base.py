from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Protocol


@dataclass
class CollectedDataset:
    """어댑터가 한 번에 가져온 한 데이터셋의 원본 레코드.

    레코드는 GeoJSON Feature 형태(`{"geometry": ..., "properties": {...}}`)이며
    공간정보가 없는 자료는 geometry를 생략한다.
    """

    dataset: str
    records: list[dict]
    scope: str = "ALL"
    # 원본 필드명 → 공통 스키마 필드명. 원본은 그대로 보관하고 정규화 시점에 적용한다.
    field_mapping: dict[str, str] | None = None
    note: str | None = None


class SourceAdapter(Protocol):
    source: str

    def fetch(self, as_of: date) -> Iterable[CollectedDataset]: ...

"""Data Collector — 외부 출처별 수집기. 분석 계층은 이 패키지를 직접 호출하지 않는다."""

from .base import CollectedDataset, SourceAdapter
from .service import collect

__all__ = ["CollectedDataset", "SourceAdapter", "collect"]

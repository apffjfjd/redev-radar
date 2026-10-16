from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from ..models import CollectionSnapshot, RawRecord
from ..normalizer import normalize
from .base import SourceAdapter


def collect(session: Session, adapter: SourceAdapter, as_of: date) -> list[CollectionSnapshot]:
    """어댑터로 수집해 원본과 수집 시점을 보관하고 정규화까지 수행한다.

    기존 스냅샷은 건드리지 않고 새 스냅샷을 추가한다.
    """
    snapshots = []
    for collected in adapter.fetch(as_of):
        snapshot = CollectionSnapshot(
            source=adapter.source,
            dataset=collected.dataset,
            scope=collected.scope,
            as_of_date=as_of,
            collected_at=datetime.now(),
            field_mapping=collected.field_mapping,
            note=collected.note,
        )
        session.add(snapshot)
        session.flush()
        session.add_all(RawRecord(snapshot_id=snapshot.id, payload=r) for r in collected.records)
        loaded, skipped = normalize(session, snapshot, collected.records)
        snapshot.record_count = loaded
        snapshot.skipped_count = skipped
        session.flush()
        snapshots.append(snapshot)
    return snapshots

from collections import Counter, defaultdict
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from redev_radar import models as m
from redev_radar.analysis import run_analysis
from redev_radar.api import create_app


@pytest.fixture(scope="module")
def result(analyzed):
    _, session, run = analyzed
    blocks = session.scalars(select(m.CandidateBlock).where(m.CandidateBlock.run_id == run.id)).all()
    metrics = defaultdict(dict)
    for x in session.scalars(select(m.BlockMetric).where(m.BlockMetric.run_id == run.id)):
        metrics[x.block_id][x.code] = x
    return session, run, blocks, metrics


def test_run_records_inputs(result):
    _, run, _, _ = result

    assert run.stages[0] == "block_generator" and run.stages[-1] == "price"
    assert set(run.snapshot_ids) >= {m.DS_PARCELS, m.DS_BUILDINGS, m.DS_ROADS, m.DS_STATIONS, m.DS_TRANSACTIONS}
    assert run.criteria_snapshot["base_date"] == "2026-09-30"
    assert any(c["item_code"] == "MIN_AREA" for c in run.criteria_snapshot["criteria"])


def test_multiple_candidate_boundaries_per_region(result):
    _, _, blocks, _ = result
    by_variant = Counter(b.variant for b in blocks)

    assert set(by_variant) == {"FINE", "MEDIUM", "COARSE"}
    assert by_variant["FINE"] > by_variant["MEDIUM"] > by_variant["COARSE"] > 0
    # 모든 블록은 가장 큰 경계의 블록(지역)에 묶인다.
    regions = {b.block_key for b in blocks if b.variant == "COARSE"}
    assert {b.region_key for b in blocks} == regions
    # 한 variant 안에서는 필지가 중복 배정되지 않는다.
    for variant in by_variant:
        pnus = [p for b in blocks if b.variant == variant for p in b.parcel_pnus]
        assert len(pnus) == len(set(pnus))


def test_blocks_follow_roads_not_station_radius(result):
    _, _, blocks, _ = result
    # 샘플의 MEDIUM 블록은 폭 8m 이상 도로로 둘러싸인 가구 하나(약 186m × 186m)와 일치한다.
    for b in (b for b in blocks if b.variant == "MEDIUM"):
        assert b.area_m2 == pytest.approx(186 * 186, rel=0.02)
        assert b.parcel_area_m2 < b.area_m2  # 내부 골목 포함


def test_aging_is_preliminary_estimate_and_reflects_data(result):
    _, _, blocks, metrics = result
    ratios = {}
    for b in (b for b in blocks if b.variant == "COARSE"):
        aged = metrics[b.id]["AGED_COUNT_RATIO"]
        assert aged.value_kind == m.ESTIMATED and aged.verification == m.PRELIMINARY
        assert "법적" in aged.basis["notice"]
        assert aged.basis["unknown_age"] > 0  # 사용승인일 누락은 분모에서 빼고 건수로 남긴다.
        assert aged.basis["known_age"] + aged.basis["unknown_age"] == aged.basis["buildings"]
        ratios[b.block_key] = aged.value
    # 샘플은 서쪽 절반이 노후 지역이다.
    assert max(ratios.values()) > 0.75 and min(ratios.values()) < 0.5


def test_far_headroom_is_developable_minus_current(result):
    _, _, blocks, metrics = result
    for b in blocks:
        x = metrics[b.id]
        for scenario in ("LOW", "BASE", "HIGH"):
            headroom = x[f"FAR_HEADROOM_{scenario}"]
            assert headroom.value == pytest.approx(x[f"DEVELOPABLE_FAR_{scenario}"].value - x["CURRENT_FAR"].value)
            assert headroom.value_kind == m.ESTIMATED and "실현 가능한 연면적" in headroom.basis["notice"]
        assert x["FAR_HEADROOM_LOW"].value <= x["FAR_HEADROOM_BASE"].value <= x["FAR_HEADROOM_HIGH"].value


def test_accessibility(result):
    _, _, blocks, metrics = result
    for b in blocks:
        x = metrics[b.id]
        assert x["STATION_EDGE_DISTANCE_M"].value <= x["STATION_DISTANCE_M"].value
        # 보행거리는 출입구까지의 직선거리보다 짧을 수 없다.
        assert x["WALK_DISTANCE_M"].value >= x["ENTRANCE_DISTANCE_M"].value - 1
        assert x["WALK_DISTANCE_M"].value_kind == m.ESTIMATED
        assert x["CBD_DISTANCE_M"].basis["district"]
    assert {metrics[b.id]["TRANSFER_STATION"].value for b in blocks} == {0.0, 1.0}


def test_zone_overlap_distinguishes_full_and_partial(result):
    session, run, blocks, metrics = result
    overlaps = session.scalars(select(m.ZoneOverlap).where(m.ZoneOverlap.run_id == run.id)).all()

    assert {o.overlap_kind for o in overlaps} == {"FULL", "PARTIAL"}
    assert all(o.stage == "조합설립인가" for o in overlaps)
    overlapped = {o.block_id for o in overlaps}
    for b in blocks:
        assert (metrics[b.id]["ZONE_OVERLAP_RATIO"].value > 0) == (b.id in overlapped)


def test_eligibility_for_every_block_and_project_type(result):
    session, run, blocks, _ = result
    rows = session.scalars(select(m.EligibilityResult).where(m.EligibilityResult.run_id == run.id)).all()

    assert len(rows) == len(blocks) * 3
    statuses = Counter((r.project_type, r.status) for r in rows)
    assert statuses[("REDEV_GENERAL", "PASS")] > 0 and statuses[("REDEV_GENERAL", "FAIL")] > 0
    sample = next(r for r in rows if r.project_type == "REDEV_GENERAL")
    assert all(i["basis"]["law_name"] and i["basis"]["effective_from"] for i in sample.items)


def test_transactions_are_matched_by_jibun_and_sparse_blocks_are_not_estimated(result):
    session, run, blocks, metrics = result
    links = session.scalars(select(m.BlockTransaction).where(m.BlockTransaction.run_id == run.id)).all()

    assert links and {link.match_method for link in links} == {"JIBUN"}
    cancelled = {t.id for t in session.scalars(select(m.Transaction).where(m.Transaction.cancelled))}
    assert cancelled and not cancelled & {link.transaction_id for link in links}
    sparse = [b for b in blocks if metrics[b.id]["TX_COUNT"].value < 5]
    assert sparse
    for b in sparse:
        price = metrics[b.id]["TX_MEDIAN_PRICE_PER_M2"]
        assert price.value is None and "추정 불가" in price.basis["reason"]


def test_past_base_date_does_not_use_later_data_or_criteria(analyzed):
    _, session, _ = analyzed

    # 수집 이전 기준일: 자료가 없으므로 블록이 만들어지지 않는다.
    before_data = run_analysis(session, date(2026, 9, 29))
    assert before_data.status == "COMPLETED" and before_data.snapshot_ids == {}
    assert not session.scalars(select(m.CandidateBlock).where(m.CandidateBlock.run_id == before_data.id)).all()
    assert before_data.warnings

    # 가정값 시행 이전 기준일: 가정값이 없어 실행이 실패로 기록된다.
    before_criteria = run_analysis(session, date(2019, 1, 1))
    assert before_criteria.status == "FAILED" and "가정값" in before_criteria.error
    assert before_criteria.finished_at is not None
    # 실패한 실행은 조회 대상이 아니다: 가장 최근에 완료된 실행은 그대로다.
    latest = session.scalar(
        select(m.AnalysisRun).where(m.AnalysisRun.status == "COMPLETED").order_by(m.AnalysisRun.id.desc()).limit(1)
    )
    assert latest.id == before_data.id


# ---------------------------------------------------------------- 조회 API


@pytest.fixture(scope="module")
def client(analyzed):
    settings, _, run = analyzed
    return TestClient(create_app(settings)), run


def test_api_serves_requested_completed_run(client):
    api, run = client

    meta = api.get("/api/meta", params={"run_id": run.id}).json()
    assert [v["code"] for v in meta["variants"]] == ["FINE", "MEDIUM", "COARSE"]
    assert meta["sources"] == ["sample"]

    blocks = api.get("/api/blocks", params={"run_id": run.id, "variant": "MEDIUM"}).json()["features"]
    assert len(blocks) == 24
    props = blocks[0]["properties"]
    assert set(props["eligibility"]) == {"REDEV_GENERAL", "REDEV_STATION", "REDEV_SMALL"}

    detail = api.get(f"/api/blocks/{props['id']}").json()
    assert detail["alternatives"] and all(a["variant"] != "MEDIUM" or a["id"] != props["id"] for a in detail["alternatives"])
    assert {x["verification"] for x in detail["metrics"]} == {"PRELIMINARY"}
    assert isinstance(api.get(f"/api/blocks/{props['id']}/transactions").json(), list)

    none = api.get("/api/blocks", params={"run_id": run.id, "bbox": "0,0,1,1"}).json()["features"]
    assert none == []
    assert api.get("/api/blocks", params={"run_id": run.id, "bbox": "x"}).status_code == 422
    assert api.get("/api/blocks/999999").status_code == 404
    assert api.get("/api/meta", params={"run_id": 999999}).status_code == 404


def test_api_criteria_can_be_registered_and_revised(client):
    api, run = client
    body = {
        "jurisdiction": "11",
        "project_type": "REDEV_GENERAL",
        "item_code": "MIN_AREA",
        "requirement": "REQUIRED",
        "operator": ">=",
        "value": 8000,
        "unit": "㎡",
        "law_name": "테스트 조례",
        "effective_from": "2027-01-01",
    }
    created = api.post("/api/criteria", json=body)
    assert created.status_code == 201

    def effective(as_of):
        rows = api.get("/api/criteria", params={"project_type": "REDEV_GENERAL", "item_code": "MIN_AREA", "as_of": as_of}).json()
        return sorted(r["value"] for r in rows)

    assert effective("2026-12-31") == [10000]
    assert effective("2027-01-01") == [8000, 10000]  # 기존 행에 종료일을 넣기 전에는 둘 다 시행 중

    revised = api.put(f"/api/criteria/{created.json()['id']}", json={**body, "value": 9000, "verified": True})
    assert revised.json()["value"] == 9000 and revised.json()["verified"] is True
    assert api.post("/api/criteria", json={**body, "operator": None}).status_code == 422
    assert api.post("/api/criteria", json={**body, "effective_to": "2026-01-01"}).status_code == 422

    # 이미 완료된 실행은 실행 시점의 기준 사본을 유지한다.
    meta = api.get("/api/meta", params={"run_id": run.id}).json()
    assert meta["run"]["id"] == run.id

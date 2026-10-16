"""명령행 진입점. 수집·분석은 배치로 실행하며 OS 스케줄러(cron)에서 호출할 수 있다."""

from __future__ import annotations

import argparse
import sys
from datetime import date

import httpx
from sqlalchemy import select

from . import models as m
from .analysis import run_analysis
from .collector import collect
from .collector.file_adapter import FileAdapter
from .collector.molit_rtms import MolitRtmsAdapter, RtmsError
from .collector.sample import SampleAdapter
from .config import get_settings
from .criteria import seed_criteria
from .db import init_db, make_engine, make_session_factory, session_scope

DATASETS = sorted(
    [
        m.DS_ADMIN_AREAS,
        m.DS_PARCELS,
        m.DS_BUILDINGS,
        m.DS_ROADS,
        m.DS_STATIONS,
        m.DS_STATION_ENTRANCES,
        m.DS_ZONING_AREAS,
        m.DS_REDEVELOPMENT_ZONES,
        m.DS_BUSINESS_DISTRICTS,
        m.DS_TRANSACTIONS,
    ]
)


def _date(text: str) -> date:
    return date.fromisoformat(text)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="redev-radar", description="역세권 재개발 빌라 경매물건 탐색기")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="테이블을 만들고 기준값·가정값 시드를 등록한다")

    seed = sub.add_parser("seed-criteria", help="기준값·가정값 시드 파일을 등록한다 (비어 있는 종류만)")
    seed.add_argument("--file", help="시드 JSON 경로 (기본: 내장 시드)")

    coll = sub.add_parser("collect", help="자료를 수집해 스냅샷으로 적재한다")
    coll_sub = coll.add_subparsers(dest="source", required=True)
    sample = coll_sub.add_parser("sample", help="합성 샘플 데이터 (실제 지역 아님)")
    file = coll_sub.add_parser("file", help="GeoJSON/CSV 파일")
    file.add_argument("--dataset", required=True, choices=DATASETS)
    file.add_argument("--path", required=True)
    file.add_argument("--scope", default="ALL", help="자료 범위 (예: 시군구코드). 같은 범위의 새 스냅샷이 이전 것을 대체한다")
    file.add_argument("--map", action="append", default=[], metavar="원본필드=공통필드", help="필드명 매핑 (여러 번 지정 가능)")
    rtms = coll_sub.add_parser("rtms", help="국토교통부 연립다세대 매매 실거래가 API")
    rtms.add_argument("--lawd", required=True, help="법정동 시군구코드 5자리")
    rtms.add_argument("--ym", required=True, help="계약년월 YYYYMM")
    for p in (sample, file, rtms):
        p.add_argument("--as-of", type=_date, default=date.today(), help="자료 기준일 (기본: 오늘)")

    analyze = sub.add_parser("analyze", help="분석 기준일로 파이프라인을 실행한다")
    analyze.add_argument("--base-date", type=_date, default=date.today())
    analyze.add_argument("--note")

    sub.add_parser("runs", help="분석 실행 목록")

    demo = sub.add_parser("demo", help="초기화 + 샘플 수집 + 분석을 한 번에 실행한다")
    demo.add_argument("--base-date", type=_date, default=date.today())

    serve = sub.add_parser("serve", help="조회 API와 지도 화면을 띄운다")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def _print_snapshots(snapshots: list[m.CollectionSnapshot]) -> None:
    for s in snapshots:
        skipped = f", 제외 {s.skipped_count}건" if s.skipped_count else ""
        print(f"  [{s.id}] {s.dataset} ({s.scope}): {s.record_count}건{skipped}")


def _print_run(run: m.AnalysisRun, session) -> int:
    print(f"분석 실행 #{run.id} 기준일 {run.base_date} → {run.status}")
    if run.status != "COMPLETED":
        print(run.error, file=sys.stderr)
        return 1
    blocks = session.scalars(select(m.CandidateBlock.variant).where(m.CandidateBlock.run_id == run.id)).all()
    for variant in sorted(set(blocks)):
        print(f"  후보 블록 [{variant}]: {blocks.count(variant)}개")
    for warning in run.warnings:
        print(f"  경고: {warning}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()

    if args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return 0

    engine = make_engine(settings.database_url)
    init_db(engine)
    factory = make_session_factory(engine)

    with session_scope(factory) as session:
        if args.command in ("init-db", "seed-criteria", "demo"):
            counts = seed_criteria(session, getattr(args, "file", None))
            print(f"시드 등록: 사업방식 {counts['project_types']}, 기준값 {counts['criteria']}, 가정값 {counts['assumptions']}")
            if counts["criteria"]:
                print("  주의: 시드 기준값은 법령 원문과 대조하지 않은 예시값입니다 (verified=false).")

        if args.command == "collect":
            if args.source == "sample":
                adapter = SampleAdapter()
            elif args.source == "file":
                mapping = dict(item.split("=", 1) for item in args.map)
                adapter = FileAdapter(args.dataset, args.path, scope=args.scope, field_mapping=mapping)
            else:
                try:
                    adapter = MolitRtmsAdapter(settings.data_go_kr_key or "", args.lawd, args.ym)
                except RtmsError as exc:
                    print(exc, file=sys.stderr)
                    return 1
            try:
                snapshots = collect(session, adapter, args.as_of)
            except (RtmsError, httpx.HTTPError, ValueError, OSError) as exc:
                print(f"수집 실패: {exc}", file=sys.stderr)
                return 1
            print(f"수집 완료 (기준일 {args.as_of})")
            _print_snapshots(snapshots)

        if args.command == "demo":
            print("샘플 데이터 수집 (합성 데이터, 실제 지역 아님)")
            _print_snapshots(collect(session, SampleAdapter(), args.base_date))
            session.commit()

        if args.command in ("analyze", "demo"):
            return _print_run(run_analysis(session, args.base_date, getattr(args, "note", None)), session)

        if args.command == "runs":
            for run in session.scalars(select(m.AnalysisRun).order_by(m.AnalysisRun.id.desc()).limit(20)):
                print(f"#{run.id} 기준일 {run.base_date} {run.status} ({run.started_at:%Y-%m-%d %H:%M})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

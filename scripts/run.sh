#!/usr/bin/env bash
# API 서버 실행 스크립트.
#
#   scripts/run.sh            DB에 데이터가 없으면 더미 데이터를 만든 뒤 서버를 띄운다
#   scripts/run.sh --reset    DB를 지우고 더미 데이터를 새로 만든 뒤 서버를 띄운다
#   PORT=8010 scripts/run.sh  포트 지정 (기본 8000)
#
# 더미 데이터: 기준값·가정값 시드 + 합성 샘플 지역(실제 지역 아님) + 분석 실행 1회
# Swagger UI: http://127.0.0.1:${PORT}/docs

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8000}"
RESET=0
for arg in "$@"; do
  case "$arg" in
    --reset) RESET=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "알 수 없는 옵션: $arg" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------- 가상환경

if [[ ! -x .venv/bin/redev-radar ]]; then
  echo "가상환경을 만들고 의존성을 설치합니다..."
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -e ".[dev]"
fi
PY=.venv/bin/python
CLI=.venv/bin/redev-radar

# ---------------------------------------------------------------- DB

DB_FILE=$("$PY" -c "
from sqlalchemy.engine import make_url
from redev_radar.config import get_settings
url = make_url(get_settings().database_url)
print(url.database if url.get_backend_name() == 'sqlite' else '')
")

if [[ "$RESET" == 1 ]]; then
  if [[ -z "$DB_FILE" ]]; then
    echo "--reset 은 SQLite DB에서만 지원합니다." >&2
    exit 1
  fi
  echo "DB를 삭제합니다: $DB_FILE"
  rm -f "$DB_FILE"
fi

HAS_RUN=$("$PY" -c "
from sqlalchemy import select
from redev_radar import models as m
from redev_radar.config import get_settings
from redev_radar.db import init_db, make_engine, make_session_factory
engine = make_engine(get_settings().database_url)
init_db(engine)
with make_session_factory(engine)() as s:
    print(1 if s.scalar(select(m.AnalysisRun.id).where(m.AnalysisRun.status == 'COMPLETED').limit(1)) else 0)
")

if [[ "$HAS_RUN" == 0 ]]; then
  echo "더미 데이터를 만듭니다 (기준값 시드 + 합성 샘플 지역 + 분석)..."
  "$CLI" demo
fi

# ---------------------------------------------------------------- 테스트용 값 안내

"$PY" - <<'EOF'
from sqlalchemy import select
from redev_radar import models as m
from redev_radar.config import get_settings
from redev_radar.db import make_engine, make_session_factory

with make_session_factory(make_engine(get_settings().database_url))() as s:
    run = s.scalar(select(m.AnalysisRun).where(m.AnalysisRun.status == "COMPLETED").order_by(m.AnalysisRun.id.desc()).limit(1))
    block = s.scalar(select(m.CandidateBlock).where(m.CandidateBlock.run_id == run.id).order_by(m.CandidateBlock.id).limit(1))
    overlap = s.scalar(select(m.ZoneOverlap.block_id).where(m.ZoneOverlap.run_id == run.id).limit(1))
    criterion = s.scalar(select(m.RegulationCriterion).where(m.RegulationCriterion.item_code == "MIN_AREA").order_by(m.RegulationCriterion.id).limit(1))
    print()
    print("Swagger에서 쓸 수 있는 값")
    print(f"  run_id       {run.id}  (기준일 {run.base_date})")
    print(f"  variant      FINE / MEDIUM / COARSE")
    print(f"  block_id     {block.id}" + (f", {overlap} (기존 정비구역과 겹침)" if overlap else ""))
    if criterion:
        print(f"  criterion_id {criterion.id}  ({criterion.project_type} {criterion.item_code} = {criterion.value:g})")
EOF

echo
echo "Swagger UI: http://127.0.0.1:$PORT/docs"
echo "지도 화면:  http://127.0.0.1:$PORT/"
echo "종료: Ctrl+C 또는 scripts/stop.sh"
echo

# exec 후에도 PID가 그대로이므로 stop.sh가 이 값으로 서버를 찾는다.
mkdir -p var
echo "$$ $PORT" > var/server.pid
exec "$CLI" serve --host 127.0.0.1 --port "$PORT"

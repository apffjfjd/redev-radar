#!/usr/bin/env bash
# scripts/run.sh 로 띄운 API 서버를 종료한다.
#
#   scripts/stop.sh            var/server.pid 에 기록된 서버를 종료한다
#   PORT=8010 scripts/stop.sh  PID 파일이 없으면 이 포트의 redev-radar 서버를 찾아 종료한다 (기본 8000)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$ROOT/var/server.pid"

is_server() {
  ps -p "$1" -o command= 2>/dev/null | grep -q "redev-radar serve"
}

stop_pid() {
  local pid=$1
  kill "$pid"
  for _ in $(seq 1 20); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "서버를 종료했습니다 (PID $pid)."
      return 0
    fi
    sleep 0.25
  done
  echo "정상 종료되지 않아 강제 종료합니다 (PID $pid)."
  kill -9 "$pid" 2>/dev/null || true
}

if [[ -f "$PID_FILE" ]]; then
  read -r PID PORT_IN_FILE < "$PID_FILE"
  rm -f "$PID_FILE"
  if is_server "$PID"; then
    stop_pid "$PID"
    exit 0
  fi
  echo "PID 파일의 서버(PID $PID)는 이미 종료되어 있습니다."
  PORT="${PORT:-$PORT_IN_FILE}"
fi

PORT="${PORT:-8000}"
FOUND=0
for pid in $(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null || true); do
  if is_server "$pid"; then
    stop_pid "$pid"
    FOUND=1
  fi
done
if [[ "$FOUND" == 0 ]]; then
  echo "포트 $PORT 에서 실행 중인 redev-radar 서버가 없습니다."
fi

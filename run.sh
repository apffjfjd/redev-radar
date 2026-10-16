#!/usr/bin/env bash
# 프로젝트 루트에서 간편하게 개발 서버를 실행한다.
#
#   ./run.sh
#   ./run.sh --reset
#   PORT=8010 ./run.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/scripts/run.sh" "$@"

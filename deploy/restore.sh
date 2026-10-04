#!/usr/bin/env bash
# DB 복원: 지금 DB를 지우고 백업 파일 내용으로 바꾼다 (서버 이전, 사고 복구용).
#
#   bash deploy/restore.sh ~/stocksim-backup/stocksim-2026-10-05-0400.dump --yes
#
# 순서: 앱 끄기 → DB 지우고 새로 만들기 → 복원 → Redis 비우기 → 앱 켜기
# Redis(진행 중인 게임 상태)는 비운다. 앱이 DB의 복구 지점(game_snapshots)에서 다시 만든다.
set -euo pipefail

cd "$(dirname "$0")/.."
FILE="${1:-}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  echo "사용법: bash deploy/restore.sh 백업파일.dump --yes" >&2
  exit 2
fi
if [ "${2:-}" != "--yes" ]; then
  echo "지금 DB의 모든 데이터가 '$FILE' 내용으로 바뀝니다. 계속하려면 끝에 --yes 를 붙이세요." >&2
  exit 2
fi

env_get() { { grep -E "^$1=" .env 2>/dev/null || true; } | tail -n1 | cut -d= -f2- | tr -d '\r' | sed 's/[[:space:]]*#.*$//; s/[[:space:]]*$//'; }
APP_DB="${APP_DB:-$(env_get APP_DB)}"; APP_DB="${APP_DB:-market_sim}"

echo "1/5 파일 검사"
docker compose up -d db redis
until docker compose exec -T db sh -c 'pg_isready -U "$POSTGRES_USER"' > /dev/null 2>&1; do sleep 1; done
docker compose exec -T db pg_restore -l < "$FILE" > /dev/null

echo "2/5 앱 끄기"
docker compose stop app

echo "3/5 DB '$APP_DB' 새로 만들기"
docker compose exec -T db sh -c "dropdb -U \"\$POSTGRES_USER\" --if-exists '$APP_DB' && createdb -U \"\$POSTGRES_USER\" '$APP_DB'"

echo "4/5 복원"
docker compose exec -T db sh -c "pg_restore -U \"\$POSTGRES_USER\" -d '$APP_DB' --no-owner --exit-on-error" < "$FILE"
docker compose exec -T redis redis-cli FLUSHDB > /dev/null

echo "5/5 앱 켜기 (마이그레이션이 남아 있으면 시작할 때 적용됨)"
docker compose up -d app
echo "완료"

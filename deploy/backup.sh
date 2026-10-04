#!/usr/bin/env bash
# DB 백업: ~/stocksim-backup/stocksim-YYYY-MM-DD-HHMM.dump 를 만들고, 14일 지난 것은 지운다.
# .env 에 RCLONE_REMOTE (예: gdrive:stocksim-backup) 가 있으면 서버 밖(구글 드라이브 등)으로도 복사하고
# 그쪽은 60일 지난 것을 지운다. 무료 서버는 통째로 사라질 수 있으므로 서버 밖 복사를 꼭 켜 둘 것.
#
#   손으로:  bash deploy/backup.sh
#   매일 04:00 (crontab -e):
#     0 4 * * * bash $HOME/stocksim/deploy/backup.sh >> $HOME/stocksim-backup/backup.log 2>&1
#   복원:    bash deploy/restore.sh 백업파일.dump --yes
set -euo pipefail

cd "$(dirname "$0")/.."
DIR="${BACKUP_DIR:-$HOME/stocksim-backup}"
KEEP_DAYS="${KEEP_DAYS:-14}"

# .env 에서 값 하나 읽기 (윈도우에서 만든 파일의 \r, 줄 끝 주석 제거)
env_get() { { grep -E "^$1=" .env 2>/dev/null || true; } | tail -n1 | cut -d= -f2- | tr -d '\r' | sed 's/[[:space:]]*#.*$//; s/[[:space:]]*$//'; }
APP_DB="${APP_DB:-$(env_get APP_DB)}"; APP_DB="${APP_DB:-market_sim}"
REMOTE="${RCLONE_REMOTE:-$(env_get RCLONE_REMOTE)}"

mkdir -p "$DIR"
FILE="$DIR/stocksim-$(date +%F-%H%M).dump"
echo "[$(date '+%F %T')] 백업 시작 → $FILE"

# -Fc: 압축된 사용자 지정 형식 (pg_restore 로 복원). 실패하면 반쯤 쓰인 파일을 남기지 않음
if ! docker compose exec -T db sh -c "pg_dump -U \"\$POSTGRES_USER\" -d '$APP_DB' -Fc" > "$FILE.part"; then
  rm -f "$FILE.part"
  echo "[$(date '+%F %T')] 실패: pg_dump" >&2
  exit 1
fi
# 목차를 읽어 보아 깨진 파일이 아닌지 확인
if ! docker compose exec -T db pg_restore -l < "$FILE.part" > /dev/null; then
  rm -f "$FILE.part"
  echo "[$(date '+%F %T')] 실패: 백업 파일 검사" >&2
  exit 1
fi
mv "$FILE.part" "$FILE"
echo "[$(date '+%F %T')] 완료 $(du -h "$FILE" | cut -f1)"

find "$DIR" -name 'stocksim-*.dump' -mtime +"$KEEP_DAYS" -delete

if [ -n "$REMOTE" ]; then
  if command -v rclone > /dev/null; then
    rclone copy "$FILE" "$REMOTE"
    rclone delete --min-age 60d --include 'stocksim-*.dump' "$REMOTE"
    echo "[$(date '+%F %T')] 서버 밖 복사 완료 → $REMOTE"
  else
    echo "[$(date '+%F %T')] 경고: RCLONE_REMOTE 가 있지만 rclone 이 설치되지 않음 (서버 밖 복사 안 됨)" >&2
    exit 1
  fi
fi

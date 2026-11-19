#!/bin/sh
# Nightly backup of PageMint's data (users, saved companies, usage). Run by cron from /opt/pagemint.
set -eu
cd "$(dirname "$0")"
PAGEMINT_BACKUP_PAR_URL=$(grep -E '^PAGEMINT_BACKUP_PAR_URL=' .env | cut -d= -f2- || true)
TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p backups
for db in data/hosted.sqlite3 data/jobs.sqlite3; do
  [ -f "$db" ] && sqlite3 "$db" ".backup 'backups/$(basename "$db" .sqlite3)-$TS.sqlite3'"
done
tar czf "backups/pagemint-$TS.tar.gz" -C backups $(cd backups && ls ./*-"$TS".sqlite3 2>/dev/null | xargs -n1 basename)
rm -f backups/*-"$TS".sqlite3
if [ -n "$PAGEMINT_BACKUP_PAR_URL" ]; then
  curl -fsS -T "backups/pagemint-$TS.tar.gz" "${PAGEMINT_BACKUP_PAR_URL%/}/pagemint-$TS.tar.gz"
fi
# keep the newest 14 local backups
ls -1t backups/pagemint-*.tar.gz | tail -n +15 | xargs -r rm --

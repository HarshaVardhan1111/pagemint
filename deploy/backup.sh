#!/bin/sh
# Nightly backup of PageMint's data (users, saved companies, usage). Run by cron from /opt/pagemint.
# The copy is made inside the PageMint container (which owns the data), then kept here and,
# if configured, uploaded to Oracle Object Storage.
set -eu
cd "$(dirname "$0")"
envval() { grep -E "^$1=" .env | cut -d= -f2- || true; }
PAR=$(envval PAGEMINT_BACKUP_PAR_URL)
SVC=pagemint
if [ "$(envval COMPOSE_PROFILES)" = "small" ]; then SVC=pagemint-small; fi
NAME="pagemint-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
mkdir -p backups
docker compose exec -T "$SVC" pagemint admin backup "/data/backups/$NAME" --keep 3
cp "data/backups/$NAME" "backups/$NAME"
if [ -n "$PAR" ]; then
  curl -fsS -T "backups/$NAME" "${PAR%/}/$NAME"
fi
# keep the newest 14 backups here
ls -1t backups/pagemint-*.tar.gz | tail -n +15 | xargs -r rm --

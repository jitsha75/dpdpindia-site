#!/usr/bin/env bash
# NF-06: restore one database, or one table of it, from a backup made by backup.sh.
#   scripts/restore.sh backups/<ts>/contract_db.dump               restore the whole database
#   scripts/restore.sh backups/<ts>/contract_db.dump contracts      restore one table (point in time = the backup)
set -eu
FILE="$1"; TABLE="${2:-}"; DB=$(basename "$FILE" .dump)
if [ -n "$TABLE" ]; then
  docker exec -i ghip-postgres pg_restore -U postgres -d "$DB" --clean --if-exists -t "$TABLE" < "$FILE"
else
  docker exec -i ghip-postgres pg_restore -U postgres -d "$DB" --clean --if-exists < "$FILE"
fi
echo "restored $DB ${TABLE:+table $TABLE}"

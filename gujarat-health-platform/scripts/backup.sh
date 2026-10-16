#!/usr/bin/env bash
# NF-06: dump every service database (one file per database) into backups/<timestamp>/. Encrypt the folder before it leaves the machine.
set -eu
cd "$(dirname "$0")/.."
TS=$(date +%Y%m%d-%H%M%S); OUT="backups/$TS"; mkdir -p "$OUT"
for db in $(docker exec ghip-postgres psql -U postgres -Atc "select datname from pg_database where datname like '%_db'"); do
  docker exec ghip-postgres pg_dump -U postgres -Fc "$db" > "$OUT/$db.dump"; echo "backed up $db"
done
echo "Backups in $OUT"

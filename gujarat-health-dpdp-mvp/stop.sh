#!/usr/bin/env bash
# DPDP Privacy Platform - Gujarat Health edition: stop script. Frees the memory and CPU the demo was using.
#
#   ./stop.sh                  stop and remove the containers (your demo data is kept for next time).
#                              If start.sh had to launch Docker for you and nothing else is running, Docker is quit too.
#   ./stop.sh --keep-docker    leave Docker Desktop / OrbStack / Colima running
#   ./stop.sh --quit-docker    always quit Docker as well (releases the memory its VM reserves)
#   ./stop.sh --purge          also delete the demo database and files (next start re-seeds, ~1 minute)
#   ./stop.sh --clean          --purge plus delete the demo's images and build cache (frees disk space)
#
set -u
cd "$(dirname "$0")" || exit 1
STATE_FILE=".runtime_started_by_start_sh"
PURGE=0; CLEAN=0; QUIT=auto

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; N=$'\033[0m'; else B=""; G=""; Y=""; N=""; fi
say()  { printf '%s\n' "${B}==>${N} $*"; }
ok()   { printf '%s\n' "${G} ok${N}  $*"; }
warn() { printf '%s\n' "${Y}warn${N} $*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --purge)       PURGE=1 ;;
    --clean)       PURGE=1; CLEAN=1 ;;
    --quit-docker) QUIT=yes ;;
    --keep-docker) QUIT=no ;;
    -h|--help)     sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 1 ;;
  esac
  shift
done

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  ok "Docker is not running - nothing to stop."
  exit 0
fi

# compose needs the variables only to parse the file; real values come from .env when present
[ -f .env ] && { set -a; . ./.env; set +a; }
export DB_PASSWORD="${DB_PASSWORD:-x}" TENANT_KEY="${TENANT_KEY:-x}" LEDGER_KEY="${LEDGER_KEY:-x}"

if [ $PURGE -eq 1 ]; then
  say "Stopping containers and deleting the demo database and files..."
  docker compose down -v --remove-orphans
else
  say "Stopping containers (demo data is kept)..."
  docker compose down --remove-orphans
fi
ok "Gujarat Health DPDP demo containers are stopped and removed"

if [ $CLEAN -eq 1 ]; then
  say "Removing the demo images and build cache..."
  docker image rm gj-health-dpdp-app:1.0 >/dev/null 2>&1 && ok "removed gj-health-dpdp-app:1.0"
  docker image rm postgres:16-alpine >/dev/null 2>&1 && ok "removed postgres:16-alpine (only if no other container uses it)"
  docker builder prune -f >/dev/null 2>&1
  rm -f .env
  ok "build cache cleared"
fi

# ---- release the Docker VM itself
OTHERS="$(docker ps -q 2>/dev/null | wc -l | tr -d ' ')"
RUNTIME=""; [ -f "$STATE_FILE" ] && RUNTIME="$(cat "$STATE_FILE")"
DO_QUIT=0
case "$QUIT" in
  yes)  DO_QUIT=1 ;;
  auto) [ -n "$RUNTIME" ] && [ "$OTHERS" = "0" ] && DO_QUIT=1 ;;
esac
if [ $DO_QUIT -eq 1 ]; then
  if [ "$OTHERS" != "0" ]; then warn "$OTHERS other container(s) are still running - leaving Docker open."
  else
    [ -z "$RUNTIME" ] && { ctx="$(docker context show 2>/dev/null)"; case "$ctx" in *orbstack*) RUNTIME=orbstack ;; *colima*) RUNTIME=colima ;; *) RUNTIME=docker-desktop ;; esac; }
    say "Quitting $RUNTIME to release its reserved memory..."
    case "$RUNTIME" in
      docker-desktop) osascript -e 'quit app "Docker"' >/dev/null 2>&1 || true; osascript -e 'quit app "Docker Desktop"' >/dev/null 2>&1 || true ;;
      orbstack)       (command -v orbctl >/dev/null 2>&1 && orbctl stop >/dev/null 2>&1) || osascript -e 'quit app "OrbStack"' >/dev/null 2>&1 || true ;;
      colima)         colima stop >/dev/null 2>&1 || true ;;
    esac
    rm -f "$STATE_FILE"
    ok "Docker is shutting down; memory returns to macOS in a few seconds"
  fi
elif [ "$OTHERS" = "0" ]; then
  warn "Docker itself is still running and keeps its VM memory reserved. Run  ./stop.sh --quit-docker  to release that too."
fi
printf '\n%s\n' "Done. To run the demo again:  ./start.sh"
exit 0

#!/usr/bin/env bash
# Stop the Gujarat Health Integrated Data Platform and free memory.
#   ./stop.sh               stop and remove the containers (data is kept for the next start)
#   ./stop.sh --wipe        also delete all demo databases (next start re-seeds the original story)
#   ./stop.sh --quit-docker also quit Docker Desktop / OrbStack / Colima if start.sh launched it
set -u
cd "$(dirname "$0")" || exit 1
WIPE=0; QUIT=0
for a in "$@"; do case "$a" in --wipe) WIPE=1 ;; --quit-docker) QUIT=1 ;; -h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; esac; done
F=docker-compose.yml; [ -f .compose.run.yml ] && F=.compose.run.yml
if docker info >/dev/null 2>&1; then
  if [ $WIPE -eq 1 ]; then docker compose -f "$F" --profile full down -v --remove-orphans; else docker compose -f "$F" --profile full down --remove-orphans; fi
  echo "Platform stopped."
fi
if [ $QUIT -eq 1 ] && [ -f .runtime_started_by_start_sh ]; then
  case "$(cat .runtime_started_by_start_sh)" in
    orbstack) osascript -e 'quit app "OrbStack"' ;; docker-desktop) osascript -e 'quit app "Docker"' ;; colima) colima stop ;;
  esac
  rm -f .runtime_started_by_start_sh; echo "Docker runtime stopped."
fi

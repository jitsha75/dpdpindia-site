#!/usr/bin/env bash
# DPDP Privacy Platform - Gujarat Health & Family Welfare edition: start script (macOS / Linux; works with the stock bash 3.2 on macOS)
#
#   ./start.sh                 start (first run builds / loads images and seeds 10,000 synthetic patients and ~8,500 facilities)
#   ./start.sh --reset         wipe the demo database and start from the original story
#   ./start.sh --rebuild       rebuild the application image (after changing code)
#   ./start.sh --no-open       do not open the browser
#   ./start.sh --port 8091     use another port (default 8090; the next free one is used if it is busy)
#
set -u
cd "$(dirname "$0")" || exit 1

APP_IMAGE="gj-health-dpdp-app:1.0"
DB_IMAGE="postgres:16-alpine"
APP_CONTAINER="gjh-dpdp-app"
STATE_FILE=".runtime_started_by_start_sh"
RESET=0; REBUILD=0; OPEN=1; PORT_ARG=""

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B=""; G=""; Y=""; R=""; N=""; fi
say()  { printf '%s\n' "${B}==>${N} $*"; }
ok()   { printf '%s\n' "${G} ok${N}  $*"; }
warn() { printf '%s\n' "${Y}warn${N} $*"; }
die()  { printf '%s\n' "${R}error${N} $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --reset)   RESET=1 ;;
    --rebuild) REBUILD=1 ;;
    --no-open) OPEN=0 ;;
    --port)    shift; PORT_ARG="${1:-}" ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

# ---------------------------------------------------------------- 1. Docker
command -v docker >/dev/null 2>&1 || die "Docker is not installed. Install Docker Desktop for Mac (Apple silicon) from https://www.docker.com/products/docker-desktop/ - or OrbStack / Colima - then run ./start.sh again."

docker_ready() { docker info >/dev/null 2>&1; }

launch_runtime() {   # starts whichever Docker runtime is installed; records what it started so stop.sh can quit it again
  local ctx; ctx="$(docker context show 2>/dev/null || true)"
  case "$(uname -s)" in
    Darwin)
      if   [[ "$ctx" == *orbstack* ]] && [ -d /Applications/OrbStack.app ]; then open -a OrbStack; echo "orbstack" > "$STATE_FILE"
      elif [[ "$ctx" == *colima*   ]] && command -v colima >/dev/null 2>&1; then colima start --cpu 4 --memory 4 >/dev/null 2>&1; echo "colima" > "$STATE_FILE"
      elif [ -d /Applications/Docker.app ];    then open -a Docker;    echo "docker-desktop" > "$STATE_FILE"
      elif [ -d /Applications/OrbStack.app ];  then open -a OrbStack;  echo "orbstack" > "$STATE_FILE"
      elif command -v colima >/dev/null 2>&1;  then colima start --cpu 4 --memory 4 >/dev/null 2>&1; echo "colima" > "$STATE_FILE"
      else return 1; fi ;;
    *) return 1 ;;
  esac
}

if ! docker_ready; then
  say "Docker is not running - starting it..."
  launch_runtime || die "Could not start Docker automatically. Open Docker Desktop (or OrbStack / Colima), wait until it says it is running, then run ./start.sh again."
  n=0
  until docker_ready; do
    n=$((n+1)); [ $n -gt 60 ] && die "Docker did not become ready within 3 minutes. Open it manually and re-run ./start.sh."
    printf '.'; sleep 3
  done
  printf '\n'
  LAUNCHED=1
fi
[ -n "${LAUNCHED:-}" ] || rm -f "$STATE_FILE"   # Docker was already running: stop.sh must not quit it
ok "Docker daemon is running"

docker compose version >/dev/null 2>&1 || die "'docker compose' (v2) is not available. Update Docker Desktop, or install the compose plugin."
DC="docker compose"

HOST_ARCH="$(uname -m)"
DAEMON_ARCH="$(docker info --format '{{.Architecture}}' 2>/dev/null || echo unknown)"
case "$DAEMON_ARCH" in
  aarch64|arm64) ok "Docker is running natively on Apple silicon (arm64) - no emulation" ;;
  x86_64|amd64)
    if [ "$HOST_ARCH" = "arm64" ] || [ "$HOST_ARCH" = "aarch64" ]; then
      warn "Docker is running in Intel (x86_64) mode on an Apple-silicon Mac, so containers are emulated and slower."
      warn "In Docker Desktop > Settings > General, use the Apple-silicon (arm64) engine, or switch to OrbStack."
    else ok "Docker architecture: $DAEMON_ARCH"; fi ;;
  *) warn "Could not determine Docker architecture ($DAEMON_ARCH)" ;;
esac

MEM_BYTES="$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
if [ "${MEM_BYTES:-0}" -gt 0 ] 2>/dev/null; then
  MEM_GB=$(( MEM_BYTES / 1073741824 ))
  if [ "$MEM_BYTES" -lt 3221225472 ]; then
    warn "Docker only has ~${MEM_GB} GB of memory. The demo needs about 1.5 GB. Raise it in Docker Desktop > Settings > Resources."
  else ok "Docker has ~${MEM_GB} GB available (the demo uses roughly 0.5 - 1 GB)"; fi
fi

# ---------------------------------------------------------------- 2. configuration (.env)
rand_hex() { if command -v openssl >/dev/null 2>&1; then openssl rand -hex "$1"; else LC_ALL=C tr -dc 'a-f0-9' < /dev/urandom | head -c $(( $1 * 2 )); fi; }

if [ ! -f .env ]; then
  if docker volume inspect gj-health-dpdp_pgdata >/dev/null 2>&1 && [ $RESET -eq 0 ]; then
    die "Found an existing demo database but no .env file (its password and keys are lost). Run ./start.sh --reset to start afresh."
  fi
  say "Creating .env with fresh random keys (kept on this Mac only)"
  { echo "# generated by start.sh - do not share"; echo "DB_PASSWORD=$(rand_hex 12)"; echo "TENANT_KEY=$(rand_hex 24)"; echo "LEDGER_KEY=$(rand_hex 24)"; echo "PORT=8090"; echo "SEED_CUSTOMERS=10000"; } > .env
  chmod 600 .env
fi
set -a; . ./.env; set +a
[ -n "$PORT_ARG" ] && PORT="$PORT_ARG"
PORT="${PORT:-8090}"

app_running() { [ -n "$(docker ps --filter "name=^${APP_CONTAINER}\$" --filter status=running -q 2>/dev/null)" ]; }
port_busy()   { command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

if ! app_running; then
  p="$PORT"; n=0
  while port_busy "$p" && [ $n -lt 25 ]; do p=$((p+1)); n=$((n+1)); done
  if [ "$p" != "$PORT" ]; then warn "Port $PORT is in use by another program - using $p instead."; PORT="$p"; fi
fi
export PORT
{ grep -v '^PORT=' .env; echo "PORT=$PORT"; } > .env.tmp && mv .env.tmp .env && chmod 600 .env

# ---------------------------------------------------------------- 3. images (load from images/ when offline, otherwise build)
have_img() { docker image inspect "$1" >/dev/null 2>&1; }
need_images() { ! have_img "$APP_IMAGE" || ! have_img "$DB_IMAGE"; }

if [ $RESET -eq 1 ]; then
  say "Resetting: removing containers and the demo database"
  $DC down -v --remove-orphans >/dev/null 2>&1 || true
fi

if [ $REBUILD -eq 0 ] && need_images; then
  BUNDLE=""
  for f in images/gj-health-dpdp-images.tar.gz images/*.tar.gz images/*.tar images/*.tgz; do [ -f "$f" ] && BUNDLE="$f" && break; done
  if [ -n "$BUNDLE" ]; then
    say "Loading Docker images from $BUNDLE (offline bundle)..."
    docker load -i "$BUNDLE" || die "docker load failed for $BUNDLE"
  fi
fi

if [ $REBUILD -eq 1 ] || need_images; then
  say "Building the application image (first time only; needs internet once to fetch ~150 MB of base images)..."
  if ! $DC build; then
    die "Image build failed. If you are offline, run ./save-images.sh once on a connection and copy the images/ folder, or connect to the internet and retry."
  fi
fi
ok "Images ready: $APP_IMAGE, $DB_IMAGE"

# ---------------------------------------------------------------- 4. start
say "Starting containers..."
$DC up -d --no-build || die "docker compose up failed (see output above)."

URL="http://localhost:${PORT}"
say "Waiting for the platform (the first start loads 10,000 synthetic patients and the Gujarat facility network, usually 40-90 seconds)..."
t0=$(date +%s); last=0
while :; do
  if curl -fsS --noproxy "*" "http://127.0.0.1:${PORT}/api/health" 2>/dev/null | grep -q '"seeded":true'; then break; fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$APP_CONTAINER" 2>/dev/null)" != "true" ]; then
    $DC logs --tail 40 app; die "The application container stopped. Log above."
  fi
  now=$(date +%s); el=$((now - t0))
  [ $el -gt 420 ] && { $DC logs --tail 40 app; die "Timed out after 7 minutes. Log above; try ./start.sh --reset."; }
  if [ $((el - last)) -ge 10 ]; then printf '    still loading... %ss\n' "$el"; last=$el; fi
  sleep 2
done
ok "Platform is up after $(( $(date +%s) - t0 ))s"

printf '\n%s\n' "${B}Gujarat Health DPDP Platform (MVP demo) is ready:${N}  ${G}${URL}${N}"
printf '  - Start with the "Guided demo script" page in the left menu.\n'
printf '  - Switch persona (top right) to show separation of duties; demo OTP is 246810.\n'
printf '  - To free memory and CPU when you are done:  ./stop.sh\n\n'
docker stats --no-stream --format '  {{.Name}}: {{.MemUsage}}  cpu {{.CPUPerc}}' gjh-dpdp-db gjh-dpdp-app 2>/dev/null || true
printf '\n'

if [ $OPEN -eq 1 ]; then
  case "$(uname -s)" in
    Darwin) open "$URL" >/dev/null 2>&1 || true ;;
    Linux)  command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL" >/dev/null 2>&1 || true ;;
  esac
fi
exit 0

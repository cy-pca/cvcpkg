#!/bin/bash
#
# cvcpkg-server auto-deploy script.
#
# Invoked by the deploy-prod GitHub Actions workflow or manually.
# Runs on the production host (catx-03.tx.wtf).
#
# Usage:
#   ./scripts/auto-deploy.sh <git-sha>
#
# Steps:
#   1. Fetch origin, validate sha
#   2. Backup the database
#   3. Checkout target sha
#   4. Build the Docker image
#   5. Restart the stack
#   6. Wait for /healthz to go green

set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_FILE="${LOG_FILE:-/var/log/cvcpkg-deploy.log}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8420/healthz}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"
IMAGE_REPO="cvcpkg-server"
COMPOSE_FILE="docker-compose.production.yml"
ENV_FILE=".env.production"

SHA="${1:-}"
if ! [[ "$SHA" =~ ^[0-9a-f]{7,40}$ ]]; then
    echo "usage: $0 <git-sha>" >&2
    exit 2
fi

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
exec > >(tee -a "$LOG_FILE") 2>&1

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $*"; }

log "===================================================================="
log "auto-deploy starting: sha=$SHA"
log "===================================================================="

cd "$REPO_DIR"

# Detect docker compose
if docker compose version &>/dev/null; then
    DC="docker compose"
elif command -v docker-compose &>/dev/null; then
    DC="docker-compose"
else
    log "ERROR: docker compose not available"
    exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
    log "ERROR: $ENV_FILE missing in $REPO_DIR"
    exit 1
fi

# Fetch
log "fetching origin..."
git fetch --tags --prune origin

# Resolve the full sha
if ! FULL_SHA=$(git rev-parse --verify "${SHA}^{commit}" 2>/dev/null); then
    log "ERROR: sha $SHA not found in repo"
    exit 1
fi
SHORT_SHA="${FULL_SHA:0:7}"
RELEASE_TAG="prod-${SHORT_SHA}"
log "resolved sha: $FULL_SHA (release tag: $RELEASE_TAG)"

# Skip rebuild if already on this commit and image exists
CUR_HEAD=$(git rev-parse HEAD)
SKIP_BUILD=0
if [[ "$CUR_HEAD" == "$FULL_SHA" ]] && \
   docker image inspect "${IMAGE_REPO}:${RELEASE_TAG}" >/dev/null 2>&1; then
    log "already on $FULL_SHA with image built; will still restart"
    SKIP_BUILD=1
fi

# Backup database before mutations
log "backing up database..."
if $DC -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
   exec -T postgres pg_isready -U cvcpkg >/dev/null 2>&1; then
    mkdir -p backups
    $DC -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
        exec -T postgres pg_dump -U cvcpkg cvcpkg \
        | gzip > "backups/pre-deploy-${SHORT_SHA}.sql.gz"
    log "database backup created"
else
    log "postgres not running; skipping backup (fresh deploy?)"
fi

# Check for dirty working tree
if ! git diff --quiet || ! git diff --cached --quiet; then
    log "ERROR: working tree dirty; refusing to checkout"
    git status --short | head -20
    exit 1
fi

# Remove untracked files that conflict with target tree
mapfile -t blockers < <(
    comm -12 \
        <(git ls-files --others --exclude-standard | sort) \
        <(git ls-tree -r --name-only "$FULL_SHA" | sort) 2>/dev/null || true
)
for f in "${blockers[@]}"; do
    [[ -z "$f" ]] && continue
    log "removing untracked blocker: $f"
    rm -f -- "$f"
done

# Checkout target sha (detached)
log "checking out $FULL_SHA..."
git -c advice.detachedHead=false checkout --quiet "$FULL_SHA"

# cd into the cvcpkg directory where the compose files live
cd tools/cvcpkg

export CVCPKG_RELEASE="$RELEASE_TAG"
COMPOSE="$DC -f $COMPOSE_FILE --env-file $ENV_FILE"

if [[ "$SKIP_BUILD" -eq 0 ]]; then
    log "building ${IMAGE_REPO}:${RELEASE_TAG}..."
    $COMPOSE build --build-arg "CVCPKG_RELEASE=${RELEASE_TAG}" backend
    docker tag "${IMAGE_REPO}:${RELEASE_TAG}" "${IMAGE_REPO}:prod"
    log "tagged ${IMAGE_REPO}:prod -> ${IMAGE_REPO}:${RELEASE_TAG}"
fi

# Ensure postgres is up
log "ensuring postgres is up..."
$COMPOSE up -d postgres

# Restart backend
log "restarting backend on ${IMAGE_REPO}:${RELEASE_TAG}..."
$COMPOSE up -d --force-recreate backend

# Wait for health
log "waiting for health (timeout ${HEALTH_TIMEOUT}s)..."
deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
while (( $(date +%s) < deadline )); do
    if curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null | grep -q '"ok"'; then
        log "backend healthy"
        log "===================================================================="
        log "auto-deploy SUCCESS: sha=$FULL_SHA tag=$RELEASE_TAG"
        log "===================================================================="
        exit 0
    fi
    sleep 3
done

log "ERROR: backend failed health check within ${HEALTH_TIMEOUT}s"
log "--- last 80 lines of backend logs ---"
$COMPOSE logs --tail=80 backend || true
exit 1

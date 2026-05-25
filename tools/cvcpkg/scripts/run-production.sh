#!/bin/bash
#
# cvcpkg-server production management script.
#
# Usage:
#   ./scripts/run-production.sh              # Start stack
#   ./scripts/run-production.sh --build      # Rebuild and start
#   ./scripts/run-production.sh --down       # Stop all services
#   ./scripts/run-production.sh --logs       # View logs
#   ./scripts/run-production.sh --status     # Service status
#   ./scripts/run-production.sh --backup     # Backup database
#   ./scripts/run-production.sh --shell      # Shell into backend

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.production.yml"
ENV_FILE="$PROJECT_DIR/.env.production"
ENV_EXAMPLE="$PROJECT_DIR/.env.production.example"
BACKUP_DIR="$PROJECT_DIR/backups"

CVCPKG_RELEASE="${CVCPKG_RELEASE:-dev}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }

# ── Detect docker compose variant ──────────────────────────────
if docker compose version &>/dev/null; then
    DC="docker compose"
elif command -v docker-compose &>/dev/null; then
    DC="docker-compose"
else
    log_error "docker compose is not installed"
    exit 1
fi

COMPOSE="$DC -f $COMPOSE_FILE --env-file $ENV_FILE"

# ── Ensure .env.production exists ──────────────────────────────
ensure_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        if [[ -f "$ENV_EXAMPLE" ]]; then
            log_warn ".env.production not found — copying from example"
            cp "$ENV_EXAMPLE" "$ENV_FILE"
            # Generate a random password
            local pw
            pw=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
            sed -i "s/CHANGE_ME_GENERATE_A_STRONG_PASSWORD/$pw/" "$ENV_FILE"
            log_info "Generated random POSTGRES_PASSWORD in $ENV_FILE"
            log_warn "Review $ENV_FILE before starting production!"
        else
            log_error "Neither .env.production nor .env.production.example found"
            exit 1
        fi
    fi
}

# ── Commands ───────────────────────────────────────────────────

cmd_up() {
    ensure_env
    export CVCPKG_RELEASE
    log_info "Starting cvcpkg-server production stack (release: $CVCPKG_RELEASE)..."
    $COMPOSE up -d
    log_success "Stack started.  Health: http://127.0.0.1:8420/healthz"
}

cmd_build() {
    ensure_env
    export CVCPKG_RELEASE
    log_info "Building cvcpkg-server image (release: $CVCPKG_RELEASE)..."
    $COMPOSE build --build-arg "CVCPKG_RELEASE=$CVCPKG_RELEASE"
    cmd_up
}

cmd_down() {
    log_info "Stopping production stack..."
    $COMPOSE down
    log_success "Stack stopped."
}

cmd_logs() {
    $COMPOSE logs -f --tail=100 "${1:-}"
}

cmd_status() {
    $COMPOSE ps
}

cmd_backup() {
    ensure_env
    mkdir -p "$BACKUP_DIR"
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    local ts
    ts=$(date -u +"%Y%m%dT%H%M%SZ")
    local backup_file="$BACKUP_DIR/cvcpkg-${ts}.sql.gz"
    log_info "Backing up database to $backup_file ..."
    $DC -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
        exec -T postgres pg_dump \
        -U "${POSTGRES_USER:-cvcpkg}" \
        "${POSTGRES_DB:-cvcpkg}" \
        | gzip > "$backup_file"
    log_success "Backup created: $backup_file ($(du -h "$backup_file" | cut -f1))"
}

cmd_shell() {
    $COMPOSE exec backend /bin/bash
}

# ── Main ───────────────────────────────────────────────────────

case "${1:-}" in
    --build)   cmd_build ;;
    --down)    cmd_down ;;
    --logs)    cmd_logs "${2:-}" ;;
    --status)  cmd_status ;;
    --backup)  cmd_backup ;;
    --shell)   cmd_shell ;;
    ""|--up)   cmd_up ;;
    *)
        echo "Usage: $0 [--build|--down|--logs|--status|--backup|--shell]"
        exit 1
        ;;
esac

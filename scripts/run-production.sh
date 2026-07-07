#!/bin/bash
#
# cvcpkg-server Production Runner
#
# Manages the production cvcpkg-server stack (PostgreSQL + backend).
# Modelled after the atx-crypto-club/txwtf production scripts.
#
# Usage:
#   ./scripts/run-production.sh                # Start in foreground
#   ./scripts/run-production.sh --detach       # Start in background
#   ./scripts/run-production.sh --down         # Stop services
#   ./scripts/run-production.sh --build        # Rebuild and restart
#   ./scripts/run-production.sh --logs         # View logs
#   ./scripts/run-production.sh --status       # Service status
#   ./scripts/run-production.sh --backup       # Backup database
#   ./scripts/run-production.sh --restore FILE # Restore from backup
#   ./scripts/run-production.sh --shell        # Shell into backend
#   ./scripts/run-production.sh --reset-db     # DELETE ALL DATA

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
MAGENTA='\033[0;35m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $1"; }
log_prod()    { echo -e "${MAGENTA}[PROD]${NC} $1"; }

# ── Detect docker compose variant ──────────────────────────────

check_dependencies() {
    if ! command -v docker &>/dev/null; then
        log_error "Docker is not installed or not in PATH"
        exit 1
    fi
    if docker compose version &>/dev/null; then
        DC="docker compose"
    elif command -v docker-compose &>/dev/null; then
        DC="docker-compose"
    else
        log_error "Docker Compose is not installed"
        exit 1
    fi
    COMPOSE="$DC -f $COMPOSE_FILE --env-file $ENV_FILE"
    log_info "Using: $DC"
}

# ── Ensure .env.production exists ──────────────────────────────

ensure_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        if [[ -f "$ENV_EXAMPLE" ]]; then
            log_warn ".env.production not found — generating from example"
            cp "$ENV_EXAMPLE" "$ENV_FILE"
            local pw
            pw=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
            sed -i "s/CHANGE_ME_GENERATE_A_STRONG_PASSWORD/$pw/" "$ENV_FILE"
            log_info "Generated random POSTGRES_PASSWORD in $ENV_FILE"
            log_warn "Review $ENV_FILE before starting production!"
        else
            log_error "Neither .env.production nor .env.production.example found"
            log_info "Create one from the example:"
            log_info "  cp .env.production.example .env.production"
            log_info "  \$EDITOR .env.production"
            exit 1
        fi
    fi
}

# ── Start infrastructure ───────────────────────────────────────

start_infrastructure() {
    log_info "Starting PostgreSQL..."
    $COMPOSE up -d postgres

    log_info "Waiting for PostgreSQL health..."
    local deadline=$(( $(date +%s) + 60 ))
    while (( $(date +%s) < deadline )); do
        if $COMPOSE ps postgres 2>/dev/null | grep -q "healthy"; then
            log_success "PostgreSQL is healthy"
            return 0
        fi
        sleep 2
        echo -n "."
    done
    echo ""
    log_error "PostgreSQL did not become healthy"
    $COMPOSE logs postgres
    exit 1
}

# ── Build ──────────────────────────────────────────────────────

cmd_build() {
    ensure_env
    export CVCPKG_RELEASE
    log_info "Building cvcpkg-server image (release: $CVCPKG_RELEASE)..."
    $COMPOSE build --build-arg "CVCPKG_RELEASE=$CVCPKG_RELEASE" backend
    log_success "Image built"
}

# ── Start all ──────────────────────────────────────────────────

cmd_up() {
    local detach="${1:-false}"

    log_prod "=========================================="
    log_prod "    cvcpkg-server PRODUCTION STARTUP"
    log_prod "=========================================="
    echo ""

    ensure_env
    export CVCPKG_RELEASE

    start_infrastructure

    if [ "$detach" = true ]; then
        log_info "Starting backend (detached)..."
        $COMPOSE up -d backend
        log_success "Backend started in background"
        echo ""

        log_info "Waiting for health..."
        local deadline=$(( $(date +%s) + 60 ))
        while (( $(date +%s) < deadline )); do
            if curl -sf http://127.0.0.1:8420/healthz >/dev/null 2>&1; then
                local health
                health=$(curl -s http://127.0.0.1:8420/healthz)
                log_success "Backend healthy: $health"
                echo ""
                log_info "Landing page:  http://127.0.0.1:8420"
                log_info "Health check:  http://127.0.0.1:8420/healthz"
                log_info "API docs:      http://127.0.0.1:8420/docs"
                log_info "View logs:     $(basename "$0") --logs"
                log_info "Stop:          $(basename "$0") --down"
                return 0
            fi
            sleep 2
        done
        log_error "Backend did not become healthy"
        $COMPOSE logs --tail=30 backend
        exit 1
    else
        log_info "Starting backend (foreground, Ctrl+C to stop)..."
        echo ""
        $COMPOSE up backend
    fi
}

# ── Stop ───────────────────────────────────────────────────────

cmd_down() {
    log_info "Stopping production stack..."
    $COMPOSE down
    log_success "Stack stopped"
}

# ── Logs ───────────────────────────────────────────────────────

cmd_logs() {
    local service="${1:-}"
    if [ -n "$service" ]; then
        $COMPOSE logs -f --tail=100 "$service"
    else
        $COMPOSE logs -f --tail=100
    fi
}

# ── Status ─────────────────────────────────────────────────────

cmd_status() {
    log_info "Service status:"
    $COMPOSE ps
    echo ""
    if curl -sf http://127.0.0.1:8420/healthz >/dev/null 2>&1; then
        log_success "Health: $(curl -s http://127.0.0.1:8420/healthz)"
    else
        log_warn "Backend not responding"
    fi
}

# ── Backup ─────────────────────────────────────────────────────

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

# ── Restore ────────────────────────────────────────────────────

cmd_restore() {
    local backup_file="$1"
    if [ -z "$backup_file" ]; then
        log_error "No backup file specified"
        log_info "Usage: $(basename "$0") --restore <backup_file.sql.gz>"
        log_info "Available backups:"
        ls -la "$BACKUP_DIR"/*.gz 2>/dev/null || echo "  No backups found"
        exit 1
    fi
    if [ ! -f "$backup_file" ]; then
        log_error "Backup file not found: $backup_file"
        exit 1
    fi

    log_warn "This will REPLACE ALL DATA in the database."
    read -p "Type 'yes' to confirm: " confirm
    if [ "$confirm" != "yes" ]; then
        log_info "Restore cancelled"
        exit 0
    fi

    ensure_env
    # shellcheck source=/dev/null
    source "$ENV_FILE"

    log_info "Restoring from: $backup_file"
    if [[ "$backup_file" == *.gz ]]; then
        gunzip -c "$backup_file" | docker exec -i cvcpkg-prod-postgres psql \
            -U "${POSTGRES_USER:-cvcpkg}" \
            -d "${POSTGRES_DB:-cvcpkg}"
    else
        docker exec -i cvcpkg-prod-postgres psql \
            -U "${POSTGRES_USER:-cvcpkg}" \
            -d "${POSTGRES_DB:-cvcpkg}" < "$backup_file"
    fi
    log_success "Database restored from: $backup_file"
}

# ── Reset database ─────────────────────────────────────────────

cmd_reset_db() {
    log_warn "==============================================="
    log_warn "  WARNING: This will DELETE ALL DATA!"
    log_warn "==============================================="
    echo ""
    read -p "Type 'DELETE ALL DATA' to confirm: " confirm
    if [ "$confirm" != "DELETE ALL DATA" ]; then
        log_info "Reset cancelled"
        exit 0
    fi

    log_info "Creating backup before reset..."
    cmd_backup || log_warn "Backup failed, continuing anyway..."

    log_info "Stopping services..."
    $COMPOSE down

    log_info "Removing PostgreSQL volume..."
    docker volume rm cvcpkg-prod-postgres-data 2>/dev/null || true

    log_success "Database reset complete"
    log_info "Restart with: $(basename "$0") --detach"
}

# ── Shell ──────────────────────────────────────────────────────

cmd_shell() {
    $COMPOSE exec backend /bin/bash
}

# ── Token management ──────────────────────────────────────────

cmd_token_create() {
    local name="${1:-}"
    local role="${2:-admin}"
    if [ -z "$name" ]; then
        log_error "Token name required"
        log_info "Usage: $(basename "$0") --token-create <name> [role]"
        exit 1
    fi
    $COMPOSE exec backend cvcpkg-server token create \
        --name "$name" --role "$role" --state-dir /app/data
}

# ── Usage ──────────────────────────────────────────────────────

print_usage() {
    cat << EOF
cvcpkg-server Production Runner

Manages the production cvcpkg-server stack with PostgreSQL.

Usage:
    $(basename "$0") [OPTIONS]

Options:
    --help, -h           Show this help message
    --detach, -d         Start in background (detached)
    --down               Stop all services
    --build              Rebuild backend image and restart
    --logs [SERVICE]     View logs (optionally for specific service)
    --status             Show service status and health
    --backup             Create database backup (saved to ./backups/)
    --restore FILE       Restore database from backup
    --reset-db           DELETE ALL DATA (creates backup first)
    --shell              Start bash in backend container
    --token-create N [R] Create API token (name, role=admin)

Configuration:
    Edit .env.production to customize:
    - POSTGRES_PASSWORD    Database password
    - CVCPKG_RELEASE       Release tag for the image

    Set CVCPKG_RELEASE env var before running to tag the build:
      CVCPKG_RELEASE=v1.0.0 $(basename "$0") --build

Examples:
    # First time setup
    $(basename "$0") --build --detach

    # Create admin token
    $(basename "$0") --token-create my-admin admin

    # View backend logs
    $(basename "$0") --logs backend

    # Backup before upgrade
    $(basename "$0") --backup
    $(basename "$0") --build

    # Stop everything
    $(basename "$0") --down
EOF
}

# ── Main ───────────────────────────────────────────────────────

main() {
    cd "$PROJECT_DIR"
    local do_detach=false

    if [[ $# -eq 0 ]]; then
        check_dependencies
        cmd_up false
        exit 0
    fi

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --help|-h)
                print_usage; exit 0 ;;
            --detach|-d)
                do_detach=true; shift ;;
            --down)
                check_dependencies; cmd_down; exit 0 ;;
            --build)
                check_dependencies; cmd_build
                shift ;;
            --logs)
                check_dependencies; shift; cmd_logs "${1:-}"; exit 0 ;;
            --status)
                check_dependencies; cmd_status; exit 0 ;;
            --backup)
                check_dependencies; cmd_backup; exit 0 ;;
            --restore)
                check_dependencies; shift; cmd_restore "${1:-}"; exit 0 ;;
            --reset-db)
                check_dependencies; cmd_reset_db; exit 0 ;;
            --shell)
                check_dependencies; cmd_shell; exit 0 ;;
            --token-create)
                check_dependencies; shift
                cmd_token_create "${1:-}" "${2:-admin}"; exit 0 ;;
            *)
                log_error "Unknown option: $1"
                print_usage; exit 1 ;;
        esac
    done

    if [ "$do_detach" = true ]; then
        check_dependencies
        cmd_up true
    fi
}

main "$@"

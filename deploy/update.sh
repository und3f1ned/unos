#!/bin/bash
#
# UNOS Update Script
# Updates UNOS to the latest version
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
UNOS_INSTALL_DIR="${UNOS_INSTALL_DIR:-/opt/unos}"
UNOS_BRANCH="${UNOS_BRANCH:-main}"
BACKUP_DIR="/var/lib/unos/backups"

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

print_header() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}                     ${GREEN}UNOS Update${NC}                           ${BLUE}║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_error "This script must be run as root"
        exit 1
    fi
}

check_installation() {
    if [ ! -d "$UNOS_INSTALL_DIR" ]; then
        log_error "UNOS is not installed at $UNOS_INSTALL_DIR"
        log_info "Run install.sh first"
        exit 1
    fi
}

get_current_version() {
    cd "$UNOS_INSTALL_DIR"
    CURRENT_VERSION=$(git describe --tags --always 2>/dev/null || echo "unknown")
    log_info "Current version: $CURRENT_VERSION"
}

check_for_updates() {
    log_info "Checking for updates..."
    
    cd "$UNOS_INSTALL_DIR"
    git fetch origin "$UNOS_BRANCH" --quiet
    
    LOCAL=$(git rev-parse HEAD)
    REMOTE=$(git rev-parse origin/$UNOS_BRANCH)
    
    if [ "$LOCAL" = "$REMOTE" ]; then
        log_success "UNOS is already up to date!"
        
        read -p "Force rebuild anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 0
        fi
    else
        COMMITS_BEHIND=$(git rev-list --count HEAD..origin/$UNOS_BRANCH)
        log_info "Updates available: $COMMITS_BEHIND commits behind"
    fi
}

backup_database() {
    log_info "Creating backup..."
    
    mkdir -p "$BACKUP_DIR"
    BACKUP_FILE="$BACKUP_DIR/unos-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
    
    # Backup database volume
    docker run --rm \
        -v unos-data:/data \
        -v "$BACKUP_DIR":/backup \
        alpine tar czf /backup/db-backup-$(date +%Y%m%d-%H%M%S).tar.gz -C /data . 2>/dev/null || true
    
    log_success "Backup created at $BACKUP_DIR"
}

stop_services() {
    log_info "Stopping UNOS services..."
    
    cd "$UNOS_INSTALL_DIR/deploy"
    docker compose down --remove-orphans 2>/dev/null || true
    
    log_success "Services stopped"
}

pull_updates() {
    log_info "Pulling updates..."
    
    cd "$UNOS_INSTALL_DIR"
    
    # Stash any local changes
    git stash --quiet 2>/dev/null || true
    
    # Pull latest
    git checkout "$UNOS_BRANCH"
    git pull origin "$UNOS_BRANCH"
    
    NEW_VERSION=$(git describe --tags --always 2>/dev/null || echo "unknown")
    log_success "Updated to: $NEW_VERSION"
}

rebuild_containers() {
    log_info "Rebuilding containers..."
    
    cd "$UNOS_INSTALL_DIR/deploy"
    
    # Source secrets
    if [ -f /etc/unos/secrets.env ]; then
        export $(cat /etc/unos/secrets.env | xargs)
    fi
    
    # Build with no cache to ensure fresh build
    docker compose build --no-cache
    
    log_success "Containers rebuilt"
}

start_services() {
    log_info "Starting UNOS services..."
    
    cd "$UNOS_INSTALL_DIR/deploy"
    
    # Source secrets
    if [ -f /etc/unos/secrets.env ]; then
        export $(cat /etc/unos/secrets.env | xargs)
    fi
    
    docker compose up -d
    
    log_success "Services started"
}

cleanup_old_images() {
    log_info "Cleaning up old Docker images..."
    
    docker image prune -f >/dev/null 2>&1 || true
    
    log_success "Cleanup complete"
}

wait_for_services() {
    log_info "Waiting for services..."
    
    local max_attempts=30
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        if curl -s -o /dev/null -w "%{http_code}" http://localhost/api/v1/health 2>/dev/null | grep -q "200\|401"; then
            log_success "UNOS is running!"
            return 0
        fi
        echo -n "."
        sleep 2
        attempt=$((attempt + 1))
    done
    
    echo ""
    log_warn "Services may still be starting"
}

print_completion() {
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║${NC}              ${GREEN}UNOS Update Complete!${NC}                       ${GREEN}║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "Please refresh your browser (Ctrl+Shift+R) to see changes."
    echo ""
}

# Main
main() {
    print_header
    check_root
    check_installation
    get_current_version
    check_for_updates
    backup_database
    stop_services
    pull_updates
    rebuild_containers
    start_services
    cleanup_old_images
    wait_for_services
    print_completion
}

main "$@"

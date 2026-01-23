#!/bin/bash
#
# UNOS Uninstall Script
# Removes UNOS from the system
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
BACKUP_DIR="/var/lib/unos/backups"

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

print_header() {
    echo ""
    echo -e "${RED}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║${NC}                   ${RED}UNOS Uninstallation${NC}                     ${RED}║${NC}"
    echo -e "${RED}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_error "This script must be run as root"
        exit 1
    fi
}

confirm_uninstall() {
    echo -e "${YELLOW}WARNING: This will remove UNOS and all its data!${NC}"
    echo ""
    echo "The following will be removed:"
    echo "  - UNOS containers and images"
    echo "  - UNOS installation files ($UNOS_INSTALL_DIR)"
    echo "  - UNOS configuration (/etc/unos)"
    echo ""
    echo -e "${YELLOW}Your storage pools and data on them will NOT be affected.${NC}"
    echo ""
    read -p "Are you sure you want to continue? Type 'yes' to confirm: " -r
    echo
    if [ "$REPLY" != "yes" ]; then
        log_info "Uninstallation cancelled"
        exit 0
    fi
}

backup_before_uninstall() {
    read -p "Create a backup before uninstalling? [Y/n] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        log_info "Creating backup..."
        mkdir -p "$BACKUP_DIR"
        
        # Backup database
        docker run --rm \
            -v deploy_unos-data:/data \
            -v "$BACKUP_DIR":/backup \
            alpine tar czf /backup/unos-final-backup-$(date +%Y%m%d-%H%M%S).tar.gz -C /data . 2>/dev/null || true
        
        log_success "Backup created at $BACKUP_DIR"
    fi
}

stop_and_remove_containers() {
    log_info "Stopping and removing containers..."
    
    if [ -d "$UNOS_INSTALL_DIR/deploy" ]; then
        cd "$UNOS_INSTALL_DIR/deploy"
        docker compose down -v --remove-orphans 2>/dev/null || true
    fi
    
    # Remove any remaining UNOS containers
    docker ps -a --filter "name=unos" -q | xargs -r docker rm -f 2>/dev/null || true
    
    log_success "Containers removed"
}

remove_docker_images() {
    log_info "Removing Docker images..."
    
    docker images --filter "reference=*unos*" -q | xargs -r docker rmi -f 2>/dev/null || true
    docker images --filter "reference=deploy-*" -q | xargs -r docker rmi -f 2>/dev/null || true
    
    log_success "Images removed"
}

remove_docker_volumes() {
    log_info "Removing Docker volumes..."
    
    docker volume ls --filter "name=unos" -q | xargs -r docker volume rm 2>/dev/null || true
    docker volume ls --filter "name=deploy_" -q | xargs -r docker volume rm 2>/dev/null || true
    
    log_success "Volumes removed"
}

remove_systemd_service() {
    log_info "Removing systemd service..."
    
    systemctl stop unos.service 2>/dev/null || true
    systemctl disable unos.service 2>/dev/null || true
    rm -f /etc/systemd/system/unos.service
    systemctl daemon-reload
    
    log_success "Systemd service removed"
}

remove_files() {
    log_info "Removing UNOS files..."
    
    rm -rf "$UNOS_INSTALL_DIR"
    rm -rf /etc/unos
    
    log_success "Files removed"
}

restore_samba_config() {
    log_info "Restoring Samba configuration..."
    
    if [ -f /etc/samba/smb.conf.original ]; then
        cp /etc/samba/smb.conf.original /etc/samba/smb.conf
        systemctl restart smbd nmbd 2>/dev/null || true
        log_success "Original Samba configuration restored"
    else
        log_info "No original Samba configuration to restore"
    fi
}

print_completion() {
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║${NC}           ${GREEN}UNOS Uninstallation Complete!${NC}                  ${GREEN}║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "UNOS has been removed from your system."
    echo ""
    if [ -d "$BACKUP_DIR" ]; then
        echo "Backups are preserved at: $BACKUP_DIR"
        echo ""
    fi
    echo "The following were NOT removed:"
    echo "  - Docker (may be used by other applications)"
    echo "  - Storage pools and data in /mnt/pools"
    echo "  - System packages (btrfs-progs, samba, etc.)"
    echo ""
    echo "To completely remove Docker, run:"
    echo "  apt-get purge docker-ce docker-ce-cli containerd.io"
    echo ""
}

# Main
main() {
    print_header
    check_root
    confirm_uninstall
    backup_before_uninstall
    stop_and_remove_containers
    remove_docker_images
    remove_docker_volumes
    remove_systemd_service
    remove_files
    restore_samba_config
    print_completion
}

main "$@"

#!/bin/bash
#
# UNOS (Universal NAS Open Software) Installation Script
# https://github.com/und3f1ned/unos
#
# This script installs UNOS on a fresh Debian/Ubuntu system
# Supports: Debian 11+, Ubuntu 22.04+, ARM64 and AMD64
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
UNOS_VERSION="${UNOS_VERSION:-latest}"
UNOS_INSTALL_DIR="${UNOS_INSTALL_DIR:-/opt/unos}"
UNOS_DATA_DIR="${UNOS_DATA_DIR:-/var/lib/unos}"
UNOS_REPO="${UNOS_REPO:-https://github.com/und3f1ned/unos.git}"
UNOS_BRANCH="${UNOS_BRANCH:-main}"

# Logging
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Header
print_header() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}                    ${GREEN}UNOS Installation${NC}                      ${BLUE}║${NC}"
    echo -e "${BLUE}║${NC}               Universal NAS Open Software                ${BLUE}║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# Check if running as root
check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_error "This script must be run as root"
        echo "Please run: sudo $0"
        exit 1
    fi
}

# Detect OS and architecture
detect_system() {
    log_info "Detecting system..."
    
    # Detect OS
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        OS_VERSION=$VERSION_ID
    else
        log_error "Cannot detect OS. /etc/os-release not found."
        exit 1
    fi
    
    # Detect architecture
    ARCH=$(uname -m)
    case $ARCH in
        x86_64)
            ARCH="amd64"
            ;;
        aarch64)
            ARCH="arm64"
            ;;
        armv7l)
            ARCH="armv7"
            ;;
        *)
            log_error "Unsupported architecture: $ARCH"
            exit 1
            ;;
    esac
    
    log_success "Detected: $OS $OS_VERSION ($ARCH)"
    
    # Check supported OS
    case $OS in
        debian|ubuntu|raspbian)
            log_success "OS is supported"
            ;;
        *)
            log_warn "OS '$OS' is not officially supported. Installation may fail."
            read -p "Continue anyway? [y/N] " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                exit 1
            fi
            ;;
    esac
}

# Install system dependencies
install_dependencies() {
    log_info "Installing system dependencies..."
    
    apt-get update -qq
    
    apt-get install -y -qq \
        curl \
        wget \
        git \
        ca-certificates \
        gnupg \
        lsb-release \
        btrfs-progs \
        smartmontools \
        lm-sensors \
        samba \
        samba-common-bin \
        hdparm \
        parted \
        mdadm \
        nvme-cli \
        ethtool \
        net-tools \
        iproute2 \
        >/dev/null 2>&1
    
    log_success "System dependencies installed"
}

# Install Docker
install_docker() {
    if command -v docker &> /dev/null; then
        log_success "Docker is already installed"
        return
    fi
    
    log_info "Installing Docker..."
    
    # Add Docker's official GPG key
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/$OS/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    
    # Add Docker repository
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS \
        $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    # Install Docker
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null 2>&1
    
    # Enable and start Docker
    systemctl enable docker
    systemctl start docker
    
    log_success "Docker installed and started"
}

# Create UNOS user and directories
setup_directories() {
    log_info "Setting up directories..."
    
    # Create installation directory
    mkdir -p "$UNOS_INSTALL_DIR"
    mkdir -p "$UNOS_DATA_DIR"
    mkdir -p /mnt/pools
    mkdir -p /etc/unos
    
    log_success "Directories created"
}

# Clone or download UNOS
download_unos() {
    log_info "Downloading UNOS..."
    
    if [ -d "$UNOS_INSTALL_DIR/.git" ]; then
        log_info "UNOS directory exists, pulling latest..."
        cd "$UNOS_INSTALL_DIR"
        git fetch origin
        git checkout "$UNOS_BRANCH"
        git pull origin "$UNOS_BRANCH"
    else
        # Clone repository
        rm -rf "$UNOS_INSTALL_DIR"
        git clone --branch "$UNOS_BRANCH" "$UNOS_REPO" "$UNOS_INSTALL_DIR"
    fi
    
    log_success "UNOS downloaded to $UNOS_INSTALL_DIR"
}

# Prepare the optional environment file read by docker compose and the
# systemd unit. The JWT signing secret is not stored here: the supervisor
# generates it on first start and keeps it in the unos-data volume
# (/data/jwt_secret), so every way of starting the stack uses the same secret.
generate_secrets() {
    log_info "Preparing secrets file..."
    
    if [ ! -f /etc/unos/secrets.env ]; then
        install -m 600 /dev/null /etc/unos/secrets.env
        cat > /etc/unos/secrets.env << 'EOF'
# Optional overrides for the UNOS containers (KEY=value lines).
# UNOS_HOSTNAME: name (or IP address) on the HTTPS certificate, default "unos".
# Set it to the address browsers use for this server, e.g.
# UNOS_HOSTNAME=nas.home.arpa, then run: systemctl restart unos
# JWT_SECRET: leave unset. The supervisor generates a random secret on first
# start and stores it in the unos-data volume. Set it only to pin a value of
# at least 32 random characters (e.g. openssl rand -hex 32).
EOF
        log_success "Secrets file created (JWT secret is generated by the supervisor)"
    else
        log_info "Secrets file already exists, keeping it"
    fi
}

# Configure Samba
configure_samba() {
    log_info "Configuring Samba..."

    # The SMB shares exist only as sections of smb.conf (the supervisor reads
    # them from there). Re-running this script over an installation must not
    # replace that file, or every share is gone.
    if grep -qs '^[[:space:]]*server string = UNOS NAS' /etc/samba/smb.conf; then
        log_info "Keeping /etc/samba/smb.conf: it is already managed by UNOS"
        systemctl enable smbd nmbd
        systemctl restart smbd nmbd
        log_success "Samba configured"
        return
    fi

    # Backup original smb.conf
    if [ -f /etc/samba/smb.conf ] && [ ! -f /etc/samba/smb.conf.original ]; then
        cp /etc/samba/smb.conf /etc/samba/smb.conf.original
    fi
    
    # Create minimal smb.conf (UNOS will manage shares)
    cat > /etc/samba/smb.conf << 'EOF'
[global]
   workgroup = WORKGROUP
   server string = UNOS NAS
   security = user
   map to guest = Bad User
   dns proxy = no
   
   # Performance tuning
   socket options = TCP_NODELAY IPTOS_LOWDELAY
   read raw = yes
   write raw = yes
   max xmit = 65535
   dead time = 15
   getwd cache = yes
   
   # Logging
   log file = /var/log/samba/log.%m
   max log size = 50
   log level = 1

# Shares managed by UNOS will be added below
EOF
    
    systemctl enable smbd nmbd
    systemctl restart smbd nmbd
    
    log_success "Samba configured"
}

# Build and start UNOS containers
start_unos() {
    log_info "Building and starting UNOS..."
    
    cd "$UNOS_INSTALL_DIR/deploy"
    
    # Source optional overrides (comments allowed)
    if [ -f /etc/unos/secrets.env ]; then
        set -a; . /etc/unos/secrets.env; set +a
    fi
    
    # Build containers
    docker compose build

    # Re-running over an older install: its network lacks the fixed subnet
    # (172.31.254.0/24) and must be recreated. Does nothing on a fresh system.
    docker compose down --remove-orphans || true

    # Start containers
    docker compose up -d
    
    log_success "UNOS containers started"
}

# Create systemd service for auto-start
create_systemd_service() {
    log_info "Creating systemd service..."
    
    cat > /etc/systemd/system/unos.service << EOF
[Unit]
Description=UNOS - Universal NAS Open Software
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$UNOS_INSTALL_DIR/deploy
EnvironmentFile=/etc/unos/secrets.env
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
    
    systemctl daemon-reload
    systemctl enable unos.service
    
    log_success "Systemd service created and enabled"
}

# Wait for services to be ready
wait_for_services() {
    log_info "Waiting for services to start..."
    
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
    log_warn "Services may still be starting. Check status with: docker ps"
}

# Get local IP address
get_local_ip() {
    ip route get 1 | awk '{print $7; exit}' 2>/dev/null || hostname -I | awk '{print $1}'
}

# Print completion message
print_completion() {
    local IP CERT_NAME
    IP=$(get_local_ip)
    # The name Caddy issued the certificate for (UNOS_HOSTNAME of the webui container).
    CERT_NAME=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' unos-webui 2>/dev/null |
        sed -n 's/^UNOS_HOSTNAME=//p')
    CERT_NAME="${CERT_NAME:-unos}"

    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║${NC}            ${GREEN}UNOS Installation Complete!${NC}                   ${GREEN}║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "Access UNOS at: ${BLUE}https://$IP${NC}  (or https://$CERT_NAME if that name resolves to this server)"
    echo "Plain http:// only redirects to https://."
    echo ""
    echo -e "${YELLOW}Expect a certificate warning.${NC} UNOS issues its certificate from its own"
    echo "certificate authority (Caddy's local CA), for the name \"$CERT_NAME\". Accept the"
    echo "warning, or make your devices trust that CA (see docs/INSTALL.md). Its root"
    echo "certificate:"
    echo "  docker cp unos-webui:/data/caddy/pki/authorities/local/root.crt unos-root-ca.crt"
    echo "To change the certificate name, set UNOS_HOSTNAME in /etc/unos/secrets.env and"
    echo "run: systemctl restart unos"
    echo ""
    echo -e "${RED}Create the administrator account now:${NC} the first visit asks for a user"
    echo "name and password. Until an account exists, anyone who can open the page can"
    echo "create it."
    echo ""
    echo "Useful commands:"
    echo "  - View status:    docker ps"
    echo "  - View logs:      docker logs unos-supervisor"
    echo "  - Restart:        systemctl restart unos"
    echo "  - Update:         $UNOS_INSTALL_DIR/deploy/update.sh"
    echo ""
}

# Main installation flow
main() {
    print_header
    check_root
    detect_system
    install_dependencies
    install_docker
    setup_directories
    download_unos
    generate_secrets
    configure_samba
    start_unos
    create_systemd_service
    wait_for_services
    print_completion
}

# Run main function
main "$@"

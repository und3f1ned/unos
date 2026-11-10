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
UNOS_INSTALL_DIR="${UNOS_INSTALL_DIR:-/opt/unos}"
UNOS_DATA_DIR="${UNOS_DATA_DIR:-/var/lib/unos}"
UNOS_REPO="${UNOS_REPO:-https://github.com/und3f1ned/unos.git}"
UNOS_BRANCH="${UNOS_BRANCH:-main}"
# Seconds `docker compose up` (here and in the systemd unit) waits for the
# supervisor to become healthy; webui starts only then. As long as Docker can
# take to report the supervisor healthy or unhealthy: start_period +
# (retries + 1) x (interval + timeout) of its healthcheck in
# docker-compose.yml, 600 + 4 x (10 + 10). A failed check counts only if it
# started after start_period, so the one that runs then does not, and the
# supervisor is unhealthy after retries more; a check takes up to timeout once
# its process has started, and Docker waits interval after each one. At
# startup the supervisor mounts the pools the host has not mounted, up to 2
# minutes each. Same value in update.sh; change both with the healthcheck.
SUPERVISOR_START_TIMEOUT=680
# Arguments of `docker compose up`, with --wait-timeout where Compose has it
# (set by check_compose_wait).
COMPOSE_UP_ARGS=(-d)

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

# The later steps run Docker Compose v2, the compose plugin of the docker CLI.
# Without it, the script would fail only at `docker compose build`, after the
# checkout and the Samba configuration. $1 says what has changed so far.
# Debian 13's docker-compose package (Compose 2.26) installs the plugin;
# Debian 12's (Compose 1.29, written in Python) has only the docker-compose
# command.
require_compose() {
    if docker compose version >/dev/null 2>&1; then
        return 0
    fi
    log_error "Docker Compose is not installed: \`docker compose version\` failed. $1"
    echo "  Install the Compose plugin: the package docker-compose-plugin from Docker's"
    echo "  repository (https://docs.docker.com/engine/install/), docker-compose-v2 on"
    echo "  Ubuntu, or docker-compose on Debian 13. Then run this script again."
    exit 1
}

# Whether the Compose version $1 (2.26.1, v2.40.3, 2.40.3+ds1-0ubuntu1, ...)
# is older than 2.30. False when it cannot be read.
compose_before_2_30() {
    [[ $1 =~ ^v?([0-9]+)\.([0-9]+) ]] || return 1
    local major=$((10#${BASH_REMATCH[1]})) minor=$((10#${BASH_REMATCH[2]}))
    [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -lt 30 ]; }
}

# `docker compose up` starts webui only once the supervisor is healthy
# (depends_on), and waits for that. --wait-timeout ends the wait: Compose
# 2.17 added the option (for `up --wait` only), 2.30 applies it to this wait.
# Older versions reject it, so it is passed only where `up --help` lists it.
check_compose_wait() {
    local help version
    help=$(docker compose up --help 2>/dev/null) || help=""
    version=$(docker compose version --short 2>/dev/null) || version=""
    case "$help" in
        *--wait-timeout*)
            COMPOSE_UP_ARGS=(-d --wait-timeout "$SUPERVISOR_START_TIMEOUT")
            compose_before_2_30 "$version" || return 0
            log_warn "Docker Compose $version cannot limit its wait for the supervisor to become healthy (that needs Compose 2.30 or later): \`docker compose up\`, here and in the unos service, waits until Docker reports the supervisor healthy or unhealthy."
            ;;
        *)
            COMPOSE_UP_ARGS=(-d)
            log_warn "Docker Compose ${version:-(version unknown)} has no \`up --wait-timeout\`: \`docker compose up\`, here and in the unos service, runs without it and waits until Docker reports the supervisor healthy or unhealthy. Compose 2.30 or later limits that wait; run install.sh again after updating Docker Compose."
            ;;
    esac
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

# smbd serves SMB. NetBIOS is off (disable netbios in smb.conf), so nmbd, the
# NetBIOS name server, is stopped and does not start at boot; the restart
# gives smbd the file's settings, its ports among them (ADR-0275).
start_samba() {
    systemctl enable smbd
    systemctl restart smbd
    systemctl disable --now nmbd
}

# Configure Samba
configure_samba() {
    log_info "Configuring Samba..."

    # The supervisor imports the shares of smb.conf into its database once, at
    # its first start, and from then on generates the whole file from the
    # database. Re-running this script over an installation must not replace
    # that file: the shares of an installation that has not imported them yet
    # exist only there. Every file UNOS wrote keeps this line.
    if grep -qs '^[[:space:]]*server string = UNOS NAS' /etc/samba/smb.conf; then
        log_info "Keeping /etc/samba/smb.conf: it is already managed by UNOS"
        start_samba
        log_success "Samba configured"
        return
    fi

    # Backup original smb.conf
    if [ -f /etc/samba/smb.conf ] && [ ! -f /etc/samba/smb.conf.original ]; then
        cp /etc/samba/smb.conf /etc/samba/smb.conf.original
    fi
    
    # The file the supervisor generates while there is no share: exactly
    # render_config([]) of supervisor/src/shares/smbconf.py, the hardened
    # [global] (LAN addresses only, signing required, no guest access, no
    # NetBIOS). supervisor/tests/test_deploy_scripts.py checks that they are
    # the same.
    cat > /etc/samba/smb.conf << 'EOF'
# Generated by UNOS from its database. Do not edit: UNOS rewrites this file
# when shares or SMB accounts change (Shares and Users pages).

[global]
   workgroup = WORKGROUP
   server string = UNOS NAS
   server role = standalone server
   security = user
   passdb backend = tdbsam
   map to guest = Never
   restrict anonymous = 2
   guest account = nobody
   invalid users = root
   hosts allow = 127.0.0.0/8 ::1 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 fc00::/7 fe80::/10
   server min protocol = SMB2_02
   ntlm auth = ntlmv2-only
   server signing = mandatory
   server smb encrypt = default
   smb3 unix extensions = no
   access based share enum = yes
   load printers = no
   printing = bsd
   printcap name = /dev/null
   disable spoolss = yes
   usershare max shares = 0
   unix password sync = no
   pam password change = no
   obey pam restrictions = no
   cache directory = /run/samba
   log file = /run/samba/log.samba
   max log size = 100
   log level = 1 auth_audit:2
   logging = syslog@2
   debug timestamp = no
   dns proxy = no
   disable netbios = yes
   smb ports = 445
   multicast dns register = yes
EOF

    start_samba

    log_success "Samba configured"
}

# Network discovery (ADR-0275). With NetBIOS off, macOS finds this server
# through the Bonjour service (_smb._tcp) that smbd announces through
# avahi-daemon, and Windows through wsdd2, which answers WS-Discovery, and
# LLMNR for the server's name. Both answer over IPv4 only: clients would use
# an announced global IPv6 address, from which SMB refuses them (hosts allow),
# and over IPv6 wsdd2 would also open its TCP ports on tunnels, whose global
# addresses UNOS does not filter (ADR-0301). avahi-daemon reads UNOS's file in
# /etc/avahi, which uninstall.sh keeps: Debian's settings without the IPv6
# addresses, which use-ipv6=no alone would still announce over IPv4
# (publish-aaaa-on-ipv4). The files are in place before the packages, whose
# installation starts the services. avahi-daemon comes without the
# libnss-mdns it recommends, which would change how this host resolves names
# that end in .local. Debian 11 has no wsdd2 package: the installation goes on
# without it. A package that exists but does not install stops the
# installation, as the other packages do.
configure_discovery() {
    log_info "Configuring network discovery..."

    mkdir -p /etc/avahi /etc/systemd/system/avahi-daemon.service.d /etc/systemd/system/wsdd2.service.d
    cat > /etc/avahi/avahi-daemon.unos.conf << 'EOF'
# UNOS: avahi-daemon reads this file instead of avahi-daemon.conf (the drop-in
# /etc/systemd/system/avahi-daemon.service.d/unos.conf): Debian's settings,
# without IPv6, so that this server is announced with its IPv4 addresses only
# (docs/INSTALL.md, Network Discovery). install.sh writes it again when it runs.
[server]
use-ipv4=yes
use-ipv6=no
ratelimit-interval-usec=1000000
ratelimit-burst=1000

[wide-area]
enable-wide-area=yes

[publish]
publish-hinfo=no
publish-workstation=no
publish-aaaa-on-ipv4=no
EOF
    cat > /etc/systemd/system/avahi-daemon.service.d/unos.conf << 'EOF'
# UNOS: avahi-daemon announces this server over IPv4 only (docs/INSTALL.md,
# Network Discovery).
[Service]
ExecStart=
ExecStart=/usr/sbin/avahi-daemon -s -f /etc/avahi/avahi-daemon.unos.conf
EOF
    cat > /etc/systemd/system/wsdd2.service.d/unos.conf << 'EOF'
# UNOS: wsdd2 answers over IPv4 only (docs/INSTALL.md, Network Discovery).
[Service]
ExecStart=
ExecStart=/usr/sbin/wsdd2 -4
EOF
    systemctl daemon-reload

    if ! apt-get install -y -qq --no-install-recommends avahi-daemon >/dev/null; then
        log_error "Could not install the package avahi-daemon (apt-get's error is above). Run install.sh again."
        exit 1
    fi
    systemctl enable avahi-daemon
    # An avahi-daemon that ran before its drop-in existed starts again with it.
    systemctl restart avahi-daemon

    # Debian 11 has no wsdd2 package, and Ubuntu has it in universe.
    if ! apt-cache show wsdd2 >/dev/null 2>&1; then
        log_warn "The package sources have no package wsdd2 (Debian 11 has none; Ubuntu has it in universe): Windows does not list this server in its Network view; type its address into File Explorer instead. macOS lists it."
        return
    fi
    if ! apt-get install -y -qq wsdd2 >/dev/null; then
        log_error "Could not install the package wsdd2 (apt-get's error is above). Run install.sh again."
        exit 1
    fi
    systemctl enable wsdd2
    # A wsdd2 that ran before its drop-in existed starts again with it.
    systemctl restart wsdd2

    log_success "Network discovery configured: Windows and macOS list this server"
}

# Build and start UNOS containers
start_unos() {
    log_info "Building and starting UNOS..."
    
    cd "$UNOS_INSTALL_DIR/deploy"
    
    # Source optional overrides (comments allowed)
    if [ -f /etc/unos/secrets.env ]; then
        set -a; . /etc/unos/secrets.env; set +a
    fi
    
    # Build containers. The supervisor image carries the version of the
    # checkout, which the web interface shows (ADR-0234).
    UNOS_VERSION=$(git -C "$UNOS_INSTALL_DIR" describe --tags --always 2>/dev/null || echo unknown) \
        docker compose build

    # Start containers: webui once the supervisor is healthy.
    # --remove-orphans: a re-run removes the containers of services that a
    # release dropped or renamed.
    if ! docker compose up --remove-orphans "${COMPOSE_UP_ARGS[@]}"; then
        log_error "Starting UNOS failed. If the error above says \"dependency failed to start\", \"is unhealthy\", \"exited\" or \"timeout waiting for dependencies\" (the wording depends on the Compose version), the supervisor stopped or did not become healthy in time: docker logs unos-supervisor. If it mentions an overlapping address pool, see \"Network subnet\" in docs/INSTALL.md."
        exit 1
    fi

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
ExecStart=/usr/bin/docker compose up ${COMPOSE_UP_ARGS[*]}
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
    # Docker is installed already: without its Compose plugin, stop before
    # anything changes. install_docker installs the plugin with Docker.
    if command -v docker >/dev/null 2>&1; then
        require_compose "Nothing was changed."
    fi
    install_dependencies
    install_docker
    require_compose "The system packages are installed; UNOS is not."
    # Sets the `docker compose up` arguments of start_unos and the unit.
    check_compose_wait
    setup_directories
    download_unos
    generate_secrets
    configure_samba
    configure_discovery
    start_unos
    create_systemd_service
    wait_for_services
    print_completion
}

# Run main function
main "$@"

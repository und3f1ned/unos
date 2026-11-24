#!/bin/bash
#
# UNOS (Universal NAS Open Software) Installation Script
# https://github.com/und3f1ned/unos
#
# This script installs the newest release of UNOS (a tag v<x.y.z>) on a fresh
# Debian/Ubuntu system. On AMD64, ARM64 and RISCV64 it pulls the release's
# images from GHCR, where .github/workflows/release.yml publishes them; on
# ARMv7, which has none, it builds the supervisor from the release's checkout
# (ADR-0050, deploy.riscv64-release-images).
# Supports: Debian 11+, Ubuntu 22.04+, ARM64 and AMD64; on RISCV64 Debian 13+
# and Ubuntu 24.04+
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
UNOS_REPO="${UNOS_REPO:-https://github.com/und3f1ned/unos.git}"
# Where release.yml publishes the images of a release: <registry>/unos-<service>:<x.y.z>
# (ADR-0050). update.sh has the same.
UNOS_REGISTRY="${UNOS_REGISTRY:-ghcr.io/und3f1ned}"
# A release's tag, v<x.y.z> without leading zeros. update.sh has the same.
RELEASE_TAG='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
# Compose services; their containers are named unos-<service>.
SERVICES=(supervisor webui)
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
        # shellcheck source=/dev/null # the host's file, read for ID and VERSION_ID
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
        riscv64)
            ARCH="riscv64"
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

    # Only what UNOS or this script runs on the host. Not mdadm: Debian's package writes an
    # ARRAY line into /etc/mdadm/mdadm.conf and the initramfs for every md superblock on the
    # host's disks, which assembles the arrays of an old NAS's disks (ADR-0413).
    apt-get update -qq
    
    apt-get install -y -qq \
        curl \
        git \
        ca-certificates \
        gnupg \
        lsb-release \
        btrfs-progs \
        smartmontools \
        samba \
        samba-common-bin \
        samba-vfs-modules \
        hdparm \
        ethtool \
        iproute2 \
        mmc-utils \
        rsync \
        dosfstools \
        fdisk \
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

    # Docker's repository has no packages for riscv64: Debian 13 and Ubuntu
    # 24.04 build Docker Engine, Compose v2 (the compose plugin; Debian's
    # package is docker-compose, Ubuntu's docker-compose-v2) and buildx for it
    # (ADR-0370).
    if [ "$ARCH" = riscv64 ]; then
        local compose=docker-compose
        [ "$OS" = ubuntu ] && compose=docker-compose-v2
        apt-get install -y -qq docker.io "$compose" docker-buildx >/dev/null 2>&1
        systemctl enable docker
        systemctl start docker
        log_success "Docker installed from the distribution and started"
        return
    fi

    # Add Docker's official GPG key
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$OS/gpg" | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
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

# The SpacemiT K1 (Banana Pi BPI-F3), as its device tree names it: under the
# vendor's kernel "spacemit,k1-x", under the mainline one "spacemit,k1".
is_spacemit_k1() {
    [ -r /proc/device-tree/compatible ] &&
        tr '\0' '\n' < /proc/device-tree/compatible | grep -qxE 'spacemit,k1(-x)?'
}

# The platform of a host that builds no web UI, for docker buildx, and nothing
# elsewhere: the web UI's build tools (Vite's Rolldown and Lightning CSS)
# publish no binaries for 32-bit ARM with musl, the libc of the web UI's
# builder (ADR-0456). The image is built on another machine and loaded here.
# riscv64, which they lack too, pulls the release's
# (deploy.riscv64-release-images). Docker's architecture, as for
# release_images_here: a 32-bit userland on a 64-bit Arm kernel (uname
# aarch64) builds arm images too (deploy.armv7-supervisor-build). update.sh has
# the same function.
webui_elsewhere_platform() {
    case "$(docker version --format '{{.Server.Arch}}' 2>/dev/null)" in
        arm) echo linux/arm/v7 ;;
    esac
}

ELSEWHERE_WEBUI_IMAGE=deploy-webui:latest
require_elsewhere_webui_image() {
    local platform=$1
    docker image inspect "$ELSEWHERE_WEBUI_IMAGE" >/dev/null 2>&1 && return 0
    log_error "The web UI's build tools have no build for $platform, so the image $ELSEWHERE_WEBUI_IMAGE comes from another machine. On a machine with Docker that runs $platform images (QEMU's binfmt) and the same checkout of UNOS, run in its root:"
    echo "  docker buildx build --platform $platform -t $ELSEWHERE_WEBUI_IMAGE --load webui"
    echo "  docker save -o deploy-webui.tar $ELSEWHERE_WEBUI_IMAGE"
    echo "  scp deploy-webui.tar <this host>:"
    echo "and here:"
    echo "  sudo docker load -i deploy-webui.tar"
    echo "Everything before this step is done; run install.sh again (docs/INSTALL.md, ARMv7)."
    exit 1
}

# On the SpacemiT K1 an NVMe drive hung under load ("QID 0 timeout, disable
# controller") unless its power saving (APST) and the ASPM L1 of its PCIe link
# were off, and the kernel parameters that would turn them off (pcie_aspm=off,
# nvme_core.default_ps_max_latency_us=0) keep the BPI-F3 from booting
# (docs/INSTALL.md). udev turns them off as each NVMe drive appears, also when
# the K1 enumerates PCIe late in the boot: ASPM L1 when its PCIe function
# appears, and APST once its disk appears, since the driver adds the
# controller's APST setting only after it has identified the drive. udev applies
# the rule to the drives there are now. It touches the power saving of NVMe
# drives only: SATA and USB disks keep theirs, every pool keeps TRIM and
# fstrim.timer stays as it is (ADR-0370, ADR-0429). Elsewhere this does nothing.
# uninstall.sh keeps the rule: the pools stay.
configure_spacemit_k1() {
    is_spacemit_k1 || return 0
    log_info "Configuring NVMe for the SpacemiT K1..."

    mkdir -p /usr/local/sbin /etc/udev/rules.d
    cat > /usr/local/sbin/unos-k1-nvme << 'EOF'
#!/bin/sh
# UNOS (install.sh): turns off the power saving of each NVMe drive of this
# SpacemiT K1 and of its PCIe link (ADR-0370); 60-unos-k1-nvme.rules runs it.
# 0 in pm_qos_latency_tolerance_us makes the driver turn APST off; 0 in
# l1_aspm turns ASPM L1 off.
for controller in /sys/class/nvme/nvme*; do
    [ -e "$controller" ] || continue
    if [ -w "$controller/power/pm_qos_latency_tolerance_us" ]; then
        echo 0 > "$controller/power/pm_qos_latency_tolerance_us"
    fi
    link=$(readlink -f "$controller/device")/link
    if [ -w "$link/l1_aspm" ]; then
        echo 0 > "$link/l1_aspm"
    fi
done
exit 0
EOF
    chmod 755 /usr/local/sbin/unos-k1-nvme
    cat > /etc/udev/rules.d/60-unos-k1-nvme.rules << 'EOF'
# UNOS (install.sh): NVMe power saving off on the SpacemiT K1 (ADR-0370).
# ASPM L1 of an NVMe drive's PCIe link goes off when the function appears;
# APST once the drive's disk appears, after the driver has identified it.
ACTION=="add|change", SUBSYSTEM=="pci", ATTR{class}=="0x010802", ATTR{link/l1_aspm}="0"
ACTION=="add|change", SUBSYSTEM=="block", KERNEL=="nvme*", ENV{DEVTYPE}=="disk", RUN+="/usr/local/sbin/unos-k1-nvme"
EOF
    udevadm control --reload
    udevadm trigger --action=change --subsystem-match=pci --attr-match=class=0x010802
    udevadm trigger --action=change --subsystem-match=block --sysname-match='nvme*'

    log_success "SpacemiT K1: NVMe power saving off whenever an NVMe drive appears"
}

# Create UNOS user and directories
setup_directories() {
    log_info "Setting up directories..."
    
    # Create installation directory
    mkdir -p "$UNOS_INSTALL_DIR"
    # The backups of update.sh, which names this folder too.
    mkdir -p /var/lib/unos
    mkdir -p /mnt/pools
    mkdir -p /etc/unos
    
    log_success "Directories created"
}

# The newest release among the tags on standard input, one per line: by
# version (v1.10.0 after v1.9.0). update.sh has the same.
newest_release() {
    grep -E "$RELEASE_TAG" | sort -V | tail -n 1
}

# Whether GHCR has images for this host's Docker: release.yml builds
# linux/amd64, linux/arm64 and linux/riscv64; 32-bit arm has none
# (deploy.riscv64-release-images). The daemon's architecture, which a pull
# takes, not the kernel's: a 32-bit userland on a 64-bit Arm kernel runs arm
# images. update.sh has the same.
release_images_here() {
    case "$(docker version --format '{{.Server.Arch}}' 2>/dev/null)" in
        amd64 | arm64 | riscv64) return 0 ;;
        *) return 1 ;;
    esac
}

# The image of the service $1 of the release $2 on GHCR (v1.3.0: 1.3.0), and
# the name compose starts it by: its project (deploy, the folder of
# docker-compose.yml) and the service. update.sh has the same.
release_image() { echo "$UNOS_REGISTRY/unos-$1:${2#v}"; }
local_image() { echo "deploy-$1"; }

# Clone UNOS and check out its newest release, a detached HEAD at the tag
# (ADR-0050). A second run fetches the tags and checks out the newest again;
# a tag that moved on origin stops it (git refuses to move it here).
download_unos() {
    log_info "Downloading UNOS..."

    if [ -d "$UNOS_INSTALL_DIR/.git" ]; then
        log_info "UNOS directory exists, fetching its releases..."
        git -C "$UNOS_INSTALL_DIR" fetch --quiet --tags origin
    else
        rm -rf "$UNOS_INSTALL_DIR"
        git clone --quiet --no-checkout "$UNOS_REPO" "$UNOS_INSTALL_DIR"
    fi

    local tag
    tag=$(git -C "$UNOS_INSTALL_DIR" tag --list 'v*' | newest_release)
    if [ -z "$tag" ]; then
        log_error "$UNOS_REPO has no release yet (a tag v<x.y.z>): there is nothing to install. The system packages are installed; UNOS is not."
        exit 1
    fi
    git -C "$UNOS_INSTALL_DIR" -c advice.detachedHead=false checkout --quiet --detach "refs/tags/$tag"

    log_success "UNOS $tag downloaded to $UNOS_INSTALL_DIR"
}

# The release's images from GHCR under the names compose starts; their own
# names go, so that update.sh's next pull is the only copy (ADR-0050).
pull_release_images() {
    local service ref
    for service in "${SERVICES[@]}"; do
        ref=$(release_image "$service" "$1")
        if ! docker pull --quiet "$ref"; then
            log_error "Could not pull $ref (docker's error is above): is $UNOS_REGISTRY reachable, and are its packages public? Run install.sh again."
            exit 1
        fi
        docker image tag "$ref" "$(local_image "$service")"
        docker image rm "$ref" || true
    done
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

# UNOS reads the disks' SMART data itself (Disk Health, and the hourly SMART
# monitor with its alerts: ADR-0235, ADR-0332). smartd, which the package
# smartmontools starts, asks every awake disk its power mode and reads its
# SMART data every 30 minutes with Debian's /etc/smartd.conf: each command
# restarts the disk's standby timer, so a disk with an idle timeout of 30
# minutes or more might never spin down. Once UNOS runs, smartd is stopped and
# does not start at boot (ADR-0378).
stop_smartd() {
    systemctl disable --now smartmontools
    log_success "smartd is off: UNOS reads the disks' SMART data itself"
}

# smbd serves SMB. NetBIOS is off (disable netbios in smb.conf), so nmbd, the
# NetBIOS name server, is stopped and does not start at boot; the restart
# gives smbd the file's settings, its ports among them (ADR-0275).
start_samba() {
    systemctl enable smbd
    systemctl restart smbd
    systemctl disable --now nmbd
}

# The file the supervisor generates while there is no share: exactly
# render_config([]) of supervisor/src/shares/smbconf.py, the hardened
# [global] (LAN addresses only, signing required, no guest access, no
# NetBIOS, no password change over SAMR: ADR-0342) under its header.
# supervisor/tests/test_deploy_scripts.py checks that they are the same.
unos_smb_conf() {
    cat << 'EOF'
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
   check password script = /bin/false
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
}

# Configure Samba
configure_samba() {
    log_info "Configuring Samba..."

    # The supervisor generates the whole file from its database, under its
    # header (HEADER of supervisor/src/shares/smbconf.py, the first two lines
    # of unos_smb_conf): a re-run keeps a file that starts with that header.
    # Any other smb.conf is replaced, and kept as smb.conf.original only when
    # there is none yet: a re-run keeps no copy of it (ADR-0316).
    if [ "$(head -n 2 /etc/samba/smb.conf 2>/dev/null)" = "$(unos_smb_conf | head -n 2)" ]; then
        log_info "Keeping /etc/samba/smb.conf: it is already managed by UNOS"
        start_samba
        log_success "Samba configured"
        return
    fi

    # Backup original smb.conf
    if [ -f /etc/samba/smb.conf ] && [ ! -f /etc/samba/smb.conf.original ]; then
        cp /etc/samba/smb.conf /etc/samba/smb.conf.original
    fi

    unos_smb_conf > /etc/samba/smb.conf

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

# Get the release's images and start the UNOS containers
start_unos() {
    log_info "Starting UNOS..."

    cd "$UNOS_INSTALL_DIR/deploy"

    # Source optional overrides (comments allowed)
    if [ -f /etc/unos/secrets.env ]; then
        set -a
        # shellcheck source=/dev/null # the host's file, written by this script
        . /etc/unos/secrets.env
        set +a
    fi

    # The release of the checkout (download_unos), whose version the images
    # carry and the web interface shows (ADR-0234).
    local version
    version=$(git -C "$UNOS_INSTALL_DIR" describe --tags --always 2>/dev/null || echo unknown)
    if release_images_here; then
        # AMD64, ARM64 and RISCV64, a SpacemiT K1 included: the release's
        # images from GHCR; nothing is built here
        # (deploy.riscv64-release-images).
        if ! [[ $version =~ $RELEASE_TAG ]]; then
            log_error "$UNOS_INSTALL_DIR is at $version, not at a release (a tag v<x.y.z>), whose images it could pull. Run install.sh again."
            exit 1
        fi
        pull_release_images "$version"
    else
        # Hosts without release images build them from the release's checkout.
        # On 32-bit ARM only the supervisor: the web UI's image comes from
        # another machine (require_elsewhere_webui_image, ADR-0456).
        local services=() platform
        platform=$(webui_elsewhere_platform)
        if [ -n "$platform" ]; then
            require_elsewhere_webui_image "$platform"
            services=(supervisor)
        fi
        UNOS_VERSION=$version docker compose build "${services[@]}"
    fi

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
    # --now: the unit is active from the install on, so that `systemctl stop unos` and the
    # first shutdown run its `docker compose down`; its `docker compose up` finds the
    # containers that start_unos started and waited for until healthy, and changes nothing.
    systemctl enable --now unos.service

    log_success "Systemd service created, enabled and started"
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
    configure_spacemit_k1
    setup_directories
    download_unos
    generate_secrets
    configure_samba
    configure_discovery
    start_unos
    create_systemd_service
    stop_smartd
    wait_for_services
    print_completion
}

# Run main function
main "$@"

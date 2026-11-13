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
SAMBA_DIR="/etc/samba"
# The SMB accounts that UNOS created on the host, which are kept
# (remove_samba_passwords; print_completion says how to remove them).
SMB_ACCOUNTS=()
# Those of them whose Samba password may be left, because pdbedit failed
# (print_completion names them).
SMB_PASSWORDS_LEFT=()
# "failed" when smb.conf.original could not be copied back (print_completion
# says so).
SAMBA_CONF_RESTORE=""
# "yes" while the connections of the Samba passwords that remove_samba_passwords
# removed wait for the restart of smbd that restore_samba_config makes.
SMBD_RESTART_PENDING=""
# The export file of UNOS (Shares > NFS); /etc/exports and the other files of
# /etc/exports.d are not UNOS's and stay.
NFS_EXPORTS_FILE="/etc/exports.d/unos.exports"
# The udev rule that install.sh writes on a SpacemiT K1, with the script it
# runs (configure_spacemit_k1, ADR-0370). They stay, as the pools on NVMe do:
# print_completion names them.
K1_NVME_RULE="/etc/udev/rules.d/60-unos-k1-nvme.rules"

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
    echo "  - The Samba passwords of the SMB accounts created by UNOS"
    echo "  - The NFS exports added in UNOS ($NFS_EXPORTS_FILE)"
    echo ""
    echo -e "${YELLOW}Your storage pools and data on them will NOT be affected.${NC}"
    echo "The SMB accounts themselves and the group unos-smb are kept: the end of"
    echo "the uninstallation says how to remove them."
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
        # The backup holds password hashes, 2FA secrets and the JWT signing
        # secret (jwt_secret): root only, as in update.sh.
        chmod 700 "$BACKUP_DIR"

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

# The SMB accounts UNOS created on the host: the "marked" accounts, with the
# GECOS "UNOS SMB user", a uid from 70000 to 79999 and the primary group
# 70000 (unos-smb). They are kept: they have no password and no login shell,
# files on the pools belong to them, and while they exist a new installation
# of UNOS gives its SMB accounts other uids (the next uid was in the database,
# which went with the Docker volumes). Only their Samba entries are removed,
# each only when passdb stores it under exactly the account's name: tdbsam
# finds an entry by the lower-cased name, so `pdbedit -x -u alice` would also
# remove a hand-made entry 'Alice'. No other account or entry is touched.
remove_samba_passwords() {
    log_info "Removing the Samba passwords of the SMB accounts created by UNOS..."

    # The names UNOS gives its accounts, spelled out: [a-z] can match other
    # letters in some locales.
    local lower=abcdefghijklmnopqrstuvwxyz
    local upper=ABCDEFGHIJKLMNOPQRSTUVWXYZ
    local valid_name="^[$lower][${lower}0123456789_-]{0,31}\$"
    # Names that UNOS never gives either: RESERVED_ACCOUNT_NAMES, the prefix
    # systemd- and names that start with s- and a digit (Samba reads them as
    # SIDs) of linux_name_problem in supervisor/src/shares/constants.py.
    local -a reserved=(
        root daemon bin sys sync games man lp mail news
        uucp proxy www-data backup list irc gnats nobody nogroup
        adm tty disk kmem dialout fax voice cdrom floppy tape
        sudo audio dip operator src shadow utmp video sasl
        plugdev staff users netdev input kvm render sgx crontab
        messagebus sshd ssh polkitd avahi avahi-autoipd dnsmasq ntp
        ntpsec tss uuidd tcpdump lpadmin bluetooth i2c gpio spi
        docker sambashare postfix postdrop mysql postgres redis nginx
        statd rpc unbound usbmux pulse rtkit geoclue colord saned wsdd2
        unos unos-smb
    )
    local name _password uid gid gecos _rest usable word
    SMB_ACCOUNTS=()
    while IFS=: read -r name _password uid gid gecos _rest; do
        [ "$gecos" = "UNOS SMB user" ] && [ "$gid" = "70000" ] || continue
        case "$uid" in
            7[0-9][0-9][0-9][0-9]) ;;
            *) continue ;;
        esac
        # Any other name is not passed to pdbedit: it could look like an
        # option, or name an account of the system.
        if [[ "$name" =~ $valid_name ]] && [[ "$name" != systemd-* ]] && [[ "$name" != s-[0123456789]* ]]; then
            usable=yes
        else
            usable=no
        fi
        for word in "${reserved[@]}"; do
            if [ "$word" = "$name" ]; then
                usable=no
            fi
        done
        if [ "$usable" != yes ]; then
            log_warn "Not touching the account '$name': UNOS does not create accounts with such a name"
            continue
        fi
        SMB_ACCOUNTS+=("$name")
    done < <(getent passwd)
    if [ "${#SMB_ACCOUNTS[@]}" -eq 0 ]; then
        log_info "No SMB accounts created by UNOS"
        return
    fi

    # Samba's entries, listed once: the name each one is stored under, the
    # first field of `pdbedit -L`.
    local listing line stored
    local -a stored_names=()
    SMB_PASSWORDS_LEFT=()
    if ! listing=$(pdbedit -L 2>/dev/null); then
        SMB_PASSWORDS_LEFT=("${SMB_ACCOUNTS[@]}")
        log_warn "Could not list Samba's accounts (pdbedit -L), so no Samba password was removed (see the end of the uninstallation)"
        return
    fi
    while IFS= read -r line; do
        stored=${line%%:*}
        if [ "$stored" != "$line" ] && [ -n "$stored" ]; then
            stored_names+=("$stored")
        fi
    done <<< "$listing"

    local removed=no found
    for name in "${SMB_ACCOUNTS[@]}"; do
        found=no
        for stored in "${stored_names[@]}"; do
            if [ "$stored" = "$name" ]; then
                found=yes
            elif [ "${#stored}" -eq "${#name}" ] && [ "$(printf '%s' "$stored" | tr "$upper" "$lower")" = "$name" ]; then
                log_warn "Not removing the Samba account '$stored': UNOS did not create it"
            fi
        done
        [ "$found" = yes ] || continue
        if pdbedit -x -u "$name" >/dev/null 2>&1; then
            removed=yes
            log_success "Removed the Samba password of $name"
        else
            SMB_PASSWORDS_LEFT+=("$name")
            log_warn "Could not remove the Samba password of $name. Remove it with: pdbedit -x -u $name"
        fi
    done

    # An open SMB connection keeps its session when the password is gone. One
    # restart of smbd ends them all: with smb.conf.original, the one that
    # restore_samba_config makes with that file (ADR-0322).
    if [ "$removed" = yes ]; then
        if [ -f "$SAMBA_DIR/smb.conf.original" ]; then
            SMBD_RESTART_PENDING=yes
        else
            end_smb_connections
        fi
    fi
}

# The restart of smbd, if it runs, that ends the connections of the removed
# Samba passwords; it starts no stopped smbd.
end_smb_connections() {
    SMBD_RESTART_PENDING=""
    if systemctl try-restart smbd; then
        log_success "Restarted Samba (smbd): open SMB connections were ended"
    else
        log_warn "Could not restart Samba (smbd): open SMB connections end when the computers disconnect, or with: systemctl restart smbd"
    fi
}

restore_samba_config() {
    log_info "Restoring Samba configuration..."

    if [ -f "$SAMBA_DIR/smb.conf.original" ]; then
        # Copied next to smb.conf with its mode, owner and times, then renamed
        # over it: a copy that fails (a read-only or full /etc) leaves smb.conf
        # as UNOS wrote it, never a part of the file (ADR-0322). The name is
        # one of the supervisor's temporary files, which a new installation
        # removes when one is left. In a condition: under set -e a failure
        # would end the uninstallation before its last text, which names the
        # Samba passwords that may be left.
        local staged="$SAMBA_DIR/.smb.conf.unos-restore.unos-tmp"
        if cp -p "$SAMBA_DIR/smb.conf.original" "$staged" && mv -f "$staged" "$SAMBA_DIR/smb.conf"; then
            # The file from before UNOS leaves NetBIOS on: nmbd, which
            # install.sh turned off (ADR-0275), runs again, also at boot.
            systemctl enable nmbd 2>/dev/null || true
            # Also the restart that ends the removed passwords' connections.
            SMBD_RESTART_PENDING=""
            if ! systemctl restart smbd nmbd 2>/dev/null; then
                log_warn "Could not restart Samba (smbd and nmbd): until they are restarted, Samba may still use UNOS's smb.conf and keep open SMB connections. Restart them with: systemctl restart smbd nmbd"
            fi
            log_success "Original Samba configuration restored"
        else
            rm -f "$staged" 2>/dev/null || true
            SAMBA_CONF_RESTORE=failed
            log_warn "Could not restore $SAMBA_DIR/smb.conf from smb.conf.original; copy it back by hand (see the end of the uninstallation)"
        fi
    else
        log_info "No original Samba configuration to restore: $SAMBA_DIR/smb.conf stays"
    fi
    if [ "$SMBD_RESTART_PENDING" = yes ]; then
        end_smb_connections
    fi
}

# The folders exported in UNOS are no longer exported: its export file goes, and
# exportfs applies the host's other export files without it. A failure of
# exportfs (a host without the NFS server has none) does not stop the
# uninstallation: the NFS server reads the files again when it starts.
remove_nfs_exports() {
    if [ ! -e "$NFS_EXPORTS_FILE" ]; then
        return
    fi
    log_info "Removing the NFS exports of UNOS..."
    if ! rm -f "$NFS_EXPORTS_FILE"; then
        log_warn "Could not remove $NFS_EXPORTS_FILE: remove it, then run exportfs -ra"
        return
    fi
    if command -v exportfs >/dev/null 2>&1 && ! exportfs -ra; then
        log_warn "exportfs -ra failed: the host may keep exporting the folders of UNOS until its NFS server restarts"
    fi
    log_success "NFS exports of UNOS removed"
}

# What stays of UNOS's SMB setup, and how to remove it.
print_smb_leftovers() {
    local name
    if [ "${#SMB_PASSWORDS_LEFT[@]}" -gt 0 ]; then
        echo "Samba passwords: Samba may still have the passwords of these SMB"
        echo "accounts, which can then still sign in and open the shares that"
        echo "smb.conf gives them:"
        for name in "${SMB_PASSWORDS_LEFT[@]}"; do
            echo "  $name"
        done
        echo "Run pdbedit -L. For each of these names that it lists exactly so (an"
        echo "entry in other letter case is not UNOS's, and pdbedit -x would remove"
        echo "it too), run pdbedit -x -u NAME. Then run:"
        echo "  systemctl restart smbd"
        echo ""
    fi
    if [ "${#SMB_ACCOUNTS[@]}" -gt 0 ]; then
        echo "SMB accounts: the host accounts that UNOS created for SMB and the group"
        echo "unos-smb are kept. Files on the pools belong to them, and while these"
        echo "accounts exist, a new installation of UNOS gives its SMB accounts other"
        echo "user ids (it removes these accounts at its first start). If UNOS will"
        echo "not be installed again, remove them with:"
        for name in "${SMB_ACCOUNTS[@]}"; do
            echo "  userdel $name"
        done
    else
        echo "The group unos-smb is kept: files on the pools belong to it. If UNOS"
        echo "will not be installed again, remove it with:"
    fi
    echo "  groupdel unos-smb"
    echo ""
    if [ "$SAMBA_CONF_RESTORE" = failed ]; then
        echo "Samba configuration: $SAMBA_DIR/smb.conf could not be replaced by the file"
        echo "from before UNOS ($SAMBA_DIR/smb.conf.original): it is still the file"
        echo "UNOS wrote. Copy it back, then restart Samba:"
        echo "  cp $SAMBA_DIR/smb.conf.original $SAMBA_DIR/smb.conf"
        echo "  systemctl enable nmbd"
        echo "  systemctl restart smbd nmbd"
    elif [ -f "$SAMBA_DIR/smb.conf.original" ]; then
        echo "Samba configuration: $SAMBA_DIR/smb.conf is the file from before UNOS"
        echo "again ($SAMBA_DIR/smb.conf.original)."
    else
        echo "Samba configuration: $SAMBA_DIR/smb.conf stays, and Samba still serves"
        echo "the shares it lists, guest shares to anyone on your network."
        # The supervisor's import backups, each named with the UTC time it was
        # taken; the glob lists them oldest first (ADR-0362).
        local import_backups=() backup
        for backup in "$SAMBA_DIR"/smb.conf.unos-import-backup-*; do
            if [[ ${backup##*/} =~ ^smb\.conf\.unos-import-backup-[0-9]{8}_[0-9]{6}_[0-9]{6}$ ]] && [ -f "$backup" ]; then
                import_backups+=("$backup")
            fi
        done
        if [ ${#import_backups[@]} -gt 0 ]; then
            echo "The smb.conf files that UNOS replaced with its own (UNOS imported their"
            echo "shares) are kept, each named with the UTC time it was replaced, oldest"
            echo "first:"
            printf '  %s\n' "${import_backups[@]}"
            echo "Check one before you copy it back: it may give every Samba account"
            echo "access to its shares."
        fi
        echo "To change what Samba serves, edit or replace smb.conf, then run:"
        echo "  systemctl restart smbd"
    fi
    echo ""
    # install.sh set them up (ADR-0275); like samba, the packages stay, and so
    # do the files that keep them to IPv4 (none of them in /etc/unos, which
    # remove_files removes: avahi-daemon would not start without its file).
    echo "Network discovery: avahi-daemon and wsdd2 (where install.sh could install"
    echo "it) keep running, so Windows and macOS still list this server while Samba"
    echo "runs, over IPv4 only, as UNOS set them up:"
    echo "  /etc/systemd/system/wsdd2.service.d/unos.conf runs wsdd2 -4"
    echo "  /etc/systemd/system/avahi-daemon.service.d/unos.conf makes avahi-daemon"
    echo "  read /etc/avahi/avahi-daemon.unos.conf, without IPv6"
    echo "To stop wsdd2, run:"
    echo "  systemctl disable --now wsdd2"
    echo "To give avahi-daemon its own settings back (/etc/avahi/avahi-daemon.conf),"
    echo "remove the drop-in and /etc/avahi/avahi-daemon.unos.conf, then run:"
    echo "  systemctl daemon-reload"
    echo "  systemctl restart avahi-daemon"
    echo ""
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
    echo "  - The SMB accounts created by UNOS and the group unos-smb (see below)"
    echo "  - System packages (btrfs-progs, samba, avahi-daemon, wsdd2, etc.)"
    echo ""
    echo "To completely remove Docker, run:"
    echo "  apt-get purge docker-ce docker-ce-cli containerd.io"
    echo "or, where install.sh took Docker from the distribution (riscv64):"
    echo "  apt-get purge docker.io docker-buildx docker-compose"
    echo "(docker-compose-v2 instead of docker-compose on Ubuntu)"
    echo ""
    if [ -f "$K1_NVME_RULE" ]; then
        echo "SpacemiT K1: the udev rule $K1_NVME_RULE keeps turning off the power"
        echo "saving of NVMe drives, because without that an NVMe drive on the K1 can hang"
        echo "under load. To remove it, run:"
        echo "  rm $K1_NVME_RULE /usr/local/sbin/unos-k1-nvme"
        echo "and reboot."
        echo ""
    fi
    print_smb_leftovers
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
    remove_samba_passwords
    restore_samba_config
    remove_nfs_exports
    print_completion
}

main "$@"

#!/bin/bash
#
# UNOS Uninstall Script
# Removes UNOS from the system
#

set -e

# The system's folders of programs, after those of the PATH given: root's PATH
# may lack them (su without "-" keeps the user's PATH, which on Debian has no
# /usr/sbin and /sbin), and hdparm, smartctl, udevadm and exportfs are there,
# as the disk idle rule's /sbin/hdparm is.
export PATH="${PATH:+$PATH:}/usr/sbin:/usr/bin:/sbin:/bin"

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
# UNOS's udev rule of the disks' idle timeouts, which the supervisor writes
# (supervisor/src/storage/spindown.py, ADR-0378): remove_disk_idle_timeouts
# undoes what UNOS set on the disks it names, then removes it.
DISK_IDLE_RULE="/etc/udev/rules.d/90-unos-disk-spindown.rules"
# The start of its record of a disk whose automatic offline data collection
# UNOS turned off (spindown.RECORD), before the disk's serial.
DISK_IDLE_RECORD='# UNOS turned off the automatic offline data collection of the disk with the serial '
# What became of the rule when it is not simply gone (print_completion says
# so): "unread" (it could not be read), "nul" (it holds a NUL byte), "kept" (it
# could not be removed) or "records" (it now holds only the records of the
# disks whose scans are still off).
DISK_IDLE_RULE_LEFT=""
# The disks whose idle timeout it turned off (print_completion says what they
# have now).
DISK_IDLE_OFF=()
# What it could not undo, two entries each: what is left and why, then the
# command that undoes it (print_completion names them).
DISK_IDLE_LEFT=()
# The serials of the disks whose scans, which UNOS turned off, are still off.
DISK_IDLE_SCANS_OFF=()
# The rule's lines other than comments that are not as UNOS writes them
# (edited by hand): nothing was run for them, and print_completion shows them.
DISK_IDLE_FOREIGN=()
# The exit status of a Ctrl-C (130), a TERM (143) or a HUP (129, a dropped SSH
# session) that came while the step undid the disks: it stops once the command
# then running has ended.
DISK_IDLE_STOP=""

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
    echo "  - The disk idle timeouts set in UNOS ($DISK_IDLE_RULE):"
    echo "    the disks get none, and their own automatic offline scans that UNOS"
    echo "    turned off are turned on again"
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
    # A run after one that removed the volumes (one stopped in
    # remove_disk_idle_timeouts, which says to run it again) has no data to back
    # up: docker run -v would create the volume empty, and the newest final
    # backup would be an empty archive beside the real one.
    if ! docker volume inspect deploy_unos-data >/dev/null 2>&1; then
        log_info "No backup: Docker shows no volume deploy_unos-data with UNOS's data (an earlier run of the uninstallation removes it)"
        return 0
    fi
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

# Whether $1 is one of the other arguments.
is_one_of() {
    local want=$1 item
    shift
    for item in "$@"; do
        if [ "$item" = "$want" ]; then
            return 0
        fi
    done
    return 1
}

# The disks' idle timeouts of UNOS (ADR-0378), once the supervisor that writes
# their rule has stopped, and before remove_files removes this script with
# $UNOS_INSTALL_DIR. On each disk the rule names, what UNOS set is undone
# with the host's tools (the supervisor's container, whose smartctl UNOS used,
# is gone): hdparm -S 0 for a disk's line, as Off on the Storage page, and
# smartctl -o on for a disk whose own automatic offline data collection UNOS
# turned off, which a comment of the rule records (a setting that the drive is
# expected to keep across restarts: assumed, not confirmed). Then the rule goes,
# as UNOS's configuration: a run stopped before that finds it whole again, with
# the script still there to run. A disk whose scans stay off keeps its record in
# the rule (write_disk_idle_records). Only the lines that spindown.py's
# read_rule takes as UNOS's count, read as udev reads a file without NUL bytes
# (a line ends at \n or \r, with spaces and tabs around it; udev also ends one at
# a NUL byte, which UNOS never writes): its exact form, a serial that
# _RULE_SERIAL accepts and the hdparm -S value of an offered timeout. The file is
# read as text, never run; a disk is found by its serial with lsblk, which reads
# no disk, and no other disk is touched. A rule that cannot be read, or that
# holds a NUL byte, stays: it is the only record of the disks whose scans are
# off. A Ctrl-C, a TERM or a HUP while the disks are undone ends the
# uninstallation, with the rule whole (disk_idle_stop_if_asked); nothing else
# here stops it: print_completion names what is left.
remove_disk_idle_timeouts() {
    remove_disk_idle_leftovers
    [ -e "$DISK_IDLE_RULE" ] || return 0
    log_info "Removing the disk idle timeouts of UNOS..."
    local content byte
    # Bash drops NUL bytes from what it reads, which would join the parts of a
    # line around one: read_rule counts no such line, and udev ends a line at a
    # NUL. read returns 0 only when it found one.
    if IFS= read -r -d '' byte 2>/dev/null <"$DISK_IDLE_RULE"; then
        DISK_IDLE_RULE_LEFT=nul
        log_warn "$DISK_IDLE_RULE holds a NUL byte, which UNOS never writes: it stays, and nothing is run for it (see the end of the uninstallation)"
        return 0
    fi
    if ! content=$(cat -- "$DISK_IDLE_RULE"); then
        DISK_IDLE_RULE_LEFT=unread
        log_warn "Could not read $DISK_IDLE_RULE, so it stays (see the end of the uninstallation)"
        return 0
    fi

    # What spindown.py writes, spelled out, as are the blanks: [A-Z] and
    # [[:space:]] can match other characters in some locales. The values are
    # hdparm_value of SPINDOWN_MINUTES: 5, 10 and 20 minutes, then 30 minutes
    # and 1 to 5 hours.
    local serial_pattern='^[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789#+.:=@_-]{1,64}$'
    local rule_pattern='^ACTION=="add", SUBSYSTEM=="block", ENV[{]DEVTYPE[}]=="disk", ENV[{]ID_SERIAL_SHORT[}]=="([^"]+)", RUN[+]="/sbin/hdparm -S ([0123456789]+) [$]devnode"$'
    local offered=' 60 120 240 241 242 244 246 248 250 '
    local blanks=$' \t' cr=$'\r' nl=$'\n'
    local line serial value
    local -a serials=() timed=() recorded=()
    while IFS= read -r line; do
        line=${line#"${line%%[!$blanks]*}"}
        line=${line%"${line##*[!$blanks]}"}
        if [ -z "$line" ]; then
            continue
        elif [[ $line =~ $rule_pattern ]]; then
            serial=${BASH_REMATCH[1]}
            value=${BASH_REMATCH[2]}
            # read_rule reads the value as a number: 060 is 60.
            while [[ $value == 0?* ]]; do
                value=${value#0}
            done
            if [[ $serial =~ $serial_pattern ]] && [[ $offered == *" $value "* ]]; then
                is_one_of "$serial" "${serials[@]}" || serials+=("$serial")
                is_one_of "$serial" "${timed[@]}" || timed+=("$serial")
                continue
            fi
        elif [[ $line == "# UNOS turned off"* ]]; then
            serial=${line#"$DISK_IDLE_RECORD"}
            if [ "$serial" != "$line" ] && [[ $serial =~ $serial_pattern ]]; then
                is_one_of "$serial" "${serials[@]}" || serials+=("$serial")
                is_one_of "$serial" "${recorded[@]}" || recorded+=("$serial")
                continue
            fi
        elif [[ $line == "#"* ]]; then
            # Another comment, such as the header, does nothing.
            continue
        fi
        # Shown at the end as printable ASCII: a control character of the file
        # could drive the terminal.
        DISK_IDLE_FOREIGN+=("$(LC_ALL=C; printf '%s' "${line//[![:print:]]/?}")")
    done <<< "${content//$cr/$nl}"

    if [ "${#serials[@]}" -gt 0 ]; then
        # timeout runs hdparm and smartctl in a process group of their own,
        # which Ctrl-C does not reach, and bash goes on after a SIGINT that its
        # command survived: a Ctrl-C, a TERM or a HUP is noted, and the step
        # stops once the command then running has ended and its temporary
        # folder is gone, before anything is written.
        trap 'DISK_IDLE_STOP=130' INT
        trap 'DISK_IDLE_STOP=143' TERM
        trap 'DISK_IDLE_STOP=129' HUP
        # The whole disks, with the serials that udev gave them, as the
        # supervisor's lsblk reads them.
        local listing listed=yes
        if ! listing=$(lsblk -dnr -o NAME,TYPE,SERIAL); then
            listed=no
        fi
        disk_idle_stop_if_asked
        local name type reported path why
        local -a found
        for serial in "${serials[@]}"; do
            path=""
            why=""
            found=()
            if [ "$listed" = yes ]; then
                while read -r name type reported; do
                    if [ "$type" = disk ] && [ "$reported" = "$serial" ]; then
                        found+=("/dev/$name")
                    fi
                done <<< "$listing"
                if [ "${#found[@]}" -eq 1 ]; then
                    path=${found[0]}
                elif [ "${#found[@]}" -eq 0 ]; then
                    why="it is not attached"
                else
                    why="more than one disk reports this serial (${found[*]})"
                fi
            else
                why="the disks could not be listed (lsblk failed)"
            fi
            if is_one_of "$serial" "${timed[@]}"; then
                undo_disk_idle "$serial" "$path" "$why" timer
                disk_idle_stop_if_asked
            fi
            if is_one_of "$serial" "${recorded[@]}"; then
                undo_disk_idle "$serial" "$path" "$why" scans
                disk_idle_stop_if_asked
            fi
        done
        trap - INT TERM HUP
    fi

    # The timeouts must not come back at boot: when the records cannot be
    # written, the rule goes all the same, and the end names those disks.
    if [ "${#DISK_IDLE_SCANS_OFF[@]}" -gt 0 ]; then
        if write_disk_idle_records; then
            DISK_IDLE_RULE_LEFT=records
            log_warn "$DISK_IDLE_RULE now holds only the records of the disks whose automatic offline scans are still off (see the end of the uninstallation)"
        else
            log_warn "Could not keep the records of the disks whose automatic offline scans are still off in $DISK_IDLE_RULE: the end of the uninstallation names them"
        fi
    fi
    if [ "$DISK_IDLE_RULE_LEFT" != records ]; then
        if rm -f -- "$DISK_IDLE_RULE"; then
            log_success "Removed UNOS's udev rule $DISK_IDLE_RULE"
        else
            DISK_IDLE_RULE_LEFT=kept
            log_warn "Could not remove $DISK_IDLE_RULE (see the end of the uninstallation)"
            return 0
        fi
    fi
    if command -v udevadm >/dev/null 2>&1 && ! udevadm control --reload; then
        log_warn "udevadm control --reload failed: udev reads its rules again by itself"
    fi
}

# Ends the uninstallation when a Ctrl-C, a TERM or a HUP came while the disks
# were undone (DISK_IDLE_STOP): nothing is written yet, so the rule stays whole
# for the next run, and so does this script ($UNOS_INSTALL_DIR goes after this
# step).
disk_idle_stop_if_asked() {
    [ -n "$DISK_IDLE_STOP" ] || return 0
    trap - INT TERM HUP
    log_warn "Stopped: UNOS's udev rule $DISK_IDLE_RULE stays as it was, and the disks it names may keep what UNOS set. Run the uninstallation again to finish."
    exit "$DISK_IDLE_STOP"
}

# Removes the temporary files that a run killed in write_disk_idle_records left
# next to the rule, <rule>.unos-tmp.<pid>, and no file of another name. udev
# does not read them: their names do not end in .rules.
remove_disk_idle_leftovers() {
    local staged
    for staged in "$DISK_IDLE_RULE".unos-tmp.*; do
        [[ ${staged#"$DISK_IDLE_RULE.unos-tmp."} =~ ^[0123456789]+$ ]] && [ -f "$staged" ] || continue
        if ! rm -f -- "$staged"; then
            log_warn "Could not remove $staged, a temporary file of an earlier uninstallation, which udev does not read: remove it with rm $staged"
        fi
    done
}

# Replaces UNOS's rule with comments and the records of the disks whose scans
# are still off (DISK_IDLE_SCANS_OFF), in the form that read_rule reads: udev
# runs nothing of it, a new installation of UNOS takes them as records without a
# timeout, which its Off turns on again (not on a disk with SMART off, whose
# scans it does not read), and another run of this script (from a checkout of
# UNOS: the uninstallation removes $UNOS_INSTALL_DIR) tries them again. Written
# next to the rule under a name that udev does not read, then renamed over it,
# as the supervisor writes it; a run killed in between leaves that file, which
# the next run removes (remove_disk_idle_leftovers).
write_disk_idle_records() {
    local staged="$DISK_IDLE_RULE.unos-tmp.$$" text serial
    text="# Left by the uninstallation of UNOS (deploy/uninstall.sh, ADR-0378). UNOS turned off the
# automatic offline data collection of the disks below for their idle timeouts, and the
# uninstallation could not turn it on again. udev runs nothing of this file. Once each disk
# has it on again (smartctl -o on DISK, or smartctl -s on -o on DISK where SMART is off on the
# disk, which turns SMART on too; DISK is the disk's path, from lsblk -o PATH,SERIAL), remove
# this file.
"
    for serial in "${DISK_IDLE_SCANS_OFF[@]}"; do
        text+="$DISK_IDLE_RECORD$serial"$'\n'
    done
    if printf '%s' "$text" >"$staged" && chmod 644 "$staged" && mv -f -- "$staged" "$DISK_IDLE_RULE"; then
        return 0
    fi
    rm -f -- "$staged" 2>/dev/null || true
    return 1
}

# Runs the command "$@" under the host's timeout, with /dev/null as its
# standard input and its standard output and errors in the files out and errors
# of the folder $1: TERM after 30 seconds, then KILL 5 seconds later, which
# timeout sends to its own process group and so dies of too (137). The script
# goes on then, 35 seconds at most after the start; a command that the kernel
# holds in uninterruptible sleep (a disk that does not answer) outlives the KILL
# until the kernel lets it go, with the files it was given open. So it gets
# none of the script's own: not the pipe of a command substitution, for which
# bash would wait as long, nor the script's output or errors, which may be a
# pipe too (uninstall.sh 2>&1 | tee log, or ssh without a terminal), whose
# reader would wait as long. bash's own line on a command that a signal ended
# ("Killed") is not shown, since the end of the uninstallation says what
# happened; the command's errors are, once it has ended (undo_disk_idle).
run_bounded() {
    local folder=$1
    shift
    { timeout --kill-after=5 30 "$@" </dev/null >"$folder/out" 2>"$folder/errors"; } 2>/dev/null
}

# Undoes on the disk with the serial $1 at the path $2 ("" when it was not
# found, $3 says why) what UNOS set: $4 "timer", its idle timeout (hdparm -S 0,
# which wakes the disk as Off does), or "scans", its automatic offline scans
# (smartctl -o on, with -c, which reads them back, as the supervisor does:
# smartctl exits 0 without sending -o to a disk with SMART off, and the end
# then names the command that turns SMART on too, which UNOS did not turn off).
# Each command runs under the host's timeout (run_bounded), its output and
# errors in a temporary folder of mktemp -d ($TMPDIR or /tmp), removed once
# they are read: its errors are shown then, and smartctl's answer is read.
undo_disk_idle() {
    local serial=$1 path=$2 why=$3 tool left package output="" undone=no status=0 folder
    local -a undo
    if [ "$4" = timer ]; then
        tool=hdparm
        package=hdparm
        undo=(hdparm -S 0)
        left="may keep its idle timeout until it restarts"
    else
        tool=smartctl
        package=smartmontools
        undo=(smartctl -o on)
        left="has its automatic offline scans off"
    fi
    if [ -z "$why" ] && ! command -v "$tool" >/dev/null 2>&1; then
        why="the host has no $tool (package $package)"
    fi
    if [ -z "$why" ] && ! folder=$(mktemp -d); then
        why="no temporary folder for the output of $tool could be made (mktemp -d failed)"
    fi
    if [ -z "$why" ]; then
        if [ "$4" = timer ]; then
            run_bounded "$folder" "${undo[@]}" "$path" || status=$?
            [ "$status" -ne 0 ] || undone=yes
        else
            run_bounded "$folder" "${undo[@]}" -c "$path" || status=$?
            output=$(cat -- "$folder/out") || output=""
            # smartctl's line of -c on them (smartmontools' ataprint.cpp).
            case $output in
                *"Auto Offline Data Collection: Enabled."*) undone=yes ;;
            esac
        fi
        cat -- "$folder/errors" >&2 || true
        rm -rf -- "$folder" || true
        if [ "$undone" = yes ]; then
            if [ "$4" = timer ]; then
                DISK_IDLE_OFF+=("$path (serial $serial)")
                log_success "Turned off the idle timeout of $path (serial $serial)"
            else
                log_success "Turned on again the automatic offline scans of $path (serial $serial)"
            fi
            return 0
        fi
        # 124: the timeout's TERM ended it; 137: its KILL, which ends timeout too.
        if [ "$status" -eq 124 ] || [ "$status" -eq 137 ]; then
            why="${undo[*]} did not answer within 30 seconds"
        elif [[ $output == *"Auto Offline Data Collection: Disabled."* ]]; then
            why="${undo[*]} left them off (exit status $status)"
        elif [[ $output == *"SMART Disabled."* ]]; then
            # SMART is off on the disk (ataprint.cpp): -o reaches it only
            # with -s on, which the uninstallation leaves to the user.
            why="SMART is off on the disk, and smartctl sends -o on only to a disk with SMART on; smartctl -s on -o on turns SMART on too"
            undo=(smartctl -s on -o on)
        elif [ "$status" -ne 0 ]; then
            why="${undo[*]} failed (exit status $status)"
        else
            why="${undo[*]} read no state"
        fi
    fi
    if [ "$4" = scans ]; then
        DISK_IDLE_SCANS_OFF+=("$serial")
    fi
    local who="The disk with the serial $serial"
    if [ -n "$path" ]; then
        who+=" ($path)"
    fi
    DISK_IDLE_LEFT+=("$who $left: $why" "${undo[*]} ${path:-DISK}")
    log_warn "$who $left: $why (see the end of the uninstallation)"
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

# What stays of UNOS's disk idle timeouts, and how to undo it.
print_disk_idle_leftovers() {
    local i placeholder=no
    if [ "$DISK_IDLE_RULE_LEFT" = unread ] || [ "$DISK_IDLE_RULE_LEFT" = nul ]; then
        if [ "$DISK_IDLE_RULE_LEFT" = nul ]; then
            echo "Disk idle timeouts: UNOS's udev rule $DISK_IDLE_RULE holds a NUL byte,"
            echo "which UNOS never writes, so the uninstallation ran nothing for it."
        else
            echo "Disk idle timeouts: UNOS's udev rule $DISK_IDLE_RULE could not be read."
        fi
        echo "It stays, and so does what it set: the idle timeouts of the disks it names,"
        echo "at every boot, and their own automatic offline scans, which UNOS turned off"
        echo "where a line \"# UNOS turned off the automatic offline data collection of the"
        echo "disk with the serial ...\" says so. Run for each disk it names, with the"
        echo "disk's path (lsblk -o PATH,SERIAL) for DISK:"
        echo "  hdparm -S 0 DISK"
        echo "and, for each disk such a line names:"
        echo "  smartctl -o on DISK"
        echo "(smartctl -s on -o on DISK where SMART is off on the disk, which turns SMART"
        echo "on too). Then remove the rule:"
        echo "  rm $DISK_IDLE_RULE"
        echo ""
        return 0
    fi
    if [ "${#DISK_IDLE_OFF[@]}" -gt 0 ]; then
        echo "Disk idle timeouts: these disks had an idle timeout set in UNOS, now turned"
        echo "off. They have no standby timer until they restart; then they have their"
        if [ "$DISK_IDLE_RULE_LEFT" = kept ]; then
            echo "timeout of UNOS's rule again, until it is removed (see below):"
        else
            echo "own, or the one that /etc/hdparm.conf sets:"
        fi
        printf '  %s\n' "${DISK_IDLE_OFF[@]}"
        echo ""
    fi
    if [ "${#DISK_IDLE_LEFT[@]}" -gt 0 ]; then
        echo "Disk idle timeouts left: the uninstallation could not undo all that UNOS set"
        echo "on the disks of its udev rule. To undo the rest, run as root:"
        for ((i = 0; i < ${#DISK_IDLE_LEFT[@]}; i += 2)); do
            echo "  ${DISK_IDLE_LEFT[i]}."
            echo "    ${DISK_IDLE_LEFT[i + 1]}"
            if [[ ${DISK_IDLE_LEFT[i + 1]} == *" DISK" ]]; then
                placeholder=yes
            fi
        done
        if [ "$placeholder" = yes ]; then
            echo "DISK: the path of the disk with that serial, which lsblk -o PATH,SERIAL"
            echo "shows while it is attached."
        fi
        echo ""
    fi
    if [ "$DISK_IDLE_RULE_LEFT" = kept ]; then
        echo "Disk idle timeouts: UNOS's udev rule $DISK_IDLE_RULE could not be removed: it"
        echo "sets the idle timeouts again at every boot. Remove it with:"
        echo "  rm $DISK_IDLE_RULE"
        echo ""
    elif [ "$DISK_IDLE_RULE_LEFT" = records ]; then
        echo "Disk idle timeouts: UNOS's udev rule $DISK_IDLE_RULE now holds only the"
        echo "records of the disks whose automatic offline scans are still off (above),"
        # The supervisor reads no scans of a disk with SMART off (spindown._scans).
        echo "which udev ignores. A new installation of UNOS reads them: saving Off for"
        echo "such a disk turns its scans on again, unless SMART is off on the disk: then"
        echo "only smartctl -s on -o on does. Once they are on, remove the rule:"
        echo "  rm $DISK_IDLE_RULE"
        echo ""
    fi
    if [ "${#DISK_IDLE_FOREIGN[@]}" -gt 0 ]; then
        echo "Disk idle timeouts: these lines of UNOS's udev rule are not as UNOS writes"
        if [ "$DISK_IDLE_RULE_LEFT" = kept ]; then
            echo "them (edited by hand), so the uninstallation ran nothing for them:"
        else
            echo "them (edited by hand). The uninstallation ran nothing for them, and they"
            echo "are no longer in the rule:"
        fi
        printf '  %s\n' "${DISK_IDLE_FOREIGN[@]}"
        echo ""
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
    echo "  - The SMB accounts created by UNOS and the group unos-smb (see below)"
    echo "  - System packages (btrfs-progs, samba, avahi-daemon, wsdd2, etc.)"
    echo ""
    echo "To completely remove Docker, run:"
    echo "  apt-get purge docker-ce docker-ce-cli containerd.io"
    echo "or, where install.sh took Docker from the distribution (riscv64):"
    echo "  apt-get purge docker.io docker-buildx docker-compose"
    echo "(docker-compose-v2 instead of docker-compose on Ubuntu)"
    echo ""
    # install.sh stopped smartd, since UNOS read SMART itself (ADR-0378).
    echo "SMART monitoring: install.sh turned off smartd, since UNOS read the disks'"
    echo "SMART data itself. To have smartd watch the disks again, run:"
    echo "  systemctl enable --now smartmontools"
    echo ""
    print_disk_idle_leftovers
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
    # Once the supervisor, which writes the disk idle rule, has stopped, and
    # before remove_files removes this script: a run stopped in the step can
    # be run again, and finds the rule whole.
    remove_disk_idle_timeouts
    remove_files
    remove_samba_passwords
    restore_samba_config
    remove_nfs_exports
    print_completion
}

main "$@"

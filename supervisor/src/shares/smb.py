import subprocess
import re
import os
import logging
from ..config import get_settings
from .schemas import SmbShareCreate, SmbShareInfo

logger = logging.getLogger(__name__)
settings = get_settings()

SMB_GLOBAL_CONFIG = """[global]
   workgroup = WORKGROUP
   server string = UNOS NAS
   security = user
   map to guest = Bad Password
   log file = /var/log/samba/log.%m
   max log size = 50
   dns proxy = no

"""


def _read_smb_conf() -> str:
    """Read current smb.conf."""
    if os.path.exists(settings.smb_conf_path):
        with open(settings.smb_conf_path, "r") as f:
            return f.read()
    return SMB_GLOBAL_CONFIG


def _write_smb_conf(content: str) -> None:
    """Write smb.conf and reload Samba if available."""
    # Ensure directory exists
    conf_dir = os.path.dirname(settings.smb_conf_path)
    if conf_dir:
        os.makedirs(conf_dir, exist_ok=True)
    
    with open(settings.smb_conf_path, "w") as f:
        f.write(content)
    
    # Try to reload Samba config, but dont fail if samba is not installed
    try:
        result = subprocess.run(
            ["smbcontrol", "all", "reload-config"], 
            capture_output=True,
            timeout=10
        )
        if result.returncode != 0:
            logger.warning(f"smbcontrol reload failed: {result.stderr.decode()}")
    except FileNotFoundError:
        logger.info("smbcontrol not found, skipping Samba reload")
    except subprocess.TimeoutExpired:
        logger.warning("smbcontrol reload timed out")
    except Exception as e:
        logger.warning(f"Failed to reload Samba config: {e}")


def _parse_shares(content: str) -> dict[str, SmbShareInfo]:
    """Parse smb.conf into share objects."""
    shares = {}
    current_share = None
    current_config = {}

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        # Section header
        match = re.match(r"\[(.+)\]", line)
        if match:
            if current_share and current_share != "global":
                shares[current_share] = _config_to_share(current_share, current_config)
            current_share = match.group(1)
            current_config = {}
            continue

        # Config line
        if "=" in line and current_share:
            key, value = line.split("=", 1)
            current_config[key.strip()] = value.strip()

    if current_share and current_share != "global":
        shares[current_share] = _config_to_share(current_share, current_config)

    return shares


def _config_to_share(name: str, config: dict) -> SmbShareInfo:
    """Convert config dict to SmbShareInfo."""
    valid_users = []
    if "valid users" in config:
        valid_users = [u.strip() for u in config["valid users"].split(",") if u.strip()]

    return SmbShareInfo(
        name=name,
        path=config.get("path", ""),
        comment=config.get("comment", ""),
        browseable=config.get("browseable", "yes").lower() == "yes",
        readonly=config.get("read only", "no").lower() == "yes",
        guest_ok=config.get("guest ok", "no").lower() == "yes",
        valid_users=valid_users,
    )


def _share_to_config(share: SmbShareInfo) -> str:
    """Convert SmbShareInfo to config section."""
    lines = [f"[{share.name}]"]
    lines.append(f"   path = {share.path}")
    if share.comment:
        lines.append(f"   comment = {share.comment}")
    browseable_val = "yes" if share.browseable else "no"
    readonly_val = "yes" if share.readonly else "no"
    guest_ok_val = "yes" if share.guest_ok else "no"
    lines.append(f"   browseable = {browseable_val}")
    lines.append(f"   read only = {readonly_val}")
    lines.append(f"   guest ok = {guest_ok_val}")
    if share.valid_users:
        users_str = ", ".join(share.valid_users)
        lines.append(f"   valid users = {users_str}")
    # For guest-writable shares, force file ownership and permissions
    if share.guest_ok and not share.readonly:
        lines.append("   force user = nobody")
        lines.append("   force group = nogroup")
        lines.append("   create mask = 0666")
        lines.append("   directory mask = 0777")
    lines.append("")
    return "\n".join(lines)


def list_shares() -> list[SmbShareInfo]:
    """List all SMB shares."""
    content = _read_smb_conf()
    shares = _parse_shares(content)
    return list(shares.values())


def get_share(name: str) -> SmbShareInfo | None:
    """Get a specific share."""
    content = _read_smb_conf()
    shares = _parse_shares(content)
    return shares.get(name)


def create_share(share: SmbShareCreate) -> SmbShareInfo:
    """Create a new SMB share."""
    # Validate name
    if not re.match(r"^[a-zA-Z0-9_]+$", share.name):
        raise ValueError("Share name must be alphanumeric with underscores only")

    content = _read_smb_conf()
    shares = _parse_shares(content)

    if share.name in shares:
        raise ValueError(f"Share {share.name} already exists")

    # Create directory if needed
    os.makedirs(share.path, exist_ok=True)

    # Set directory permissions for guest-writable shares
    if share.guest_ok and not share.readonly:
        os.chmod(share.path, 0o777)

    share_info = SmbShareInfo(
        name=share.name,
        path=share.path,
        comment=share.comment,
        browseable=share.browseable,
        readonly=share.readonly,
        guest_ok=share.guest_ok,
        valid_users=share.valid_users,
    )

    # Append new share
    content += "\n" + _share_to_config(share_info)
    _write_smb_conf(content)

    return share_info


def update_share(name: str, share: SmbShareCreate) -> SmbShareInfo:
    """Update an existing SMB share."""
    content = _read_smb_conf()
    shares = _parse_shares(content)

    if name not in shares:
        raise ValueError(f"Share {name} not found")

    share_info = SmbShareInfo(
        name=share.name,
        path=share.path,
        comment=share.comment,
        browseable=share.browseable,
        readonly=share.readonly,
        guest_ok=share.guest_ok,
        valid_users=share.valid_users,
    )

    # Update directory permissions for guest-writable shares
    if os.path.exists(share.path):
        if share.guest_ok and not share.readonly:
            os.chmod(share.path, 0o777)

    # If name changed, remove old and add new
    if name != share.name:
        del shares[name]
    shares[share.name] = share_info

    # Rebuild config
    new_content = SMB_GLOBAL_CONFIG
    for s in shares.values():
        new_content += _share_to_config(s)

    _write_smb_conf(new_content)
    return share_info


def delete_share(name: str) -> None:
    """Delete an SMB share."""
    content = _read_smb_conf()
    shares = _parse_shares(content)

    if name not in shares:
        raise ValueError(f"Share {name} not found")

    del shares[name]

    # Rebuild config
    new_content = SMB_GLOBAL_CONFIG
    for s in shares.values():
        new_content += _share_to_config(s)

    _write_smb_conf(new_content)

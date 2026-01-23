import subprocess
import os
import logging
from ..config import get_settings
from .schemas import NfsExportCreate, NfsExportInfo

logger = logging.getLogger(__name__)
settings = get_settings()


def _read_exports() -> str:
    """Read current /etc/exports."""
    if os.path.exists(settings.nfs_exports_path):
        with open(settings.nfs_exports_path, "r") as f:
            return f.read()
    return ""


def _write_exports(content: str) -> None:
    """Write /etc/exports and refresh NFS if available."""
    # Ensure directory exists
    exports_dir = os.path.dirname(settings.nfs_exports_path)
    if exports_dir:
        os.makedirs(exports_dir, exist_ok=True)
        
    with open(settings.nfs_exports_path, "w") as f:
        f.write(content)
    
    # Try to refresh NFS exports, but dont fail if exportfs is not installed
    try:
        result = subprocess.run(["exportfs", "-ra"], capture_output=True, timeout=10)
        if result.returncode != 0:
            logger.warning(f"exportfs refresh failed: {result.stderr.decode()}")
    except FileNotFoundError:
        logger.info("exportfs not found, skipping NFS refresh")
    except subprocess.TimeoutExpired:
        logger.warning("exportfs refresh timed out")
    except Exception as e:
        logger.warning(f"Failed to refresh NFS exports: {e}")


def _parse_exports(content: str) -> list[NfsExportInfo]:
    """Parse /etc/exports into export objects."""
    exports = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split()
        if len(parts) >= 2:
            path = parts[0]
            # Parse client(options) format
            client_opts = parts[1]
            if "(" in client_opts:
                clients, options = client_opts.split("(", 1)
                options = options.rstrip(")")
            else:
                clients = client_opts
                options = "rw,sync"

            exports.append(
                NfsExportInfo(
                    path=path,
                    clients=clients,
                    options=options,
                )
            )

    return exports


def list_exports() -> list[NfsExportInfo]:
    """List all NFS exports."""
    content = _read_exports()
    return _parse_exports(content)


def create_export(export: NfsExportCreate) -> NfsExportInfo:
    """Create a new NFS export."""
    content = _read_exports()
    exports = _parse_exports(content)

    # Check for duplicates
    for e in exports:
        if e.path == export.path:
            raise ValueError(f"Export for {export.path} already exists")

    # Create directory if needed
    os.makedirs(export.path, exist_ok=True)

    # Append new export
    new_line = f"{export.path} {export.clients}({export.options})\n"
    content += new_line
    _write_exports(content)

    return NfsExportInfo(
        path=export.path,
        clients=export.clients,
        options=export.options,
    )


def delete_export(path: str) -> None:
    """Delete an NFS export."""
    content = _read_exports()
    exports = _parse_exports(content)

    found = False
    new_exports = []
    for e in exports:
        if e.path == path:
            found = True
        else:
            new_exports.append(e)

    if not found:
        raise ValueError(f"Export for {path} not found")

    # Rebuild exports file
    new_content = ""
    for e in new_exports:
        new_content += f"{e.path} {e.clients}({e.options})\n"

    _write_exports(new_content)

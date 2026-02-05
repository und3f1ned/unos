"""
Mock data for deviceless testing.

This module provides realistic mock data for all device-dependent operations.
Enable via UNOS_TEST_MODE=true environment variable.
"""

import json
from datetime import datetime

# Mock lsblk output - simulated disks
MOCK_LSBLK_OUTPUT = {
    "blockdevices": [
        {
            "name": "sda",
            "path": "/dev/sda",
            "model": "WDC WD40EFRX-68N",
            "serial": "WD-WCC7K0123456",
            "size": 4000787030016,  # 4TB
            "rota": True,
            "mountpoint": None,
            "type": "disk",
            "children": []
        },
        {
            "name": "sdb",
            "path": "/dev/sdb",
            "model": "WDC WD40EFRX-68N",
            "serial": "WD-WCC7K0789012",
            "size": 4000787030016,  # 4TB
            "rota": True,
            "mountpoint": None,
            "type": "disk",
            "children": []
        },
        {
            "name": "sdc",
            "path": "/dev/sdc",
            "model": "Samsung SSD 870",
            "serial": "S5VENG0N123456",
            "size": 500107862016,  # 500GB SSD
            "rota": False,
            "mountpoint": None,
            "type": "disk",
            "children": []
        },
        {
            "name": "sdd",
            "path": "/dev/sdd",
            "model": "Seagate IronWolf",
            "serial": "ZA10ABC123",
            "size": 8001563222016,  # 8TB
            "rota": True,
            "mountpoint": None,
            "type": "disk",
            "children": []
        }
    ]
}


def get_mock_smart_data(device_path: str) -> dict:
    """Generate mock SMART data for a device."""
    device_name = device_path.split("/")[-1]

    # SSD vs HDD smart data differs
    is_ssd = device_name == "sdc"

    base_data = {
        "json_format_version": [1, 0],
        "smartctl": {"version": [7, 2]},
        "device": {"name": device_path, "type": "sat"},
        "model_name": MOCK_LSBLK_OUTPUT["blockdevices"][0]["model"] if device_name == "sda" else "Generic Drive",
        "serial_number": "TEST12345",
        "smart_status": {"passed": True},
        "temperature": {"current": 38 if not is_ssd else 32},
        "power_on_time": {"hours": 12500},
        "ata_smart_attributes": {
            "table": [
                {"id": 1, "name": "Raw_Read_Error_Rate", "value": 100, "worst": 100, "raw": {"value": 0}},
                {"id": 5, "name": "Reallocated_Sector_Ct", "value": 100, "worst": 100, "raw": {"value": 0}},
                {"id": 9, "name": "Power_On_Hours", "value": 99, "worst": 99, "raw": {"value": 12500}},
                {"id": 194, "name": "Temperature_Celsius", "value": 62, "worst": 50, "raw": {"value": 38}},
                {"id": 197, "name": "Current_Pending_Sector", "value": 100, "worst": 100, "raw": {"value": 0}},
            ]
        }
    }

    return base_data


# Mock btrfs filesystem show output
MOCK_BTRFS_POOLS = """Label: 'data'  uuid: 550e8400-e29b-41d4-a716-446655440000
        Total devices 2 FS bytes used 1.5TiB
        devid    1 size 4.00TiB used 2.00TiB path /dev/sda
        devid    2 size 4.00TiB used 2.00TiB path /dev/sdb

Label: 'backup'  uuid: 6ba7b810-9dad-11d1-80b4-00c04fd430c8
        Total devices 1 FS bytes used 500GiB
        devid    1 size 8.00TiB used 500GiB path /dev/sdd

"""

# Mock btrfs filesystem usage output
MOCK_BTRFS_USAGE = {
    "data": """Overall:
    Device size:                   8001563222016
    Device allocated:              4398046511104
    Device unallocated:            3603516710912
    Device missing:                          0
    Device slack:                            0
    Used:                          1649267441664
    Free (estimated):              6352295780352
    Free (statfs, df):             6352295780352

Data,RAID1: Size:2.00TiB, Used:1.50TiB (75.00%)
Metadata,RAID1: Size:8.00GiB, Used:1.50GiB (18.75%)
System,RAID1: Size:32.00MiB, Used:16.00KiB (0.05%)
""",
    "backup": """Overall:
    Device size:                   8001563222016
    Device allocated:              536870912000
    Device unallocated:            7464692310016
    Device missing:                          0
    Device slack:                            0
    Used:                          536870912000
    Free (estimated):              7464692310016
    Free (statfs, df):             7464692310016

Data,single: Size:500.00GiB, Used:500.00GiB (100.00%)
Metadata,single: Size:2.00GiB, Used:100.00MiB (4.88%)
System,single: Size:32.00MiB, Used:16.00KiB (0.05%)
"""
}

# Mock Docker containers
MOCK_CONTAINERS = [
    {
        "Id": "abc123def456",
        "Names": ["/plex"],
        "Image": "plexinc/pms-docker:latest",
        "ImageID": "sha256:abc123",
        "State": "running",
        "Status": "Up 3 days",
        "Ports": [{"PrivatePort": 32400, "PublicPort": 32400, "Type": "tcp"}],
        "Created": 1704067200,
    },
    {
        "Id": "def456ghi789",
        "Names": ["/jellyfin"],
        "Image": "jellyfin/jellyfin:latest",
        "ImageID": "sha256:def456",
        "State": "running",
        "Status": "Up 5 hours",
        "Ports": [{"PrivatePort": 8096, "PublicPort": 8096, "Type": "tcp"}],
        "Created": 1704153600,
    },
    {
        "Id": "ghi789jkl012",
        "Names": ["/nextcloud"],
        "Image": "nextcloud:latest",
        "ImageID": "sha256:ghi789",
        "State": "exited",
        "Status": "Exited (0) 2 hours ago",
        "Ports": [],
        "Created": 1704240000,
    }
]

# Mock network interfaces
MOCK_NETWORK_INTERFACES = {
    "eth0": {
        "name": "eth0",
        "ipv4": "10.1.1.103",
        "ipv6": "fe80::1",
        "mac": "00:11:22:33:44:55",
        "speed": 1000,
        "is_up": True,
        "bytes_sent": 1234567890,
        "bytes_recv": 9876543210,
    },
    "lo": {
        "name": "lo",
        "ipv4": "127.0.0.1",
        "ipv6": "::1",
        "mac": "00:00:00:00:00:00",
        "speed": 0,
        "is_up": True,
        "bytes_sent": 12345,
        "bytes_recv": 12345,
    }
}

# Mock system info
MOCK_SYSTEM_INFO = {
    "hostname": "unos-test",
    "os": "Debian GNU/Linux 12 (bookworm)",
    "kernel": "6.1.0-test",
    "architecture": "aarch64",
    "cpu": {
        "model": "ARM Cortex-A76 (Test)",
        "cores": 8,
        "threads": 8,
    },
    "memory": {
        "total": 8589934592,  # 8GB
        "available": 6442450944,  # 6GB
    },
    "uptime": 259200,  # 3 days
}

# Mock SMB shares
MOCK_SMB_SHARES = [
    {
        "name": "media",
        "path": "/mnt/pools/data/media",
        "comment": "Media files",
        "browseable": True,
        "readonly": False,
        "guest_ok": False,
    },
    {
        "name": "backups",
        "path": "/mnt/pools/backup/backups",
        "comment": "Backup storage",
        "browseable": True,
        "readonly": False,
        "guest_ok": False,
    }
]


def get_mock_subprocess_output(cmd: list[str]) -> tuple[int, str, str]:
    """
    Return mock output for subprocess commands.

    Returns: (returncode, stdout, stderr)
    """
    cmd_str = " ".join(cmd)

    # lsblk commands
    if "lsblk" in cmd_str and "-J" in cmd_str:
        return (0, json.dumps(MOCK_LSBLK_OUTPUT), "")

    # smartctl commands
    if "smartctl" in cmd_str:
        device = cmd[-1] if cmd[-1].startswith("/dev/") else "/dev/sda"
        return (0, json.dumps(get_mock_smart_data(device)), "")

    # btrfs filesystem show
    if "btrfs" in cmd_str and "filesystem" in cmd_str and "show" in cmd_str:
        if len(cmd) > 3:  # specific pool
            pool_name = cmd[-1]
            if pool_name == "data":
                return (0, MOCK_BTRFS_POOLS.split("\n\n")[0], "")
            elif pool_name == "backup":
                return (0, MOCK_BTRFS_POOLS.split("\n\n")[1], "")
        return (0, MOCK_BTRFS_POOLS, "")

    # btrfs filesystem usage
    if "btrfs" in cmd_str and "filesystem" in cmd_str and "usage" in cmd_str:
        mountpoint = cmd[-1]
        pool_name = mountpoint.split("/")[-1]
        if pool_name in MOCK_BTRFS_USAGE:
            return (0, MOCK_BTRFS_USAGE[pool_name], "")
        return (0, MOCK_BTRFS_USAGE["data"], "")

    # findmnt
    if "findmnt" in cmd_str:
        mountpoint = cmd[1] if len(cmd) > 1 else ""
        pool_name = mountpoint.split("/")[-1]
        if pool_name in ["data", "backup"]:
            return (0, mountpoint, "")
        return (1, "", "")

    # Docker info
    if "docker" in cmd_str and "info" in cmd_str:
        return (0, "2", "")

    # smbcontrol (reload)
    if "smbcontrol" in cmd_str:
        return (0, "", "")

    # Default: success with empty output
    return (0, "", "")

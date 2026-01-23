import subprocess
import json
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from ..auth.utils import verify_token

router = APIRouter(prefix="/network", tags=["network"])


class NetworkInterface(BaseModel):
    name: str
    mac: str
    state: str
    ip_addresses: list[str]
    mtu: int
    type: str


class InterfaceConfig(BaseModel):
    name: str
    dhcp: bool = True
    ip_address: Optional[str] = None
    netmask: Optional[str] = None
    gateway: Optional[str] = None
    dns_servers: list[str] = []


class DNSConfig(BaseModel):
    nameservers: list[str]
    search_domains: list[str]


class HostnameConfig(BaseModel):
    hostname: str


class NetworkStats(BaseModel):
    interface: str
    rx_bytes: int
    tx_bytes: int
    rx_packets: int
    tx_packets: int
    rx_errors: int
    tx_errors: int


@router.get("/interfaces")
async def get_interfaces(_: dict = Depends(verify_token)):
    """Get all network interfaces."""
    try:
        result = subprocess.run(
            ["ip", "-j", "addr", "show"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise HTTPException(500, "Failed to get network interfaces")

        interfaces_data = json.loads(result.stdout)
        interfaces = []

        for iface in interfaces_data:
            name = iface.get("ifname", "")
            # Skip loopback, docker, veth interfaces
            if name in ["lo"] or name.startswith("veth") or name.startswith("br-"):
                continue

            # Get IP addresses
            ip_addresses = []
            for addr_info in iface.get("addr_info", []):
                if addr_info.get("family") == "inet":
                    ip_addresses.append(f"{addr_info.get('local')}/{addr_info.get('prefixlen')}")

            interfaces.append(NetworkInterface(
                name=name,
                mac=iface.get("address", ""),
                state=iface.get("operstate", "unknown"),
                ip_addresses=ip_addresses,
                mtu=iface.get("mtu", 1500),
                type=iface.get("link_type", "unknown")
            ))

        return {"interfaces": interfaces}
    except json.JSONDecodeError:
        raise HTTPException(500, "Failed to parse network data")
    except FileNotFoundError:
        raise HTTPException(500, "ip command not found")
    except Exception as e:
        raise HTTPException(500, f"Failed to get interfaces: {e}")


@router.get("/interfaces/{name}")
async def get_interface(name: str, _: dict = Depends(verify_token)):
    """Get details for a specific interface."""
    try:
        result = subprocess.run(
            ["ip", "-j", "addr", "show", name],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise HTTPException(404, f"Interface {name} not found")

        data = json.loads(result.stdout)
        if not data:
            raise HTTPException(404, f"Interface {name} not found")

        iface = data[0]
        ip_addresses = []
        for addr_info in iface.get("addr_info", []):
            if addr_info.get("family") == "inet":
                ip_addresses.append(f"{addr_info.get('local')}/{addr_info.get('prefixlen')}")

        return NetworkInterface(
            name=iface.get("ifname", name),
            mac=iface.get("address", ""),
            state=iface.get("operstate", "unknown"),
            ip_addresses=ip_addresses,
            mtu=iface.get("mtu", 1500),
            type=iface.get("link_type", "unknown")
        )
    except json.JSONDecodeError:
        raise HTTPException(500, "Failed to parse network data")
    except Exception as e:
        raise HTTPException(500, f"Failed to get interface: {e}")


@router.get("/stats")
async def get_network_stats(_: dict = Depends(verify_token)):
    """Get network statistics for all interfaces."""
    try:
        stats = []
        net_path = Path("/proc/net/dev")
        if not net_path.exists():
            return {"stats": []}

        with open(net_path) as f:
            lines = f.readlines()[2:]  # Skip headers

        for line in lines:
            parts = line.split()
            if len(parts) < 17:
                continue

            name = parts[0].rstrip(":")
            if name in ["lo"] or name.startswith("veth") or name.startswith("br-"):
                continue

            stats.append(NetworkStats(
                interface=name,
                rx_bytes=int(parts[1]),
                tx_bytes=int(parts[9]),
                rx_packets=int(parts[2]),
                tx_packets=int(parts[10]),
                rx_errors=int(parts[3]),
                tx_errors=int(parts[11])
            ))

        return {"stats": stats}
    except Exception as e:
        raise HTTPException(500, f"Failed to get network stats: {e}")


@router.get("/dns")
async def get_dns_config(_: dict = Depends(verify_token)):
    """Get DNS configuration."""
    try:
        nameservers = []
        search_domains = []

        resolv_conf = Path("/etc/resolv.conf")
        if resolv_conf.exists():
            with open(resolv_conf) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("nameserver"):
                        parts = line.split()
                        if len(parts) >= 2:
                            nameservers.append(parts[1])
                    elif line.startswith("search"):
                        parts = line.split()
                        search_domains.extend(parts[1:])

        return DNSConfig(nameservers=nameservers, search_domains=search_domains)
    except Exception as e:
        raise HTTPException(500, f"Failed to get DNS config: {e}")


@router.get("/hostname")
async def get_hostname(_: dict = Depends(verify_token)):
    """Get system hostname."""
    try:
        result = subprocess.run(["hostname"], capture_output=True, text=True)
        hostname = result.stdout.strip() if result.returncode == 0 else "unknown"
        return HostnameConfig(hostname=hostname)
    except Exception as e:
        raise HTTPException(500, f"Failed to get hostname: {e}")


@router.put("/hostname")
async def set_hostname(config: HostnameConfig, _: dict = Depends(verify_token)):
    """Set system hostname."""
    # Validate hostname
    if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$', config.hostname):
        raise HTTPException(400, "Invalid hostname format")

    try:
        # Try using hostnamectl first (systemd)
        result = subprocess.run(
            ["hostnamectl", "set-hostname", config.hostname],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            # Fallback to hostname command
            result = subprocess.run(
                ["hostname", config.hostname],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                raise HTTPException(500, f"Failed to set hostname: {result.stderr}")

        return {"status": "success", "hostname": config.hostname}
    except FileNotFoundError:
        raise HTTPException(500, "hostname command not found")
    except Exception as e:
        raise HTTPException(500, f"Failed to set hostname: {e}")


@router.get("/routes")
async def get_routes(_: dict = Depends(verify_token)):
    """Get routing table."""
    try:
        result = subprocess.run(
            ["ip", "-j", "route", "show"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise HTTPException(500, "Failed to get routes")

        routes = json.loads(result.stdout)
        return {"routes": routes}
    except json.JSONDecodeError:
        raise HTTPException(500, "Failed to parse routing data")
    except Exception as e:
        raise HTTPException(500, f"Failed to get routes: {e}")


@router.get("/gateway")
async def get_default_gateway(_: dict = Depends(verify_token)):
    """Get default gateway."""
    try:
        result = subprocess.run(
            ["ip", "-j", "route", "show", "default"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return {"gateway": None, "interface": None}

        routes = json.loads(result.stdout)
        if routes:
            return {
                "gateway": routes[0].get("gateway"),
                "interface": routes[0].get("dev")
            }
        return {"gateway": None, "interface": None}
    except Exception as e:
        raise HTTPException(500, f"Failed to get gateway: {e}")


@router.post("/interfaces/{name}/up")
async def bring_interface_up(name: str, _: dict = Depends(verify_token)):
    """Bring a network interface up."""
    try:
        result = subprocess.run(
            ["ip", "link", "set", name, "up"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise HTTPException(500, f"Failed to bring interface up: {result.stderr}")
        return {"status": "success", "interface": name, "state": "up"}
    except Exception as e:
        raise HTTPException(500, f"Failed to bring interface up: {e}")


@router.post("/interfaces/{name}/down")
async def bring_interface_down(name: str, _: dict = Depends(verify_token)):
    """Bring a network interface down."""
    try:
        result = subprocess.run(
            ["ip", "link", "set", name, "down"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise HTTPException(500, f"Failed to bring interface down: {result.stderr}")
        return {"status": "success", "interface": name, "state": "down"}
    except Exception as e:
        raise HTTPException(500, f"Failed to bring interface down: {e}")


@router.get("/connections")
async def get_connections(_: dict = Depends(verify_token)):
    """Get active network connections (simplified)."""
    try:
        result = subprocess.run(
            ["ss", "-tunap"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return {"connections": []}

        connections = []
        lines = result.stdout.strip().split("\n")[1:]  # Skip header
        for line in lines:
            parts = line.split()
            if len(parts) >= 5:
                connections.append({
                    "protocol": parts[0],
                    "state": parts[1],
                    "local": parts[4],
                    "remote": parts[5] if len(parts) > 5 else ""
                })

        return {"connections": connections[:50]}  # Limit to 50
    except Exception as e:
        raise HTTPException(500, f"Failed to get connections: {e}")

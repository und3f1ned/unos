import subprocess
import json
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from ..auth.utils import verify_token
from ..security.validators import validate_interface_name, validate_hostname, sanitize_error_message

router = APIRouter(prefix="/network", tags=["network"])


def run_host_cmd(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """Run a command in the host's network and mount namespace using nsenter."""
    full_cmd = ["nsenter", "--target", "1", "--net", "--mount"] + cmd
    return subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)


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
    """Get all network interfaces from the host."""
    try:
        result = run_host_cmd(["ip", "-j", "addr", "show"])
        if result.returncode != 0:
            raise HTTPException(500, "Failed to get network interfaces")

        interfaces_data = json.loads(result.stdout)
        interfaces = []

        for iface in interfaces_data:
            name = iface.get("ifname", "")
            # Skip loopback, docker, veth, and bridge interfaces
            if name in ["lo", "docker0", "dummy0"] or name.startswith("veth") or name.startswith("br-"):
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
    """Get details for a specific interface from the host."""
    # Validate interface name to prevent command injection
    validated_name = validate_interface_name(name)

    try:
        result = run_host_cmd(["ip", "-j", "addr", "show", validated_name])
        if result.returncode != 0:
            raise HTTPException(404, f"Interface {validated_name} not found")

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
    """Get network statistics for all host interfaces."""
    try:
        # Read from host's /proc/net/dev via nsenter
        result = run_host_cmd(["cat", "/proc/net/dev"])
        if result.returncode != 0:
            return {"stats": []}

        stats = []
        lines = result.stdout.strip().split("\n")[2:]  # Skip headers

        for line in lines:
            parts = line.split()
            if len(parts) < 17:
                continue

            name = parts[0].rstrip(":")
            # Skip loopback, docker, veth, and bridge interfaces
            if name in ["lo", "docker0", "dummy0"] or name.startswith("veth") or name.startswith("br-"):
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
    """Get DNS configuration from the host."""
    try:
        nameservers = []
        search_domains = []

        # Try resolvectl first (systemd-resolved)
        result = run_host_cmd(["resolvectl", "status"])
        if result.returncode == 0:
            # Parse resolvectl output for DNS servers from all sections
            for line in result.stdout.split("\n"):
                line = line.strip()
                if "DNS Servers:" in line:
                    # Extract DNS servers (format: "DNS Servers: 10.1.1.1 fe80::...")
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        servers = parts[1].strip().split()
                        # Filter out IPv6 link-local addresses for cleaner display
                        for s in servers:
                            if not s.startswith("fe80::") and s not in nameservers:
                                nameservers.append(s)
                elif "DNS Domain:" in line or "Search Domains:" in line:
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        domains = parts[1].strip().split()
                        for d in domains:
                            if d != "~." and d not in search_domains:
                                search_domains.append(d)

        # Fallback to resolv.conf if resolvectl didn't find anything
        if not nameservers:
            result = run_host_cmd(["cat", "/etc/resolv.conf"])
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    line = line.strip()
                    if line.startswith("nameserver"):
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] not in ("127.0.0.53", "127.0.0.11"):
                            nameservers.append(parts[1])
                    elif line.startswith("search"):
                        parts = line.split()
                        search_domains.extend(parts[1:])

        return DNSConfig(nameservers=nameservers, search_domains=search_domains)
    except Exception as e:
        raise HTTPException(500, f"Failed to get DNS config: {e}")


@router.get("/hostname")
async def get_hostname(_: dict = Depends(verify_token)):
    """Get system hostname from the host."""
    try:
        # Read from mounted /host/hostname first
        try:
            with open("/host/hostname") as f:
                hostname = f.read().strip()
                return HostnameConfig(hostname=hostname)
        except:
            pass
        
        # Fallback to nsenter
        result = run_host_cmd(["hostname"])
        hostname = result.stdout.strip() if result.returncode == 0 else "unknown"
        return HostnameConfig(hostname=hostname)
    except Exception as e:
        raise HTTPException(500, f"Failed to get hostname: {e}")


@router.put("/hostname")
async def set_hostname(config: HostnameConfig, _: dict = Depends(verify_token)):
    """Set system hostname on the host."""
    # Validate hostname
    if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$', config.hostname):
        raise HTTPException(400, "Invalid hostname format")

    try:
        # Try using hostnamectl on the host
        result = run_host_cmd(["hostnamectl", "set-hostname", config.hostname])
        if result.returncode != 0:
            # Fallback to hostname command
            result = run_host_cmd(["hostname", config.hostname])
            if result.returncode != 0:
                raise HTTPException(500, f"Failed to set hostname: {result.stderr}")

        return {"status": "success", "hostname": config.hostname}
    except FileNotFoundError:
        raise HTTPException(500, "hostname command not found")
    except Exception as e:
        raise HTTPException(500, f"Failed to set hostname: {e}")


@router.get("/routes")
async def get_routes(_: dict = Depends(verify_token)):
    """Get routing table from the host."""
    try:
        result = run_host_cmd(["ip", "-j", "route", "show"])
        if result.returncode != 0:
            raise HTTPException(500, "Failed to get routes")

        routes = json.loads(result.stdout)
        # Filter out docker-related routes
        filtered_routes = [
            r for r in routes 
            if not r.get("dev", "").startswith("docker") 
            and not r.get("dev", "").startswith("br-")
            and not r.get("dev", "").startswith("veth")
        ]
        return {"routes": filtered_routes}
    except json.JSONDecodeError:
        raise HTTPException(500, "Failed to parse routing data")
    except Exception as e:
        raise HTTPException(500, f"Failed to get routes: {e}")


@router.get("/gateway")
async def get_default_gateway(_: dict = Depends(verify_token)):
    """Get default gateway from the host."""
    try:
        result = run_host_cmd(["ip", "-j", "route", "show", "default"])
        if result.returncode != 0:
            return {"gateway": None, "interface": None}

        routes = json.loads(result.stdout)
        if routes:
            # Find the real gateway (not docker)
            for route in routes:
                dev = route.get("dev", "")
                if not dev.startswith("docker") and not dev.startswith("br-"):
                    return {
                        "gateway": route.get("gateway"),
                        "interface": route.get("dev")
                    }
            # Fallback to first route
            return {
                "gateway": routes[0].get("gateway"),
                "interface": routes[0].get("dev")
            }
        return {"gateway": None, "interface": None}
    except Exception as e:
        raise HTTPException(500, f"Failed to get gateway: {e}")


@router.post("/interfaces/{name}/up")
async def bring_interface_up(name: str, _: dict = Depends(verify_token)):
    """Bring a network interface up on the host."""
    # Validate interface name to prevent command injection
    validated_name = validate_interface_name(name)

    try:
        result = run_host_cmd(["ip", "link", "set", validated_name, "up"])
        if result.returncode != 0:
            raise HTTPException(500, "Failed to bring interface up")
        return {"status": "success", "interface": validated_name, "state": "up"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, sanitize_error_message(e))


@router.post("/interfaces/{name}/down")
async def bring_interface_down(name: str, _: dict = Depends(verify_token)):
    """Bring a network interface down on the host."""
    # Validate interface name to prevent command injection
    validated_name = validate_interface_name(name)

    try:
        result = run_host_cmd(["ip", "link", "set", validated_name, "down"])
        if result.returncode != 0:
            raise HTTPException(500, "Failed to bring interface down")
        return {"status": "success", "interface": validated_name, "state": "down"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, sanitize_error_message(e))


@router.get("/connections")
async def get_connections(_: dict = Depends(verify_token)):
    """Get active network connections from the host."""
    try:
        result = run_host_cmd(["ss", "-tunap"])
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

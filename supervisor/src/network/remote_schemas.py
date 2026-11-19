"""The request and response bodies of remote access and the app names (ADR-0420)."""

from __future__ import annotations


from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..times import UtcDatetime
from . import app_names, wireguard


class RemoteAccessDefaults(BaseModel):
    """What the card offers when remote access was never set up."""

    port: int = Field(description="The UDP port that WireGuard listens on by default", examples=[51820])
    network: str = Field(description="The tunnel network by default", examples=["10.99.0.0/24"])


class RemotePeer(BaseModel):
    """A device that may connect: its public key and its address in the tunnel, never a private key."""

    id: str = Field(description="The peer's id, made from its public key", examples=["wg-3f2a9c01b7d4"])
    name: str = Field(description="The name an administrator gave the peer", examples=["phone"])
    public_key: str = Field(description="The peer's WireGuard public key, base64")
    address: str = Field(description="The peer's IPv4 address in the tunnel", examples=["10.99.0.2"])
    latest_handshake: UtcDatetime | None = Field(
        None, description="The peer's last handshake while the tunnel is up, in UTC; none before its first")
    received: int | None = Field(None, description="Bytes received from the peer since the tunnel came up")
    sent: int | None = Field(None, description="Bytes sent to the peer since the tunnel came up")


class RemoteAccessStatus(BaseModel):
    """Remote access as the host has it: wireguard-tools, the unit wg-quick@unos-wg and its file."""

    model_config = ConfigDict(json_schema_extra={"examples": [{'installed': True, 'enabled': True, 'active': True, 'configured': True, 'port': 51820, 'network': '10.99.0.0/24', 'address': '10.99.0.1', 'endpoint': 'nas.example.org', 'public_key': 'ZXhhbXBsZS1wdWJsaWMta2V5LW9mLXRoZS1zZXJ2ZXI=', 'peers': [{'id': 'wg-3f2a9c01b7d4', 'name': 'phone', 'public_key': 'ZXhhbXBsZS1wdWJsaWMta2V5LW9mLWEtcGVlci0wMDE=', 'address': '10.99.0.2', 'latest_handshake': '2026-11-19T08:00:00Z', 'received': 123456, 'sent': 654321}], 'firewall_rule': None, 'defaults': {'port': 51820, 'network': '10.99.0.0/24'}}]})


    installed: bool = Field(description="Whether the host has wireguard-tools (wg and wg-quick)")
    enabled: bool = Field(description="Whether the tunnel comes up at boot (the unit is enabled)")
    active: bool = Field(description="Whether the tunnel is up now")
    configured: bool = Field(description="Whether remote access was set up: its configuration file exists")
    port: int | None = Field(None, description="The UDP port that WireGuard listens on")
    network: str | None = Field(None, description="The tunnel network")
    address: str | None = Field(None, description="The NAS's own address in the tunnel")
    endpoint: str | None = Field(None, description="The DNS name or IP address that peers reach the NAS at")
    public_key: str | None = Field(None, description="The server's public key; its private key never leaves the host")
    peers: list[RemotePeer] = Field(description="The devices that may connect")
    firewall_rule: bool | None = Field(
        None, description="Whether the host's ufw lets the tunnel's port in (UNOS: remote access); none without ufw")
    defaults: RemoteAccessDefaults = Field(description="The port and tunnel network that the card offers first")


class RemoteAccessEnable(BaseModel):
    """Turn remote access on with these settings."""

    model_config = ConfigDict(json_schema_extra={"examples": [{'port': 51820, 'network': '10.99.0.0/24', 'endpoint': 'nas.example.org'}]})


    port: int = Field(ge=wireguard.PORT_MIN, le=wireguard.PORT_MAX,
                      description="The UDP port, 1024 to 65535, that the router forwards to the NAS",
                      examples=[51820])
    network: str = Field(description="The tunnel network: IPv4, /16 to /28, inside 10.0.0.0/8, 172.16.0.0/12 "
                                     "or 192.168.0.0/16, apart from the host's networks", examples=["10.99.0.0/24"])
    endpoint: str = Field(description="The router's public address or a dynamic DNS name that peers connect to",
                          examples=["nas.example.org"])

    @field_validator("network")
    @classmethod
    def _network(cls, value: str) -> str:
        return str(wireguard.check_network(value))

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        return wireguard.check_endpoint(value)


class RemoteAccessEndpoint(BaseModel):
    """The address that new peers' configurations name."""

    model_config = ConfigDict(json_schema_extra={"examples": [{'endpoint': 'nas.example.org'}]})


    endpoint: str = Field(description="The router's public address or a dynamic DNS name that peers connect to",
                          examples=["nas.example.org"])

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        return wireguard.check_endpoint(value)


class RemoteAccessChange(BaseModel):
    """Whether a request changed remote access."""

    model_config = ConfigDict(json_schema_extra={"examples": [{'changed': True}]})

    changed: bool = Field(description="Whether the request changed anything")


class RemotePeerCreate(BaseModel):
    """A new peer: its name, and the public key of a device that made its own key pair."""

    model_config = ConfigDict(json_schema_extra={"examples": [{'name': 'phone'}]})


    name: str = Field(description="1 to 32 printable characters", examples=["phone"])
    public_key: str | None = Field(
        None, description="The device's WireGuard public key; without it UNOS makes the peer's key pair and "
                          "answers its private key once, in the configuration")

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return wireguard.check_name(value)

    @field_validator("public_key")
    @classmethod
    def _public_key(cls, value: str | None) -> str | None:
        return None if value is None or not value.strip() else wireguard.check_public_key(value)


class RemotePeerCreated(BaseModel):
    """The new peer and its configuration, answered once and kept nowhere."""

    model_config = ConfigDict(json_schema_extra={"examples": [{'peer': {'id': 'wg-3f2a9c01b7d4', 'name': 'phone', 'public_key': 'ZXhhbXBsZS1wdWJsaWMta2V5LW9mLWEtcGVlci0wMDE=', 'address': '10.99.0.2', 'latest_handshake': '2026-11-19T08:00:00Z', 'received': 123456, 'sent': 654321}, 'config': '[Interface]\nPrivateKey = example-private-key-shown-once\nAddress = 10.99.0.2/32\n\n[Peer]\nPublicKey = ZXhhbXBsZS1wdWJsaWMta2V5LW9mLXRoZS1zZXJ2ZXI=\nAllowedIPs = 10.99.0.1/32\nEndpoint = nas.example.org:51820\n'}]})


    peer: RemotePeer = Field(description="The new peer")
    config: str = Field(description="The WireGuard configuration that the device imports, with its private key "
                                    "unless the device gave its own public key; shown once")


class AppName(BaseModel):
    """A local name of an app: <label>-<host name>.local."""

    label: str = Field(description="The name's first part", examples=["jellyfin"])
    name: str = Field(description="The whole name, which avahi-daemon announces", examples=["jellyfin-nas.local"])
    app: str = Field(description="The installed app that the name reaches", examples=["jellyfin"])
    port: int = Field(description="The app's port on the host that Caddy forwards the name to", examples=[8096])
    installed: bool = Field(description="Whether the app is installed and publishes that port now")


class AppNameApp(BaseModel):
    """A published TCP port of an installed app, which a name can reach."""

    name: str = Field(description="The app's name", examples=["jellyfin"])
    port: int = Field(description="The published TCP port on the host", examples=[8096])


class AppNamesStatus(BaseModel):
    """The app names, what announces them and the apps that can get one."""

    model_config = ConfigDict(json_schema_extra={"examples": [{'volume': True, 'announcer': True, 'host_name': 'nas', 'address': '192.0.2.10', 'announced': '192.0.2.10', 'names': [{'label': 'jellyfin', 'name': 'jellyfin-nas.local', 'app': 'jellyfin', 'port': 8096, 'installed': True}], 'apps': [{'name': 'jellyfin', 'port': 8096}]}]})


    volume: bool = Field(description="Whether the volume app-names is mounted in the supervisor")
    announcer: bool = Field(description="Whether the host has avahi-publish (avahi-utils), which announces the names")
    host_name: str = Field(description="The host's name, the names' last part before .local", examples=["nas"])
    address: str | None = Field(None, description="The host's IPv4 address on its default route now")
    announced: str | None = Field(None, description="The address that avahi announces the names with now")
    names: list[AppName] = Field(description="The app names")
    apps: list[AppNameApp] = Field(description="The published TCP ports of the installed apps, each app's web port "
                                               "first")


class AppNameCreate(BaseModel):
    """A new name for an installed app."""

    model_config = ConfigDict(json_schema_extra={"examples": [{'label': 'jellyfin', 'app': 'jellyfin', 'port': 8096}]})


    label: str = Field(description="1 to 32 characters a-z, 0-9 and -", examples=["jellyfin"])
    app: str = Field(description="The installed app's name", examples=["jellyfin"])
    port: int | None = Field(None, ge=1, le=65535,
                                description="One of the app's published TCP ports; its web port when left out",
                                examples=[8096])

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        return app_names.check_label(value)

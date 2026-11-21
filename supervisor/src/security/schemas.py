import ipaddress
import re
import unicodedata
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from enum import Enum

from ..times import UtcDatetime
from .validators import NAME_MAX

# One DNS label: letters, digits and hyphens, 1-63 characters, no hyphen at
# either end.
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
MAX_HOSTNAME_LENGTH = 253

# A custom certificate is stored as <name>.crt, <name>.key and <name>.chain.pem
# (security.router._certificate_files), and each file name must fit in
# NAME_MAX bytes. The names are ASCII, so characters are bytes.
MAX_CERTIFICATE_NAME_LENGTH = NAME_MAX - len(".chain.pem")

# X.509 limits a certificate's subject common name (CN) to 64 characters
# (RFC 5280, ub-common-name), and openssl refuses a longer one.
MAX_COMMON_NAME_LENGTH = 64


def is_dns_hostname(value: str) -> bool:
    """Whether ``value`` is a DNS host name: dot-separated labels, at most 253 characters."""
    return 0 < len(value) <= MAX_HOSTNAME_LENGTH and all(
        _DNS_LABEL.fullmatch(label) for label in value.split(".")
    )


def parse_ip_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """``value`` as an IP address, or None. IPv6 zone IDs ("fe80::1%eth0") are refused."""
    if "%" in value:
        return None
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def validate_certificate_domain(
    value: str, *, max_length: int = MAX_CERTIFICATE_NAME_LENGTH
) -> str:
    """Accept a DNS host name of at most ``max_length`` characters or an IP
    address (returned in canonical form).

    The name becomes the file names of the key and the certificate and goes
    into openssl's -subj and -addext arguments, so anything else (path
    separators, "..", NUL, spaces, wildcards) is refused. The default
    ``max_length`` is the longest name those files can have.
    """
    address = parse_ip_address(value)
    if address is not None:
        return str(address)
    if not is_dns_hostname(value):
        raise ValueError(
            "must be a host name (letters, digits and hyphens, separated by dots) or an IP address"
        )
    if len(value) > max_length:
        raise ValueError(f"must be at most {max_length} characters long")
    return value


def validate_common_name(value: str) -> str:
    """A certificate domain that can also be a certificate's subject CN."""
    return validate_certificate_domain(value, max_length=MAX_COMMON_NAME_LENGTH)


# The domain of a certificate UNOS generates: it names the files and becomes
# the subject CN.
CertificateDomain = Annotated[str, AfterValidator(validate_common_name)]

# X.509 limits a subject's organization to 64 characters (RFC 5280,
# ub-organization-name) and its country to the two letters of ISO 3166.
# openssl refuses others only after it has made the key.
MAX_ORGANIZATION_LENGTH = 64
_COUNTRY_CODE = re.compile(r"[A-Za-z]{2}")


def validate_organization(value: str) -> str:
    """The organization of a self-signed certificate: at most 64 characters, text that UTF-8 can
    encode (openssl reads the subject as UTF-8), without the characters of Unicode's category C:
    control, format, surrogate, private-use and unassigned ones."""
    if len(value) > MAX_ORGANIZATION_LENGTH:
        raise ValueError(f"the organization must be at most {MAX_ORGANIZATION_LENGTH} characters long")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("the organization must be text that UTF-8 can encode") from None
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError("the organization must hold printable characters only")
    return value


def validate_country(value: str) -> str:
    """The country of a self-signed certificate: two letters, such as DE."""
    if not _COUNTRY_CODE.fullmatch(value):
        raise ValueError("the country must be two letters, such as DE")
    return value


# Login History Schemas
class LoginAttemptResponse(BaseModel):
    """One sign-in attempt."""

    id: int = Field(description="The attempt's number in the login history.")
    username: str = Field(description="The username that the attempt gave, also one without an account.")
    ip_address: str | None = Field(description="The address of the computer that tried; null when not known.")
    user_agent: str | None = Field(
        description="The browser's or program's User-Agent header, at most 500 characters; empty when it sent none."
    )
    success: bool = Field(description="Whether the attempt signed in.")
    failure_reason: str | None = Field(
        description="Why it failed, such as Incorrect username or password or Invalid 2FA code; null when it succeeded."
    )
    timestamp: UtcDatetime | None = Field(description="When it happened, in UTC, to the second.")

    model_config = ConfigDict(from_attributes=True)


class LoginHistoryResponse(BaseModel):
    """A page of the login history and the counts of the attempts the caller may see."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "attempts": [
            {"id": 42, "username": "alice", "ip_address": "192.0.2.20",
             "user_agent": "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
             "success": True, "failure_reason": None, "timestamp": "2026-11-18T09:15:02Z"},
            {"id": 41, "username": "alice", "ip_address": "192.0.2.20",
             "user_agent": "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
             "success": False, "failure_reason": "Incorrect username or password",
             "timestamp": "2026-11-18T09:14:51Z"},
        ],
        "total": 2,
        "failed_count": 1,
        "success_count": 1,
    }]})

    attempts: list[LoginAttemptResponse] = Field(description="The attempts of the page, newest first.")
    total: int = Field(
        description="How many attempts the caller may see, with the username filter but without the success "
                    "filter and the page."
    )
    failed_count: int = Field(description="How many of those failed.")
    success_count: int = Field(description="How many of those signed in.")


# Firewall Schemas: the host's ufw (ADR-0416)
class FirewallAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REJECT = "reject"


class FirewallProtocol(str, Enum):
    TCP = "tcp"
    UDP = "udp"
    ANY = "any"


class FirewallRule(BaseModel):
    """A rule of the host's ufw, as ufw records it (src/security/firewall.py)."""
    id: str = Field(description="The rule's id, made from its record: it stays while the rule does.")
    number: int = Field(description="ufw's number of the rule, as ufw status numbered shows it.")
    family: Literal["ipv4", "ipv6"] = Field(description="The address family that the rule filters.")
    action: str = Field(description="allow, deny, reject or limit.")
    direction: str = Field(description="in or out.")
    route: bool = Field(default=False, description="Whether it is a rule of forwarded traffic (ufw route).")
    log: bool = Field(default=False, description="Whether ufw logs what it matches.")
    protocol: str = Field(description="tcp, udp, any, or another protocol that ufw takes, such as esp or gre.")
    port: str | None = Field(
        default=None, description="The destination ports, such as 22 or 6000:6007; null for every port."
    )
    destination: str | None = Field(
        default=None, description="The destination address or network; null for any address of the host."
    )
    source_port: str | None = Field(default=None, description="The source ports; null for any.")
    source: str | None = Field(default=None, description="The source address or network; null for anywhere.")
    interface: str | None = Field(
        default=None, description="The interface that the rule is limited to; null for every one."
    )
    app: str | None = Field(default=None, description="The ufw application profile that the rule names; null for none.")
    comment: str | None = Field(default=None, description="The rule's comment; null for none.")
    unos: bool = Field(
        default=False,
        description="Whether it is one of UNOS's rules, whose comment starts with UNOS: and which UNOS keeps."
    )


class FirewallStatus(BaseModel):
    """The host's ufw and the change that waits to be kept."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "installed": True,
        "enabled": True,
        "default_incoming": "deny",
        "default_outgoing": "allow",
        "ipv6": True,
        "rules": [
            {"id": "fw-3c9a1e07b2d4", "number": 1, "family": "ipv4", "action": "allow", "direction": "in",
             "route": False, "log": False, "protocol": "tcp", "port": "8080", "destination": None,
             "source_port": None, "source": "192.0.2.0/24", "interface": None, "app": None,
             "comment": "Web server", "unos": False},
            {"id": "fw-81f0d5a6c3e2", "number": 2, "family": "ipv6", "action": "allow", "direction": "in",
             "route": False, "log": False, "protocol": "udp", "port": "3702,5355", "destination": None,
             "source_port": None, "source": "fe80::/10", "interface": None, "app": None,
             "comment": "UNOS: network discovery", "unos": True},
        ],
        "revert_in": 87,
        "reverting": False,
        "revert_failed": False,
        "revert_seconds": 120,
        "client_local": True,
    }]})

    installed: bool = Field(description="Whether the host has ufw.")
    enabled: bool = Field(description="Whether ufw is on; false when it is not installed.")
    default_incoming: str = Field(description="The default policy of incoming traffic: allow, deny or reject.")
    default_outgoing: str = Field(description="The default policy of outgoing traffic: allow, deny or reject.")
    ipv6: bool = Field(description="Whether ufw filters IPv6 too (IPV6=yes in /etc/default/ufw).")
    rules: list[FirewallRule] = Field(description="The host's rules in the order of ufw's numbers; none without ufw.")
    revert_in: int | None = Field(
        default=None,
        description="Seconds until the automatic revert undoes the change that waits to be kept; null when "
                          "none waits."
    )
    reverting: bool = Field(default=False, description="Whether the automatic revert undoes a change now.")
    revert_failed: bool = Field(
        default=False, description="Whether the last automatic revert could not put everything back."
    )
    revert_seconds: int = Field(description="How long a change waits to be kept, in seconds.")
    client_local: bool | None = Field(
        default=None,
        description="Whether the asking computer is in the local networks that UNOS's rules let in; null when "
                          "its address is not known."
    )


class FirewallChange(BaseModel):
    """What a change of the firewall did."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"changed": True, "revert_in": 120}]})

    changed: bool = Field(description="Whether the host's ufw changed.")
    revert_in: int | None = Field(
        default=None,
        description="Seconds until the automatic revert undoes the change unless it is kept; null when the "
                          "change armed none: a rule change while ufw is off, a turning off, a reset or no change."
    )


# ufw takes at most 15 ports in a rule, a range counting as two; a comment of UNOS's rules
# starts with this, and no rule made in UNOS may.
_MAX_RULE_PORTS = 15
_PORT_ITEM = re.compile(r"[0-9]{1,5}(?::[0-9]{1,5})?")
MAX_FIREWALL_COMMENT = 64


def validate_firewall_port(value: str) -> str:
    """One port, a range a:b or a list of them, as ufw takes them."""
    items = value.split(",")
    count = 0
    for item in items:
        if not _PORT_ITEM.fullmatch(item):
            raise ValueError("Ports are numbers from 1 to 65535, a range such as 6000:6007, or a list such as 80,443")
        bounds = [int(part) for part in item.split(":")]
        if not all(1 <= port <= 65535 for port in bounds):
            raise ValueError("Ports are numbers from 1 to 65535")
        if len(bounds) == 2 and bounds[0] >= bounds[1]:
            raise ValueError("A range of ports goes from the lower to the higher one")
        count += len(bounds)
    if count > _MAX_RULE_PORTS:
        raise ValueError(f"A rule takes at most {_MAX_RULE_PORTS} ports, a range counting as two")
    return value


def validate_firewall_source(value: str | None) -> str | None:
    """An IPv4 or IPv6 address or network, as Python's ipaddress writes it; None or "" for anywhere."""
    if value is None or value == "":
        return None
    try:
        if "/" in value:
            return str(ipaddress.ip_network(value, strict=True))
        return str(ipaddress.ip_address(value))
    except ValueError:
        raise ValueError("The source is an IPv4 or IPv6 address or network, such as 192.168.1.0/24") from None


def validate_firewall_comment(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if len(value) > MAX_FIREWALL_COMMENT or not value.isprintable():
        raise ValueError(f"A comment has at most {MAX_FIREWALL_COMMENT} printable characters")
    if value.startswith("-") or value.upper().startswith("UNOS:"):
        raise ValueError("A comment starts with neither '-' nor 'UNOS:'")
    return value


class FirewallRuleCreate(BaseModel):
    """An incoming rule made in UNOS."""
    # ADR-0416.
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "action": "allow", "protocol": "tcp", "port": "8080", "source": "192.0.2.0/24", "comment": "Web server",
    }]})
    action: FirewallAction = Field(description="What the rule does with the traffic it matches.")
    protocol: FirewallProtocol = Field(
        default=FirewallProtocol.TCP,
        description="The protocol it matches; any takes one port only, not a range or a list."
    )
    port: Annotated[str, AfterValidator(validate_firewall_port)] = Field(
        description="The ports from 1 to 65535: one, a range such as 6000:6007 or a list such as 80,443, at most 15 "
                    "ports with a range counting as two."
    )
    source: Annotated[str | None, AfterValidator(validate_firewall_source)] = Field(
        default=None,
        description="The IPv4 or IPv6 address or network that the rule matches; null or empty for anywhere."
    )
    comment: Annotated[str | None, AfterValidator(validate_firewall_comment)] = Field(
        default=None,
        description="At most 64 printable characters, starting with neither - nor UNOS:; null or empty for none."
    )

    @model_validator(mode="after")
    def _one_port_for_any_protocol(self):
        if self.protocol == FirewallProtocol.ANY and ("," in self.port or ":" in self.port):
            raise ValueError("A range or a list of ports needs TCP or UDP")
        return self


# SSL/TLS Certificate Schemas
class CertificateType(str, Enum):
    SELF_SIGNED = "self_signed"
    CUSTOM = "custom"


class ServedCertificate(str, Enum):
    """What browsers get from https://<host>/, as a TLS handshake with the web UI's Caddy shows it: the certificate of its local CA, a stored certificate, or unknown when
    Caddy did not answer the handshake."""
    # ADR-0404, ADR-0025.
    CADDY_LOCAL_CA = "caddy_local_ca"
    STORED_CERTIFICATE = "stored_certificate"
    UNKNOWN = "unknown"


class Certificate(BaseModel):
    """A certificate stored by an upload or a self-signed generation."""
    id: str = Field(description="The stored certificate's id: custom_ and its name, the subject CN.")
    type: CertificateType = Field(description="self_signed when its issuer is its subject, else custom.")
    domain: str = Field(description="Its subject CN, or its name when openssl prints none.")
    issuer: str | None = Field(
        default=None, description="The issuer's organization, else its CN; Unknown when it has neither."
    )
    valid_from: UtcDatetime | None = Field(
        default=None, description="When it becomes valid, in UTC; null when not readable."
    )
    valid_until: UtcDatetime | None = Field(
        default=None, description="When it expires, in UTC; null when not readable."
    )
    is_valid: bool = Field(default=True, description="Whether it has not expired.")
    # Valid, and expires within EXPIRY_WARNING_DAYS of src/security/serving.py.
    expires_soon: bool = Field(default=False, description="Whether it is valid and expires within 30 days.")
    # Browsers get it: the web UI's Caddy serves it (ADR-0404).
    served: bool = Field(default=False, description="Whether browsers get it: the web UI's Caddy serves it.")
    # An administrator chose it: the served file holds it, whether Caddy loaded it or not.
    chosen: bool = Field(
        default=False,
        description="Whether an administrator chose it as the served certificate, whether Caddy loaded it or not."
    )
    # SHA-256, as a TLS handshake shows the certificate and openssl prints it.
    fingerprint: str | None = Field(
        default=None,
        description="Its SHA-256 fingerprint as openssl prints it: pairs of hexadecimal digits with colons."
    )


class CertificateStatus(BaseModel):
    """What browsers get from https://<host>/, and the stored certificate an administrator chose
    to serve."""
    # ADR-0404.
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "served_by": "stored_certificate",
        "served_id": "custom_nas.example.com",
        "chosen_id": "custom_nas.example.com",
        "fingerprint": "5B:1E:0C:7D:93:A2:4F:68:E1:3C:B9:57:0A:D4:82:F6:2E:91:C8:73:4D:A0:6B:15:F9:38:C2:E7:5A:0D:B4:61",
    }]})
    served_by: ServedCertificate = Field(
        description="What browsers get: a stored certificate, the certificate of Caddy's local CA, or unknown when "
                    "Caddy did not answer a TLS handshake."
    )
    served_id: str | None = Field(
        default=None, description="The id of the stored certificate that browsers get; null when they get none of them."
    )
    chosen_id: str | None = Field(
        default=None,
        description="The id of the stored certificate an administrator chose; null when none is chosen and "
                          "Caddy's local CA is."
    )
    fingerprint: str | None = Field(
        default=None, description="The SHA-256 fingerprint of the certificate browsers get; null when it is unknown."
    )


class CertificateListResponse(BaseModel):
    """The stored certificates and what is served."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "certificates": [{
            "id": "custom_nas.example.com", "type": "self_signed", "domain": "nas.example.com",
            "issuer": "Example", "valid_from": "2026-11-18T09:00:00Z", "valid_until": "2027-11-18T09:00:00Z",
            "is_valid": True, "expires_soon": False, "served": True, "chosen": True,
            "fingerprint": "5B:1E:0C:7D:93:A2:4F:68:E1:3C:B9:57:0A:D4:82:F6:2E:91:C8:73:4D:A0:6B:15:F9:38:C2:E7:5A:0D:B4:61",
        }],
        "status": {
            "served_by": "stored_certificate", "served_id": "custom_nas.example.com",
            "chosen_id": "custom_nas.example.com",
            "fingerprint": "5B:1E:0C:7D:93:A2:4F:68:E1:3C:B9:57:0A:D4:82:F6:2E:91:C8:73:4D:A0:6B:15:F9:38:C2:E7:5A:0D:B4:61",
        },
    }]})

    certificates: list[Certificate] = Field(description="The stored certificates; none when none is stored.")
    status: CertificateStatus = Field(description="What browsers get and which stored certificate was chosen.")


class ServeCertificateResponse(BaseModel):
    """The answer of POST /security/certificates/{cert_id}/serve."""
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "changed": True,
        "expires_soon": False,
        "status": {
            "served_by": "stored_certificate", "served_id": "custom_nas.example.com",
            "chosen_id": "custom_nas.example.com",
            "fingerprint": "5B:1E:0C:7D:93:A2:4F:68:E1:3C:B9:57:0A:D4:82:F6:2E:91:C8:73:4D:A0:6B:15:F9:38:C2:E7:5A:0D:B4:61",
        },
    }]})
    changed: bool = Field(description="Whether the served certificate or what Caddy serves changed.")
    expires_soon: bool = Field(
        default=False,
        description="Whether the stored certificate now served expires within 30 days; false for Caddy's local CA."
    )
    status: CertificateStatus = Field(description="What browsers get now and which stored certificate was chosen.")


class CertificateUpload(BaseModel):
    """A certificate to store, with its private key and its chain."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "certificate": "-----BEGIN CERTIFICATE-----\n(example certificate)\n-----END CERTIFICATE-----\n",
        "private_key": "(the example certificate's private key in PEM)",
        "chain": None,
    }]})

    certificate: str = Field(
        description="The certificate in PEM; its subject CN, a host name, a wildcard host name or an IP address, "
                    "names it, and a stored certificate of that name is replaced."
    )
    private_key: str = Field(
        description="The certificate's private key in PEM; one protected by a passphrase is stored but cannot be served."
    )
    chain: str | None = Field(
        default=None, description="The intermediate certificates in PEM, the certificate's issuer first; null for none."
    )


class SelfSignedCertRequest(BaseModel):
    """A self-signed certificate to generate, with a new 2048-bit RSA key."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "domain": "nas.example.com", "validity_days": 365, "organization": "Example", "country": "DE",
    }]})

    domain: CertificateDomain = Field(
        description="A host name or an IP address of at most 64 characters: the subject CN, the subject alternative "
                    "name and the stored certificate's name; a stored certificate of that name is replaced."
    )
    validity_days: int = Field(default=365, description="How many days the certificate is valid, from now.")
    organization: Annotated[str, AfterValidator(validate_organization)] | None = Field(
        default=None, description="The subject's organization, at most 64 printable characters; null for none."
    )
    country: Annotated[str, AfterValidator(validate_country)] | None = Field(
        default=None, description="The subject's country, two letters such as DE; null for none."
    )


# Every account must have two-factor authentication. The grace periods are
# GRACE_DAYS_CHOICES of src/auth/requirement.py.
GraceDays = Literal[0, 1, 3, 7, 14, 30]


class TwoFactorRequirementStatus(BaseModel):
    """Whether every account must have two-factor authentication."""

    model_config = ConfigDict(json_schema_extra={"examples": [{
        "enabled": True, "grace_days": 7, "enabled_at": "2026-11-18T09:20:00Z", "enabled_by": "admin",
    }]})

    enabled: bool = Field(description="Whether the two-factor requirement is on.")
    grace_days: int | None = Field(
        default=None, description="The grace period in days, from when it was turned on or the account was created, "
                          "whichever is later; null when it is off."
    )
    enabled_at: UtcDatetime | None = Field(  # when it was turned on
        default=None,
        description="When it was turned on, in UTC; null when it is off or the stored time cannot be read."
    )
    enabled_by: str | None = Field(  # the administrator who turned it on
        default=None, description="The administrator who turned it on; null when it is off."
    )


class TwoFactorRequirementEnable(BaseModel):
    """Turn the two-factor requirement on."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"grace_days": 7, "code": "123456"}]})
    grace_days: GraceDays = Field(
        default=7,
        description="The grace period in days: 0, 1, 3, 7, 14 or 30. An account without two-factor authentication "
                       "reaches only its setup after it."
    )
    code: str = Field(  # a current code of the administrator's own authenticator
        max_length=16, description="A current code of the administrator's own authenticator app; it is used up."
    )


class TwoFactorRequirementDisable(BaseModel):
    """Turn the two-factor requirement off."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"code": "123456"}]})
    code: str = Field(
        max_length=16, description="A current code of the administrator's own authenticator app; it is used up."
    )

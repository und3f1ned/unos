import ipaddress
import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict
from datetime import datetime
from enum import Enum

from .validators import NAME_MAX

# One DNS label: letters, digits and hyphens, 1-63 characters, no hyphen at
# either end.
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
MAX_HOSTNAME_LENGTH = 253

# A custom certificate is stored as <name>.crt, <name>.key and <name>.chain.crt
# (security.router._certificate_files), and each file name must fit in
# NAME_MAX bytes. The names are ASCII, so characters are bytes.
MAX_CERTIFICATE_NAME_LENGTH = NAME_MAX - len(".chain.crt")

# X.509 limits a certificate's subject common name (CN) to 64 characters
# (RFC 5280, ub-common-name). openssl refuses a longer one, after it has
# already written the new key.
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


# Login History Schemas
class LoginAttemptResponse(BaseModel):
    id: int
    username: str
    ip_address: str | None
    user_agent: str | None
    success: bool
    failure_reason: str | None
    timestamp: datetime | None

    model_config = ConfigDict(from_attributes=True)


class LoginHistoryResponse(BaseModel):
    attempts: list[LoginAttemptResponse]
    total: int
    failed_count: int
    success_count: int


# Firewall Schemas
class FirewallAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REJECT = "reject"


class FirewallProtocol(str, Enum):
    TCP = "tcp"
    UDP = "udp"
    ANY = "any"


class FirewallDirection(str, Enum):
    IN = "in"
    OUT = "out"


class FirewallRule(BaseModel):
    id: str | None = None
    name: str
    action: FirewallAction
    direction: FirewallDirection = FirewallDirection.IN
    protocol: FirewallProtocol = FirewallProtocol.ANY
    port: str | None = None  # Can be single port, range (80:443), or None for all
    source: str | None = None  # IP or CIDR
    destination: str | None = None  # IP or CIDR
    enabled: bool = True
    comment: str | None = None


class FirewallStatus(BaseModel):
    enabled: bool
    default_incoming: str
    default_outgoing: str
    rules: list[FirewallRule]


class FirewallRuleCreate(BaseModel):
    name: str
    action: FirewallAction
    direction: FirewallDirection = FirewallDirection.IN
    protocol: FirewallProtocol = FirewallProtocol.ANY
    port: str | None = None
    source: str | None = None
    destination: str | None = None
    comment: str | None = None


# SSL/TLS Certificate Schemas
class CertificateType(str, Enum):
    SELF_SIGNED = "self_signed"
    LETS_ENCRYPT = "lets_encrypt"
    CUSTOM = "custom"


class Certificate(BaseModel):
    id: str
    type: CertificateType
    domain: str
    issuer: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    is_valid: bool = True
    auto_renew: bool = False
    fingerprint: str | None = None


class CertificateListResponse(BaseModel):
    certificates: list[Certificate]
    active_certificate: str | None


class CertificateUpload(BaseModel):
    certificate: str  # PEM-encoded certificate
    private_key: str  # PEM-encoded private key
    chain: str | None = None  # PEM-encoded certificate chain


class SelfSignedCertRequest(BaseModel):
    domain: CertificateDomain
    validity_days: int = 365
    organization: str | None = None
    country: str | None = None


class LetsEncryptRequest(BaseModel):
    domain: str
    email: str
    staging: bool = False  # Use staging server for testing


class CertificateStatus(BaseModel):
    has_certificate: bool
    type: CertificateType | None = None
    domain: str | None = None
    valid_until: str | None = None
    is_valid: bool = False
    auto_renew: bool = False

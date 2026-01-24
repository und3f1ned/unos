from pydantic import BaseModel
from datetime import datetime
from enum import Enum


# Login History Schemas
class LoginAttemptResponse(BaseModel):
    id: int
    username: str
    ip_address: str | None
    user_agent: str | None
    success: bool
    failure_reason: str | None
    timestamp: datetime | None

    class Config:
        from_attributes = True


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
    domain: str
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

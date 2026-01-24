import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
import json
import os


class TestLoginAttemptModel:
    """Tests for LoginAttempt model."""

    def test_login_attempt_model_has_required_fields(self):
        """Test that LoginAttempt model has required fields."""
        from src.security.models import LoginAttempt

        assert hasattr(LoginAttempt, "id")
        assert hasattr(LoginAttempt, "username")
        assert hasattr(LoginAttempt, "ip_address")
        assert hasattr(LoginAttempt, "user_agent")
        assert hasattr(LoginAttempt, "success")
        assert hasattr(LoginAttempt, "failure_reason")
        assert hasattr(LoginAttempt, "timestamp")

    def test_login_attempt_tablename(self):
        """Test LoginAttempt model tablename."""
        from src.security.models import LoginAttempt

        assert LoginAttempt.__tablename__ == "login_attempts"


class TestSecuritySchemas:
    """Tests for security schemas."""

    def test_login_attempt_response_schema(self):
        """Test LoginAttemptResponse schema."""
        from src.security.schemas import LoginAttemptResponse

        attempt = LoginAttemptResponse(
            id=1,
            username="testuser",
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
            success=True,
            failure_reason=None,
            timestamp=datetime.now()
        )

        assert attempt.id == 1
        assert attempt.username == "testuser"
        assert attempt.success is True

    def test_login_history_response_schema(self):
        """Test LoginHistoryResponse schema."""
        from src.security.schemas import LoginHistoryResponse, LoginAttemptResponse

        response = LoginHistoryResponse(
            attempts=[],
            total=10,
            failed_count=2,
            success_count=8
        )

        assert response.total == 10
        assert response.failed_count == 2
        assert response.success_count == 8

    def test_firewall_rule_schema(self):
        """Test FirewallRule schema."""
        from src.security.schemas import FirewallRule, FirewallAction, FirewallDirection, FirewallProtocol

        rule = FirewallRule(
            id="rule_001",
            name="SSH Access",
            action=FirewallAction.ALLOW,
            direction=FirewallDirection.IN,
            protocol=FirewallProtocol.TCP,
            port="22",
            enabled=True
        )

        assert rule.name == "SSH Access"
        assert rule.action == FirewallAction.ALLOW
        assert rule.port == "22"

    def test_firewall_status_schema(self):
        """Test FirewallStatus schema."""
        from src.security.schemas import FirewallStatus

        status = FirewallStatus(
            enabled=True,
            default_incoming="deny",
            default_outgoing="allow",
            rules=[]
        )

        assert status.enabled is True
        assert status.default_incoming == "deny"

    def test_certificate_schema(self):
        """Test Certificate schema."""
        from src.security.schemas import Certificate, CertificateType

        cert = Certificate(
            id="cert_001",
            type=CertificateType.SELF_SIGNED,
            domain="nas.local",
            issuer="Self",
            is_valid=True
        )

        assert cert.domain == "nas.local"
        assert cert.type == CertificateType.SELF_SIGNED

    def test_certificate_status_schema(self):
        """Test CertificateStatus schema."""
        from src.security.schemas import CertificateStatus, CertificateType

        status = CertificateStatus(
            has_certificate=True,
            type=CertificateType.LETS_ENCRYPT,
            domain="example.com",
            is_valid=True,
            auto_renew=True
        )

        assert status.has_certificate is True
        assert status.auto_renew is True

    def test_self_signed_cert_request_schema(self):
        """Test SelfSignedCertRequest schema."""
        from src.security.schemas import SelfSignedCertRequest

        request = SelfSignedCertRequest(
            domain="nas.local",
            validity_days=365,
            organization="Home Lab",
            country="US"
        )

        assert request.domain == "nas.local"
        assert request.validity_days == 365


class TestFirewallFunctions:
    """Tests for firewall management functions."""

    def test_load_firewall_rules_empty(self, tmp_path):
        """Test loading firewall rules when file doesn't exist."""
        from src.security.router import load_firewall_rules

        with patch("src.security.router.FIREWALL_RULES_FILE", str(tmp_path / "rules.json")):
            rules = load_firewall_rules()
            assert rules == []

    def test_save_and_load_firewall_rules(self, tmp_path):
        """Test saving and loading firewall rules."""
        from src.security.router import load_firewall_rules, save_firewall_rules
        from src.security.schemas import FirewallRule, FirewallAction, FirewallDirection, FirewallProtocol

        rules_file = tmp_path / "rules.json"

        rule = FirewallRule(
            id="rule_test",
            name="Test Rule",
            action=FirewallAction.ALLOW,
            direction=FirewallDirection.IN,
            protocol=FirewallProtocol.TCP,
            port="8080",
            enabled=True
        )

        with patch("src.security.router.FIREWALL_RULES_FILE", str(rules_file)):
            save_firewall_rules([rule])
            loaded = load_firewall_rules()

            assert len(loaded) == 1
            assert loaded[0].name == "Test Rule"
            assert loaded[0].port == "8080"

    def test_get_ufw_status_active(self):
        """Test parsing UFW status when active."""
        from src.security.router import get_ufw_status

        mock_result = MagicMock()
        mock_result.stdout = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)"""

        with patch("subprocess.run", return_value=mock_result):
            enabled, default_in, default_out = get_ufw_status()

            assert enabled is True
            assert default_in == "deny"
            assert default_out == "allow"

    def test_get_ufw_status_inactive(self):
        """Test parsing UFW status when inactive."""
        from src.security.router import get_ufw_status

        mock_result = MagicMock()
        mock_result.stdout = "Status: inactive"

        with patch("subprocess.run", return_value=mock_result):
            enabled, default_in, default_out = get_ufw_status()

            assert enabled is False

    def test_get_ufw_status_not_installed(self):
        """Test UFW status when not installed."""
        from src.security.router import get_ufw_status

        with patch("subprocess.run", side_effect=FileNotFoundError):
            enabled, default_in, default_out = get_ufw_status()

            assert enabled is False
            assert default_in == "deny"
            assert default_out == "allow"


class TestCertificateFunctions:
    """Tests for certificate management functions."""

    def test_get_certificate_info_valid(self, tmp_path):
        """Test parsing valid certificate info."""
        from src.security.router import get_certificate_info

        # Create a mock openssl output
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = """subject=CN = nas.local
issuer=CN = nas.local
notBefore=Jan  1 00:00:00 2025 GMT
notAfter=Dec 31 23:59:59 2025 GMT
SHA256 Fingerprint=AA:BB:CC:DD:EE:FF"""

        verify_result = MagicMock()
        verify_result.returncode = 0

        with patch("subprocess.run", side_effect=[mock_result, verify_result]):
            info = get_certificate_info("/tmp/test.crt")

            assert info is not None
            assert info["domain"] == "nas.local"
            assert info["is_valid"] is True

    def test_get_certificate_info_invalid(self):
        """Test parsing certificate when openssl fails."""
        from src.security.router import get_certificate_info

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            info = get_certificate_info("/tmp/invalid.crt")

            assert info is None

    def test_get_custom_certificates_empty(self, tmp_path):
        """Test getting custom certificates when directory is empty."""
        from src.security.router import get_custom_certificates

        with patch("src.security.router.CERTS_DIR", str(tmp_path)):
            certs = get_custom_certificates()
            assert certs == []

    def test_get_caddy_certificates_missing_dir(self, tmp_path):
        """Test getting Caddy certificates when directory doesn't exist."""
        from src.security.router import get_caddy_certificates

        with patch("src.security.router.CADDY_CERTS_DIR", str(tmp_path / "nonexistent")):
            certs = get_caddy_certificates()
            assert certs == []


class TestRecordLoginAttempt:
    """Tests for login attempt recording."""

    @pytest.mark.asyncio
    async def test_record_login_attempt_success(self):
        """Test recording a successful login attempt."""
        from src.security.router import record_login_attempt

        mock_db = AsyncMock()
        mock_request = MagicMock()
        mock_request.headers = {"User-Agent": "Test Browser", "X-Forwarded-For": "192.168.1.100"}
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"

        await record_login_attempt(
            db=mock_db,
            username="testuser",
            success=True,
            request=mock_request
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_login_attempt_failed(self):
        """Test recording a failed login attempt."""
        from src.security.router import record_login_attempt

        mock_db = AsyncMock()

        await record_login_attempt(
            db=mock_db,
            username="baduser",
            success=False,
            failure_reason="Incorrect password"
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


class TestFirewallRuleApplication:
    """Tests for applying firewall rules."""

    def test_apply_ufw_rule_allow(self):
        """Test applying an allow rule."""
        from src.security.router import apply_ufw_rule
        from src.security.schemas import FirewallRule, FirewallAction, FirewallDirection, FirewallProtocol

        rule = FirewallRule(
            id="test",
            name="Test",
            action=FirewallAction.ALLOW,
            direction=FirewallDirection.IN,
            protocol=FirewallProtocol.TCP,
            port="80",
            enabled=True
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = "Rule added"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            success, error = apply_ufw_rule(rule)

            assert success is True
            assert error == ""
            mock_run.assert_called_once()

    def test_apply_ufw_rule_failure(self):
        """Test applying a rule that fails."""
        from src.security.router import apply_ufw_rule
        from src.security.schemas import FirewallRule, FirewallAction, FirewallDirection, FirewallProtocol

        rule = FirewallRule(
            id="test",
            name="Test",
            action=FirewallAction.DENY,
            direction=FirewallDirection.OUT,
            protocol=FirewallProtocol.UDP,
            port="53",
            enabled=True
        )

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Permission denied"
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            success, error = apply_ufw_rule(rule)

            assert success is False
            assert "Permission denied" in error

    def test_apply_ufw_rule_delete(self):
        """Test deleting a rule."""
        from src.security.router import apply_ufw_rule
        from src.security.schemas import FirewallRule, FirewallAction, FirewallDirection, FirewallProtocol

        rule = FirewallRule(
            id="test",
            name="Test",
            action=FirewallAction.ALLOW,
            direction=FirewallDirection.IN,
            protocol=FirewallProtocol.TCP,
            port="443",
            enabled=True
        )

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = "Rule deleted"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            success, error = apply_ufw_rule(rule, delete=True)

            assert success is True
            # Verify 'delete' is in the command
            call_args = mock_run.call_args[0][0]
            assert "delete" in call_args


class TestEnums:
    """Tests for security enums."""

    def test_firewall_action_values(self):
        """Test FirewallAction enum values."""
        from src.security.schemas import FirewallAction

        assert FirewallAction.ALLOW.value == "allow"
        assert FirewallAction.DENY.value == "deny"
        assert FirewallAction.REJECT.value == "reject"

    def test_firewall_protocol_values(self):
        """Test FirewallProtocol enum values."""
        from src.security.schemas import FirewallProtocol

        assert FirewallProtocol.TCP.value == "tcp"
        assert FirewallProtocol.UDP.value == "udp"
        assert FirewallProtocol.ANY.value == "any"

    def test_firewall_direction_values(self):
        """Test FirewallDirection enum values."""
        from src.security.schemas import FirewallDirection

        assert FirewallDirection.IN.value == "in"
        assert FirewallDirection.OUT.value == "out"

    def test_certificate_type_values(self):
        """Test CertificateType enum values."""
        from src.security.schemas import CertificateType

        assert CertificateType.SELF_SIGNED.value == "self_signed"
        assert CertificateType.LETS_ENCRYPT.value == "lets_encrypt"
        assert CertificateType.CUSTOM.value == "custom"

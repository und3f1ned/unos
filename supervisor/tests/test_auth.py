import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from jose import jwt, JWTError, ExpiredSignatureError
import os


class TestPasswordHashing:
    """Tests for password hashing functions."""

    def test_hash_password_returns_string(self):
        """Test that hash_password returns a string."""
        from src.auth.utils import hash_password
        
        result = hash_password("test_password")
        
        assert isinstance(result, str)
        assert result != "test_password"

    def test_hash_password_different_for_same_input(self):
        """Test that same password produces different hashes (salted)."""
        from src.auth.utils import hash_password
        
        hash1 = hash_password("test_password")
        hash2 = hash_password("test_password")
        
        # Bcrypt produces different hashes due to random salt
        assert hash1 != hash2

    def test_verify_password_correct(self):
        """Test that correct password is verified."""
        from src.auth.utils import hash_password, verify_password
        
        password = "test_password"
        hashed = hash_password(password)
        
        assert verify_password(password, hashed) is True

    def test_verify_password_incorrect(self):
        """Test that incorrect password fails verification."""
        from src.auth.utils import hash_password, verify_password
        
        hashed = hash_password("correct_password")
        
        assert verify_password("wrong_password", hashed) is False

    def test_verify_password_empty(self):
        """Test that empty password fails verification."""
        from src.auth.utils import hash_password, verify_password
        
        hashed = hash_password("real_password")
        
        assert verify_password("", hashed) is False


class TestJWTTokens:
    """Tests for JWT token creation and verification."""

    @pytest.fixture
    def mock_settings(self):
        """Mock settings for JWT."""
        mock = MagicMock()
        mock.jwt_secret = "test-secret-key"
        mock.jwt_algorithm = "HS256"
        mock.jwt_expire_minutes = 60
        return mock

    def test_create_access_token(self, mock_settings):
        """Test creating an access token."""
        from src.auth.utils import create_access_token
        
        with patch("src.auth.utils.settings", mock_settings):
            token = create_access_token({"sub": "testuser"})
        
        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_access_token_contains_subject(self, mock_settings):
        """Test that token contains the subject."""
        from src.auth.utils import create_access_token
        
        with patch("src.auth.utils.settings", mock_settings):
            token = create_access_token({"sub": "testuser"})
            
            # Decode and verify
            payload = jwt.decode(
                token, 
                mock_settings.jwt_secret, 
                algorithms=[mock_settings.jwt_algorithm]
            )
        
        assert payload["sub"] == "testuser"

    def test_create_access_token_has_expiry(self, mock_settings):
        """Test that token has expiry time."""
        from src.auth.utils import create_access_token
        
        with patch("src.auth.utils.settings", mock_settings):
            token = create_access_token({"sub": "testuser"})
            
            payload = jwt.decode(
                token, 
                mock_settings.jwt_secret, 
                algorithms=[mock_settings.jwt_algorithm]
            )
        
        assert "exp" in payload

    def test_create_access_token_expiry_is_correct(self, mock_settings):
        """Test token expiry time is correct."""
        from src.auth.utils import create_access_token
        
        with patch("src.auth.utils.settings", mock_settings):
            before = datetime.utcnow()
            token = create_access_token({"sub": "testuser"})
            after = datetime.utcnow()
            
            payload = jwt.decode(
                token, 
                mock_settings.jwt_secret, 
                algorithms=[mock_settings.jwt_algorithm]
            )
        
        # Verify expiry is approximately 60 minutes from now (as per mock settings)
        exp_time = datetime.utcfromtimestamp(payload["exp"])
        expected_delta = timedelta(minutes=mock_settings.jwt_expire_minutes)
        
        # Allow some tolerance
        assert (before + expected_delta - timedelta(seconds=5)) <= exp_time
        assert exp_time <= (after + expected_delta + timedelta(seconds=5))


class TestUserModel:
    """Tests for User model."""

    def test_user_model_has_required_fields(self):
        """Test that User model has required fields."""
        from src.auth.models import User
        
        # Check that model has the expected columns
        assert hasattr(User, "id")
        assert hasattr(User, "username")
        assert hasattr(User, "hashed_password")
        assert hasattr(User, "is_admin")
        assert hasattr(User, "created_at")

    def test_user_tablename(self):
        """Test User model tablename."""
        from src.auth.models import User
        
        assert User.__tablename__ == "users"


class TestAuthSchemas:
    """Tests for authentication schemas."""

    def test_user_create_schema(self):
        """Test UserCreate schema."""
        from src.auth.schemas import UserCreate
        
        user = UserCreate(username="testuser", password="password123")
        
        assert user.username == "testuser"
        assert user.password == "password123"

    def test_user_create_schema_validation(self):
        """Test UserCreate schema requires both fields."""
        from src.auth.schemas import UserCreate
        from pydantic import ValidationError
        
        # Should raise validation error without password
        with pytest.raises(ValidationError):
            UserCreate(username="testuser")

    def test_user_response_schema(self):
        """Test UserResponse schema."""
        from src.auth.schemas import UserResponse
        
        user = UserResponse(
            id=1,
            username="testuser",
            is_admin=True,
            created_at=datetime.now()
        )
        
        assert user.id == 1
        assert user.username == "testuser"
        assert user.is_admin is True

    def test_token_schema(self):
        """Test Token schema."""
        from src.auth.schemas import Token
        
        token = Token(access_token="abc123", token_type="bearer")
        
        assert token.access_token == "abc123"
        assert token.token_type == "bearer"


class TestVerifyToken:
    """Tests for token verification."""

    @pytest.fixture
    def mock_settings(self):
        """Mock settings for JWT."""
        mock = MagicMock()
        mock.jwt_secret = "test-secret-key"
        mock.jwt_algorithm = "HS256"
        mock.jwt_expire_minutes = 60
        return mock

    def test_verify_token_valid(self, mock_settings):
        """Test verifying a valid token."""
        from src.auth.utils import create_access_token
        
        with patch("src.auth.utils.settings", mock_settings):
            token = create_access_token({"sub": "testuser"})
            
            # Verify token
            payload = jwt.decode(
                token,
                mock_settings.jwt_secret,
                algorithms=[mock_settings.jwt_algorithm]
            )
            
            assert payload["sub"] == "testuser"

    def test_verify_token_invalid_signature(self, mock_settings):
        """Test that token with invalid signature fails."""
        from src.auth.utils import create_access_token
        
        with patch("src.auth.utils.settings", mock_settings):
            token = create_access_token({"sub": "testuser"})
            
            # Try to decode with wrong secret
            with pytest.raises(JWTError):
                jwt.decode(
                    token,
                    "wrong-secret",
                    algorithms=[mock_settings.jwt_algorithm]
                )

    def test_verify_token_invalid_algorithm(self, mock_settings):
        """Test that token with wrong algorithm fails."""
        from src.auth.utils import create_access_token
        
        with patch("src.auth.utils.settings", mock_settings):
            token = create_access_token({"sub": "testuser"})
            
            # Try to decode with wrong algorithm
            with pytest.raises(JWTError):
                jwt.decode(
                    token,
                    mock_settings.jwt_secret,
                    algorithms=["HS512"]
                )

    def test_token_can_be_decoded_manually(self, mock_settings):
        """Test that we can manually create and decode tokens."""
        # Create token manually
        expire = datetime.utcnow() + timedelta(minutes=30)
        payload = {"sub": "manual_user", "exp": expire}
        token = jwt.encode(
            payload, 
            mock_settings.jwt_secret, 
            algorithm=mock_settings.jwt_algorithm
        )
        
        # Decode
        decoded = jwt.decode(
            token,
            mock_settings.jwt_secret,
            algorithms=[mock_settings.jwt_algorithm]
        )
        
        assert decoded["sub"] == "manual_user"

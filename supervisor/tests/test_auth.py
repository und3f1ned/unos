import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import jwt
from jwt import PyJWTError
import secrets


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

    # A hash written by the passlib 1.7.4 + bcrypt 4.0.1 code before Phase 0.
    PASSLIB_HASH = ("unos-e2e-pass", "$2b$12$70/WS7hrwVa4RW2kJdhIZuGbVq2hWk12O0dsSq1ZrsCQycx3gCUbe")

    def test_a_hash_of_the_passlib_era_still_verifies_its_password(self):
        from src.auth.utils import verify_password

        password, hashed = self.PASSLIB_HASH
        assert verify_password(password, hashed) is True
        assert verify_password(password + "x", hashed) is False

    @pytest.mark.parametrize("password", ["a" * 72, "\u00e4" * 36], ids=["72-ascii", "72-bytes-of-umlauts"])
    def test_a_password_longer_than_72_bytes_matches_no_hash(self, password):
        """Passwords are checked as given (auth.no-legacy-password-truncation, ADR-0315): one longer
        than bcrypt's 72 bytes matches no hash, also not the hash of its first 72 bytes, which the
        passlib-era code made of it."""
        from src.auth.utils import hash_password, verify_password

        hashed = hash_password(password)
        assert verify_password(password, hashed) is True
        assert verify_password(password + "a", hashed) is False
        assert verify_password(password + "\u00e4", hashed) is False

    def test_hash_password_rejects_more_than_72_bytes(self):
        from src.auth.utils import hash_password

        hash_password("a" * 72)
        with pytest.raises(ValueError):
            hash_password("a" * 73)
        with pytest.raises(ValueError):
            hash_password("\u00e4" * 37)  # 74 bytes in UTF-8

    def test_verify_password_malformed_hash_is_false(self):
        from src.auth.utils import verify_password

        assert verify_password("x", "") is False
        assert verify_password("x", "not-a-bcrypt-hash") is False

    def test_new_password_schemas_enforce_bcrypt_limit(self):
        from pydantic import ValidationError
        from src.auth.schemas import LoginRequest, PasswordChange, UserCreate, UserUpdate

        long_password = "a" * 73
        with pytest.raises(ValidationError):
            UserCreate(username="u", password=long_password)
        with pytest.raises(ValidationError):
            PasswordChange(current_password="old", new_password=long_password)
        with pytest.raises(ValidationError):
            UserUpdate(password=long_password)
        # Passwords that are only checked are bounded by the request limits alone: a
        # longer one fails like a wrong password (ADR-0315), not with a 422.
        assert LoginRequest(username="u", password=long_password).password == long_password
        assert PasswordChange(current_password=long_password, new_password="new").new_password == "new"


class TestJWTTokens:
    """Tests for JWT token creation and verification."""

    @pytest.fixture
    def mock_settings(self):
        """Mock settings for JWT."""
        mock = MagicMock()
        mock.jwt_secret = secrets.token_hex(32)
        mock.jwt_algorithm = "HS256"
        mock.jwt_expire_minutes = 60
        return mock

    def test_create_access_token(self, mock_settings):
        """Test creating an access token."""
        from src.auth.utils import create_access_token
        
        with patch("src.auth.utils.get_settings", return_value=mock_settings):
            token = create_access_token({"sub": "testuser"})
        
        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_access_token_contains_subject(self, mock_settings):
        """Test that token contains the subject."""
        from src.auth.utils import create_access_token
        
        with patch("src.auth.utils.get_settings", return_value=mock_settings):
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
        
        with patch("src.auth.utils.get_settings", return_value=mock_settings):
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
        
        with patch("src.auth.utils.get_settings", return_value=mock_settings):
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

    def test_user_create_smb_fields(self):
        from pydantic import ValidationError
        from src.auth.schemas import UserCreate

        assert UserCreate(username="carol", password="pw").smb_enabled is False
        assert UserCreate(username="carol", password="pw", smb_enabled=True).smb_enabled is True
        with pytest.raises(ValidationError):
            UserCreate(username="c" * 65, password="pw")

    def test_user_update_username(self):
        from pydantic import ValidationError
        from src.auth.schemas import UserUpdate

        assert UserUpdate().username is None
        assert UserUpdate(username="alice", password="new").username == "alice"
        with pytest.raises(ValidationError):
            UserUpdate(username="a" * 65)

    def test_smb_request_schemas_forbid_extra_fields(self):
        from pydantic import ValidationError
        from src.auth.schemas import SmbEnableRequest, SmbSelfEnableRequest

        assert SmbEnableRequest(username="alice").password is None
        with pytest.raises(ValidationError):
            SmbEnableRequest(username="alice", enabled=True)
        with pytest.raises(ValidationError):
            SmbEnableRequest(password="Correct-horse-1")  # the username is required
        with pytest.raises(ValidationError):
            SmbEnableRequest(username="alice", password="a" * 73)  # bcrypt limit
        assert SmbSelfEnableRequest(current_password="pw").totp_code is None
        with pytest.raises(ValidationError):
            SmbSelfEnableRequest(current_password="pw", password="x")
        with pytest.raises(ValidationError):
            SmbSelfEnableRequest(current_password="pw", totp_code="1" * 17)

    def test_smb_disable_request_names_the_user(self):
        from pydantic import ValidationError
        from src.auth.schemas import SmbDisableRequest

        assert SmbDisableRequest(username="alice").username == "alice"
        for body in ({}, {"username": "alice", "enabled": False}, {"username": "a" * 65}):
            with pytest.raises(ValidationError):
                SmbDisableRequest(**body)

    def test_login_request_is_bounded(self):
        """A failed login is recorded and audited before anyone is authenticated."""
        from pydantic import ValidationError
        from src.auth.schemas import LoginRequest

        assert LoginRequest(username="a" * 255, password="p" * 1024, totp_code="1" * 16).totp_code == "1" * 16
        for body in (
            {"username": "a" * 256, "password": "pw"},
            {"username": "alice", "password": "p" * 1025},
            {"username": "alice", "password": "pw", "totp_code": "1" * 17},
        ):
            with pytest.raises(ValidationError):
                LoginRequest(**body)

    def test_user_response_smb_defaults(self):
        from src.auth.schemas import UserResponse

        user = UserResponse(id=1, username="alice", is_admin=False)
        assert user.smb is None and user.warnings == []

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
        mock.jwt_secret = secrets.token_hex(32)
        mock.jwt_algorithm = "HS256"
        mock.jwt_expire_minutes = 60
        return mock

    def test_verify_token_valid(self, mock_settings):
        """Test verifying a valid token."""
        from src.auth.utils import create_access_token
        
        with patch("src.auth.utils.get_settings", return_value=mock_settings):
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
        
        with patch("src.auth.utils.get_settings", return_value=mock_settings):
            token = create_access_token({"sub": "testuser"})
            
            # Try to decode with wrong secret
            with pytest.raises(PyJWTError):
                jwt.decode(
                    token,
                    "wrong-secret",
                    algorithms=[mock_settings.jwt_algorithm]
                )

    def test_verify_token_invalid_algorithm(self, mock_settings):
        """Test that token with wrong algorithm fails."""
        from src.auth.utils import create_access_token
        
        with patch("src.auth.utils.get_settings", return_value=mock_settings):
            token = create_access_token({"sub": "testuser"})
            
            # Try to decode with wrong algorithm
            with pytest.raises(PyJWTError):
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


class TestFirstRunSetup:
    """POST /auth/setup creates exactly one administrator, even under concurrency."""

    @pytest.fixture
    def empty_db(self, tmp_path, monkeypatch):
        import asyncio

        from sqlalchemy import create_engine
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from src.auth import router as auth_router
        from src.database import Base, get_db
        from src.main import app

        path = tmp_path / "setup.db"
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(engine)
        maker = async_sessionmaker(
            create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool),
            expire_on_commit=False,
        )

        async def override_get_db():
            async with maker() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        # A lock bound to no event loop yet (the module's may belong to an earlier test's loop).
        monkeypatch.setattr(auth_router, "_setup_lock", asyncio.Lock())
        auth_router.limiter.reset()
        try:
            yield engine
        finally:
            app.dependency_overrides.pop(get_db, None)
            auth_router.limiter.reset()
            engine.dispose()

    @pytest.fixture
    def slow_hash(self, monkeypatch):
        """bcrypt-like delay between the "no users yet" check and the insert."""
        import time

        import bcrypt

        from src.auth import router as auth_router

        cheap = bcrypt.hashpw(b"pw", bcrypt.gensalt(rounds=4)).decode()

        def hash_password(password):
            time.sleep(0.2)
            return cheap

        monkeypatch.setattr(auth_router, "hash_password", hash_password)

    async def test_concurrent_setup_creates_one_admin(self, empty_db, slow_hash):
        import asyncio

        import httpx
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from src.auth.models import User
        from src.main import app

        async def setup(ip, username):
            transport = httpx.ASGITransport(app=app, client=(ip, 40000))
            async with httpx.AsyncClient(transport=transport, base_url="http://unos") as client:
                return await client.post(
                    "/api/v1/auth/setup", json={"username": username, "password": "pw-" + username}
                )

        responses = await asyncio.gather(setup("10.0.0.5", "owner"), setup("10.0.0.6", "intruder"))

        assert sorted(r.status_code for r in responses) == [200, 400]
        [refused] = [r for r in responses if r.status_code == 400]
        assert refused.json()["detail"] == "Admin user already exists. Use /login instead."
        [created] = [r for r in responses if r.status_code == 200]
        with Session(empty_db) as session:
            users = [(u.username, u.is_admin) for u in session.scalars(select(User))]
        assert users == [(created.json()["username"], True)]

    def test_setup_is_refused_once_a_user_exists(self, empty_db, slow_hash):
        from fastapi.testclient import TestClient
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from src.auth.models import User
        from src.main import app

        client = TestClient(app)
        first = client.post("/api/v1/auth/setup", json={"username": "owner", "password": "pw"})
        second = client.post("/api/v1/auth/setup", json={"username": "other", "password": "pw"})

        assert first.status_code == 200 and first.json()["is_admin"] is True
        assert second.status_code == 400
        assert second.json()["detail"] == "Admin user already exists. Use /login instead."
        # The refused setup created nobody.
        with Session(empty_db) as session:
            assert [(u.username, u.is_admin) for u in session.scalars(select(User))] == [("owner", True)]
        assert client.get("/api/v1/auth/status").json() == {"setup_required": False}

    @pytest.mark.parametrize("dockerfile, compose_file", [
        ("Dockerfile", "docker-compose.yml"),
        ("Dockerfile.test", "docker-compose.test.yml"),
    ])
    def test_the_supervisor_runs_as_one_process(self, dockerfile, compose_file):
        """_setup_lock makes "no users yet" and the insert one step only inside one process
        (ADR-0029), and the used TOTP steps live in its memory (ADR-0067): the image runs one
        uvicorn process, without workers (uvicorn takes their number from WEB_CONCURRENCY too)."""
        from .test_authz import _compose, _environment, _uvicorn_args

        args = _uvicorn_args(dockerfile)
        # The test image's stack_app:app is src.main:app with the sign-in limit off (ADR-0450).
        assert args[:2] == ["uvicorn", "src.main:app" if dockerfile == "Dockerfile" else "stack_app:app"]
        assert not [arg for arg in args if arg.split("=")[0] in ("--workers", "--reload")]
        assert "WEB_CONCURRENCY" not in _environment(_compose(compose_file)["services"]["supervisor"])

    def test_the_new_administrator_signs_in_and_nobody_else(self, empty_db, tmp_path, monkeypatch):
        """What the /setup page does after the setup: sign in with the same name and password."""
        import bcrypt
        from fastapi.testclient import TestClient

        from src.auth import router as auth_router
        from src.main import app

        monkeypatch.setattr(
            auth_router, "hash_password", lambda pw: bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4)).decode()
        )
        monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(tmp_path / "audit.jsonl"))
        client = TestClient(app)
        assert client.post("/api/v1/auth/setup", json={"username": "owner", "password": "Owner-pass-2026"}).status_code == 200
        assert client.post("/api/v1/auth/setup", json={"username": "other", "password": "Other-pass-2026"}).status_code == 400

        login = client.post("/api/v1/auth/login", json={"username": "owner", "password": "Owner-pass-2026"})
        assert login.status_code == 200, login.text
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
        assert (me.json()["username"], me.json()["is_admin"]) == ("owner", True)
        refused = client.post("/api/v1/auth/login", json={"username": "other", "password": "Other-pass-2026"})
        assert refused.status_code == 401

    def test_a_new_installation_has_no_account(self, tmp_path, monkeypatch):
        """No default account: the app started on a new database has no user, asks for the
        setup, and the default credentials that older docs promised do not sign in (ADR-0029)."""
        import asyncio

        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine, func, select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.orm import Session
        from sqlalchemy.pool import NullPool

        from src import database
        from src.auth import router as auth_router
        from src.auth.models import User
        from src.main import app

        path = tmp_path / "unos.db"  # no file yet: a new installation
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
        monkeypatch.setattr(database, "engine", engine)
        monkeypatch.setattr(database, "async_session", async_sessionmaker(engine, expire_on_commit=False))
        monkeypatch.setenv("UNOS_DISABLE_BACKGROUND_TASKS", "1")
        monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(tmp_path / "audit.jsonl"))
        auth_router.limiter.reset()
        try:
            # The lifespan creates the tables (init_db), as at the first start of an installation.
            with TestClient(app) as client:
                status = client.get("/api/v1/auth/status")
                default = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        finally:
            auth_router.limiter.reset()
            asyncio.run(engine.dispose())

        assert status.json() == {"setup_required": True}
        assert default.status_code == 401
        assert default.json()["detail"] == "Incorrect username or password"
        check = create_engine(f"sqlite:///{path}")
        try:
            with Session(check) as session:
                assert session.scalar(select(func.count()).select_from(User)) == 0
        finally:
            check.dispose()


def test_a_sign_in_with_a_password_longer_than_72_bytes_fails_like_a_wrong_one(tmp_path, monkeypatch, caplog):
    """auth.no-legacy-password-truncation (ADR-0315): the password of an account is 72 bytes long,
    and a sign-in with it and one more character is refused as a wrong password, not taken for its
    first 72 bytes, and with no server error."""
    from src.auth import router as auth_router

    from .test_smb_accounts import API, World

    world = World(tmp_path, monkeypatch, caplog)
    try:
        password = "Seventy-two-bytes-" + "x" * 54
        assert len(password.encode()) == 72
        world.add_user("carol", password)
        right = world.client.post(f"{API}/auth/login", json={"username": "carol", "password": password})
        auth_router.limiter.reset()
        longer = world.client.post(f"{API}/auth/login", json={"username": "carol", "password": password + "y"})
    finally:
        world.close()

    assert right.status_code == 200, right.text
    assert (longer.status_code, longer.json()["detail"]) == (401, "Incorrect username or password")

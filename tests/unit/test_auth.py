"""Unit tests for authentication system"""

import pytest
from datetime import timedelta
from jose import jwt

from services.shared.auth import (
    verify_password,
    get_password_hash,
    create_access_token,
    decode_access_token
)
from services.shared.config import settings


@pytest.mark.unit
class TestPasswordHashing:
    """Test password hashing functionality"""

    def test_hash_password(self):
        """Test password hashing"""
        password = "mysecurepassword123"
        hashed = get_password_hash(password)

        assert hashed != password
        assert len(hashed) > 0
        assert hashed.startswith("$2b$")  # bcrypt prefix

    def test_verify_correct_password(self):
        """Test verifying correct password"""
        password = "mysecurepassword123"
        hashed = get_password_hash(password)

        assert verify_password(password, hashed) is True

    def test_verify_incorrect_password(self):
        """Test verifying incorrect password"""
        password = "mysecurepassword123"
        wrong_password = "wrongpassword"
        hashed = get_password_hash(password)

        assert verify_password(wrong_password, hashed) is False

    def test_different_hashes_for_same_password(self):
        """Test that same password produces different hashes (salt)"""
        password = "mysecurepassword123"
        hash1 = get_password_hash(password)
        hash2 = get_password_hash(password)

        assert hash1 != hash2
        assert verify_password(password, hash1) is True
        assert verify_password(password, hash2) is True


@pytest.mark.unit
class TestJWTTokens:
    """Test JWT token creation and validation"""

    def test_create_access_token(self):
        """Test creating access token"""
        user_id = "test-user-123"
        token = create_access_token(data={"sub": user_id})

        assert token is not None
        assert len(token) > 0
        assert isinstance(token, str)

    def test_decode_valid_token(self):
        """Test decoding valid token"""
        user_id = "test-user-123"
        token = create_access_token(data={"sub": user_id})

        decoded_user_id = decode_access_token(token)

        assert decoded_user_id == user_id

    def test_decode_invalid_token(self):
        """Test decoding invalid token"""
        invalid_token = "invalid.token.here"

        decoded = decode_access_token(invalid_token)

        assert decoded is None

    def test_token_with_expiration(self):
        """Test token with custom expiration"""
        user_id = "test-user-123"
        expires_delta = timedelta(minutes=30)

        token = create_access_token(
            data={"sub": user_id},
            expires_delta=expires_delta
        )

        # Decode manually to check expiration
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )

        assert "exp" in payload
        assert payload["sub"] == user_id

    def test_token_contains_user_id(self):
        """Test that token contains correct user ID"""
        user_id = "test-user-456"
        token = create_access_token(data={"sub": user_id})

        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )

        assert payload["sub"] == user_id

    def test_decode_token_wrong_signature(self):
        """Test decoding token with wrong signature"""
        user_id = "test-user-123"
        token = create_access_token(data={"sub": user_id})

        # Tamper with token
        parts = token.split(".")
        tampered_token = parts[0] + "." + parts[1] + ".tampered"

        decoded = decode_access_token(tampered_token)

        assert decoded is None


@pytest.mark.unit
class TestAuthenticationHelpers:
    """Test authentication helper functions"""

    def test_password_hash_is_one_way(self):
        """Ensure password hashing is one-way"""
        password = "verysecurepassword"
        hashed = get_password_hash(password)

        # There should be no way to reverse the hash
        assert password not in hashed
        assert hashed != password

    def test_empty_password_handling(self):
        """Test handling of empty password"""
        password = ""
        hashed = get_password_hash(password)

        # Even empty passwords should be hashed
        assert len(hashed) > 0
        assert verify_password("", hashed) is True

    def test_special_characters_in_password(self):
        """Test passwords with special characters"""
        password = "p@ssw0rd!#$%^&*()"
        hashed = get_password_hash(password)

        assert verify_password(password, hashed) is True

    def test_unicode_in_password(self):
        """Test passwords with unicode characters"""
        password = "пароль密码🔐"
        hashed = get_password_hash(password)

        assert verify_password(password, hashed) is True

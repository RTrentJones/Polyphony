"""Integration tests for API endpoints"""

import pytest
from uuid import uuid4


@pytest.mark.integration
class TestAuthEndpoints:
    """Test authentication endpoints"""

    @pytest.mark.asyncio
    async def test_register_user(self, client, test_invite):
        """Test user registration (invite-gated)"""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "newuser@example.com",
                "password": "securepassword123",
                "full_name": "New User",
                "invite_code": test_invite.code,
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == "newuser@example.com"

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self, client, test_user, test_invite):
        """Test registering with duplicate email fails"""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": test_user.email,
                "password": "anotherpassword",
                "full_name": "Duplicate User",
                "invite_code": test_invite.code,
            },
        )

        assert response.status_code == 400
        assert "already registered" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_login_success(self, client, test_user):
        """Test successful login"""
        response = await client.post(
            "/api/v1/auth/login",
            data={
                "username": test_user.email,  # OAuth2 uses 'username'
                "password": "testpassword123",
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert "access_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_login_invalid_credentials(self, client, test_user):
        """Test login with invalid credentials"""
        response = await client.post(
            "/api/v1/auth/login",
            data={"username": test_user.email, "password": "wrongpassword"},
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_user(self, client, auth_headers):
        """Test getting current user info"""
        response = await client.get("/api/v1/auth/me", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert "email" in data
        assert "id" in data

    @pytest.mark.asyncio
    async def test_get_current_user_no_token(self, client):
        """Test getting current user without token fails"""
        response = await client.get("/api/v1/auth/me")

        assert response.status_code == 401


@pytest.mark.integration
class TestManuscriptEndpoints:
    """Test manuscript endpoints"""

    @pytest.mark.asyncio
    async def test_list_manuscripts_empty(self, client, auth_headers):
        """Test listing manuscripts when user has none"""
        response = await client.get("/api/v1/manuscripts/", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert data["manuscripts"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_manuscripts(self, client, auth_headers, test_manuscript):
        """Test listing manuscripts"""
        response = await client.get("/api/v1/manuscripts/", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert len(data["manuscripts"]) == 1
        assert data["total"] == 1
        assert data["manuscripts"][0]["title"] == test_manuscript.title

    @pytest.mark.asyncio
    async def test_get_manuscript(self, client, auth_headers, test_manuscript):
        """Test getting specific manuscript"""
        response = await client.get(
            f"/api/v1/manuscripts/{test_manuscript.id}", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()

        assert data["id"] == str(test_manuscript.id)
        assert data["title"] == test_manuscript.title

    @pytest.mark.asyncio
    async def test_get_nonexistent_manuscript(self, client, auth_headers):
        """Test getting non-existent manuscript"""
        fake_id = uuid4()
        response = await client.get(
            f"/api/v1/manuscripts/{fake_id}", headers=auth_headers
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_manuscript_no_auth(self, client, test_manuscript):
        """Test getting manuscript without authentication"""
        response = await client.get(f"/api/v1/manuscripts/{test_manuscript.id}")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_manuscript_characters(
        self, client, auth_headers, test_manuscript, test_character
    ):
        """Test getting manuscript characters"""
        response = await client.get(
            f"/api/v1/manuscripts/{test_manuscript.id}/characters", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()

        assert "characters" in data
        assert len(data["characters"]) == 1
        assert data["characters"][0]["name"] == test_character.name


@pytest.mark.integration
class TestSceneEndpoints:
    """Test scene generation endpoints"""

    @pytest.mark.asyncio
    async def test_list_scenes_empty(self, client, auth_headers):
        """Test listing scenes when user has none"""
        response = await client.get("/api/v1/scenes/", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert data["scenes"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_scenes_no_auth(self, client):
        """Test listing scenes without authentication"""
        response = await client.get("/api/v1/scenes/")

        assert response.status_code == 401

    # Scene generation requires orchestrator service
    # These tests would require mocking or actual service


@pytest.mark.integration
class TestHealthEndpoints:
    """Test health and metrics endpoints"""

    @pytest.mark.asyncio
    async def test_root_endpoint(self, client):
        """Test root endpoint"""
        response = await client.get("/")

        # JSON status when no frontend build is present; the static export
        # (index.html) when one is. Both are healthy roots.
        assert response.status_code == 200
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            assert response.json()["service"] == "Polyphony"
        else:
            assert "html" in content_type

    @pytest.mark.asyncio
    async def test_health_endpoint(self, client):
        """Test health check endpoint"""
        response = await client.get("/health")

        assert response.status_code == 200
        data = response.json()

        assert "status" in data
        assert "checks" in data

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self, client):
        """Test Prometheus metrics endpoint"""
        response = await client.get("/metrics")

        assert response.status_code == 200
        # Prometheus metrics are in text format
        assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.integration
class TestErrorHandling:
    """Test error handling"""

    @pytest.mark.asyncio
    async def test_404_error(self, client):
        """Test 404 on non-existent endpoint"""
        response = await client.get("/nonexistent/endpoint")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_validation_error(self, client, auth_headers):
        """Test validation error response"""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "invalid-email",  # Invalid email format
                "password": "password",
            },
        )

        assert response.status_code == 422  # Validation error
        data = response.json()

        assert "detail" in data

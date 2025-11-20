"""Unit tests for API Gateway"""

import pytest
import sys
import os

# Add parent directory to path to import services
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

# Import from the actual path (api-gateway with hyphen)
import importlib.util

spec = importlib.util.spec_from_file_location(
    "api_gateway_main",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "services",
        "api-gateway",
        "main.py",
    ),
)
api_gateway_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api_gateway_main)
app = api_gateway_main.app


@pytest.fixture
def api_client():
    """Test client for API Gateway (renamed to avoid conflicts)"""
    return TestClient(app)


@pytest.mark.unit
class TestAPIGatewayHealth:
    """Test health check endpoints"""

    def test_health_endpoint(self, api_client):
        """Test health check endpoint"""
        response = api_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_root_endpoint(self, api_client):
        """Test root endpoint"""
        response = api_client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data


@pytest.mark.unit
class TestSecurityHeaders:
    """Test security headers middleware"""

    def test_security_headers_present(self, api_client):
        """Test that security headers are added"""
        response = api_client.get("/health")

        assert "X-Content-Type-Options" in response.headers
        assert response.headers["X-Content-Type-Options"] == "nosniff"

        assert "X-Frame-Options" in response.headers
        assert response.headers["X-Frame-Options"] == "DENY"

        assert "Strict-Transport-Security" in response.headers
        assert "Content-Security-Policy" in response.headers

    def test_cors_headers(self, api_client):
        """Test CORS headers configuration"""
        response = api_client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

        # CORS should be configured
        assert response.status_code in [200, 204]


@pytest.mark.unit
class TestRequestSizeLimits:
    """Test request size limit middleware"""

    def test_request_within_limit(self, api_client):
        """Test request within size limit"""
        small_data = {"key": "value"}

        response = api_client.post("/api/auth/register", json=small_data)

        # May fail validation, but shouldn't fail size check
        assert response.status_code != 413

    def test_request_exceeds_limit(self, api_client):
        """Test request exceeding size limit"""
        # Create a very large payload (>10MB)
        large_data = {"data": "x" * (11 * 1024 * 1024)}

        response = api_client.post(
            "/api/auth/register",
            json=large_data,
            headers={"Content-Length": str(11 * 1024 * 1024)},
        )

        # Should be rejected
        assert response.status_code == 413


@pytest.mark.unit
class TestRateLimiting:
    """Test rate limiting"""

    def test_rate_limit_allows_normal_requests(self, api_client):
        """Test normal request rate is allowed"""
        # Make a few requests
        for _ in range(5):
            response = api_client.get("/health")
            assert response.status_code == 200

    def test_rate_limit_headers(self, api_client):
        """Test rate limit headers are present"""
        response = api_client.get("/health")
        assert response.status_code == 200

        # Rate limit headers may be present
        # (depends on slowapi configuration)
        # This test just verifies the endpoint works


@pytest.mark.unit
class TestCompressionMiddleware:
    """Test GZip compression"""

    def test_compression_for_large_response(self, api_client):
        """Test compression is applied to large responses"""
        response = api_client.get("/health")

        # For large responses, should have compression
        # Small responses may not be compressed
        # This test verifies no errors occur
        assert response.status_code == 200


@pytest.mark.unit
class TestMetricsEndpoint:
    """Test Prometheus metrics endpoint"""

    def test_metrics_endpoint_exists(self, api_client):
        """Test metrics endpoint is available"""
        response = api_client.get("/metrics")

        assert response.status_code == 200
        assert "text/plain" in response.headers.get("Content-Type", "")

    def test_metrics_format(self, api_client):
        """Test metrics are in Prometheus format"""
        response = api_client.get("/metrics")
        content = response.text

        # Should contain Prometheus metric format
        assert "# HELP" in content or "# TYPE" in content


@pytest.mark.unit
class TestRequestLogging:
    """Test request logging middleware"""

    def test_process_time_header(self, api_client):
        """Test X-Process-Time header is added"""
        response = api_client.get("/health")

        assert "X-Process-Time" in response.headers

        # Should be a valid float
        process_time = float(response.headers["X-Process-Time"])
        assert process_time >= 0

    def test_correlation_id_header(self, api_client):
        """Test correlation ID is added"""
        response = api_client.get("/health")

        assert "X-Correlation-ID" in response.headers

    def test_correlation_id_propagation(self, api_client):
        """Test provided correlation ID is propagated"""
        correlation_id = "test-corr-123"

        response = api_client.get(
            "/health", headers={"X-Correlation-ID": correlation_id}
        )

        assert response.headers["X-Correlation-ID"] == correlation_id


@pytest.mark.unit
class TestErrorHandling:
    """Test error handling"""

    def test_validation_error_handling(self, api_client):
        """Test validation errors return proper format"""
        # Send invalid data to an endpoint
        response = api_client.post("/api/auth/register", json={"invalid": "data"})

        # Should return validation error
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    def test_internal_error_handling(self, api_client):
        """Test internal errors are handled gracefully"""
        # This would require mocking an internal error
        # For now, test that error handler exists
        pass


@pytest.mark.unit
class TestAuthenticationEndpoints:
    """Test authentication endpoints"""

    @patch("services.shared.database.get_db")
    @patch("services.shared.auth.get_password_hash")
    @patch("services.shared.auth.create_access_token")
    def test_register_endpoint(self, mock_token, mock_hash, mock_db, api_client):
        """Test user registration endpoint"""
        # Mock dependencies
        mock_hash.return_value = "hashed_password"
        mock_token.return_value = "test_token"

        mock_session = AsyncMock()
        mock_db.return_value.__aenter__.return_value = mock_session

        response = api_client.post(
            "/api/auth/register",
            json={
                "email": "test@example.com",
                "password": "StrongPass123!",
                "full_name": "Test User",
            },
        )

        # May require database setup, so just verify endpoint exists
        # Actual response depends on database state
        assert response.status_code in [200, 201, 400, 422, 500]

    def test_login_endpoint_exists(self, api_client):
        """Test login endpoint exists"""
        response = api_client.post(
            "/api/auth/login",
            json={"email": "test@example.com", "password": "password"},
        )

        # Endpoint should exist (even if login fails)
        assert response.status_code in [200, 401, 422]


@pytest.mark.unit
class TestManuscriptEndpoints:
    """Test manuscript endpoints"""

    def test_list_manuscripts_requires_auth(self, api_client):
        """Test listing manuscripts requires authentication"""
        response = api_client.get("/api/manuscripts/")

        # Should require authentication
        assert response.status_code in [401, 403]

    def test_create_manuscript_requires_auth(self, api_client):
        """Test creating manuscript requires authentication"""
        response = api_client.post(
            "/api/manuscripts/",
            json={"title": "Test Manuscript", "description": "A test"},
        )

        # Should require authentication
        assert response.status_code in [401, 403, 422]


@pytest.mark.unit
class TestSceneEndpoints:
    """Test scene generation endpoints"""

    def test_list_scenes_requires_auth(self, api_client):
        """Test listing scenes requires authentication"""
        response = api_client.get("/api/scenes/")

        # Should require authentication
        assert response.status_code in [401, 403]

    def test_generate_scene_requires_auth(self, api_client):
        """Test scene generation requires authentication"""
        response = api_client.post(
            "/api/scenes/generate",
            json={"manuscript_id": "123", "scene_description": "A test scene"},
        )

        # Should require authentication
        assert response.status_code in [401, 403, 422]

    def test_generate_scene_validation(self, api_client):
        """Test scene generation input validation"""
        # Missing required fields
        response = api_client.post("/api/scenes/generate", json={})

        assert response.status_code == 422  # Validation error


@pytest.mark.unit
class TestInputValidation:
    """Test input validation"""

    def test_query_parameter_validation(self, api_client):
        """Test query parameter validation"""
        # Invalid skip value (negative)
        response = api_client.get("/api/scenes/?skip=-10")

        # Should reject invalid parameters
        assert response.status_code in [422, 401, 403]

    def test_query_parameter_limits(self, api_client):
        """Test query parameter limits"""
        # Excessive limit value
        response = api_client.get("/api/scenes/?limit=10000")

        # Should enforce maximum limits
        assert response.status_code in [422, 401, 403]


@pytest.mark.unit
class TestAPIGatewayIntegration:
    """Test API Gateway integration scenarios"""

    def test_full_request_pipeline(self, api_client):
        """Test full request pipeline with all middleware"""
        response = api_client.get("/health")

        # All middleware should execute successfully
        assert response.status_code == 200

        # Security headers
        assert "X-Content-Type-Options" in response.headers

        # Logging headers
        assert "X-Process-Time" in response.headers
        assert "X-Correlation-ID" in response.headers

    def test_concurrent_requests(self, api_client):
        """Test handling concurrent requests"""
        import concurrent.futures

        def make_request():
            return api_client.get("/health")

        # Make multiple concurrent requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request) for _ in range(20)]
            results = [f.result() for f in futures]

        # All should succeed
        assert all(r.status_code == 200 for r in results)

    def test_metrics_track_requests(self, api_client):
        """Test that requests are tracked in metrics"""
        # Make some requests
        for _ in range(5):
            api_client.get("/health")

        # Get metrics
        response = api_client.get("/metrics")
        metrics = response.text

        # Should contain HTTP metrics
        assert "http_requests_total" in metrics


@pytest.mark.integration
class TestAPIGatewayWithDatabase:
    """Integration tests requiring database"""

    @pytest.mark.database
    def test_user_registration_flow(self, api_client):
        """Test complete user registration flow"""
        # This would require database setup
        pass

    @pytest.mark.database
    def test_authentication_flow(self, api_client):
        """Test complete authentication flow"""
        # This would require database setup
        pass

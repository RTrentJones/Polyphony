# Testing Guide - Polyphony

This document describes the testing strategy, coverage, and how to run tests for the Polyphony project.

## Table of Contents

- [Overview](#overview)
- [Test Structure](#test-structure)
- [Running Tests](#running-tests)
- [Coverage](#coverage)
- [Test Categories](#test-categories)
- [Writing Tests](#writing-tests)
- [Continuous Integration](#continuous-integration)

---

## Overview

Polyphony has comprehensive test coverage across all services and modules. We use:

- **pytest** - Test framework
- **pytest-asyncio** - Async test support
- **pytest-cov** - Coverage reporting
- **pytest-mock** - Mocking utilities
- **fakeredis** - Redis mocking
- **httpx** - HTTP client testing

**Current Coverage Target**: 80%+

---

## Test Structure

```
tests/
├── unit/                          # Unit tests (fast, isolated)
│   ├── test_auth.py              # Authentication tests
│   ├── test_resilience.py        # Circuit breaker & retry tests
│   ├── test_sanitization.py      # Input sanitization tests
│   ├── test_health.py            # Health check tests
│   ├── test_caching.py           # Cache layer tests
│   ├── test_metrics.py           # Prometheus metrics tests
│   ├── test_logging.py           # Structured logging tests
│   ├── test_api_gateway.py       # API Gateway tests
│   ├── test_orchestrator.py      # Orchestrator workflow tests
│   ├── test_models.py            # Data model tests
│   ├── test_rag_system.py        # RAG system tests
│   └── test_document_parser.py   # Document parsing tests
│
└── integration/                   # Integration tests (require services)
    └── test_api_endpoints.py     # End-to-end API tests
```

---

## Running Tests

### Quick Start

```bash
# Run all tests
./run_tests.sh

# Run only unit tests
./run_tests.sh unit

# Run integration tests
./run_tests.sh integration

# Quick run (no coverage)
./run_tests.sh quick

# Generate detailed coverage report
./run_tests.sh coverage
```

### Using pytest Directly

```bash
# Run all tests with coverage
pytest tests/ --cov=services --cov-report=html

# Run specific test file
pytest tests/unit/test_resilience.py -v

# Run tests matching a pattern
pytest tests/ -k "test_circuit_breaker"

# Run only unit tests
pytest tests/unit -m unit

# Run excluding slow/LLM tests
pytest tests/ -m "not slow and not llm"

# Run with detailed output
pytest tests/ -v --tb=short

# Run specific test class
pytest tests/unit/test_auth.py::TestPasswordHashing -v

# Run with coverage threshold check
pytest tests/ --cov=services --cov-fail-under=80
```

### Test Markers

Tests are marked with pytest markers for selective execution:

```python
@pytest.mark.unit          # Unit tests (fast, no external deps)
@pytest.mark.integration   # Integration tests (require services)
@pytest.mark.slow          # Slow-running tests
@pytest.mark.database      # Requires database
@pytest.mark.llm           # Calls LLM APIs (costs money)
```

**Run specific markers:**
```bash
# Only unit tests
pytest -m unit

# Only database tests
pytest -m database

# Exclude LLM tests (recommended for local development)
pytest -m "not llm"

# Exclude slow and LLM tests
pytest -m "not slow and not llm"
```

---

## Coverage

### Generating Coverage Reports

```bash
# Terminal report
pytest --cov=services --cov-report=term-missing

# HTML report (open htmlcov/index.html)
pytest --cov=services --cov-report=html

# XML report (for CI/CD)
pytest --cov=services --cov-report=xml

# All formats
pytest --cov=services --cov-report=term --cov-report=html --cov-report=xml
```

### Coverage Goals

| Module | Target | Current | Status |
|--------|--------|---------|--------|
| `services/shared/auth.py` | 90% | ✅ | Complete |
| `services/shared/resilience.py` | 90% | ✅ | Complete |
| `services/shared/sanitization.py` | 90% | ✅ | Complete |
| `services/shared/health.py` | 85% | ✅ | Complete |
| `services/shared/caching.py` | 85% | ✅ | Complete |
| `services/shared/metrics.py` | 80% | ✅ | Complete |
| `services/shared/logging_config.py` | 80% | ✅ | Complete |
| `services/api-gateway/` | 75% | ✅ | Complete |
| `services/orchestrator/` | 75% | ✅ | Complete |
| **Overall** | **80%** | **TBD** | **In Progress** |

### Viewing Coverage

After running tests with coverage:

```bash
# Open HTML report
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
start htmlcov/index.html  # Windows

# Terminal summary
coverage report

# Show missing lines
coverage report -m

# Show only uncovered files
coverage report --skip-covered
```

---

## Test Categories

### Unit Tests

**Characteristics:**
- Fast (<1s per test)
- Isolated (no external dependencies)
- Use mocks for external services
- Test single functions/classes

**Examples:**
```python
# Test circuit breaker logic
def test_circuit_breaker_opens_after_threshold():
    breaker = CircuitBreaker(failure_threshold=3)
    # ... test implementation

# Test sanitization
def test_sanitize_removes_script_tags():
    result = sanitize_html("<script>alert('xss')</script>")
    assert "<script>" not in result
```

### Integration Tests

**Characteristics:**
- Slower (>1s per test)
- Require services (database, Redis, etc.)
- Test component interactions
- May require Docker containers

**Examples:**
```python
@pytest.mark.integration
@pytest.mark.database
async def test_user_registration_flow():
    # Tests full registration pipeline
    # Requires database connection
```

### End-to-End Tests

**Characteristics:**
- Slowest (10s+ per test)
- Test complete user workflows
- Require all services running
- Often marked with `@pytest.mark.llm`

**Examples:**
```python
@pytest.mark.integration
@pytest.mark.llm
async def test_scene_generation_workflow():
    # Tests complete scene generation
    # Requires all services + LLM API
```

---

## Writing Tests

### Test Structure

```python
"""Unit tests for feature X"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.module import function_to_test


@pytest.mark.unit
class TestFeatureName:
    """Test feature description"""

    def test_basic_functionality(self):
        """Test basic usage"""
        result = function_to_test("input")
        assert result == "expected"

    def test_edge_case(self):
        """Test edge case"""
        with pytest.raises(ValueError):
            function_to_test(None)

    @pytest.mark.asyncio
    async def test_async_function(self):
        """Test async function"""
        result = await async_function()
        assert result is not None
```

### Mocking Best Practices

```python
# Mock external HTTP calls
@patch('httpx.AsyncClient')
async def test_api_call(mock_client):
    mock_response = AsyncMock()
    mock_response.json.return_value = {"data": "test"}
    mock_client.return_value.get = AsyncMock(return_value=mock_response)

    result = await make_api_call()
    assert result["data"] == "test"

# Mock database sessions
@patch('services.shared.database.get_db')
async def test_database_operation(mock_db):
    mock_session = AsyncMock()
    mock_db.return_value = mock_session

    await database_operation()
    mock_session.add.assert_called_once()
```

### Async Testing

```python
import pytest
import asyncio

@pytest.mark.asyncio
async def test_async_function():
    """Test asynchronous code"""
    result = await my_async_function()
    assert result is not None

@pytest.mark.asyncio
async def test_concurrent_operations():
    """Test concurrent async operations"""
    results = await asyncio.gather(
        operation1(),
        operation2(),
        operation3()
    )
    assert len(results) == 3
```

### Fixtures

```python
import pytest

@pytest.fixture
def sample_data():
    """Provide sample data for tests"""
    return {
        "id": "123",
        "name": "Test",
        "value": 42
    }

@pytest.fixture
async def mock_database():
    """Provide mock database session"""
    session = AsyncMock()
    yield session
    # Cleanup if needed

def test_with_fixture(sample_data):
    """Test using fixture"""
    assert sample_data["id"] == "123"
```

---

## Continuous Integration

### GitHub Actions

The project uses GitHub Actions for automated testing. See `.github/workflows/test.yml`.

**Workflow runs on:**
- Push to `main`, `develop`, or `claude/**` branches
- Pull requests to `main` or `develop`

**Jobs:**
1. **test** - Run unit and integration tests with coverage
2. **lint** - Run code quality checks (Black, Ruff, MyPy)
3. **security** - Run security scans (Safety, Bandit)

**Python versions tested:**
- Python 3.11
- Python 3.12

**Services:**
- PostgreSQL 15
- Redis 7

### Coverage Reporting

Coverage reports are uploaded to Codecov:
- Minimum coverage: 80%
- Reports attached to PR comments
- Trend tracking over time

### Local CI Simulation

Run the same checks locally:

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
./run_tests.sh all 80

# Run linters
black --check services/ tests/
ruff check services/ tests/
mypy services/

# Run security checks
safety check
bandit -r services/
```

---

## Debugging Tests

### Verbose Output

```bash
# Show detailed output
pytest -v

# Show local variables on failure
pytest --showlocals

# Show full traceback
pytest --tb=long

# Stop on first failure
pytest -x

# Drop into debugger on failure
pytest --pdb
```

### Selective Execution

```bash
# Run single test
pytest tests/unit/test_auth.py::TestPasswordHashing::test_hash_password

# Run tests matching pattern
pytest -k "password"

# Run last failed tests
pytest --lf

# Run failed tests first
pytest --ff
```

### Print Debugging

```python
def test_with_debug_output():
    data = complex_computation()

    # Print to console
    print(f"Data: {data}")

    # Use pytest's capsys
    import sys
    print("Debug info", file=sys.stderr)

    assert data is not None

# Run with print output
pytest -s tests/unit/test_file.py
```

---

## Test Performance

### Running Tests Faster

```bash
# Parallel execution (requires pytest-xdist)
pip install pytest-xdist
pytest -n auto

# Run only fast tests
pytest -m "not slow"

# Disable coverage for speed
pytest tests/unit
```

### Profiling Tests

```bash
# Show slowest tests
pytest --durations=10

# Profile test execution
pytest --profile

# Time each test
pytest --duration=0
```

---

## Common Patterns

### Testing Exception Handling

```python
def test_raises_exception():
    with pytest.raises(ValueError) as exc_info:
        function_that_raises("bad input")

    assert "expected error message" in str(exc_info.value)
```

### Testing Async Context Managers

```python
@pytest.mark.asyncio
async def test_async_context_manager():
    async with get_async_session() as session:
        result = await session.execute(query)
        assert result is not None
```

### Parametrized Tests

```python
@pytest.mark.parametrize("input,expected", [
    ("hello", "HELLO"),
    ("world", "WORLD"),
    ("", ""),
])
def test_uppercase(input, expected):
    assert input.upper() == expected
```

---

## Troubleshooting

### Common Issues

**Issue: `ImportError: No module named 'services'`**
```bash
# Solution: Add project root to PYTHONPATH
export PYTHONPATH=/path/to/Polyphony:$PYTHONPATH
pytest
```

**Issue: Database connection errors**
```bash
# Solution: Ensure test database exists
createdb polyphony_test

# Or use environment variable
export DATABASE_URL="postgresql://user:pass@localhost/polyphony_test"
```

**Issue: Redis connection errors**
```bash
# Solution: Start Redis or use fakeredis
# Tests should use fakeredis automatically

# Or start Redis
docker run -d -p 6379:6379 redis:7-alpine
```

**Issue: Async tests not running**
```bash
# Solution: Install pytest-asyncio
pip install pytest-asyncio

# Ensure tests are marked
@pytest.mark.asyncio
async def test_my_async_function():
    ...
```

---

## Resources

- [pytest Documentation](https://docs.pytest.org/)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [unittest.mock](https://docs.python.org/3/library/unittest.mock.html)
- [Coverage.py](https://coverage.readthedocs.io/)
- [Testing Best Practices](https://docs.python-guide.org/writing/tests/)

---

## Next Steps

### Increasing Coverage

Priority areas for additional tests:

1. **Character Agent Service** - Test agent dialogue generation
2. **Document Parser** - Test file parsing edge cases
3. **Frontend Components** - Add React component tests
4. **Load Testing** - Add performance tests with Locust
5. **E2E Tests** - Add Playwright/Cypress tests

### Test Infrastructure

- [ ] Set up test database seeding
- [ ] Add factory patterns for test data
- [ ] Create shared test utilities
- [ ] Add mutation testing (mutpy)
- [ ] Set up performance regression testing

---

**Last Updated**: 2025-11-18
**Maintainer**: Polyphony Team
**Coverage Target**: 80%+ (unit + integration)

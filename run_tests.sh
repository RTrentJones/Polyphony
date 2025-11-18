#!/bin/bash
# Test runner script for Polyphony

set -e

echo "🧪 Polyphony Test Suite"
echo "======================="
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Parse arguments
TEST_TYPE="${1:-all}"
COVERAGE_MIN="${2:-80}"

# Function to print colored output
print_status() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

# Check if pytest is installed
if ! python -m pytest --version > /dev/null 2>&1; then
    print_error "pytest not installed. Installing dependencies..."
    pip install -r requirements.txt
fi

# Create test database if needed
print_status "Setting up test environment..."

# Run tests based on type
case "$TEST_TYPE" in
    "unit")
        print_status "Running unit tests..."
        python -m pytest tests/unit -v \
            --cov=services \
            --cov-report=term-missing \
            --cov-report=html \
            --cov-report=xml \
            -m "unit and not llm" \
            --tb=short
        ;;

    "integration")
        print_status "Running integration tests..."
        python -m pytest tests/integration -v \
            --cov=services \
            --cov-append \
            --cov-report=term-missing \
            --cov-report=html \
            --cov-report=xml \
            -m "integration and not llm" \
            --tb=short
        ;;

    "all")
        print_status "Running all tests..."
        python -m pytest tests/ -v \
            --cov=services \
            --cov-report=term-missing \
            --cov-report=html \
            --cov-report=xml \
            -m "not llm" \
            --tb=short
        ;;

    "quick")
        print_status "Running quick test suite (unit tests only, no coverage)..."
        python -m pytest tests/unit -v \
            -m "unit and not slow and not llm" \
            --tb=short
        ;;

    "coverage")
        print_status "Running tests with detailed coverage report..."
        python -m pytest tests/ -v \
            --cov=services \
            --cov-report=term-missing:skip-covered \
            --cov-report=html \
            --cov-report=xml \
            --cov-branch \
            -m "not llm"

        echo ""
        print_status "Coverage report generated at: htmlcov/index.html"
        ;;

    *)
        print_error "Unknown test type: $TEST_TYPE"
        echo "Usage: ./run_tests.sh [unit|integration|all|quick|coverage] [min_coverage]"
        exit 1
        ;;
esac

# Check coverage threshold
if [ "$TEST_TYPE" != "quick" ]; then
    echo ""
    print_status "Checking coverage threshold (minimum: ${COVERAGE_MIN}%)..."

    if python -m coverage report --fail-under="${COVERAGE_MIN}" > /dev/null 2>&1; then
        print_status "Coverage meets minimum threshold of ${COVERAGE_MIN}%"
    else
        print_warning "Coverage below minimum threshold of ${COVERAGE_MIN}%"
        python -m coverage report
        exit 1
    fi
fi

echo ""
print_status "All tests completed successfully!"
echo ""
echo "📊 Coverage Report: htmlcov/index.html"
echo "📝 Test Results: Check terminal output above"
echo ""

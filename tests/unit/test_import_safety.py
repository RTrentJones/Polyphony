"""Guard against import-time dependency creep.

Importing the app must not pull in optional native dependencies (libmagic via
`magic`) — otherwise bare `pytest` collection breaks on machines without the
native library installed. Runs in a fresh interpreter so imports from other
tests can't pollute the check.
"""

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_app_import_does_not_load_optional_native_deps():
    env = {
        **os.environ,
        "SECRET_KEY": "test_secret_key_minimum_32_characters_long_12345",
        "POSTGRES_PASSWORD": "test_password_12345",
    }
    code = (
        "import sys\n"
        "import app.main\n"
        "assert 'magic' not in sys.modules, "
        "'magic must be imported lazily, not at app import time'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

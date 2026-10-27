import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="sea-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["COCKPIT_SECRET_KEY"] = "test"
os.environ["DEMO_MODE"] = "1"
os.environ["MCP_TOKEN"] = "mcp-test"
os.environ["BOOKINGS_WEBHOOK_SECRET"] = "hook-test"
os.environ["OPENAI_API_KEY"] = ""

from cockpit.auth import hash_password  # noqa: E402

os.environ["COCKPIT_USERS"] = f"chris:{hash_password('pw')}"

from cockpit import config  # noqa: E402

config.settings = config.Settings()  # Umgebung neu einlesen

from cockpit import demo  # noqa: E402
from cockpit.db import init_db, session_scope  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded():
    init_db()
    with session_scope() as s:
        demo.seed(s)
    yield


@pytest.fixture
def session():
    with session_scope() as s:
        yield s

"""The example environment must agree with the code's own defaults.

This has bitten twice, the same way both times: a default was changed in
base.py, .env.example kept the old value, and because the deployment's
environment variables were copied from .env.example, the stale value won in
production. Nothing failed - the setting was simply not what the code said it
was.

    MAX_UPLOAD_BYTES   code said 10 MB,  env said 200 MB -> uploads travelled
                                                            the whole way to
                                                            Cloudinary before
                                                            being refused
    JWT_ACCESS_MINUTES code said 1440,   env said 30     -> the mobile app was
                                                            logged out every
                                                            half hour

Neither is visible in a test that reads `settings`, because under test the
environment is whatever the runner has. So this reads both files as text and
compares the numbers directly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Settings where a mismatch changes behaviour quietly rather than loudly.
#: Add to this whenever a default is worth pinning, not every setting.
WATCHED = [
    "JWT_ACCESS_MINUTES",
    "JWT_REFRESH_DAYS",
    "MAX_UPLOAD_BYTES",
    "SESSION_COOKIE_AGE",
]


def code_default(name: str) -> int | None:
    """The fallback in base.py, with any arithmetic evaluated."""
    source = (ROOT / "config" / "settings" / "base.py").read_text(encoding="utf-8")
    match = re.search(rf'env_int\(\s*"{name}"\s*,\s*([^)]+?)\s*\)', source)
    if match is None:
        return None
    # The defaults are written as plain arithmetic - "10 * 1024 * 1024" - so
    # they read as sizes rather than as a number nobody can check.
    return int(eval(match.group(1), {"__builtins__": {}}, {}))  # noqa: S307


def example_value(name: str) -> int | None:
    source = (ROOT / ".env.example").read_text(encoding="utf-8")
    match = re.search(rf"^{name}=(\d+)\s*$", source, re.MULTILINE)
    return int(match.group(1)) if match else None


@pytest.mark.parametrize("name", WATCHED)
def test_the_example_env_matches_the_code_default(name):
    in_code = code_default(name)
    in_example = example_value(name)

    assert in_code is not None, f"{name} is no longer an env_int default in base.py"
    assert in_example is not None, f"{name} is missing from .env.example"
    assert in_code == in_example, (
        f"{name} disagrees: base.py says {in_code}, .env.example says {in_example}. "
        "Deployment environments are copied from .env.example, so the stale one "
        "wins in production and nothing fails to tell you."
    )


def test_the_access_token_lasts_a_working_day():
    """Pinned outright, because this is the one that logs people out.

    Anything much shorter needs the mobile app to refresh mid-session, and the
    app not doing that is what a person experiences as "it keeps logging me
    out".
    """
    assert code_default("JWT_ACCESS_MINUTES") == 1440
    assert example_value("JWT_ACCESS_MINUTES") == 1440

"""Root pytest configuration.

pytest imports the rootdir ``conftest.py`` before any test module, which is the only
reliable point to set environment variables: service settings are read from the
environment at import time and cached, so they must already point at the test
infrastructure by the time a service package is first imported.

Existing environment variables win, so CI can override the DSNs without editing code.
"""

from __future__ import annotations

import os

from marsool_core.testing import TEST_DATABASE_DSN, TEST_JWT_SECRET, TEST_REDIS_DSN

_TEST_ENVIRONMENT = {
    "MARSOOL_ENVIRONMENT": "test",
    "MARSOOL_LOG_LEVEL": "WARNING",
    "MARSOOL_LOG_FORMAT": "console",
    "MARSOOL_DATABASE_DSN": TEST_DATABASE_DSN,
    "MARSOOL_REDIS_DSN": TEST_REDIS_DSN,
    "MARSOOL_JWT_SECRET": TEST_JWT_SECRET,
    "MARSOOL_EVENT_BUS_BACKEND": "memory",
}

for key, value in _TEST_ENVIRONMENT.items():
    os.environ.setdefault(key, value)

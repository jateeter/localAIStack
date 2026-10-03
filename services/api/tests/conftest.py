"""
Put services/api/ on sys.path so tests can import the app modules
(`core`, `config`, `graphs`, `routers`) the same way main.py does.
"""

import pathlib
import sys

import pytest

_API_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))


@pytest.fixture(autouse=True)
def _fresh_engine_fanout():
    """Each test enumerates engines afresh and starts with an empty parity report.

    The e2e suite (tests/e2e) runs against a live stack from a minimal
    environment without the app's dependencies, and inherits this conftest; it
    never touches the bridge, so there is nothing to reset there.
    """
    try:
        from core import engine_fanout
    except ImportError:
        yield
        return

    engine_fanout.reset_cache()
    engine_fanout.reset_parity_report()
    yield
    engine_fanout.reset_cache()

"""VCR configuration for integration tests."""

import pytest
from vcr import VCR
from vcr.request import Request


@pytest.fixture(scope="module")
def vcr_config():
    return {
        "match_on": ["method", "scheme", "host", "port", "path", "query"],
    }


def _same_accept(recorded: Request, replayed: Request) -> None:
    """The one request header a cassette tells responses apart by.

    One URL answers JSON or XML depending on ``Accept``. Matching every header
    also matched urllib's ``User-Agent``, which names the Python version, so
    each Python release failed every such cassette.
    """
    assert recorded.headers.get("Accept") == replayed.headers.get("Accept")


def pytest_recording_configure(vcr: VCR) -> None:
    vcr.register_matcher("accept", _same_accept)

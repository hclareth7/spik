"""Tests for the web server's loopback-only bind guard (no network, no uvicorn).

Privacy invariant ("todo local"): the GUI exposes camera/mic and must never be reachable from
the network. The desktop shell (desktop/) picks a free localhost port via SPIK_PORT, but a
non-loopback SPIK_HOST must be refused and downgraded to 127.0.0.1.
"""

from __future__ import annotations

import pytest

from web.main import _loopback_host


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_hosts_are_kept(host):
    assert _loopback_host(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com", ""])
def test_non_loopback_is_downgraded(host):
    assert _loopback_host(host) == "127.0.0.1"


def test_none_defaults_to_loopback():
    assert _loopback_host(None) == "127.0.0.1"


def test_config_defaults():
    from spik import config

    # Defaults hold unless SPIK_HOST/SPIK_PORT are set in the environment.
    assert isinstance(config.WEB_PORT, int)
    assert config.WEB_HOST in ("127.0.0.1", "localhost", "::1")

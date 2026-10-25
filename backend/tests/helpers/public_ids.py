"""Shared helpers, from test_public_ids.py."""

import pytest

from mailr.core import publicid


def _set(monkeypatch, name, value):
    from mailr.config import get_settings

    monkeypatch.setenv(name, value)
    get_settings.cache_clear()


@pytest.fixture
def pub(monkeypatch):
    from mailr.config import get_settings

    _set(monkeypatch, "MAILR_PUBLIC_IDS", "true")
    _set(monkeypatch, "MAILR_PUBLIC_IDS_LEGACY", "false")
    yield publicid
    monkeypatch.undo()
    get_settings.cache_clear()

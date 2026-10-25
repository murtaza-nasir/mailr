"""The server's version (config.server_version): the image's MAILR_RELEASE, else the package's own;
GET /me carries it for the web's help menu and the phone's Settings."""

from importlib.metadata import version

from mailr.config import get_settings, server_version


def test_release_wins_without_its_v(monkeypatch):
    monkeypatch.setattr(get_settings(), "release", "v0.40.13")
    assert server_version() == "0.40.13"
    monkeypatch.setattr(get_settings(), "release", "sha-1234567")
    assert server_version() == "sha-1234567"


def test_without_a_release_the_package_version(monkeypatch):
    monkeypatch.setattr(get_settings(), "release", "")
    assert server_version() == version("mailr")


async def test_me_reports_it(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "release", "0.40.13")
    assert (await client.get("/api/v1/me")).json()["version"] == "0.40.13"

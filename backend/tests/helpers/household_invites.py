"""Shared helpers, from test_household_invites.py."""



def parse_phone(app_url: str) -> str:
    from urllib.parse import parse_qs, urlsplit

    return parse_qs(urlsplit(app_url).query)["invite"][0]

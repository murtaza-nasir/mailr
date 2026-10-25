"""Label colours: scheme tokens and the legacy hex forms.

A label colour is stored in one of three forms:

- a scheme token "@<scheme>/<hue>/<shade>", for example "@default/teal/3", resolved in the web and
  Android apps to a background and text colour for the light or the dark theme
  (spec/fixtures/label-colours.json);
- "#bg,#fg", Gmail's background and text pair (the form before schemes);
- "#bg", a background alone, shown with a readable text colour.

Only the form and the names are checked here; a token is never resolved to hex on the server.
"""

from __future__ import annotations

import re

# The ids in spec/fixtures/label-colours.json (compared in test_label_colours).
SCHEMES = ("default", "muted", "vivid", "pastel", "earth", "ocean")
HUES = ("red", "orange", "yellow", "green", "teal", "blue", "indigo", "purple", "pink", "grey")
SHADES = 4

_HEX = re.compile(r"^#[0-9a-f]{6}(,#[0-9a-f]{6})?$")
_TOKEN = re.compile(r"^@([a-z]+)/([a-z]+)/([0-9])$")

FORMS = "#rrggbb, #rrggbb,#rrggbb or a scheme colour such as @default/teal/3"


def parse_token(value: str | None) -> tuple[str, str, int] | None:
    """(scheme, hue, shade) for a scheme token, else None."""
    m = _TOKEN.match(value or "")
    if not m or m[1] not in SCHEMES or m[2] not in HUES or not 1 <= int(m[3]) <= SHADES:
        return None
    return m[1], m[2], int(m[3])


def normalize(value: str | None) -> str | None:
    """The stored form of a label colour (None clears it). "#bg|#fg" is accepted. ValueError
    for anything else."""
    if not value:
        return None
    v = value.strip().replace("|", ",").lower()
    if _HEX.match(v) or parse_token(v):
        return v
    raise ValueError(f"colour must be {FORMS}")


def rescheme(value: str | None, scheme: str) -> str | None:
    """The same hue and shade in another scheme; None when the colour is not a scheme token."""
    t = parse_token(value)
    if t is None:
        return None
    return f"@{scheme}/{t[1]}/{t[2]}"

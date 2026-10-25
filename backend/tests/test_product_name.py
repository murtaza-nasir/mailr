"""The product's name is "Mailr" in prose and `mailr` in code (tools/product_name.py).

Fails when (a) a lowercase `mailr` stands as a word in user-facing prose: the web catalogue
(frontend/src/locales/en.json), the server texts (backend/mailr/i18n/en.json), the Android strings
and widget/shortcut labels, the page title and manifest, the docs site name and the docs prose outside
code; or (b) "Mailr" appears in a code span, a fenced block or a command.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("product_name", ROOT / "tools" / "product_name.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pn = _load()


def test_user_facing_text_uses_the_name_and_code_keeps_the_command():
    problems = pn.check()
    assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])


def test_prose_is_flagged_and_code_is_not():
    assert pn.problems_string("Open mailr to read it.")
    assert pn.problems_string("mailr's colours")
    assert not pn.problems_string("Open Mailr to read it.")
    # identifiers, addresses, schemes, paths and commands stay lowercase
    for s in ("mailr://setup", "io.github.murtazanasir.mailr", "MAILR_URL", "mailr-cite:3", "/api/v1/app/mailr.apk",
              "the mailr.example.com host", "Run <code>mailr app-publish</code>.", "Run `mailr reembed`.",
              "docker compose exec app mailr create-user sam@example.com", "PRODID:-//mailr//calendar export//EN",
              "claude mcp add mailr --transport http {{origin}}/mcp"):
        assert not pn.problems_string(s), s


def test_capital_in_code_is_flagged():
    assert pn.problems_string("Run `Mailr reembed`.")
    assert pn.problems_string("Run <code>Mailr app-publish</code>.")
    assert pn.problems_markdown("Text\n\n```sh\ndocker compose exec app Mailr migrate\n```\n")
    assert not pn.problems_markdown("Text\n\n```sh\ndocker compose exec app mailr migrate\n```\n")


def test_markdown_fix_leaves_code_alone():
    src = ("Use mailr on your phone: run `mailr reembed`, see [the guide](user-guide/mailr.md) "
           "and <https://mailr.example.com>.\n\n```sh\nmailr migrate # mailr\n```\nYour mailr calendars.")
    out = pn.fix_markdown(src)
    assert out.startswith("Use Mailr on your phone: run `mailr reembed`, see [the guide](user-guide/mailr.md) "
                          "and <https://mailr.example.com>.")
    assert "```sh\nmailr migrate # mailr\n```" in out
    assert out.endswith("Your Mailr calendars.")
